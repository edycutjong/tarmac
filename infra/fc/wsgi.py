"""Alibaba Function Compute 3.0 — MANAGED python runtime entrypoint (WSGI).

No container / no ACR: FC installs requirements.txt and invokes this WSGI
`handler(environ, start_response)` for the HTTP trigger. Tarmac needs
enum.StrEnum (3.11+); the managed runtime is 3.10, so we shim it BEFORE importing
the package (StrEnum is just str+Enum — byte-identical behaviour, verified).

Endpoints: GET / · /health · /verify · /run  (see infra/fc/handler.py for docs).
"""

from __future__ import annotations

import enum
import json
import os
import sys

# --- 3.10 compat shim: must run before any tarmac_society import -------------
if not hasattr(enum, "StrEnum"):
    class StrEnum(str, enum.Enum):  # noqa: D401,UP042 - 3.11 enum.StrEnum polyfill for the 3.10 runtime
        def __str__(self) -> str:
            return str(self.value)
    enum.StrEnum = StrEnum  # type: ignore[attr-defined]

# The package ships under src/ in the deployed code bundle.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "..", "src")
if os.path.isdir(_SRC):
    sys.path.insert(0, os.path.abspath(_SRC))

_PROOF_DB = os.path.abspath(os.path.join(_HERE, "..", "..", "docs", "proof", "live_run.db"))


def _verify() -> dict:
    from tarmac_society.chainlog import ChainLog
    from tarmac_society.storage import SQLiteStorage
    from tarmac_society.tarmac.metrics import crew_duty_check
    from tarmac_society.tarmac.seed import generate
    from tarmac_society.verify import verify_log

    if not os.path.exists(_PROOF_DB):
        return {"error": f"proof db not found at {_PROOF_DB}", "checks": []}
    entries = ChainLog(SQLiteStorage(_PROOF_DB)).entries()
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
        "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in report.checks],
        "source": "live qwen3.7-plus / qwen3.7-max run (committed proof)",
    }


def _run(scenario: str, seed: int) -> dict:
    from tarmac_society.tarmac.run import run_society
    from tarmac_society.tarmac.seed import generate

    bundle = run_society(generate(scenario, seed), seed, condition="society", db_path=":memory:")
    r = bundle.result
    return {
        "scenario": scenario, "seed": seed,
        "transport": "FakeQwen (offline deterministic — no key required)",
        "rounds_used": r.rounds_used, "quiescent": r.quiescent, "rulings": len(r.rulings),
        "manifest_hash": r.manifest_hash,
        "manifest_groups": {k: len(v) for k, v in r.manifest.items()},
    }


def _route(path: str, qs: dict) -> tuple[int, dict]:
    path = path.rstrip("/") or "/"
    if path == "/":
        return 200, {"service": "tarmac — auditable multi-agent society (Qwen Cloud)",
                     "endpoints": {"/health": "liveness",
                                   "/verify": "re-verify the committed live-Qwen run (I1/I3/I4/I5)",
                                   "/run": "run one deterministic offline society round (?scenario=&seed=)"},
                     "repo": "https://github.com/edycutjong/tarmac"}
    if path == "/health":
        return 200, {"status": "ok"}
    if path == "/verify":
        return 200, _verify()
    if path == "/run":
        return 200, _run(qs.get("scenario", ["storm_dfw"])[0], int(qs.get("seed", ["7"])[0]))
    return 404, {"error": f"no route {path}"}


def handler(event, context):
    """FC 3.0 event handler for an HTTP trigger.

    `event` is the HTTP request as JSON bytes; return {statusCode, headers, body}.
    """
    from urllib.parse import parse_qs
    try:
        req = json.loads(event) if isinstance(event, (bytes, bytearray, str)) else (event or {})
    except Exception:
        req = {}
    rc_http = (req.get("requestContext") or {}).get("http") or {}
    path = req.get("rawPath") or req.get("path") or rc_http.get("path") or "/"
    # queryParameters may be a flat dict, or fall back to parsing rawQueryString
    qp = req.get("queryParameters") or req.get("queryStringParameters")
    if qp:
        qs = {k: (v if isinstance(v, list) else [v]) for k, v in qp.items()}
    else:
        qs = parse_qs(req.get("rawQueryString", "") or "")
    try:
        code, payload = _route(path, qs)
    except Exception as exc:  # never 500 opaque
        code, payload = 500, {"error": type(exc).__name__, "detail": str(exc)[:400]}
    return {
        "statusCode": code,
        "headers": {"Content-Type": "application/json"},
        "isBase64Encoded": False,
        "body": json.dumps(payload, sort_keys=True, indent=2),
    }
