"""Outcome metrics for a storm_dfw run — computed mechanically from the final
manifest against the scenario's ground-truth labels (no LLM judgment).

The ground truth (SLA class per passenger, legal flights, arrival times) lets
every headline number be a deterministic function of the manifest, so the
ablation is reproducible and auditable:

- ``stranded_overnight`` — passengers who do NOT reach DEN the same operating
  day (no seat on a flight arriving on/before the same-day cutoff);
- ``unseated`` — passengers with no seat on any flight at all;
- ``special_needs_sla_pct`` — % of {MED, UM, WCHR} meeting their regulated SLA;
- ``tight_connections_saved`` — TC passengers seated on a connection-protecting
  arrival (QW441/QW519);
- ``crew_violations`` — crew assigned beyond remaining duty (**I2**: must be 0);
- ``rounds_to_quiescence`` / ``contest_spend`` — process metrics.
"""

from __future__ import annotations

from typing import Any

from ..ledger import ClaimLedger
from ..verify import replay_manifest
from .scenario import ferry_required_duty, flight_by_id

__all__ = [
    "SAME_DAY_ARR_CUTOFF",
    "seat_assignment",
    "flight_protects",
    "crew_violations",
    "crew_duty_check",
    "special_needs_sla",
    "compute_metrics",
]

# a flight "recovers same day" iff it arrives on or before this cutoff
SAME_DAY_ARR_CUTOFF = 1440  # 24:00 of the scenario day


def seat_assignment(ledger: ClaimLedger, scenario: dict[str, Any]) -> dict[str, str]:
    """passenger id -> flight id (only real seat allocations)."""
    fids = {f["id"] for f in scenario["flights"]}
    out: dict[str, str] = {}
    for resource, pairs in ledger.allocations().items():
        if not resource.startswith("seat:"):
            continue
        fid = resource.split(":", 1)[1]
        if fid not in fids:
            continue
        for beneficiary, _claim in pairs:
            out[beneficiary] = fid
    return out


def flight_protects(scenario: dict[str, Any], pax: dict[str, Any], flight: dict[str, Any]) -> bool:
    """Does ``flight`` satisfy ``pax``'s regulated SLA (protected categories)?"""
    flags = pax["flags"]
    if "MED" in flags:
        return flight["arr_min"] <= pax["med_deadline_min"]
    if "UM" in flags:
        return flight["nonstop"] and flight["dep_min"] <= scenario["um_curfew_dep_min"]
    if "WCHR" in flags:
        return flight["nonstop"] and flight["arr_min"] <= scenario["wchr_arr_limit_min"]
    if "TC" in flags:
        return flight["arr_min"] <= pax["tc_cutoff_min"]
    return flight["arr_min"] <= SAME_DAY_ARR_CUTOFF


def crew_violations(ledger: ClaimLedger, scenario: dict[str, Any]) -> int:
    """I2: crews assigned to the ferry section beyond their remaining duty."""
    required = ferry_required_duty(scenario)
    duty = {c["id"]: c["duty_remaining_min"] for c in scenario["crews"]}
    violations = 0
    for resource, pairs in ledger.allocations().items():
        if not resource.startswith("crew:"):
            continue
        for crew_id, _claim in pairs:
            if duty.get(crew_id, 0) < required:
                violations += 1
    return violations


def crew_duty_check(scenario: dict[str, Any]):
    """A ``verify.DomainCheck`` for **I2**, re-derived from the log alone.

    Replays the manifest and asserts no crew resource is held by a crew whose
    remaining duty is below the ferry requirement. Passed to ``verify_log`` so
    ``tarmac verify-log`` re-checks the zero-crew-violation invariant.
    """
    required = ferry_required_duty(scenario)
    duty = {c["id"]: c["duty_remaining_min"] for c in scenario["crews"]}

    def _check(entries):
        manifest = replay_manifest(entries)
        bad: list[str] = []
        for res, bens in sorted(manifest.items()):
            if not res.startswith("crew:"):
                continue
            for b in bens:
                if duty.get(b, 0) < required:
                    bad.append(f"{b} on {res} ({duty.get(b, 0)} < {required} min)")
        return (
            "I2.crew_duty",
            not bad,
            "zero crew duty violations in the final manifest" if not bad else "; ".join(bad),
        )

    return _check


def special_needs_sla(ledger: ClaimLedger, scenario: dict[str, Any]) -> dict[str, Any]:
    """Per-category SLA satisfaction for MED/UM/WCHR passengers."""
    assign = seat_assignment(ledger, scenario)
    fmap = flight_by_id(scenario)
    met: list[str] = []
    failed: list[str] = []
    for p in scenario["pax"]:
        if not any(f in ("MED", "UM", "WCHR") for f in p["flags"]):
            continue
        fid = assign.get(p["id"])
        ok = fid is not None and flight_protects(scenario, p, fmap[fid])
        (met if ok else failed).append(p["id"])
    total = len(met) + len(failed)
    return {
        "met": sorted(met),
        "failed": sorted(failed),
        "total": total,
        "pct": round(100.0 * len(met) / total, 1) if total else 100.0,
    }


def compute_metrics(
    ledger: ClaimLedger,
    scenario: dict[str, Any],
    *,
    rounds_to_quiescence: int,
    contest_spend: int,
    quiescent: bool = True,
) -> dict[str, Any]:
    """The full metrics row for one run (society or baseline)."""
    assign = seat_assignment(ledger, scenario)
    fmap = flight_by_id(scenario)
    pax = scenario["pax"]

    seated = set(assign)
    same_day = {
        pid for pid, fid in assign.items() if fmap[fid]["arr_min"] <= SAME_DAY_ARR_CUTOFF
    }
    # a passenger with no seat at all must be lodged overnight (the hotel block
    # exists for exactly these) — this is the operational "stranded overnight".
    unseated = len(pax) - len(seated)
    stranded_overnight = unseated
    not_same_day = len(pax) - len(same_day)

    tc_saved = 0
    protected_stranded = 0  # {MED,UM,WCHR,TC} pax whose SLA/connection was NOT met
    for p in pax:
        flags = p["flags"]
        is_protected = any(f in ("MED", "UM", "WCHR", "TC") for f in flags)
        if not is_protected:
            continue
        fid = assign.get(p["id"])
        protected = fid is not None and flight_protects(scenario, p, fmap[fid])
        if not protected:
            protected_stranded += 1
        if "TC" in flags and protected:
            tc_saved += 1

    sla = special_needs_sla(ledger, scenario)

    return {
        "stranded_overnight": stranded_overnight,
        "unseated": unseated,
        "not_same_day": not_same_day,
        "seated": len(seated),
        "same_day_recovered": len(same_day),
        "protected_stranded": protected_stranded,
        "tight_connections_saved": tc_saved,
        "special_needs_sla_pct": sla["pct"],
        "special_needs_failed": sla["failed"],
        "crew_violations": crew_violations(ledger, scenario),
        "rounds_to_quiescence": rounds_to_quiescence,
        "quiescent": quiescent,
        "contest_spend": contest_spend,
    }
