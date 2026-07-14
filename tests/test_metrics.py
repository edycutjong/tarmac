"""Outcome metrics computed from the manifest against ground truth."""

from __future__ import annotations

from tarmac_society.tarmac.baseline import run_single_planner
from tarmac_society.tarmac.metrics import (
    compute_metrics,
    crew_duty_check,
    crew_violations,
    flight_protects,
    seat_assignment,
    special_needs_sla,
)
from tarmac_society.tarmac.run import run_society
from tarmac_society.tarmac.scenario import flight_by_id, pax_by_id


def _society_metrics(scenario):
    b = run_society(scenario, 7, condition="society")
    return compute_metrics(
        b.ledger, scenario, rounds_to_quiescence=b.result.rounds_used,
        contest_spend=b.bank.total_staked(), quiescent=b.result.quiescent,
    ), b


def _single_metrics(scenario):
    b = run_single_planner(scenario, 7)
    return compute_metrics(
        b.ledger, scenario, rounds_to_quiescence=1, contest_spend=0,
    ), b


def test_flight_protects_med_only_on_qw441(scenario):
    fmap = flight_by_id(scenario)
    med = pax_by_id(scenario)["MED-02"]
    assert flight_protects(scenario, med, fmap["QW441"]) is True
    assert flight_protects(scenario, med, fmap["QW519"]) is False


def test_flight_protects_general_same_day(scenario):
    fmap = flight_by_id(scenario)
    general = next(p for p in scenario["pax"] if not p["flags"] and not p["elite"])
    assert flight_protects(scenario, general, fmap["QW441"]) is True
    assert flight_protects(scenario, general, fmap["QW777"]) is False  # next-day arrival


def test_society_metrics_are_ideal(scenario):
    m, _ = _society_metrics(scenario)
    assert m["crew_violations"] == 0
    assert m["special_needs_sla_pct"] == 100.0
    assert m["special_needs_failed"] == []
    assert m["protected_stranded"] == 3  # 0 special-needs + 3 tight connections
    assert m["tight_connections_saved"] == 9


def test_single_planner_metrics_are_worse(scenario):
    m, _ = _single_metrics(scenario)
    assert m["crew_violations"] == 1  # illegal ferry crew on the board
    assert m["special_needs_sla_pct"] == 0.0
    assert m["protected_stranded"] == 17  # all 5 special-needs + all 12 connections


def test_society_beats_single_on_protected(scenario):
    soc, _ = _society_metrics(scenario)
    single, _ = _single_metrics(scenario)
    assert soc["protected_stranded"] < single["protected_stranded"]
    assert soc["stranded_overnight"] <= single["stranded_overnight"]


def test_crew_violations_helper(scenario):
    sb = run_single_planner(scenario, 7)
    assert crew_violations(sb.ledger, scenario) == 1
    society = run_society(scenario, 7, condition="society")
    assert crew_violations(society.ledger, scenario) == 0


def test_special_needs_sla_shape(scenario):
    b = run_society(scenario, 7, condition="society")
    sla = special_needs_sla(b.ledger, scenario)
    assert sla["total"] == 5 and sla["pct"] == 100.0
    assert set(sla["met"]) >= {"MED-02", "UM-07"}


def test_seat_assignment_maps_pax_to_flight(scenario):
    b = run_society(scenario, 7, condition="society")
    assign = seat_assignment(b.ledger, scenario)
    assert assign["MED-02"] == "QW441"
    assert all(fid in {f["id"] for f in scenario["flights"]} for fid in assign.values())


def test_seat_assignment_skips_resources_not_in_scenario_flights(scenario):
    """A seat:* resource whose suffix is not a scenario flight id must be skipped."""
    from tarmac_society import ClaimLedger, ClaimProposal

    lg = ClaimLedger()
    lg.register_resource("seat:GHOST999", 1, group="seat")
    lg.submit_plain(
        ClaimProposal(agent="x", resource="seat:GHOST999", qty=1, beneficiaries=["ZZZ"], basis="t"),
        1,
    )
    assign = seat_assignment(lg, scenario)
    assert "ZZZ" not in assign


def test_crew_duty_check_domaincheck(scenario):
    check = crew_duty_check(scenario)
    society = run_society(scenario, 7, condition="society")
    name, ok, _ = check(society.chainlog.entries())
    assert name == "I2.crew_duty" and ok is True
    single = run_single_planner(scenario, 7)
    _, ok2, detail = check(single.chainlog.entries())
    assert ok2 is False and "C" in detail
