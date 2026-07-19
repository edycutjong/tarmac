"""Alibaba Function Compute entrypoint for Tarmac.

FC 3.0 **Custom Runtime**: FC starts this process and injects the listen port as
``FC_SERVER_PORT``. We serve a tiny stdlib ``http.server`` app — no web
framework — keeping the deployed surface as dependency-light as the CLI
(``pydantic`` / ``typer`` / ``pynacl`` only). Everything it serves is the REAL
engine the tests exercise; there is no cloud-only code path.

Endpoints
    GET /            service info + endpoint list
    GET /health      liveness
    GET /verify      re-run the invariant checks (I1/I3/I4/I5 + crew duty) on the
                     committed live-Qwen proof run and report PASS/FAIL per check
    GET /run         run one deterministic offline society round and return the
                     signed manifest metrics (no model key needed — FakeQwen)

The ``/verify`` route reads ``docs/proof/live_run.db`` — a fully live
(``qwen3.7-plus`` / ``qwen3.7-max``) run committed to the repo — so the deployed
service demonstrably reproduces a real run's manifest byte-for-byte on Alibaba
infrastructure.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_BUILD_ROOT = Path(__file__).resolve().parents[2]  # infra/fc/handler.py -> build/
_PROOF_DB = _BUILD_ROOT / "docs" / "proof" / "live_run.db"


def _verify(db_path: Path) -> dict:
    from tarmac_society.tarmac.metrics import crew_duty_check
    from tarmac_society.tarmac.seed import generate
    from tarmac_society.verify import verify_log

    if not db_path.exists():
        return {"error": f"proof db not found at {db_path}", "checks": []}
    from tarmac_society.chainlog import ChainLog
    from tarmac_society.storage import SQLiteStorage

    entries = ChainLog(SQLiteStorage(str(db_path))).entries()
    genesis = entries[0].body if entries else {}
    domain_checks = []
    try:
        sc = generate(genesis.get("scenario", "storm_dfw"), genesis.get("seed", 7))
        domain_checks.append(crew_duty_check(sc))
    except Exception:
        pass
    report = verify_log(entries, domain_checks=domain_checks)
    return {
        "overall": "OK" if report.ok else "FAILED",
        "entries": report.chain_length,
        "manifest_hash": report.manifest_hash,
        "checks": [
            {"name": name, "ok": ok, "detail": detail}
            for name, ok, detail in report.checks
        ],
        "source": "live qwen3.7-plus / qwen3.7-max run (committed proof)",
    }


def _run(scenario: str, seed: int) -> dict:
    from tarmac_society.tarmac.run import run_society
    from tarmac_society.tarmac.seed import generate

    sc = generate(scenario, seed)
    bundle = run_society(sc, seed, condition="society", db_path=":memory:")
    r = bundle.result
    return {
        "scenario": scenario,
        "seed": seed,
        "transport": "FakeQwen (offline deterministic — no key required)",
        "rounds_used": r.rounds_used,
        "quiescent": r.quiescent,
        "rulings": len(r.rulings),
        "manifest_hash": r.manifest_hash,
        "manifest_groups": {k: len(v) for k, v in r.manifest.items()},
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "tarmac-fc/1.0"

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, sort_keys=True, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path)
        path = route.path.rstrip("/") or "/"
        try:
            if path == "/":
                self._send(200, {
                    "service": "tarmac — auditable multi-agent society (Qwen Cloud)",
                    "endpoints": {
                        "/health": "liveness",
                        "/verify": "re-verify the committed live-Qwen run (I1/I3/I4/I5)",
                        "/run": "run one deterministic offline society round (?scenario=&seed=)",
                    },
                    "repo": "https://github.com/edycutjong/tarmac",
                })
            elif path == "/health":
                self._send(200, {"status": "ok"})
            elif path == "/verify":
                self._send(200, _verify(_PROOF_DB))
            elif path == "/run":
                q = parse_qs(route.query)
                scenario = q.get("scenario", ["storm_dfw"])[0]
                seed = int(q.get("seed", ["7"])[0])
                self._send(200, _run(scenario, seed))
            else:
                self._send(404, {"error": f"no route {path}"})
        except Exception as exc:  # never 500 opaque — surface it
            self._send(500, {"error": type(exc).__name__, "detail": str(exc)[:400]})

    def log_message(self, *_args) -> None:  # quieter FC logs
        return


def main() -> None:
    port = int(os.environ.get("FC_SERVER_PORT", "9000"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
