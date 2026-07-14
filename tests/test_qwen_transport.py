"""Qwen transport layer: FakeQwen policies + LiveQwen guards + embeddings."""

from __future__ import annotations

import json
import sys
import types

import pytest

from tarmac_society.mediator import AgentView, MediatorView
from tarmac_society.qwen.models import BASE_URL, EMBED_MODEL, MEDIATOR_MODEL, ROLE_MODEL, TEMPERATURE
from tarmac_society.qwen.transport import (
    FakeQwen,
    LiveQwen,
    PersonaSpec,
    RoleAgent,
    TransportError,
    TransportMediator,
    _hash_embed,
    view_to_prompt_dict,
)
from tarmac_society.schemas import Deadlock, Ruling


class _StubPolicy:
    def propose(self, view):
        return ["PROPOSED"]

    def respond(self, view):
        return ["POSITION"]


class _StubMediator:
    def rule(self, deadlock, positions, view):
        return Ruling(deadlock_id=deadlock.id, decision="d", rationale="r", citations=["c"])

    def fiat(self, view):
        return Ruling(deadlock_id="d-fiat", decision="d", rationale="r", citations=["c"])


SPEC = PersonaSpec(name="x", display="X", objective="obj", system_prompt="sys")


def _view():
    return AgentView(
        round=1, agent="x", resources={}, granted_claims=[], blocked_claims=[],
        my_granted=[], my_blocked=[], open_contests=[], rulings=[], balances={},
        scenario={"name": "unit"}, private={"k": 1},
    )


def test_model_constants_are_verified_ids():
    assert ROLE_MODEL == "qwen3.7-plus"
    assert MEDIATOR_MODEL == "qwen3.7-max"
    assert EMBED_MODEL == "text-embedding-v4"
    assert TEMPERATURE == 0.2


def test_fakeqwen_delegates_to_policy():
    fq = FakeQwen({"x": _StubPolicy()}, _StubMediator())
    assert fq.propose(SPEC, _view()) == ["PROPOSED"]
    assert fq.respond(SPEC, _view()) == ["POSITION"]


def test_fakeqwen_unknown_persona_raises():
    fq = FakeQwen({}, _StubMediator())
    with pytest.raises(TransportError):
        fq.propose(SPEC, _view())


def test_fakeqwen_rule_and_fiat():
    fq = FakeQwen({"x": _StubPolicy()}, _StubMediator())
    dl = Deadlock(id="d-1", kind="cycle", round=1, resources=["r"], agents=["x"], claims=["c"])
    assert fq.rule(dl, [], _view()).deadlock_id == "d-1"
    assert fq.fiat(_view()).deadlock_id == "d-fiat"


def test_fakeqwen_without_mediator_policy_raises():
    fq = FakeQwen({"x": _StubPolicy()}, None)
    dl = Deadlock(id="d-1", kind="cycle", round=1, resources=["r"], agents=["x"], claims=["c"])
    with pytest.raises(TransportError):
        fq.rule(dl, [], _view())
    with pytest.raises(TransportError):
        fq.fiat(_view())


def test_hash_embed_is_deterministic_and_dim_64():
    v1 = _hash_embed("crew duty ferry")
    v2 = _hash_embed("crew duty ferry")
    assert v1 == v2 and len(v1) == 64
    assert _hash_embed("crew duty ferry") != _hash_embed("hotel voucher block")


def test_fakeqwen_embed_batches():
    fq = FakeQwen({}, None)
    out = fq.embed(["a", "b", "c"])
    assert len(out) == 3 and all(len(v) == 64 for v in out)


def test_role_agent_and_transport_mediator_wrap():
    fq = FakeQwen({"x": _StubPolicy()}, _StubMediator())
    agent = RoleAgent(SPEC, fq)
    assert agent.name == "x"
    assert agent.propose(_view()) == ["PROPOSED"]
    med = TransportMediator(fq)
    assert med.fiat(_view()).deadlock_id == "d-fiat"


def test_view_to_prompt_dict_has_shared_scenario():
    d = view_to_prompt_dict(_view())
    assert d["shared_scenario"] == {"name": "unit"}
    assert d["your_private_brief"] == {"k": 1}
    assert d["round"] == 1


def test_liveqwen_without_key_raises(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    lq = LiveQwen(api_key=None, client=None)
    with pytest.raises(TransportError):
        _ = lq.client


# --------------------------------------------------------------------------
# LiveQwen — a stub OpenAI-shaped client so the DashScope wire format,
# retry-on-invalid-JSON, and structured-output plumbing all run offline.
# --------------------------------------------------------------------------
class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _ChatResponse:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _ChatCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _ChatResponse(self._responses.pop(0))


class _Chat:
    def __init__(self, responses):
        self.completions = _ChatCompletions(responses)


class _EmbeddingDatum:
    def __init__(self, embedding):
        self.embedding = embedding


class _EmbeddingResponse:
    def __init__(self, vectors):
        self.data = [_EmbeddingDatum(v) for v in vectors]


class _Embeddings:
    def __init__(self, vectors):
        self._vectors = vectors
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _EmbeddingResponse(self._vectors)


class _StubClient:
    def __init__(self, chat_responses=None, embed_vectors=None):
        self.chat = _Chat(chat_responses or [])
        self.embeddings = _Embeddings(embed_vectors or [])


def _ruling_json(**kw):
    defaults = dict(deadlock_id="d-1", decision="dec", rationale="rat", citations=["c1"])
    defaults.update(kw)
    return Ruling(**defaults).model_dump_json()


# ------------------------------------------------------- client (lazy import)
def test_liveqwen_client_lazily_imports_openai_with_env_key(monkeypatch):
    captured = {}

    class _FakeOpenAI:
        def __init__(self, api_key, base_url):
            captured["api_key"] = api_key
            captured["base_url"] = base_url

    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = _FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test-env")

    lq = LiveQwen()
    client = lq.client
    assert isinstance(client, _FakeOpenAI)
    assert captured == {"api_key": "sk-test-env", "base_url": BASE_URL}
    assert lq.client is client  # cached, not re-constructed


def test_liveqwen_client_prefers_explicit_key_over_env(monkeypatch):
    captured = {}

    class _FakeOpenAI:
        def __init__(self, api_key, base_url):
            captured["api_key"] = api_key

    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = _FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-env")

    lq = LiveQwen(api_key="sk-explicit")
    _ = lq.client
    assert captured["api_key"] == "sk-explicit"


# ------------------------------------------------------------------- _chat
def test_liveqwen_chat_without_thinking_omits_extra_body():
    client = _StubClient(chat_responses=["hello"])
    lq = LiveQwen(client=client)
    out = lq._chat("model-x", "sys", "usr", thinking=False)
    assert out == "hello"
    kwargs = client.chat.completions.calls[0]
    assert "extra_body" not in kwargs
    assert kwargs["model"] == "model-x"
    assert kwargs["temperature"] == TEMPERATURE
    assert kwargs["messages"] == [
        {"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}
    ]


def test_liveqwen_chat_with_thinking_sets_extra_body():
    client = _StubClient(chat_responses=["thinking-out"])
    lq = LiveQwen(client=client)
    out = lq._chat("model-x", "sys", "usr", thinking=True)
    assert out == "thinking-out"
    assert client.chat.completions.calls[0]["extra_body"] == {"enable_thinking": True}


def test_liveqwen_chat_none_content_becomes_empty_string():
    client = _StubClient(chat_responses=[None])
    lq = LiveQwen(client=client)
    assert lq._chat("m", "s", "u", thinking=False) == ""


# --------------------------------------------------------------- _structured
def test_liveqwen_structured_valid_first_try():
    client = _StubClient(chat_responses=[_ruling_json(deadlock_id="d-solo")])
    lq = LiveQwen(client=client)
    out = lq._structured("m", "sys", "usr", Ruling)
    assert out.deadlock_id == "d-solo"
    assert len(client.chat.completions.calls) == 1


def test_liveqwen_structured_strips_markdown_json_fence():
    payload = "```json\n" + _ruling_json(deadlock_id="d-fenced") + "\n```"
    client = _StubClient(chat_responses=[payload])
    lq = LiveQwen(client=client)
    out = lq._structured("m", "sys", "usr", Ruling)
    assert out.deadlock_id == "d-fenced"


def test_liveqwen_structured_retries_after_validation_error_then_succeeds():
    bad = json.dumps({"deadlock_id": "d-3"})  # missing required fields
    good = _ruling_json(deadlock_id="d-3")
    client = _StubClient(chat_responses=[bad, good])
    lq = LiveQwen(client=client)
    out = lq._structured("m", "sys", "usr", Ruling)
    assert out.deadlock_id == "d-3"
    assert len(client.chat.completions.calls) == 2
    retry_prompt = client.chat.completions.calls[1]["messages"][1]["content"]
    assert "failed validation" in retry_prompt


def test_liveqwen_structured_exhausts_retries_returns_none():
    client = _StubClient(chat_responses=["not json {{{", "still not json {{{"])
    lq = LiveQwen(client=client)
    out = lq._structured("m", "sys", "usr", Ruling)
    assert out is None
    assert len(client.chat.completions.calls) == 2


# -------------------------------------------------------- propose / respond
def test_liveqwen_propose_filters_claims_to_own_agent():
    payload = json.dumps({
        "claims": [
            {"agent": "x", "resource": "seat:A", "qty": 1, "beneficiaries": ["p1"], "basis": "b"},
            {"agent": "y", "resource": "seat:A", "qty": 1, "beneficiaries": ["p2"], "basis": "b"},
        ]
    })
    client = _StubClient(chat_responses=[payload])
    lq = LiveQwen(client=client)
    claims = lq.propose(SPEC, _view())  # SPEC.name == "x"
    assert [c.agent for c in claims] == ["x"]


def test_liveqwen_propose_returns_empty_when_structured_output_fails():
    client = _StubClient(chat_responses=["garbage {{{", "still garbage {{{"])
    lq = LiveQwen(client=client)
    assert lq.propose(SPEC, _view()) == []


def test_liveqwen_respond_filters_positions_to_own_agent():
    payload = json.dumps({
        "positions": [
            {"agent": "x", "stance": "support", "target_claim": "c1", "argument": "a"},
            {"agent": "y", "stance": "support", "target_claim": "c2", "argument": "a"},
        ]
    })
    client = _StubClient(chat_responses=[payload])
    lq = LiveQwen(client=client)
    positions = lq.respond(SPEC, _view())
    assert [p.agent for p in positions] == ["x"]


def test_liveqwen_respond_returns_empty_when_structured_output_fails():
    client = _StubClient(chat_responses=["garbage {{{", "still garbage {{{"])
    lq = LiveQwen(client=client)
    assert lq.respond(SPEC, _view()) == []


# ------------------------------------------------------------- rule / fiat
def _med_view():
    return MediatorView(
        round=1, agent="__mediator__", resources={}, granted_claims=[], blocked_claims=[],
        my_granted=[], my_blocked=[], open_contests=[], rulings=[], balances={},
        scenario={"name": "unit"}, private={}, positions=[], deadlocks=[],
    )


def test_liveqwen_rule_returns_valid_ruling():
    client = _StubClient(chat_responses=[_ruling_json(deadlock_id="d-rule")])
    lq = LiveQwen(client=client)
    dl = Deadlock(id="d-rule", kind="cycle", round=1, resources=["r"], agents=["x"], claims=["c"])
    ruling = lq.rule(dl, [], _med_view())
    assert ruling.deadlock_id == "d-rule"
    assert client.chat.completions.calls[0]["extra_body"] == {"enable_thinking": True}


def test_liveqwen_rule_raises_after_exhausting_retries():
    client = _StubClient(chat_responses=["nope {{{", "still nope {{{"])
    lq = LiveQwen(client=client)
    dl = Deadlock(id="d-1", kind="cycle", round=1, resources=["r"], agents=["x"], claims=["c"])
    with pytest.raises(TransportError):
        lq.rule(dl, [], _med_view())


def test_liveqwen_fiat_returns_valid_ruling():
    client = _StubClient(chat_responses=[_ruling_json(deadlock_id="d-fiat")])
    lq = LiveQwen(client=client)
    ruling = lq.fiat(_med_view())
    assert ruling.deadlock_id == "d-fiat"


def test_liveqwen_fiat_raises_after_exhausting_retries():
    client = _StubClient(chat_responses=["nope {{{", "still nope {{{"])
    lq = LiveQwen(client=client)
    with pytest.raises(TransportError):
        lq.fiat(_med_view())


# ------------------------------------------------------------------- embed
def test_liveqwen_embed_calls_client_with_model_and_texts():
    client = _StubClient(embed_vectors=[[0.1, 0.2], [0.3, 0.4]])
    lq = LiveQwen(client=client)
    out = lq.embed(["a", "b"])
    assert out == [[0.1, 0.2], [0.3, 0.4]]
    assert client.embeddings.calls[0] == {"model": EMBED_MODEL, "input": ["a", "b"]}
