#!/usr/bin/env python
"""Submission readiness checklist — fails loudly if anything is missing.

Verifies the deliverables exist AND that the core claims still hold on the
committed fixture (society beats the single planner, invariants pass). Run
before recording the demo / submitting.

    python scripts/check_submission_readiness.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))


def _exists(rel: str) -> None:
    p = ROOT / rel
    check(f"file: {rel}", p.exists(), str(p) if p.exists() else "MISSING")


def main() -> int:
    for rel in (
        "README.md", "LICENSE", "pyproject.toml", "DEMO.md",
        "fixtures/storm_dfw_seed7.json", "docs/BENCH.md", "docs/friction-log.md",
        "examples/meeting_rooms.py", "scripts/ablation_bench.py",
        "scripts/verify_offline.py", "infra/ecs/setup.md",
    ):
        _exists(rel)

    readme = (ROOT / "README.md").read_text() if (ROOT / "README.md").exists() else ""
    check("README embeds the hero image", "docs/readme-hero.svg" in readme)
    check("README cites the test count", "326" in readme, "expected exact test count")
    check("MIT license visible", "MIT" in (ROOT / "LICENSE").read_text())

    from tarmac_society import verify_log
    from tarmac_society.tarmac.baseline import run_single_planner
    from tarmac_society.tarmac.metrics import compute_metrics, crew_duty_check
    from tarmac_society.tarmac.run import run_society
    from tarmac_society.tarmac.seed import load_fixture

    sc = load_fixture(ROOT / "fixtures" / "storm_dfw_seed7.json")

    soc = run_society(sc, 7, condition="society")
    single = run_single_planner(sc, 7)
    sm = compute_metrics(soc.ledger, sc, rounds_to_quiescence=soc.result.rounds_used,
                         contest_spend=soc.bank.total_staked())
    gm = compute_metrics(single.ledger, sc, rounds_to_quiescence=1, contest_spend=0)

    check("fixture produces >= 1 deadlock", len(soc.result.deadlocks) >= 1,
          f"{len(soc.result.deadlocks)} deadlocks")
    check("society quiesces", soc.result.quiescent)
    check("society SLA 100%", sm["special_needs_sla_pct"] == 100.0,
          f"{sm['special_needs_sla_pct']}%")
    check("society beats single (protected stranded)",
          sm["protected_stranded"] < gm["protected_stranded"],
          f"{sm['protected_stranded']} vs {gm['protected_stranded']}")
    check("society crew violations == 0 (I2)", sm["crew_violations"] == 0)
    check("single planner violates I2 (crew)", gm["crew_violations"] > 0)

    report = verify_log(soc.chainlog.entries(), domain_checks=[crew_duty_check(sc)])
    check("all invariants I1–I5 pass on the fixture run", report.ok,
          "; ".join(f"{n}" for n, ok, _ in report.failures()) or "ok")

    print("\nSubmission readiness:\n")
    for name, ok, detail in CHECKS:
        tag = "OK  " if ok else "MISS"
        print(f"  [{tag}] {name}" + (f"  ({detail})" if detail and not ok else ""))

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    if failed:
        print("NOT READY — fix the MISS items above")
        return 1
    print("READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
