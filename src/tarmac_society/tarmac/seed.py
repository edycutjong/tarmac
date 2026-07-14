"""storm_dfw scenario generator — deterministic, engineered for conflict.

`generate("storm_dfw", seed)` produces the frozen storm:

- QW2214 (DFW->DEN) cancelled by the storm; QW338/QW519 delayed;
- 180 displaced pax incl. UM-07 (unaccompanied minor), MED-02 (transplant
  courier, hard 21:30 arrival deadline), WCHR-1..3, TC-01..12 (tight
  international connections), EL-01/EL-02 (elite decoys that make pure
  fare-class priority visibly fail the SLA);
- 6 candidate outbound flights whose free seats total exactly 139 — demand
  exceeds supply by exactly 41, forcing triage;
- 4 reserve crews whose duty clocks make the *obvious* ferry section
  illegal (max 150 duty minutes remaining < 215 required);
- hotel block of 60.

The **deadlock guarantee** is structural, not sampled: the Rebooking
policy's wave-1 always takes all 9 QW441 seats for the highest
connection-risk pax, and the Advocate always claims 3 QW441 seats for
MED-02 (whose only compliant flight is QW441), UM-07 and a WCHR — the
collision is provable from the fixture alone (test_fixture_deadlocks.py).

Seeds shuffle names, fare classes, booking orders and which general pax
are 'flexible' (QW777-eligible); the engineered structure is invariant.
"""

from __future__ import annotations

import json
from pathlib import Path
from random import Random
from typing import Any

from .scenario import validate_scenario

__all__ = ["generate", "write_fixture", "load_fixture", "SCENARIOS", "FIXTURE_BASENAME"]

SCENARIOS = ("storm_dfw",)
FIXTURE_BASENAME = "storm_dfw_seed7.json"

_FIRST = [
    "Avery", "Bianca", "Carlos", "Dana", "Elif", "Farid", "Grace", "Hana", "Iker",
    "Jules", "Kavya", "Liam", "Mara", "Noor", "Otis", "Priya", "Quinn", "Rosa",
    "Sam", "Tessa", "Umar", "Vera", "Wes", "Ximena", "Yara", "Zeke",
]
_LAST = [
    "Alvarez", "Brooks", "Chen", "Dubois", "Eze", "Fischer", "Garza", "Haddad",
    "Ito", "Jensen", "Khan", "Lopez", "Mbeki", "Novak", "Okafor", "Park",
    "Quintero", "Reyes", "Silva", "Tanaka", "Ueda", "Vance", "Weber", "Xu",
    "Yilmaz", "Zhou",
]

# minutes after midnight of the scenario day (arrivals > 1440 land next day)
_FLIGHTS: list[dict[str, Any]] = [
    {"id": "QW441", "dest": "DEN", "route": "DFW-DEN", "dep_min": 1180, "arr_min": 1260,
     "seats_free": 9, "nonstop": True, "flexible_only": False},
    {"id": "QW519", "dest": "DEN", "route": "DFW-DEN", "dep_min": 1265, "arr_min": 1345,
     "seats_free": 30, "nonstop": True, "flexible_only": False},
    {"id": "QW338", "dest": "DEN", "route": "DFW-ORD-DEN", "dep_min": 1215, "arr_min": 1435,
     "seats_free": 25, "nonstop": False, "flexible_only": False},
    {"id": "QW602", "dest": "DEN", "route": "DFW-DEN", "dep_min": 1330, "arr_min": 1410,
     "seats_free": 40, "nonstop": True, "flexible_only": False},
    {"id": "QW777", "dest": "DEN", "route": "DFW-SEA-DEN", "dep_min": 1290, "arr_min": 1790,
     "seats_free": 15, "nonstop": False, "flexible_only": True},
    {"id": "QW258", "dest": "DEN", "route": "DFW-DEN", "dep_min": 1425, "arr_min": 1499,
     "seats_free": 20, "nonstop": True, "flexible_only": False},
]

_NONSTOPS = ["QW441", "QW519", "QW602", "QW258"]
_GENERAL_LEGAL = ["QW338", "QW441", "QW519", "QW602", "QW258"]

UM_CURFEW_DEP_MIN = 1320   # 22:00 escort curfew (UM-4)
MED_DEADLINE_MIN = 1290    # 21:30 viability deadline (MED-2)
TC_CUTOFF_MIN = 1380       # 23:00 international connection cutoff (CONX-7)
WCHR_ARR_LIMIT_MIN = 1440  # same-day arrival for WCHR SLA (WCHR-1)

N_PAX = 180
N_TC = 12
N_WCHR = 3
N_FLEXIBLE = 18
N_J = 10
N_W = 25


def _name(rng: Random) -> str:
    return f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"


def generate(scenario: str = "storm_dfw", seed: int = 7) -> dict[str, Any]:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; supported: {SCENARIOS}")
    rng = Random(f"tarmac:{scenario}:{seed}")

    pax: list[dict[str, Any]] = []

    def add(pid: str, flags: list[str], fare: str, elite: bool, flexible: bool,
            legal: list[str], **extra: Any) -> None:
        pax.append({
            "id": pid,
            "name": _name(rng),
            "flags": flags,
            "fare_class": fare,
            "elite": elite,
            "flexible": flexible,
            "legal_flights": sorted(legal),
            "tc_cutoff_min": extra.get("tc_cutoff_min"),
            "med_deadline_min": extra.get("med_deadline_min"),
            "booking_order": 0,  # assigned below
        })

    # --- protected + decoy cast (structure identical across seeds)
    add("UM-07", ["UM"], "Y", False, False, _NONSTOPS)
    add("MED-02", ["MED"], "Y", False, False, _NONSTOPS, med_deadline_min=MED_DEADLINE_MIN)
    for i in range(1, N_WCHR + 1):
        add(f"WCHR-{i}", ["WCHR"], "Y", False, False, _NONSTOPS)
    for i in range(1, N_TC + 1):
        add(f"TC-{i:02d}", ["TC"], "Y", False, False, _NONSTOPS, tc_cutoff_min=TC_CUTOFF_MIN)
    add("EL-01", [], "F", True, False, _GENERAL_LEGAL)
    add("EL-02", [], "F", True, False, _GENERAL_LEGAL)

    # --- 161 general pax; seeded fare mix and flexibility
    n_general = N_PAX - len(pax)
    general_ids = [f"PAX-{i:03d}" for i in range(1, n_general + 1)]
    fare_pool = ["J"] * N_J + ["W"] * N_W + ["Y"] * (n_general - N_J - N_W)
    rng.shuffle(fare_pool)
    flexible_ids = set(rng.sample(general_ids, N_FLEXIBLE))
    for pid, fare in zip(general_ids, fare_pool):
        flexible = pid in flexible_ids
        legal = _GENERAL_LEGAL + (["QW777"] if flexible else [])
        add(pid, [], fare, False, flexible, legal)

    # --- booking order: seeded permutation, then constraints that make pure
    #     fare-class priority visibly fail the SLA (the decoys eat QW441 while
    #     the courier books late).
    order = list(range(1, N_PAX + 1))
    rng.shuffle(order)
    for p, o in zip(pax, order):
        p["booking_order"] = o

    def _force_late(pid: str, min_order: int) -> None:
        by_id = {p["id"]: p for p in pax}
        target = by_id[pid]
        if target["booking_order"] >= min_order:
            return
        candidates = sorted(
            (p for p in pax if not p["flags"] and not p["elite"]
             and p["booking_order"] >= min_order),
            key=lambda p: p["booking_order"],
        )
        partner = candidates[0]
        target["booking_order"], partner["booking_order"] = (
            partner["booking_order"], target["booking_order"],
        )

    _force_late("MED-02", 100)
    _force_late("UM-07", 90)
    for i in range(1, N_WCHR + 1):
        _force_late(f"WCHR-{i}", 120)

    pax.sort(key=lambda p: p["id"])

    # --- connection-risk table (Rebooking's private info): TC pax highest.
    risk: dict[str, int] = {}
    for i in range(1, N_TC + 1):
        risk[f"TC-{i:02d}"] = 96 - i  # TC-01=95 ... TC-12=84
    for p in pax:
        if p["id"] not in risk:
            risk[p["id"]] = max(1, 80 - (p["booking_order"] * 79) // (N_PAX + 1))

    # --- 4 reserve crews, all duty-illegal for the ferry (215 min required)
    crews = [
        {"id": f"C{i}", "duty_remaining_min": rng.randrange(90, 155, 5)}
        for i in range(1, 5)
    ]

    scenario_dict: dict[str, Any] = {
        "name": scenario,
        "seed": seed,
        "airport": "DFW",
        "disruption": {
            "cause": "storm",
            "cancelled": ["QW2214"],
            "delayed": {"QW338": 95, "QW519": 155},
        },
        "flights": [dict(f) for f in _FLIGHTS],
        "ferry": {
            "id": "FERRY-1", "dest": "DEN", "dep_min": 1310, "arr_min": 1390,
            "seats": 50, "block_min": 170, "brief_min": 45,
        },
        "crews": crews,
        "gates": [f"G{i}" for i in range(1, 7)],
        "hotel_block": 60,
        "um_curfew_dep_min": UM_CURFEW_DEP_MIN,
        "wchr_arr_limit_min": WCHR_ARR_LIMIT_MIN,
        "pax": pax,
        "connection_risk": risk,
    }
    validate_scenario(scenario_dict)
    return scenario_dict


def write_fixture(scenario_dict: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scenario_dict, indent=2, sort_keys=True) + "\n")
    return path


def load_fixture(path: str | Path) -> dict[str, Any]:
    sc = json.loads(Path(path).read_text())
    validate_scenario(sc)
    return sc
