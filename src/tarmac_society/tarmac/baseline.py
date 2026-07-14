"""The single-planner baseline — one greedy global optimizer.

This is the "single-agent baseline" the track text asks us to beat. It has
the *same tools* as the society (the same ledger, the same resources, the
same replayable log) but **one averaged objective and no negotiation**: it
seats passengers greedily in commercial priority order (elite, then fare
class F>J>W>Y, then booking order) onto each passenger's earliest-arriving
legal flight, and — lacking any crew-legality reasoning — it presses the
obvious ferry section into service.

Two structural failures follow, and the ablation measures both:

1. **It leaves a duty-illegal crew on the board** (crew_violations > 0): no
   agent challenges the ferry.
2. **It strands more passengers overnight** than the coordinated society:
   greedy-by-commercial-priority never reroutes flexible passengers onto the
   flexible-only late flight (QW777), so that scarce capacity is wasted while
   constrained passengers who could not use it are stranded — the classic
   greedy-matching failure a multi-agent negotiation avoids.

It emits the same hash-chained log as a society run, so ``replay`` and
``verify-log`` work identically on a baseline ``run.db``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..chainlog import ChainLog
from ..ledger import ClaimLedger
from ..schemas import ClaimProposal
from ..society import RunResult
from ..storage import SQLiteStorage
from .regs import build_reg_library
from .run import FERRY_CREW_RESOURCE, RunBundle, register_resources
from .scenario import flight_by_id, seat_resource

__all__ = ["PLANNER", "commercial_key", "run_single_planner"]

PLANNER = "single_planner"

_FARE_RANK = {"F": 0, "J": 1, "W": 2, "Y": 3}


def commercial_key(p: dict[str, Any]) -> tuple[int, int, int]:
    """Standard airline re-booking order (fareclass-policy.1): elite, then
    fare class F>J>W>Y, then time of original booking. Protected categories
    get NO priority here — that is exactly the failure the society fixes."""
    return (0 if p["elite"] else 1, _FARE_RANK.get(p["fare_class"], 9), p["booking_order"])


def run_single_planner(
    scenario: dict[str, Any],
    seed: int,
    *,
    db_path: str | Path = ":memory:",
) -> RunBundle:
    """Run the greedy single planner; return a metrics/verify-ready bundle."""
    storage = SQLiteStorage(db_path)
    chainlog = ChainLog(storage)
    chainlog.genesis(
        {
            "scenario": scenario["name"],
            "seed": scenario["seed"],
            "run_seed": seed,
            "condition": "single",
            "agents": [PLANNER],
        }
    )
    ledger = ClaimLedger(storage=storage, chainlog=chainlog)
    register_resources(ledger, scenario)

    fmap = flight_by_id(scenario)
    round_ = 1

    # (1) throughput first: press the ferry into service — no legality check.
    crew_id = scenario["crews"][0]["id"]
    ledger.submit_plain(
        ClaimProposal(
            agent=PLANNER,
            resource=FERRY_CREW_RESOURCE,
            qty=1,
            beneficiaries=[crew_id],
            basis="crew the extra ferry section for +50 seats of throughput",
        ),
        round_,
    )

    # (2) gates: one per scheduled departure (feasibility bookkeeping).
    for gate, flight in zip(
        scenario["gates"], sorted(scenario["flights"], key=lambda f: (f["dep_min"], f["id"]))
    ):
        ledger.submit_plain(
            ClaimProposal(
                agent=PLANNER,
                resource=f"gate:{gate}",
                qty=1,
                beneficiaries=[flight["id"]],
                basis=f"gate for {flight['id']}",
            ),
            round_,
        )

    # (3) seats: greedy, commercial priority, earliest legal arrival.
    order = sorted(scenario["pax"], key=commercial_key)
    for p in order:
        legal = sorted(p["legal_flights"], key=lambda fid: (fmap[fid]["arr_min"], fid))
        for fid in legal:
            res = seat_resource(fid)
            if ledger.free(res) >= 1:
                ledger.submit_plain(
                    ClaimProposal(
                        agent=PLANNER,
                        resource=res,
                        qty=1,
                        beneficiaries=[p["id"]],
                        basis="earliest legal arrival by commercial priority",
                    ),
                    round_,
                )
                break

    manifest = ledger.manifest()
    manifest_hash = ledger.manifest_hash()
    chainlog.append(
        "manifest",
        {
            "manifest": manifest,
            "manifest_hash": manifest_hash,
            "rounds_used": round_,
            "quiescent": True,
        },
        round_,
    )

    result = RunResult(
        manifest=manifest,
        manifest_hash=manifest_hash,
        rounds_used=round_,
        quiescent=True,
        quiescent_round=round_,
        deadlocks=[],
        rulings=[],
        chain_head=chainlog.head,
        chain_length=chainlog.length,
        reveal_rejections=0,
    )
    return RunBundle(
        condition="single",
        seed=seed,
        result=result,
        ledger=ledger,
        bank=None,  # type: ignore[arg-type]
        chainlog=chainlog,
        storage=storage,
        scenario=scenario,
        reglib=build_reg_library(),
    )
