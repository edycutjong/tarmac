"""Protocol wire-type validation: ClaimProposal, Position, Ruling, RulingOp."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tarmac_society.schemas import (
    ClaimProposal,
    ClaimStatus,
    Position,
    Ruling,
    RulingOp,
    SignedRuling,
)


def test_claim_proposal_valid():
    p = ClaimProposal(agent="a", resource="seat:A", qty=2, beneficiaries=["x", "y"], basis="b")
    assert p.revocable is True and p.canonical_dict()["qty"] == 2


def test_claim_qty_must_equal_beneficiaries():
    with pytest.raises(ValidationError):
        ClaimProposal(agent="a", resource="r", qty=3, beneficiaries=["x", "y"], basis="b")


def test_claim_rejects_duplicate_beneficiaries():
    with pytest.raises(ValidationError):
        ClaimProposal(agent="a", resource="r", qty=2, beneficiaries=["x", "x"], basis="b")


def test_claim_qty_must_be_positive():
    with pytest.raises(ValidationError):
        ClaimProposal(agent="a", resource="r", qty=0, beneficiaries=[], basis="b")


def test_claim_basis_required_nonempty():
    with pytest.raises(ValidationError):
        ClaimProposal(agent="a", resource="r", qty=1, beneficiaries=["x"], basis="")


def test_claim_forbids_extra_fields():
    with pytest.raises(ValidationError):
        ClaimProposal(agent="a", resource="r", qty=1, beneficiaries=["x"], basis="b", surprise=1)


def test_canonical_dict_is_json_mode():
    p = ClaimProposal(agent="a", resource="r", qty=1, beneficiaries=["x"], basis="b")
    d = p.canonical_dict()
    assert d["beneficiaries"] == ["x"] and isinstance(d, dict)


def test_position_block_requires_citation():
    with pytest.raises(ValidationError):
        Position(agent="crew", stance="block", target_claim="c-1", argument="illegal", citations=[])


def test_position_block_with_citation_ok():
    pos = Position(
        agent="crew", stance="block", target_claim="c-1", argument="illegal",
        citations=["far117.11"],
    )
    assert pos.stance == "block"


def test_position_support_needs_no_citation():
    pos = Position(agent="crew", stance="support", target_claim="c-1", argument="ok")
    assert pos.citations == []


def test_position_yield_release_field():
    pos = Position(agent="hotel", stance="yield", target_claim="c-1", argument="rooms back",
                   release=["p1", "p2"])
    assert pos.release == ["p1", "p2"]


def test_ruling_requires_citation():
    with pytest.raises(ValidationError):
        Ruling(deadlock_id="d", decision="x", rationale="y", citations=[])


def test_ruling_rejects_blank_citations():
    with pytest.raises(ValidationError):
        Ruling(deadlock_id="d", decision="x", rationale="y", citations=["  "])


def test_ruling_op_kinds():
    assert RulingOp(op="revoke", claim_id="c-1").beneficiaries is None
    with pytest.raises(ValidationError):
        RulingOp(op="explode", claim_id="c-1")


def test_signed_ruling_signable_body_sorts_citation_hashes():
    ruling = Ruling(deadlock_id="d", decision="x", rationale="y", citations=["a"])
    sr = SignedRuling(
        ruling_id="r-01", round=1, body=ruling,
        citation_hashes={"b": "2", "a": "1"}, signature="", signer_public_hex="pk",
    )
    body = sr.signable_body()
    assert list(body["citation_hashes"]) == ["a", "b"]
    assert body["ruling_id"] == "r-01"


def test_claim_status_enum_values():
    assert ClaimStatus.GRANTED.value == "granted"
    assert ClaimStatus("blocked") is ClaimStatus.BLOCKED
