"""The greedy single-planner baseline."""

from __future__ import annotations

from tarmac_society import replay_manifest
from tarmac_society.tarmac.baseline import PLANNER, commercial_key, run_single_planner
from tarmac_society.tarmac.metrics import seat_assignment
from tarmac_society.tarmac.run import FERRY_CREW_RESOURCE


def test_commercial_key_orders_elite_and_fare():
    elite_f = {"elite": True, "fare_class": "F", "booking_order": 100}
    plain_y = {"elite": False, "fare_class": "Y", "booking_order": 1}
    mid_j = {"elite": False, "fare_class": "J", "booking_order": 50}
    order = sorted([plain_y, mid_j, elite_f], key=commercial_key)
    assert order[0] is elite_f and order[-1] is plain_y


def test_single_planner_fills_seats(scenario):
    b = run_single_planner(scenario, 7)
    seated = sum(len(v) for k, v in b.ledger.manifest().items() if k.startswith("seat:"))
    assert 120 <= seated <= 139  # greedy fills nearly all legal capacity


def test_single_planner_schedules_illegal_ferry(scenario):
    b = run_single_planner(scenario, 7)
    assert FERRY_CREW_RESOURCE in b.ledger.manifest()  # crew left on the illegal section


def test_single_planner_strands_special_needs(scenario):
    b = run_single_planner(scenario, 7)
    assign = seat_assignment(b.ledger, scenario)
    # commercial priority buries the courier/minor -> not on the compliant flight
    assert assign.get("MED-02") != "QW441"


def test_single_planner_log_is_replayable(scenario):
    b = run_single_planner(scenario, 7)
    assert replay_manifest(b.chainlog.entries()) == b.result.manifest


def test_single_planner_bundle_metadata(scenario):
    b = run_single_planner(scenario, 7)
    assert b.condition == "single"
    assert b.result.quiescent is True
    assert b.bank is None
    # every seat claim carries the planner's identity
    agents = {r[0] for r in b.ledger.storage.query("SELECT DISTINCT agent FROM claims")}
    assert agents == {PLANNER}
