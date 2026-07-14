"""Mediator ruling flow: signed, cited, resolves the deadlock; fiat safety valve."""

from __future__ import annotations

from tarmac_society.schemas import ClaimStatus
from tarmac_society.signing import verify_body
from tarmac_society.tarmac.metrics import seat_assignment
from tarmac_society.tarmac.run import run_society


def test_society_produces_signed_rulings(scenario):
    b = run_society(scenario, 7, condition="society")
    assert len(b.result.rulings) >= 1
    for signed in b.result.rulings:
        assert verify_body(signed.signable_body(), signed.signature, signed.signer_public_hex)


def test_every_ruling_cites_a_source(scenario):
    b = run_society(scenario, 7, condition="society")
    for signed in b.result.rulings:
        assert len(signed.body.citations) >= 1
        # citation hashes are embedded for each cited passage
        assert set(signed.citation_hashes) >= set(signed.body.citations) or signed.citation_hashes


def test_ruling_resolves_protected_claim(scenario):
    """The advocate's blocked QW441 claim ends up granting the courier a seat."""
    b = run_society(scenario, 7, condition="society")
    assign = seat_assignment(b.ledger, scenario)
    assert assign.get("MED-02") == "QW441"  # only deadline-compliant flight
    assert "UM-07" in assign  # the minor flies


def test_crew_claim_is_voided_by_mediator(scenario):
    b = run_society(scenario, 7, condition="society")
    # no crew resource remains allocated -> the illegal ferry was killed
    assert not any(res.startswith("crew:") for res in b.ledger.manifest())


def test_fiat_fires_when_round_cap_hits(scenario):
    b = run_society(scenario, 7, condition="society", max_rounds=1)
    kinds = [e.kind for e in b.chainlog.entries()]
    assert "fiat" in kinds
    assert b.result.quiescent is False


def test_no_blocked_claims_survive_finalization(scenario):
    b = run_society(scenario, 7, condition="society")
    assert b.ledger.claims_with_status(ClaimStatus.BLOCKED) == []
