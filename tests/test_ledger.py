"""ClaimLedger: capacity, exclusivity, commit/reveal, rulings, double-claim."""

from __future__ import annotations

import pytest

from tarmac_society import ClaimLedger, ClaimProposal, ClaimStatus, LedgerError
from tarmac_society.schemas import RulingOp
from tarmac_society.storage import IntegrityViolation


def _claim(agent, resource, bens, basis="b"):
    return ClaimProposal(agent=agent, resource=resource, qty=len(bens), beneficiaries=bens, basis=basis)


def test_register_and_free(ledger):
    assert ledger.free("seat:A") == 2
    assert ledger.resources()["seat:A"]["capacity"] == 2


def test_register_duplicate_resource_raises(ledger):
    with pytest.raises(LedgerError):
        ledger.register_resource("seat:A", 5)


def test_free_unknown_resource_raises(ledger):
    with pytest.raises(LedgerError):
        ledger.free("seat:ZZZ")


def test_submit_plain_grants_within_capacity(ledger):
    rec = ledger.submit_plain(_claim("a", "seat:A", ["p1", "p2"]), 1)
    assert rec.status == ClaimStatus.GRANTED
    assert ledger.free("seat:A") == 0
    assert sorted(rec.holders) == ["p1", "p2"]


def test_submit_plain_blocks_over_capacity(ledger):
    rec = ledger.submit_plain(_claim("a", "seat:B", ["p1", "p2"]), 1)  # cap 1
    assert rec.status == ClaimStatus.BLOCKED
    assert ledger.free("seat:B") == 1  # nothing allocated (all-or-nothing)


def test_all_or_nothing_allocation(ledger):
    ledger.submit_plain(_claim("a", "seat:A", ["p1"]), 1)  # 1 of 2 used
    rec = ledger.submit_plain(_claim("b", "seat:A", ["p2", "p3"]), 1)  # needs 2, only 1 free
    assert rec.status == ClaimStatus.BLOCKED
    assert ledger.free("seat:A") == 1


def test_exclusivity_group_blocks_second_seat(ledger):
    ledger.submit_plain(_claim("a", "seat:A", ["p1"]), 1)
    rec = ledger.submit_plain(_claim("a", "seat:B", ["p1"]), 1)  # p1 already in group 'seat'
    assert rec.status == ClaimStatus.BLOCKED


def test_double_claim_last_seat_only_one_wins(ledger):
    r1 = ledger.submit_plain(_claim("a", "seat:B", ["p1"]), 1)
    r2 = ledger.submit_plain(_claim("b", "seat:B", ["p2"]), 1)
    grants = [r for r in (r1, r2) if r.status == ClaimStatus.GRANTED]
    assert len(grants) == 1
    assert ledger.free("seat:B") == 0


def test_seal_commit_reveal_match_grants(ledger):
    p = _claim("a", "seat:A", ["p1"])
    cid, nonce = ledger.seal_and_commit(p, 1)
    rec = ledger.reveal(cid, p, nonce, 1)
    assert rec.status == ClaimStatus.GRANTED
    assert rec.commitment_id == cid


def test_reveal_mismatch_is_rejected(ledger):
    p = _claim("a", "seat:A", ["p1"])
    cid, nonce = ledger.seal_and_commit(p, 1)
    tampered = _claim("a", "seat:A", ["p2"])  # same agent, different bid
    rec = ledger.reveal(cid, tampered, nonce, 1)
    assert rec.status == ClaimStatus.REVEAL_REJECTED
    assert ledger.free("seat:A") == 2  # nothing allocated


def test_reveal_wrong_agent_raises(ledger):
    p = _claim("a", "seat:A", ["p1"])
    cid, nonce = ledger.seal_and_commit(p, 1)
    with pytest.raises(LedgerError):
        ledger.reveal(cid, _claim("b", "seat:A", ["p1"]), nonce, 1)


def test_reveal_twice_raises(ledger):
    p = _claim("a", "seat:A", ["p1"])
    cid, nonce = ledger.seal_and_commit(p, 1)
    ledger.reveal(cid, p, nonce, 1)
    with pytest.raises(LedgerError):
        ledger.reveal(cid, p, nonce, 1)


def test_reveal_unknown_commitment_raises(ledger):
    with pytest.raises(LedgerError):
        ledger.reveal("m-999", _claim("a", "seat:A", ["p1"]), "00" * 16, 1)


def test_release_partial_then_full(ledger):
    rec = ledger.submit_plain(_claim("a", "seat:A", ["p1", "p2"]), 1)
    ledger.release("a", rec.id, ["p1"], 2)
    assert ledger.free("seat:A") == 1
    ledger.release("a", rec.id, None, 3)  # release remaining
    assert ledger.free("seat:A") == 2
    assert ledger.claim(rec.id).status == ClaimStatus.RELEASED


def test_release_by_non_owner_raises(ledger):
    rec = ledger.submit_plain(_claim("a", "seat:A", ["p1"]), 1)
    with pytest.raises(LedgerError):
        ledger.release("b", rec.id, None, 2)


def test_release_non_granted_raises(ledger):
    rec = ledger.submit_plain(_claim("a", "seat:B", ["p1", "p2"]), 1)  # blocked
    with pytest.raises(LedgerError):
        ledger.release("a", rec.id, None, 2)


def test_ruling_revoke_then_grant(ledger):
    held = ledger.submit_plain(_claim("a", "seat:B", ["p1"]), 1)  # grabs the only seat
    blocked = ledger.submit_plain(_claim("b", "seat:B", ["p2"]), 1)  # blocked
    assert blocked.status == ClaimStatus.BLOCKED
    ledger.apply_ruling_ops([RulingOp(op="revoke", claim_id=held.id)], "r-01", 2)
    ledger.apply_ruling_ops([RulingOp(op="grant", claim_id=blocked.id)], "r-01", 2)
    assert ledger.claim(blocked.id).status == ClaimStatus.GRANTED
    assert ledger.claim(held.id).status == ClaimStatus.REVOKED


def test_ruling_grant_violating_capacity_raises(ledger):
    ledger.submit_plain(_claim("a", "seat:B", ["p1"]), 1)
    blocked = ledger.submit_plain(_claim("b", "seat:B", ["p2"]), 1)
    with pytest.raises(LedgerError):  # no free seat, grant must fail
        ledger.apply_ruling_ops([RulingOp(op="grant", claim_id=blocked.id)], "r-01", 2)


def test_ruling_void(ledger):
    held = ledger.submit_plain(_claim("a", "seat:A", ["p1"]), 1)
    ledger.apply_ruling_ops([RulingOp(op="void", claim_id=held.id)], "r-01", 2)
    assert ledger.claim(held.id).status == ClaimStatus.VOIDED
    assert ledger.free("seat:A") == 2


def test_void_granted_claim_frees_seats(ledger):
    held = ledger.submit_plain(_claim("a", "seat:A", ["p1", "p2"]), 1)
    ledger.void_claim(held.id, "test", 2)
    assert ledger.free("seat:A") == 2


def test_withdraw_blocked_claim(ledger):
    ledger.submit_plain(_claim("a", "seat:B", ["p1"]), 1)
    blocked = ledger.submit_plain(_claim("b", "seat:B", ["p2"]), 1)
    ledger.withdraw_claim("b", blocked.id, 2)
    assert ledger.claim(blocked.id).status == ClaimStatus.WITHDRAWN


def test_withdraw_granted_claim_raises(ledger):
    held = ledger.submit_plain(_claim("a", "seat:A", ["p1"]), 1)
    with pytest.raises(LedgerError):
        ledger.withdraw_claim("a", held.id, 2)


def test_manifest_and_hash_stable(ledger):
    ledger.submit_plain(_claim("a", "seat:A", ["p2", "p1"]), 1)
    m = ledger.manifest()
    assert m["seat:A"] == ["p1", "p2"]  # sorted
    assert ledger.manifest_hash() == ledger.manifest_hash()


def test_claim_unknown_raises(ledger):
    with pytest.raises(LedgerError):
        ledger.claim("c-999")


def test_blocked_and_granted_queries(ledger):
    g = ledger.submit_plain(_claim("a", "seat:A", ["p1"]), 1)
    ledger.submit_plain(_claim("b", "seat:B", ["p2", "p3"]), 1)  # blocked
    assert [c.id for c in ledger.granted_claims()] == [g.id]
    assert len(ledger.blocked_claims()) == 1


def test_holder_agents(ledger):
    ledger.submit_plain(_claim("a", "seat:A", ["p1"]), 1)
    holders = ledger.holder_agents("seat:A")
    assert "a" in holders


def test_db_trigger_blocks_direct_overcapacity(ledger):
    ledger.submit_plain(_claim("a", "seat:B", ["p1"]), 1)  # fills capacity-1 resource
    with pytest.raises(IntegrityViolation):
        ledger.storage.execute(
            "INSERT INTO allocations(resource_id, beneficiary, claim_id, round, excl_group)"
            " VALUES ('seat:B', 'p9', 'c-x', 1, 'seat')"
        )


def test_negative_capacity_rejected():
    lg = ClaimLedger()
    with pytest.raises(LedgerError):
        lg.register_resource("seat:X", -1)


def test_reopened_ledger_continues_claim_and_commit_sequence():
    """A second ClaimLedger over the same storage must not reuse claim/commit ids."""
    from tarmac_society.storage import SQLiteStorage

    st = SQLiteStorage(":memory:")
    lg1 = ClaimLedger(storage=st)
    lg1.register_resource("seat:A", 2, group="seat")
    rec1 = lg1.submit_plain(_claim("a", "seat:A", ["p1"]), 1)
    assert rec1.id == "c-001"

    lg2 = ClaimLedger(storage=st)
    rec2 = lg2.submit_plain(_claim("a", "seat:A", ["p2"]), 1)
    assert rec2.id == "c-002"  # continues, does not collide


def test_reopened_ledger_ignores_malformed_claim_ids():
    from tarmac_society.storage import SQLiteStorage

    st = SQLiteStorage(":memory:")
    lg1 = ClaimLedger(storage=st)
    lg1.register_resource("seat:A", 2, group="seat")
    # simulate a foreign/corrupted claim id that won't parse as "<prefix>-<int>"
    st.execute(
        "INSERT INTO claims(id, agent, resource_id, qty, beneficiaries, basis, revocable,"
        " payload, status, round_committed, commitment_id)"
        " VALUES ('weird', 'a', 'seat:A', 1, '[\"p9\"]', 'x', 1, '{}', 'granted', 1, NULL)"
    )
    lg2 = ClaimLedger(storage=st)
    assert lg2._claim_seq == 0  # malformed id ignored, sequence unaffected


def test_insert_and_apply_unknown_resource_raises():
    lg = ClaimLedger()
    bad = ClaimProposal(agent="a", resource="seat:GHOST", qty=1, beneficiaries=["p1"], basis="b")
    with pytest.raises(LedgerError):
        lg.submit_plain(bad, 1)


def test_try_allocate_integrity_violation_bubbles_up(ledger):
    """A capacity-free resource (no exclusivity group) lets the same beneficiary
    be claimed twice by different claims — the PK-level trigger/unique index
    is the last line of defense the Python pre-checks don't cover."""
    ledger.register_resource("gate:G1", 2, group=None)
    rec1 = ledger.submit_plain(_claim("a", "gate:G1", ["p1"]), 1)
    assert rec1.status == ClaimStatus.GRANTED
    with pytest.raises(IntegrityViolation):
        ledger.submit_plain(_claim("b", "gate:G1", ["p1"]), 1)


def test_deallocate_missing_allocation_raises(ledger):
    rec = ledger.submit_plain(_claim("a", "seat:A", ["p1", "p2"]), 1)
    with pytest.raises(LedgerError):
        ledger.release("a", rec.id, ["p9"], 2)  # p9 never held this claim


def test_apply_ruling_ops_revoke_non_granted_raises(ledger):
    blocked = ledger.submit_plain(_claim("a", "seat:B", ["p1", "p2"]), 1)  # blocked (cap 1)
    with pytest.raises(LedgerError):
        ledger.apply_ruling_ops([RulingOp(op="revoke", claim_id=blocked.id)], "r-01", 2)


def test_apply_ruling_ops_grant_non_blocked_raises(ledger):
    held = ledger.submit_plain(_claim("a", "seat:A", ["p1"]), 1)  # granted
    with pytest.raises(LedgerError):
        ledger.apply_ruling_ops([RulingOp(op="grant", claim_id=held.id)], "r-01", 2)


def test_withdraw_by_non_owner_raises(ledger):
    ledger.submit_plain(_claim("a", "seat:B", ["p1"]), 1)
    blocked = ledger.submit_plain(_claim("b", "seat:B", ["p2"]), 1)
    with pytest.raises(LedgerError):
        ledger.withdraw_claim("c", blocked.id, 2)


def test_void_claim_is_idempotent(ledger):
    held = ledger.submit_plain(_claim("a", "seat:A", ["p1"]), 1)
    ledger.void_claim(held.id, "first", 2)
    assert ledger.claim(held.id).status == ClaimStatus.VOIDED
    ledger.void_claim(held.id, "second", 3)  # no-op, must not raise
    assert ledger.claim(held.id).status == ClaimStatus.VOIDED


def test_beneficiary_resource_lookup(ledger):
    ledger.submit_plain(_claim("a", "seat:A", ["p1"]), 1)
    assert ledger.beneficiary_resource("seat", "p1") == "seat:A"
    assert ledger.beneficiary_resource("seat", "nobody") is None
