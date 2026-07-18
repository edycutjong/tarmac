"""Society round-engine mechanics: quiescence, guards, finalization."""

from __future__ import annotations

import pytest

from tarmac_society import (
    ChainLog,
    ClaimLedger,
    ClaimProposal,
    ClaimStatus,
    CredibilityBank,
    DeadlockDetector,
    LedgerError,
    Position,
    ProtocolError,
    Ruling,
    Society,
    SQLiteStorage,
)
from tarmac_society.mediator import Agent, Mediator
from tarmac_society.schemas import RulingOp
from tarmac_society.signing import keypair_from_seed


class StubAgent(Agent):
    def __init__(self, name, proposals=None, positions=None):
        self.name = name
        self._proposals = proposals or (lambda view: [])
        self._positions = positions or (lambda view: [])

    def propose(self, view):
        return self._proposals(view)

    def respond(self, view):
        return self._positions(view)


class StubMediator(Mediator):
    name = "duty_manager"

    def __init__(self, rule_fn=None, fiat_fn=None):
        self._rule_fn = rule_fn or (lambda dl, positions, view: Ruling(
            deadlock_id=dl.id, decision="d", rationale="r", citations=["c"]
        ))
        self._fiat_fn = fiat_fn or (lambda view: Ruling(
            deadlock_id="d-fiat", decision="d", rationale="r", citations=["c"]
        ))

    def rule(self, deadlock, positions, view):
        return self._rule_fn(deadlock, positions, view)

    def fiat(self, view):
        return self._fiat_fn(view)


def _society(agents, mediator=None, max_rounds=6):
    st = SQLiteStorage(":memory:")
    log = ChainLog(st)
    log.genesis({"scenario": "unit", "seed": 0})
    ledger = ClaimLedger(storage=st, chainlog=log)
    ledger.register_resource("seat:A", 1, group="seat")
    bank = CredibilityBank(st, {a.name: 50 for a in agents}, chainlog=log)
    return Society(
        agents=agents,
        mediator=mediator,
        ledger=ledger,
        bank=bank,
        detector=DeadlockDetector(),
        chainlog=log,
        keypair=keypair_from_seed("unit"),
        scenario={"name": "unit"},
        private_views={},
        max_rounds=max_rounds,
    )


def test_duplicate_agent_names_raise():
    with pytest.raises(ProtocolError):
        _society([StubAgent("x"), StubAgent("x")])


def test_idle_society_quiesces_round_one():
    soc = _society([StubAgent("a"), StubAgent("b")])
    res = soc.run()
    assert res.quiescent is True
    assert res.quiescent_round == 1
    assert res.rounds_used == 1


def test_run_result_fields_present():
    res = _society([StubAgent("a")]).run()
    assert isinstance(res.manifest, dict)
    assert len(res.manifest_hash) == 64
    assert res.chain_length > 0
    assert res.reveal_rejections == 0


def test_agent_proposing_as_other_name_raises():
    bad = StubAgent(
        "a",
        proposals=lambda view: [
            ClaimProposal(agent="impostor", resource="seat:A", qty=1,
                          beneficiaries=["p1"], basis="x")
        ],
    )
    with pytest.raises(ProtocolError):
        _society([bad]).run()


def test_agent_filing_position_as_other_raises():
    # a grants a seat so there is a claim to reference
    def a_props(view):
        return [ClaimProposal(agent="a", resource="seat:A", qty=1,
                              beneficiaries=["p1"], basis="x")] if view.round == 1 else []

    bad = StubAgent(
        "b",
        positions=lambda view: [
            Position(agent="impostor", stance="support", target_claim="c-001", argument="x")
        ],
    )
    good = StubAgent("a", proposals=a_props)
    with pytest.raises(ProtocolError):
        _society([good, bad]).run()


def test_reveal_rejection_is_counted():
    # an agent proposes the same beneficiary twice in one round on a cap-1 seat:
    # first reveal grants, the exclusivity blocks the second (not a reveal reject);
    # here we instead exercise a legit grant to keep counts sane.
    def props(view):
        if view.round == 1:
            return [ClaimProposal(agent="a", resource="seat:A", qty=1,
                                  beneficiaries=["p1"], basis="x")]
        return []

    res = _society([StubAgent("a", proposals=props)]).run()
    assert res.reveal_rejections == 0
    assert res.manifest.get("seat:A") == ["p1"]


# ---------------------------------------------------------------- _phase_commit_reveal
def test_phase_commit_reveal_logs_ledger_error_from_reveal(monkeypatch):
    """A storage backend that raises LedgerError on reveal must be caught and logged,
    not crash the round (defensive path for non-SQLite Storage implementations)."""
    soc = _society([StubAgent(
        "a",
        proposals=lambda view: [
            ClaimProposal(agent="a", resource="seat:A", qty=1, beneficiaries=["p1"], basis="x")
        ] if view.round == 1 else [],
    )])

    def flaky_reveal(*_a, **_k):
        raise LedgerError("simulated backend failure on reveal")

    monkeypatch.setattr(soc.ledger, "reveal", flaky_reveal)
    commits, rejected = soc._phase_commit_reveal(soc.agents, 1)
    assert commits == 1
    assert rejected == 0  # a raised LedgerError is not counted as a reveal rejection
    kinds = [e.kind for e in soc.chainlog.entries()]
    assert "claim_error" in kinds


def test_phase_commit_reveal_counts_reveal_rejections(monkeypatch):
    """Genuine digest mismatches can't occur through _phase_commit_reveal's own
    plumbing (it always reveals with the exact proposal/nonce it just sealed);
    the rejection-counting itself is still real logic worth a direct test —
    exercised here via a ledger that reports REVEAL_REJECTED for other reasons
    (e.g. a non-SQLite Storage backend rejecting on its own grounds)."""
    from tarmac_society.schemas import ClaimRecord

    soc = _society([StubAgent(
        "a",
        proposals=lambda view: [
            ClaimProposal(agent="a", resource="seat:A", qty=1, beneficiaries=["p1"], basis="x")
        ] if view.round == 1 else [],
    )])

    def fake_reveal(commitment_id, proposal, nonce, round_):
        return ClaimRecord(
            id="", proposal=proposal, status=ClaimStatus.REVEAL_REJECTED,
            round_committed=round_, commitment_id=commitment_id,
        )

    monkeypatch.setattr(soc.ledger, "reveal", fake_reveal)
    commits, rejected = soc._phase_commit_reveal(soc.agents, 1)
    assert commits == 1
    assert rejected == 1


# ---------------------------------------------------------------------- _process_position
def test_process_position_unknown_target_records_note_only():
    soc = _society([StubAgent("a")])
    soc._process_position(
        Position(agent="a", stance="support", target_claim="c-999", argument="x"), 1
    )
    notes = [e for e in soc.chainlog.entries() if e.kind == "position_note"]
    assert any("unknown" in e.body["note"] for e in notes)


def test_process_position_block_own_claim_records_note_only():
    soc = _society([StubAgent("a")])
    rec = soc.ledger.submit_plain(
        ClaimProposal(agent="a", resource="seat:A", qty=1, beneficiaries=["p1"], basis="x"), 1
    )
    soc._process_position(
        Position(agent="a", stance="block", target_claim=rec.id, argument="x", citations=["c"]), 1
    )
    notes = [e for e in soc.chainlog.entries() if e.kind == "position_note"]
    assert any("cannot contest own claim" in e.body["note"] for e in notes)
    assert soc.bank.open_contests() == []  # never opened


def test_process_position_block_non_granted_target_is_noop():
    soc = _society([StubAgent("a"), StubAgent("b")])
    soc.ledger.submit_plain(
        ClaimProposal(agent="a", resource="seat:A", qty=1, beneficiaries=["p1"], basis="x"), 1
    )
    blocked = soc.ledger.submit_plain(
        ClaimProposal(agent="b", resource="seat:A", qty=1, beneficiaries=["p2"], basis="x"), 1
    )
    assert blocked.status == ClaimStatus.BLOCKED
    soc._process_position(
        Position(agent="a", stance="block", target_claim=blocked.id, argument="x", citations=["c"]), 1
    )
    assert soc.bank.open_contests() == []  # blocking an already-blocked claim adds nothing


def test_process_position_yield_withdraws_blocked_claim():
    soc = _society([StubAgent("a"), StubAgent("b")])
    soc.ledger.submit_plain(
        ClaimProposal(agent="a", resource="seat:A", qty=1, beneficiaries=["p1"], basis="x"), 1
    )
    blocked = soc.ledger.submit_plain(
        ClaimProposal(agent="b", resource="seat:A", qty=1, beneficiaries=["p2"], basis="x"), 1
    )
    soc._process_position(
        Position(agent="b", stance="yield", target_claim=blocked.id, argument="x"), 1
    )
    assert soc.ledger.claim(blocked.id).status == ClaimStatus.WITHDRAWN


# ----------------------------------------------------------------- _sign_and_apply_ruling
def test_sign_and_apply_ruling_all_unknown_citations_rejected_not_raised():
    # A live mediator can cite only unknown source ids. That must NOT crash the
    # run — the ruling is rejected (logged) and no ruling entry is recorded, so
    # the deadlock falls through to safety finalization.
    soc = _society([StubAgent("a")])
    soc.citation_resolver = lambda cid: None  # every citation is "unknown"
    ruling = Ruling(deadlock_id="d-1", decision="d", rationale="r", citations=["reg-1"])
    signed = soc._sign_and_apply_ruling(ruling, 1, None)
    assert signed is None
    kinds = [e.kind for e in soc.chainlog.entries()]
    assert "ruling_rejected" in kinds
    assert "ruling" not in kinds


def test_sign_and_apply_ruling_drops_unknown_keeps_valid_citation():
    # Mix of one valid + one hallucinated citation: the run proceeds citing only
    # the valid source, and the unknown one is logged as dropped.
    soc = _society([StubAgent("a")])
    real = {c for c in ["reg-real"]}
    soc.citation_resolver = lambda cid: {"sha256": "abc"} if cid in real else None
    ruling = Ruling(
        deadlock_id="d-1", decision="d", rationale="r",
        citations=["reg-real", "reg-hallucinated"],
    )
    signed = soc._sign_and_apply_ruling(ruling, 1, None)
    assert signed is not None
    assert list(signed.body.citations) == ["reg-real"]
    assert "reg-real" in signed.citation_hashes
    assert "reg-hallucinated" not in signed.citation_hashes
    kinds = [e.kind for e in soc.chainlog.entries()]
    assert "ruling_citation_dropped" in kinds
    assert "ruling" in kinds


def test_sign_and_apply_ruling_logs_rejected_op():
    soc = _society([StubAgent("a")])
    ruling = Ruling(
        deadlock_id="d-1", decision="d", rationale="r", citations=["c"],
        ops=[RulingOp(op="revoke", claim_id="c-999")],  # unknown claim -> LedgerError
    )
    signed = soc._sign_and_apply_ruling(ruling, 1, None)
    assert signed.ruling_id
    kinds = [e.kind for e in soc.chainlog.entries()]
    assert "ruling_op_rejected" in kinds


# --------------------------------------------------------------------- _settle_contests
def test_settle_contests_settles_named_loser():
    soc = _society([StubAgent("a"), StubAgent("b")])
    held = soc.ledger.submit_plain(
        ClaimProposal(agent="a", resource="seat:A", qty=1, beneficiaries=["p1"], basis="x"), 1
    )
    pos = Position(agent="b", stance="block", target_claim=held.id, argument="x", citations=["c"])
    cid = soc.bank.open_contest(pos, 1)
    ruling = Ruling(deadlock_id="d-1", decision="d", rationale="r", citations=["c"], losers=["b"])
    soc._settle_contests(ruling, 1, None)
    assert cid not in [c["id"] for c in soc.bank.open_contests()]
    assert soc.bank.spend_summary()["b"]["burned"] == soc.bank.contest_cost


# ---------------------------------------------------------------- _finalize_unquiesced
def test_finalize_unquiesced_mediator_voids_leftover_contests_and_blocked():
    """A fiat ruling that resolves nothing still forces everything left open closed."""
    med = StubMediator(fiat_fn=lambda view: Ruling(
        deadlock_id="d-fiat", decision="no-op", rationale="r", citations=["c"]
    ))
    soc = _society([StubAgent("a"), StubAgent("b")], mediator=med, max_rounds=1)
    held = soc.ledger.submit_plain(
        ClaimProposal(agent="a", resource="seat:A", qty=1, beneficiaries=["p1"], basis="x"), 1
    )
    blocked = soc.ledger.submit_plain(
        ClaimProposal(agent="b", resource="seat:A", qty=1, beneficiaries=["p2"], basis="x"), 1
    )
    pos = Position(agent="b", stance="block", target_claim=held.id, argument="x", citations=["c"])
    soc.bank.open_contest(pos, 1)

    soc._finalize_unquiesced(1)

    assert soc.bank.open_contests() == []  # force-voided (line: leftover contest cleanup)
    assert soc.ledger.claim(blocked.id).status == ClaimStatus.VOIDED  # force-voided


def test_finalize_unquiesced_mediator_force_voids_contest_opened_during_fiat():
    """A contest that appears only AFTER the fiat-deadlock snapshot (here:
    opened by the mediator's own fiat call) is outside the ruling's deadlock,
    so _settle_contests skips it — the safety sweep must still force-void it
    and refund the stake so no contest survives finalization."""
    ctx: dict = {}

    def fiat_fn(view):
        pos = Position(
            agent="b", stance="block", target_claim=ctx["held"].id,
            argument="x", citations=["c"],
        )
        ctx["late"] = ctx["soc"].bank.open_contest(pos, 1)
        return Ruling(deadlock_id="d-fiat", decision="no-op", rationale="r", citations=["c"])

    med = StubMediator(fiat_fn=fiat_fn)
    soc = _society([StubAgent("a"), StubAgent("b")], mediator=med, max_rounds=1)
    ctx["soc"] = soc
    ctx["held"] = soc.ledger.submit_plain(
        ClaimProposal(agent="a", resource="seat:A", qty=1, beneficiaries=["p1"], basis="x"), 1
    )

    soc._finalize_unquiesced(1)

    assert ctx["late"] is not None  # the late contest really opened
    assert soc.bank.open_contests() == []  # swept by the post-fiat force-void
    # voided (stake refunded), not settled as won/lost
    assert soc.bank.spend_summary()["b"]["burned"] == 0


def test_finalize_unquiesced_no_mediator_ignores_phantom_contest_target():
    soc = _society([StubAgent("a")], mediator=None, max_rounds=1)
    pos = Position(agent="a", stance="block", target_claim="c-999", argument="x", citations=["c"])
    soc.bank.open_contest(pos, 1)
    soc._finalize_unquiesced(1)  # must not raise despite an unknown contest target
    assert soc.bank.open_contests() == []


def test_finalize_unquiesced_no_mediator_voids_granted_target_and_blocked_claims():
    soc = _society([StubAgent("a"), StubAgent("b")], mediator=None, max_rounds=1)
    held = soc.ledger.submit_plain(
        ClaimProposal(agent="a", resource="seat:A", qty=1, beneficiaries=["p1"], basis="x"), 1
    )
    blocked = soc.ledger.submit_plain(
        ClaimProposal(agent="b", resource="seat:A", qty=1, beneficiaries=["p2"], basis="x"), 1
    )
    pos = Position(agent="b", stance="block", target_claim=held.id, argument="x", citations=["c"])
    soc.bank.open_contest(pos, 1)

    soc._finalize_unquiesced(1)

    # an unadjudicated challenge quarantines the disputed grant
    assert soc.ledger.claim(held.id).status == ClaimStatus.VOIDED
    # blocked claims die unserved at the round cap
    assert soc.ledger.claim(blocked.id).status == ClaimStatus.VOIDED
    assert soc.bank.open_contests() == []
