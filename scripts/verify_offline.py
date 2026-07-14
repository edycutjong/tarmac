#!/usr/bin/env python
"""Zero-key offline verification — the judge's trust path.

1. Hard-disables all network access (any socket attempt raises).
2. Loads the committed fixture, runs the full society OFFLINE (deterministic
   policy agents), and writes a run.db.
3. Replays the manifest from the log and re-checks invariants I1–I5 (+ the
   airline I2 crew-duty check) with ``verify_log``.

Exit 0 iff the offline run reproduces its manifest and every invariant holds
without a single byte crossing the network.

    python scripts/verify_offline.py
"""

from __future__ import annotations

import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _disable_network() -> None:
    """Make any socket creation raise — proves the offline path needs no net."""

    def _blocked(*_a, **_k):  # pragma: no cover - only fires on a violation
        raise RuntimeError("network access is disabled in offline verification")

    socket.socket = _blocked  # type: ignore[assignment]
    socket.create_connection = _blocked  # type: ignore[assignment]


def main() -> int:
    _disable_network()

    from tarmac_society import replay_manifest, verify_log
    from tarmac_society.tarmac.metrics import crew_duty_check
    from tarmac_society.tarmac.run import run_society
    from tarmac_society.tarmac.seed import load_fixture

    fixture = ROOT / "fixtures" / "storm_dfw_seed7.json"
    scenario = load_fixture(fixture)
    print(f"loaded fixture: {fixture.name} ({len(scenario['pax'])} pax, seed {scenario['seed']})")

    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "offline_run.db")
        bundle = run_society(scenario, scenario["seed"], condition="society", db_path=db)

        replayed = replay_manifest(bundle.chainlog.entries())
        if replayed != bundle.result.manifest:
            print("FAIL: replay does not reproduce the manifest (I5)")
            return 1
        print(f"replay reproduces the manifest (I5): {bundle.result.manifest_hash[:16]}…")

        report = verify_log(db, domain_checks=[crew_duty_check(scenario)])
        for name, ok, detail in report.checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

        if not report.ok:
            print("\nOFFLINE VERIFICATION FAILED")
            return 1

    print(f"\nOFFLINE VERIFICATION OK — {report.chain_length} log entries, no network used")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
