"""Scenario accessors + structural validation for storm_dfw.

The scenario dict is the ground truth: flights, crews, the ferry trap, and
per-passenger labels (SLA class, legal re-booking options) that let metrics
be computed mechanically, without LLM judgment. Times are minutes after
midnight of the scenario day (arrivals past 1440 = next day).
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "flight_by_id",
    "pax_by_id",
    "seat_resource",
    "special_needs_ids",
    "tc_ids",
    "ferry_required_duty",
    "ferry_is_illegal_for_all_crews",
    "validate_scenario",
    "SUPPLY_SHORTFALL",
]

SUPPLY_SHORTFALL = 41  # engineered: demand (180) exceeds seat supply by exactly this


def flight_by_id(scenario: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["id"]: f for f in scenario["flights"]}

def pax_by_id(scenario: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["id"]: p for p in scenario["pax"]}

def seat_resource(flight_id: str) -> str:
    return f"seat:{flight_id}"

def special_needs_ids(scenario: dict[str, Any]) -> list[str]:
    return sorted(
        p["id"] for p in scenario["pax"] if any(f in ("UM", "MED", "WCHR") for f in p["flags"])
    )

def tc_ids(scenario: dict[str, Any]) -> list[str]:
    return sorted(p["id"] for p in scenario["pax"] if "TC" in p["flags"])

def ferry_required_duty(scenario: dict[str, Any]) -> int:
    ferry = scenario["ferry"]
    return ferry["block_min"] + ferry["brief_min"]

def ferry_is_illegal_for_all_crews(scenario: dict[str, Any]) -> bool:
    need = ferry_required_duty(scenario)
    return all(c["duty_remaining_min"] < need for c in scenario["crews"])


def validate_scenario(sc: dict[str, Any]) -> None:
    """Assert every engineered guarantee of storm_dfw. Raises AssertionError."""
    pax = sc["pax"]
    assert len(pax) == 180, f"expected 180 displaced pax, got {len(pax)}"
    flags = [p["flags"] for p in pax]
    assert sum(1 for f in flags if "UM" in f) == 1, "exactly one unaccompanied minor"
    assert sum(1 for f in flags if "MED" in f) == 1, "exactly one medical courier"
    assert sum(1 for f in flags if "WCHR" in f) == 3, "exactly three WCHR pax"
    assert sum(1 for f in flags if "TC" in f) == 12, "exactly 12 tight connections"
    assert sum(1 for p in pax if p["elite"]) == 2, "exactly 2 elite decoys"

    flights = sc["flights"]
    assert len(flights) == 6, "exactly 6 candidate outbound flights"
    supply = sum(f["seats_free"] for f in flights)
    assert len(pax) - supply == SUPPLY_SHORTFALL, (
        f"demand-supply gap must be exactly {SUPPLY_SHORTFALL}, got {len(pax) - supply}"
    )

    assert len(sc["crews"]) == 4, "exactly 4 reserve crews"
    assert ferry_is_illegal_for_all_crews(sc), "the obvious ferry MUST be duty-illegal"
    assert sc["hotel_block"] == 60, "hotel block of 60"

    ids = {p["id"] for p in pax}
    assert len(ids) == 180, "duplicate pax ids"
    orders = sorted(p["booking_order"] for p in pax)
    assert orders == list(range(1, 181)), "booking_order must be a permutation of 1..180"

    fmap = flight_by_id(sc)
    for p in pax:
        assert p["legal_flights"], f"{p['id']} has no legal re-booking option"
        for fid in p["legal_flights"]:
            assert fid in fmap, f"{p['id']} legal flight {fid} unknown"
        if "MED" in p["flags"]:
            compliant = [
                fid for fid in p["legal_flights"] if fmap[fid]["arr_min"] <= p["med_deadline_min"]
            ]
            assert compliant == ["QW441"], "MED courier must have exactly one compliant flight"
        if "TC" in p["flags"]:
            protecting = [
                fid for fid in p["legal_flights"] if fmap[fid]["arr_min"] <= p["tc_cutoff_min"]
            ]
            assert set(protecting) == {"QW441", "QW519"}, (
                "tight connections must be protectable only by QW441/QW519"
            )
        if not p["flexible"]:
            assert "QW777" not in p["legal_flights"], "QW777 is flexible-only"
