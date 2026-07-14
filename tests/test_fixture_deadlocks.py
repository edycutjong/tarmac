"""The deadlock guarantee: storm_dfw ALWAYS produces >= 1 genuine deadlock.

If the society never deadlocked, the mediator would be decorative and the
"agreeable-agents" failure mode would have crept in. The engineered fixture
guarantees the Advocate and Rebooking agents provably contend for the nine
QW441 seats, so mediation is always triggered — asserted here across every
bench seed.
"""

from __future__ import annotations

import pytest

from tarmac_society.tarmac import bench as B
from tarmac_society.tarmac.baseline import run_single_planner
from tarmac_society.tarmac.metrics import compute_metrics
from tarmac_society.tarmac.run import run_society
from tarmac_society.tarmac.seed import generate


def test_seed7_produces_at_least_one_deadlock(scenario):
    b = run_society(scenario, 7, condition="society")
    assert len(b.result.deadlocks) >= 1


def test_seed7_deadlock_is_the_qw441_collision(scenario):
    b = run_society(scenario, 7, condition="society")
    resources = {r for d in b.result.deadlocks for r in d.resources}
    assert "seat:QW441" in resources
    # the contention is between the advocate and rebooking over the scarce seats
    agents = {a for d in b.result.deadlocks for a in d.agents}
    assert {"advocate", "rebooking"} <= agents


@pytest.mark.parametrize("seed", B.DEFAULT_SEEDS)
def test_every_bench_seed_deadlocks(seed):
    sc = generate("storm_dfw", seed)
    b = run_society(sc, seed, condition="society")
    assert len(b.result.deadlocks) >= 1, f"seed {seed} produced no deadlock"


def test_deadlocks_are_genuine_not_fabricated(scenario):
    b = run_society(scenario, 7, condition="society")
    for d in b.result.deadlocks:
        assert d.kind in ("cycle", "contested")
        assert d.claims and d.agents  # references real claims/agents


def test_society_resolves_to_quiescence(scenario):
    b = run_society(scenario, 7, condition="society")
    assert b.result.quiescent is True
    assert b.result.rulings, "the deadlock must be adjudicated"


def test_society_beats_single_planner_on_fixture(scenario):
    soc = run_society(scenario, 7, condition="society")
    sm = compute_metrics(soc.ledger, scenario, rounds_to_quiescence=soc.result.rounds_used,
                         contest_spend=soc.bank.total_staked())
    single = run_single_planner(scenario, 7)
    gm = compute_metrics(single.ledger, scenario, rounds_to_quiescence=1, contest_spend=0)
    assert sm["protected_stranded"] < gm["protected_stranded"]
    assert sm["special_needs_sla_pct"] > gm["special_needs_sla_pct"]
    assert sm["crew_violations"] < gm["crew_violations"]
