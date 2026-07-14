"""storm_dfw generator: determinism + engineered guarantees."""

from __future__ import annotations

import copy

import pytest

from tarmac_society.tarmac.scenario import (
    SUPPLY_SHORTFALL,
    ferry_is_illegal_for_all_crews,
    ferry_required_duty,
    flight_by_id,
    pax_by_id,
    seat_resource,
    special_needs_ids,
    tc_ids,
    validate_scenario,
)
from tarmac_society.tarmac.seed import generate, load_fixture, write_fixture


def test_generate_is_deterministic():
    assert generate("storm_dfw", 7) == generate("storm_dfw", 7)


def test_generate_seed_changes_names_not_structure():
    a, b = generate("storm_dfw", 7), generate("storm_dfw", 8)
    assert a != b  # different seed -> different roster
    validate_scenario(a)
    validate_scenario(b)  # both still satisfy every guarantee


def test_unknown_scenario_raises():
    with pytest.raises(ValueError):
        generate("not_a_storm", 7)


def test_pax_count_and_supply(scenario):
    assert len(scenario["pax"]) == 180
    supply = sum(f["seats_free"] for f in scenario["flights"])
    assert supply == 139
    assert len(scenario["pax"]) - supply == SUPPLY_SHORTFALL == 41


def test_special_and_tight_connection_counts(scenario):
    assert len(special_needs_ids(scenario)) == 5  # 1 MED + 1 UM + 3 WCHR
    assert len(tc_ids(scenario)) == 12


def test_ferry_is_illegal_for_all_crews(scenario):
    assert ferry_is_illegal_for_all_crews(scenario) is True
    assert ferry_required_duty(scenario) == 170 + 45  # block + brief


def test_six_flights_four_crews_hotel_block(scenario):
    assert len(scenario["flights"]) == 6
    assert len(scenario["crews"]) == 4
    assert scenario["hotel_block"] == 60


def test_med_courier_single_compliant_flight(scenario):
    fmap = flight_by_id(scenario)
    med = pax_by_id(scenario)["MED-02"]
    compliant = [f for f in med["legal_flights"] if fmap[f]["arr_min"] <= med["med_deadline_min"]]
    assert compliant == ["QW441"]


def test_seat_resource_naming():
    assert seat_resource("QW441") == "seat:QW441"


def test_validate_rejects_mutated_scenario(scenario):
    broken = copy.deepcopy(scenario)
    broken["pax"].pop()  # now 179
    with pytest.raises(AssertionError):
        validate_scenario(broken)


def test_validate_rejects_legal_ferry(scenario):
    broken = copy.deepcopy(scenario)
    for c in broken["crews"]:
        c["duty_remaining_min"] = 999  # make the ferry legal -> guarantee broken
    with pytest.raises(AssertionError):
        validate_scenario(broken)


def test_booking_order_is_permutation(scenario):
    orders = sorted(p["booking_order"] for p in scenario["pax"])
    assert orders == list(range(1, 181))


def test_write_and_load_fixture_roundtrip(scenario, tmp_path):
    path = write_fixture(scenario, tmp_path / "s.json")
    loaded = load_fixture(path)
    assert loaded == scenario
