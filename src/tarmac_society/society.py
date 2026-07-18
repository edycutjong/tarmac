"""Society — the round engine.

Round loop (cap ``max_rounds``, default 6):

1. **commit** — every agent seals its new claims (`SHA256(claim || nonce)`)
   before *any* reveal happens (sealed-bid semantics);
2. **reveal** — the ledger verifies each reveal against its commitment
   (mismatch => rejected, I4) and applies it atomically (granted/blocked);
3. **positions** — agents file structured position papers; a blocking
   position on someone else's granted claim opens a *paid* contest
   (credibility currency); a yield releases/withdraws one's own claim;
4. **detect** — the deadlock detector scans (wait-for cycles + contested
   streaks);
5. **mediate** — each deadlock gets a binding, Ed25519-signed, citation-
   carrying ruling whose ops the ledger applies under full constraints;
6. **quiescence** — no new commits, no new positions, nothing blocked, no
   open contests -> the society is done.

If the cap is hit without quiescence, the mediator issues one final *fiat*
ruling; without a mediator, safety finalization voids everything still
contested (an unadjudicated challenge quarantines its target claim).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from random import Random
from typing import Any, Protocol

from .chainlog import ChainLog
from .currency import CredibilityBank
from .deadlock import DeadlockDetector
from .ledger import ClaimLedger, LedgerError
from .mediator import Agent, AgentView, Mediator, MediatorView
from .schemas import ClaimStatus, Deadlock, Position, Ruling, SignedRuling
from .signing import KeyPair, sign_body

__all__ = ["Society", "RunResult", "CitationResolver", "ProtocolError"]


class ProtocolError(Exception):
    pass


class CitationResolver(Protocol):
    def __call__(self, citation_id: str) -> dict[str, str] | None:
        """Return {'id', 'title', 'text', 'sha256'} or None if unknown."""


@dataclass
class RunResult:
    manifest: dict[str, list[str]]
    manifest_hash: str
    rounds_used: int
    quiescent: bool
    quiescent_round: int | None
    deadlocks: list[Deadlock]
    rulings: list[SignedRuling]
    chain_head: str
    chain_length: int
    reveal_rejections: int = 0


@dataclass
class Society:
    agents: list[Agent]
    mediator: Mediator | None
    ledger: ClaimLedger
    bank: CredibilityBank
    detector: DeadlockDetector
    chainlog: ChainLog
    keypair: KeyPair
    scenario: dict[str, Any]
    private_views: dict[str, dict[str, Any]] = field(default_factory=dict)
    citation_resolver: CitationResolver | None = None
    max_rounds: int = 6
    rng: Random | None = None

    def __post_init__(self) -> None:
        names = [a.name for a in self.agents]
        if len(set(names)) != len(names):
            raise ProtocolError("duplicate agent names")
        self._ruling_seq = 0
        self._positions: list[tuple[int, Position]] = []
        self._deadlocks: list[Deadlock] = []
        self._rulings: list[SignedRuling] = []
        self._reveal_rejections = 0

    # ------------------------------------------------------------------ views
    def _resources_view(self) -> dict[str, dict[str, Any]]:
        out = {}
        for rid, info in self.ledger.resources().items():
            out[rid] = {
                "capacity": info["capacity"],
                "group": info["group"],
                "free": self.ledger.free(rid),
            }
        return out

    def _view_for(self, agent_name: str, round_: int) -> AgentView:
        granted = self.ledger.granted_claims()
        blocked = self.ledger.blocked_claims()
        return AgentView(
            round=round_,
            agent=agent_name,
            resources=self._resources_view(),
            granted_claims=granted,
            blocked_claims=blocked,
            my_granted=[c for c in granted if c.proposal.agent == agent_name],
            my_blocked=[c for c in blocked if c.proposal.agent == agent_name],
            open_contests=self.bank.open_contests(),
            rulings=[r.model_dump(mode="json") for r in self._rulings],
            balances=self.bank.balances(),
            scenario=self.scenario,
            private=self.private_views.get(agent_name, {}),
        )

    def _mediator_view(
        self, round_: int, positions: list[Position], deadlocks: list[Deadlock]
    ) -> MediatorView:
        base = self._view_for("__mediator__", round_)
        return MediatorView(
            **{k: getattr(base, k) for k in base.__dataclass_fields__},
            positions=positions,
            deadlocks=deadlocks,
        )

    # ----------------------------------------------------------------- phases
    def _phase_commit_reveal(self, order: list[Agent], round_: int) -> tuple[int, int]:
        sealed: list[tuple[Agent, Any, str, str]] = []
        for agent in order:
            view = self._view_for(agent.name, round_)
            for proposal in agent.propose(view):
                if proposal.agent != agent.name:
                    raise ProtocolError(
                        f"agent {agent.name} proposed a claim as {proposal.agent!r}"
                    )
                commitment_id, nonce = self.ledger.seal_and_commit(proposal, round_)
                sealed.append((agent, proposal, commitment_id, nonce))
        rejected = 0
        for agent, proposal, commitment_id, nonce in sealed:
            try:
                rec = self.ledger.reveal(commitment_id, proposal, nonce, round_)
                if rec.status == ClaimStatus.REVEAL_REJECTED:
                    rejected += 1
            except LedgerError as exc:
                self.chainlog.append(
                    "claim_error",
                    {"agent": agent.name, "commitment_id": commitment_id, "error": str(exc)},
                    round_,
                )
        self._reveal_rejections += rejected
        return len(sealed), rejected

    def _phase_positions(self, order: list[Agent], round_: int) -> int:
        count = 0
        for agent in order:
            view = self._view_for(agent.name, round_)
            for pos in agent.respond(view):
                if pos.agent != agent.name:
                    raise ProtocolError(f"agent {agent.name} filed a position as {pos.agent!r}")
                count += 1
                self.chainlog.append("position", pos.model_dump(mode="json"), round_)
                self._positions.append((round_, pos))
                self._process_position(pos, round_)
        return count

    def _process_position(self, pos: Position, round_: int) -> None:
        try:
            target = self.ledger.claim(pos.target_claim)
        except LedgerError:
            self.chainlog.append(
                "position_note",
                {"agent": pos.agent, "note": f"target {pos.target_claim} unknown; recorded only"},
                round_,
            )
            return
        if pos.stance == "block":
            if target.proposal.agent == pos.agent:
                self.chainlog.append(
                    "position_note",
                    {"agent": pos.agent, "note": "cannot contest own claim; recorded only"},
                    round_,
                )
                return
            if target.status != ClaimStatus.GRANTED:
                # blocking a blocked claim adds nothing; it is already contested
                return
            self.bank.open_contest(pos, round_)
        elif pos.stance == "yield" and target.proposal.agent == pos.agent:
            if target.status == ClaimStatus.GRANTED:
                self.ledger.release(pos.agent, target.id, pos.release, round_)
            elif target.status == ClaimStatus.BLOCKED:
                self.ledger.withdraw_claim(pos.agent, target.id, round_)
        # support / third-party yield: recorded only

    def _sign_and_apply_ruling(
        self, ruling: Ruling, round_: int, deadlock: Deadlock | None
    ) -> SignedRuling | None:
        self._ruling_seq += 1
        ruling_id = f"r-{self._ruling_seq:02d}"
        citation_hashes: dict[str, str] = {}
        if self.citation_resolver is not None:
            # A live mediator model can cite a source id that isn't in the
            # regulation library (the deterministic FakeQwen never does). Drop
            # unknown citations rather than aborting the whole society run; if
            # nothing resolves we reject the ruling and leave the deadlock for
            # safety finalization, so I3 (recorded rulings cite >= 1 source) holds.
            valid_citations: list[str] = []
            for cid in ruling.citations:
                passage = self.citation_resolver(cid)
                if passage is None:
                    self.chainlog.append(
                        "ruling_citation_dropped",
                        {"ruling_id": ruling_id, "citation": str(cid)},
                        round_,
                    )
                    continue
                citation_hashes[cid] = passage["sha256"]
                valid_citations.append(cid)
            if not valid_citations:
                self.chainlog.append(
                    "ruling_rejected",
                    {
                        "ruling_id": ruling_id,
                        "reason": "no resolvable citations",
                        "cited": [str(c) for c in ruling.citations],
                    },
                    round_,
                )
                return None
            if valid_citations != list(ruling.citations):
                ruling = ruling.model_copy(update={"citations": valid_citations})
        unsigned = SignedRuling(
            ruling_id=ruling_id,
            round=round_,
            body=ruling,
            citation_hashes=citation_hashes,
            signature="",
            signer_public_hex=self.keypair.public_hex,
        )
        signature = sign_body(unsigned.signable_body(), self.keypair)
        signed = unsigned.model_copy(update={"signature": signature})
        self.chainlog.append("ruling", signed.model_dump(mode="json"), round_)
        self._rulings.append(signed)

        for op in ruling.ops:
            try:
                self.ledger.apply_ruling_ops([op], ruling_id, round_)
            except LedgerError as exc:
                self.chainlog.append(
                    "ruling_op_rejected",
                    {"ruling_id": ruling_id, "op": op.model_dump(mode="json"), "error": str(exc)},
                    round_,
                )

        self._settle_contests(ruling, round_, deadlock)
        for res in deadlock.resources if deadlock else []:
            self.detector.reset_resource(res)
        return signed

    def _settle_contests(self, ruling: Ruling, round_: int, deadlock: Deadlock | None) -> None:
        touched_claims = {op.claim_id for op in ruling.ops}
        if deadlock is not None:
            touched_claims.update(deadlock.claims)
        for contest in self.bank.open_contests():
            relevant = contest["target_claim"] in touched_claims
            if not relevant and contest["agent"] not in set(ruling.winners) | set(ruling.losers):
                continue
            if contest["agent"] in ruling.winners:
                self.bank.settle(contest["id"], won=True, round_=round_)
            elif contest["agent"] in ruling.losers:
                self.bank.settle(contest["id"], won=False, round_=round_)
            elif relevant:
                # the contested claim was adjudicated but the mediator did not
                # name this agent: refund the stake without premium
                self.bank.void_contest(contest["id"], round_)

    # -------------------------------------------------------------------- run
    def run(self) -> RunResult:
        n = len(self.agents)
        quiescent = False
        quiescent_round: int | None = None
        rounds_used = 0

        for round_ in range(1, self.max_rounds + 1):
            rounds_used = round_
            order = self.agents[(round_ - 1) % n :] + self.agents[: (round_ - 1) % n]
            self.chainlog.append(
                "round_start", {"round": round_, "order": [a.name for a in order]}, round_
            )

            commits, rejected = self._phase_commit_reveal(order, round_)
            positions = self._phase_positions(order, round_)

            deadlocks = self.detector.scan(self.ledger, self.bank.open_contests(), round_)
            for dl in deadlocks:
                self.chainlog.append("deadlock", dl.model_dump(mode="json"), round_)
                self._deadlocks.append(dl)

            if self.mediator is not None:
                for dl in deadlocks:
                    relevant_positions = [
                        p
                        for _, p in self._positions
                        if p.target_claim in dl.claims or p.agent in dl.agents
                    ]
                    med_view = self._mediator_view(round_, relevant_positions, [dl])
                    ruling = self.mediator.rule(dl, relevant_positions, med_view)
                    self._sign_and_apply_ruling(ruling, round_, dl)

            self.bank.snapshot(round_)
            blocked = self.ledger.blocked_claims()
            open_contests = self.bank.open_contests()
            self.chainlog.append(
                "round_end",
                {
                    "round": round_,
                    "commits": commits,
                    "reveals_rejected": rejected,
                    "positions": positions,
                    "blocked": len(blocked),
                    "open_contests": len(open_contests),
                    "deadlocks": len(deadlocks),
                },
                round_,
            )
            if commits == 0 and positions == 0 and not blocked and not open_contests:
                quiescent = True
                quiescent_round = round_
                self.chainlog.append("quiescent", {"round": round_}, round_)
                break

        if not quiescent:
            self._finalize_unquiesced(rounds_used)

        manifest = self.ledger.manifest()
        manifest_hash = self.ledger.manifest_hash()
        self.chainlog.append(
            "manifest",
            {
                "manifest": manifest,
                "manifest_hash": manifest_hash,
                "rounds_used": rounds_used,
                "quiescent": quiescent,
            },
            rounds_used,
        )
        return RunResult(
            manifest=manifest,
            manifest_hash=manifest_hash,
            rounds_used=rounds_used,
            quiescent=quiescent,
            quiescent_round=quiescent_round,
            deadlocks=list(self._deadlocks),
            rulings=list(self._rulings),
            chain_head=self.chainlog.head,
            chain_length=self.chainlog.length,
            reveal_rejections=self._reveal_rejections,
        )

    def _finalize_unquiesced(self, round_: int) -> None:
        """Round cap hit: mediator fiat, or safety finalization without one."""
        if self.mediator is not None:
            blocked = self.ledger.blocked_claims()
            contests = self.bank.open_contests()
            fiat_dl = Deadlock(
                id="d-fiat",
                kind="contested",
                round=round_,
                resources=sorted({c.proposal.resource for c in blocked}),
                agents=sorted({c.proposal.agent for c in blocked} | {c["agent"] for c in contests}),
                claims=sorted(
                    {c.id for c in blocked} | {c["target_claim"] for c in contests}
                ),
                detail="round cap reached without quiescence",
            )
            all_positions = [p for _, p in self._positions]
            med_view = self._mediator_view(round_, all_positions, [fiat_dl])
            ruling = self.mediator.fiat(med_view)
            self.chainlog.append("fiat", {"round": round_}, round_)
            self._sign_and_apply_ruling(ruling, round_, fiat_dl)
            for contest in self.bank.open_contests():
                self.bank.void_contest(contest["id"], round_)
            for rec in self.ledger.blocked_claims():
                self.ledger.void_claim(rec.id, "unresolved at round cap (post-fiat)", round_)
        else:
            # No mediator: an unadjudicated challenge quarantines its target —
            # a disputed grant may not board. Blocked claims die unserved.
            self.chainlog.append("safety_finalize", {"round": round_}, round_)
            for contest in self.bank.open_contests():
                try:
                    target = self.ledger.claim(contest["target_claim"])
                    if target.status == ClaimStatus.GRANTED:
                        self.ledger.void_claim(
                            target.id, f"unadjudicated challenge {contest['id']}", round_
                        )
                except LedgerError:
                    pass
                self.bank.void_contest(contest["id"], round_)
            for rec in self.ledger.blocked_claims():
                self.ledger.void_claim(rec.id, "unresolved at round cap (no mediator)", round_)
        self.bank.snapshot(round_ + 1)
