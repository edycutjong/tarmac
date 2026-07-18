"""Qwen transport abstraction: LiveQwen (DashScope) and FakeQwen (policies).

The society never talks to a model directly; it talks to a
``QwenTransport``. Swapping the transport swaps the agents' *brains* while
every other part — sealed bids, ledger physics, deadlock detection,
mediation, signing, logging — stays identical:

- **LiveQwen** — the OpenAI SDK pointed at DashScope's compatible mode
  (``https://dashscope-intl.aliyuncs.com/compatible-mode/v1``,
  ``DASHSCOPE_API_KEY``). Role agents run on ``qwen3.7-plus``; the mediator
  runs on ``qwen3.7-max`` with thinking enabled; regulation citations are
  retrieved with ``text-embedding-v4``. Structured output = the JSON schema
  of the pydantic wire type embedded in the prompt + strict pydantic
  validation with exactly one reject-and-retry.

- **FakeQwen** — deterministic, rule-based *policy* implementations of each
  persona's objective (not canned transcripts). Given the same views the
  LLM would get, the policies genuinely negotiate, collide, deadlock and
  resolve, so the whole protocol machinery is exercised offline with zero
  keys and byte-stable results.
"""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError

from ..canonical import sha256_hex
from ..mediator import Agent, AgentView, Mediator, MediatorView
from ..schemas import ClaimProposal, Deadlock, Position, Ruling
from .models import API_KEY_ENV, BASE_URL, EMBED_MODEL, MEDIATOR_MODEL, ROLE_MODEL, TEMPERATURE

__all__ = [
    "PersonaSpec",
    "PersonaPolicy",
    "MediatorPolicy",
    "QwenTransport",
    "FakeQwen",
    "LiveQwen",
    "RoleAgent",
    "TransportMediator",
    "TransportError",
]


class TransportError(Exception):
    pass


@dataclass(frozen=True)
class PersonaSpec:
    """A role agent's identity: public objective + prompt for live mode."""

    name: str
    display: str
    objective: str
    system_prompt: str


class PersonaPolicy(Protocol):
    def propose(self, view: AgentView) -> list[ClaimProposal]: ...
    def respond(self, view: AgentView) -> list[Position]: ...


class MediatorPolicy(Protocol):
    def rule(self, deadlock: Deadlock, positions: list[Position], view: MediatorView) -> Ruling: ...
    def fiat(self, view: MediatorView) -> Ruling: ...


class QwenTransport(ABC):
    """Decision-level interface between the society and its brains."""

    @abstractmethod
    def propose(self, persona: PersonaSpec, view: AgentView) -> list[ClaimProposal]: ...

    @abstractmethod
    def respond(self, persona: PersonaSpec, view: AgentView) -> list[Position]: ...

    @abstractmethod
    def rule(self, deadlock: Deadlock, positions: list[Position], view: MediatorView) -> Ruling: ...

    @abstractmethod
    def fiat(self, view: MediatorView) -> Ruling: ...

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...


# --------------------------------------------------------------------------
# Society adapters (transport-agnostic)
# --------------------------------------------------------------------------
class RoleAgent(Agent):
    """Thin persona shell; the transport supplies the decisions."""

    def __init__(self, persona: PersonaSpec, transport: QwenTransport) -> None:
        self.persona = persona
        self.transport = transport
        self.name = persona.name

    def propose(self, view: AgentView) -> list[ClaimProposal]:
        return self.transport.propose(self.persona, view)

    def respond(self, view: AgentView) -> list[Position]:
        return self.transport.respond(self.persona, view)


class TransportMediator(Mediator):
    name = "duty_manager"

    def __init__(self, transport: QwenTransport) -> None:
        self.transport = transport

    def rule(self, deadlock: Deadlock, positions: list[Position], view: MediatorView) -> Ruling:
        return self.transport.rule(deadlock, positions, view)

    def fiat(self, view: MediatorView) -> Ruling:
        return self.transport.fiat(view)


# --------------------------------------------------------------------------
# FakeQwen — deterministic policy agents (offline mode)
# --------------------------------------------------------------------------
_EMBED_DIM = 64
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _hash_embed(text: str) -> list[float]:
    """Deterministic feature-hash embedding (offline stand-in for text-embedding-v4)."""
    vec = [0.0] * _EMBED_DIM
    for token in _TOKEN_RE.findall(text.lower()):
        h = sha256_hex(token)
        idx = int(h[:8], 16) % _EMBED_DIM
        sign = 1.0 if int(h[8], 16) % 2 == 0 else -1.0
        vec[idx] += sign
    return vec


class FakeQwen(QwenTransport):
    """Deterministic scripted POLICY agents.

    Each persona name maps to a rule-based implementation of that persona's
    objective; the mediator maps to a rule-based adjudication policy. These
    are decision *policies* over live ledger views — they earn their
    grants/blocks/rulings through the same machinery live agents use.
    """

    def __init__(
        self,
        policies: dict[str, PersonaPolicy],
        mediator_policy: MediatorPolicy | None = None,
    ) -> None:
        self.policies = dict(policies)
        self.mediator_policy = mediator_policy

    def _policy(self, persona: PersonaSpec) -> PersonaPolicy:
        try:
            return self.policies[persona.name]
        except KeyError as exc:
            raise TransportError(f"no offline policy registered for persona {persona.name!r}") from exc

    def propose(self, persona: PersonaSpec, view: AgentView) -> list[ClaimProposal]:
        return self._policy(persona).propose(view)

    def respond(self, persona: PersonaSpec, view: AgentView) -> list[Position]:
        return self._policy(persona).respond(view)

    def rule(self, deadlock: Deadlock, positions: list[Position], view: MediatorView) -> Ruling:
        if self.mediator_policy is None:
            raise TransportError("no offline mediator policy registered")
        return self.mediator_policy.rule(deadlock, positions, view)

    def fiat(self, view: MediatorView) -> Ruling:
        if self.mediator_policy is None:
            raise TransportError("no offline mediator policy registered")
        return self.mediator_policy.fiat(view)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_hash_embed(t) for t in texts]


# --------------------------------------------------------------------------
# LiveQwen — DashScope via the OpenAI SDK (live mode)
# --------------------------------------------------------------------------
class _ProposeBundle(BaseModel):
    claims: list[ClaimProposal] = Field(default_factory=list)


class _RespondBundle(BaseModel):
    positions: list[Position] = Field(default_factory=list)


def view_to_prompt_dict(view: AgentView) -> dict[str, Any]:
    """Serializable slice of a view for prompting (shared prefix first)."""
    d: dict[str, Any] = {
        "shared_scenario": view.scenario,  # stable prefix — context-cache friendly
        "round": view.round,
        "resources": view.resources,
        "granted_claims": [c.model_dump(mode="json") for c in view.granted_claims],
        "blocked_claims": [c.model_dump(mode="json") for c in view.blocked_claims],
        "open_contests": view.open_contests,
        "rulings_so_far": view.rulings,
        "credibility_balances": view.balances,
        "your_private_brief": view.private,
    }
    return d


_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LiveQwen(QwenTransport):
    """Live Qwen Cloud transport (needs ``DASHSCOPE_API_KEY``).

    Structured output contract: the JSON schema of the expected pydantic
    type is embedded in the prompt; the reply must be a single JSON object.
    Invalid output gets exactly one reject-and-retry with the validation
    errors quoted; a second failure is a hard ``TransportError`` for the
    mediator (rulings must not be improvised) and a safe no-op for role
    agents (the round simply proceeds without them).
    """

    def __init__(self, api_key: str | None = None, client: Any | None = None) -> None:
        self._client = client
        self._api_key = api_key

    @property
    def client(self) -> Any:
        if self._client is None:
            api_key = self._api_key or os.environ.get(API_KEY_ENV)
            if not api_key:
                raise TransportError(
                    f"{API_KEY_ENV} is not set — live mode needs a Qwen Cloud key "
                    "(offline mode: omit --live)"
                )
            from openai import OpenAI  # lazy: offline installs never import this

            # A society run makes dozens of sequential calls; a per-request
            # timeout keeps one slow response from hanging the whole run.
            self._client = OpenAI(
                api_key=api_key, base_url=BASE_URL, timeout=120.0, max_retries=2
            )
        return self._client

    # ------------------------------------------------------------- plumbing
    def _chat(self, model: str, system: str, user: str, thinking: bool) -> str:
        kwargs: dict[str, Any] = {
            "model": model,
            "temperature": TEMPERATURE,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        # Qwen3 models default to "thinking" mode, which spends thousands of
        # reasoning tokens per call (~45s+, and it times out on the large
        # negotiation-state prompts). Only the mediator needs it; role agents
        # (propose/respond) run with thinking OFF, which is both faster and
        # sufficient for structured JSON emission.
        kwargs["extra_body"] = {"enable_thinking": bool(thinking)}
        resp = self.client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    def _structured(
        self,
        model: str,
        system: str,
        user: str,
        schema: type[BaseModel],
        thinking: bool = False,
    ) -> BaseModel | None:
        schema_json = json.dumps(schema.model_json_schema(), indent=None, sort_keys=True)
        contract = (
            "\n\nRespond with ONE JSON object only — no prose, no markdown fences —"
            f" valid against this JSON schema:\n{schema_json}"
        )
        attempt_user = user
        last_error = ""
        for attempt in range(2):  # initial + one reject-retry
            raw = self._chat(model, system + contract, attempt_user, thinking)
            text = _JSON_FENCE.sub("", raw.strip()).strip()
            try:
                return schema.model_validate_json(text)
            except ValidationError as exc:
                last_error = str(exc)
                attempt_user = (
                    user
                    + "\n\nYour previous reply failed validation with these errors:\n"
                    + last_error
                    + "\nReply again with ONE corrected JSON object."
                )
            except Exception as exc:  # malformed JSON
                last_error = str(exc)
                attempt_user = (
                    user
                    + "\n\nYour previous reply was not parseable JSON ("
                    + last_error
                    + "). Reply again with ONE JSON object only."
                )
        return None

    # ------------------------------------------------------------ decisions
    def propose(self, persona: PersonaSpec, view: AgentView) -> list[ClaimProposal]:
        user = (
            "Current negotiation state:\n"
            + json.dumps(view_to_prompt_dict(view), sort_keys=True)
            + f"\n\nYou are agent '{persona.name}'. Emit the NEW claims you want to seal"
            " this round (empty list if none). Every claim's 'agent' field must be"
            f" exactly '{persona.name}'."
        )
        out = self._structured(ROLE_MODEL, persona.system_prompt, user, _ProposeBundle)
        if out is None:
            return []  # safe no-op; the round proceeds without this agent
        return [c for c in out.claims if c.agent == persona.name]

    def respond(self, persona: PersonaSpec, view: AgentView) -> list[Position]:
        user = (
            "Current negotiation state:\n"
            + json.dumps(view_to_prompt_dict(view), sort_keys=True)
            + f"\n\nYou are agent '{persona.name}'. File your position papers on contested"
            " claims (empty list if none). stance='block' costs credibility and MUST cite"
            " at least one regulation/policy id from your brief."
        )
        out = self._structured(ROLE_MODEL, persona.system_prompt, user, _RespondBundle)
        if out is None:
            return []
        return [p for p in out.positions if p.agent == persona.name]

    _MEDIATOR_SYSTEM = (
        "You are the Duty Manager — the binding mediator of an airline IRROPS agent"
        " society. You adjudicate mechanically detected deadlocks over the claim"
        " ledger. Rule on the structured position papers, respect hard constraints"
        " (crew duty limits are inviolable), prioritize protected passengers"
        " (medical deadline > unaccompanied minor > wheelchair > tight connection),"
        " keep every seat allocation within capacity, and CITE at least one"
        " regulation/policy id for every ruling. Your ops may only revoke granted"
        " claims, grant blocked claims (optionally partial), or void claims."
    )

    def rule(self, deadlock: Deadlock, positions: list[Position], view: MediatorView) -> Ruling:
        user = (
            "Deadlock:\n"
            + json.dumps(deadlock.model_dump(mode="json"), sort_keys=True)
            + "\n\nPosition papers:\n"
            + json.dumps([p.model_dump(mode="json") for p in positions], sort_keys=True)
            + "\n\nLedger state:\n"
            + json.dumps(view_to_prompt_dict(view), sort_keys=True)
            + "\n\nIssue your binding Ruling."
        )
        out = self._structured(MEDIATOR_MODEL, self._MEDIATOR_SYSTEM, user, Ruling, thinking=True)
        if out is None:
            raise TransportError("mediator produced invalid Ruling twice; refusing to improvise")
        return out

    def fiat(self, view: MediatorView) -> Ruling:
        user = (
            "The round cap was reached without quiescence. Resolve EVERYTHING still"
            " blocked or contested in one final binding ruling.\n\nLedger state:\n"
            + json.dumps(view_to_prompt_dict(view), sort_keys=True)
        )
        out = self._structured(MEDIATOR_MODEL, self._MEDIATOR_SYSTEM, user, Ruling, thinking=True)
        if out is None:
            raise TransportError("mediator produced invalid fiat Ruling twice")
        return out

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self.client.embeddings.create(model=EMBED_MODEL, input=list(texts))
        return [d.embedding for d in resp.data]
