"""Invariants I1–I5, asserted explicitly on real fixture runs.

I1  no seat double-allocated (per-flight capacity + one-seat-per-passenger)
I2  zero crew duty violations in any final manifest
I3  every ruling cites >= 1 regulation/policy source
I4  every accepted reveal matches its commitment; rejects genuinely mismatch
I5  replay from the log reproduces the identical manifest (byte-for-byte)
"""

from __future__ import annotations

import pytest

from tarmac_society import ClaimLedger, ClaimProposal, ClaimStatus, replay_manifest, verify_log
from tarmac_society.tarmac.metrics import crew_duty_check, crew_violations
from tarmac_society.tarmac.run import register_resources, run_society
from tarmac_society.tarmac.seed import generate

SEEDS = [7, 3, 5]


@pytest.fixture(params=SEEDS)
def society_run(request):
    sc = generate("storm_dfw", request.param)
    return sc, run_society(sc, request.param, condition="society")


# ---- I1 ------------------------------------------------------------------
def test_I1_no_flight_over_capacity(society_run):
    scenario, b = society_run
    cap = {f["id"]: f["seats_free"] for f in scenario["flights"]}
    for res, bens in b.ledger.manifest().items():
        if res.startswith("seat:"):
            assert len(bens) <= cap[res.split(":")[1]]


def test_I1_no_passenger_on_two_flights(society_run):
    _scenario, b = society_run
    seen: set[str] = set()
    for res, bens in b.ledger.manifest().items():
        if not res.startswith("seat:"):
            continue
        for p in bens:
            assert p not in seen, f"{p} double-seated"
            seen.add(p)


def test_I1_enforced_as_db_constraint(scenario):
    from tarmac_society.storage import IntegrityViolation

    lg = ClaimLedger()
    register_resources(lg, scenario)
    lg.submit_plain(
        ClaimProposal(agent="x", resource="seat:QW441", qty=9, basis="fill",
                      beneficiaries=[f"z{i}" for i in range(9)]),
        1,
    )
    with pytest.raises(IntegrityViolation):
        lg.storage.execute(
            "INSERT INTO allocations(resource_id, beneficiary, claim_id, round, excl_group)"
            " VALUES ('seat:QW441','over','c-x',1,'seat')"
        )


# ---- I2 ------------------------------------------------------------------
def test_I2_zero_crew_violations(society_run):
    scenario, b = society_run
    assert crew_violations(b.ledger, scenario) == 0
    name, ok, _ = crew_duty_check(scenario)(b.chainlog.entries())
    assert name == "I2.crew_duty" and ok


# ---- I3 ------------------------------------------------------------------
def test_I3_every_ruling_cites_source(society_run):
    _scenario, b = society_run
    assert b.result.rulings, "expected at least one ruling"
    for signed in b.result.rulings:
        assert len(signed.body.citations) >= 1


# ---- I4 ------------------------------------------------------------------
def test_I4_verify_log_reveal_checks_pass(society_run):
    _scenario, b = society_run
    report = verify_log(b.chainlog.entries())
    reveal_checks = [c for c in report.checks if c[0].startswith("I4")]
    assert reveal_checks and all(ok for _n, ok, _d in reveal_checks)


def test_I4_mismatched_reveal_is_rejected():
    lg = ClaimLedger()
    lg.register_resource("seat:A", 2, group="seat")
    p = ClaimProposal(agent="a", resource="seat:A", qty=1, beneficiaries=["p1"], basis="b")
    cid, nonce = lg.seal_and_commit(p, 1)
    tampered = ClaimProposal(agent="a", resource="seat:A", qty=1, beneficiaries=["p2"], basis="b")
    rec = lg.reveal(cid, tampered, nonce, 1)
    assert rec.status == ClaimStatus.REVEAL_REJECTED


# ---- I5 ------------------------------------------------------------------
def test_I5_replay_reproduces_manifest(society_run):
    _scenario, b = society_run
    assert replay_manifest(b.chainlog.entries()) == b.result.manifest


def test_I5_run_is_byte_identical_across_runs(society_run):
    scenario, b = society_run
    again = run_society(scenario, scenario["seed"], condition="society")
    assert again.result.manifest_hash == b.result.manifest_hash
    assert again.result.chain_head == b.result.chain_head


def test_all_invariants_via_verify_log(society_run):
    scenario, b = society_run
    report = verify_log(b.chainlog.entries(), domain_checks=[crew_duty_check(scenario)])
    assert report.ok, report.failures()
