"""Replay + invariant verification over a hash-chained decision log.

``verify_log`` is the zero-trust auditor behind ``tarmac verify-log``: given
only the log (a run.db or a list of :class:`LogEntry`), it

1. re-derives the whole SHA-256 chain (``verify_chain``);
2. **replays** the low-level ``alloc``/``dealloc`` stream into a manifest and
   checks it byte-matches the ``manifest`` entry the run recorded (**I5**);
3. re-checks the domain-agnostic invariants it can see from the log alone:
   **I1** no resource over capacity and no beneficiary holding two units in
   one exclusivity group; **I3** every ruling cites >= 1 source; **I4** every
   accepted reveal re-derives its commitment digest and every rejected reveal
   genuinely does not.

Domain-specific invariants (e.g. Tarmac's **I2** zero crew duty violations)
are supplied as ``domain_checks`` — callables ``(entries) -> (name, ok,
detail)`` — so the core stays airline-agnostic while ``verify-log`` on a
Tarmac run still re-checks everything.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .canonical import hash_obj
from .chainlog import ChainLog, LogEntry, verify_chain
from .commitment import commitment_digest
from .storage import SQLiteStorage, Storage

__all__ = ["VerifyReport", "replay_manifest", "verify_log", "DomainCheck"]

DomainCheck = Callable[[Sequence[LogEntry]], "tuple[str, bool, str]"]


@dataclass
class VerifyReport:
    """Result of :func:`verify_log`. ``ok`` iff every check passed."""

    ok: bool
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    manifest: dict[str, list[str]] = field(default_factory=dict)
    manifest_hash: str = ""
    chain_length: int = 0

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))
        if not ok:
            self.ok = False

    def failures(self) -> list[tuple[str, bool, str]]:
        return [c for c in self.checks if not c[1]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in self.checks],
            "manifest_hash": self.manifest_hash,
            "chain_length": self.chain_length,
        }


def _load_entries(source: Storage | ChainLog | Iterable[LogEntry] | str) -> list[LogEntry]:
    if isinstance(source, str):
        source = SQLiteStorage(source)
    if isinstance(source, Storage):
        return ChainLog(source).entries()
    if isinstance(source, ChainLog):
        return source.entries()
    return list(source)


def replay_manifest(entries: Iterable[LogEntry]) -> dict[str, list[str]]:
    """Rebuild the final allocation state from the ``alloc``/``dealloc`` stream.

    Every grant (initial, or by a ruling) emits one ``alloc`` per beneficiary;
    every release/revoke/void emits one ``dealloc``. Replaying them in
    sequence order reconstructs the manifest from the log alone (**I5**).
    """
    held: dict[str, set[str]] = {}
    for e in entries:
        if e.kind == "alloc":
            res = e.body["resource"]
            held.setdefault(res, set()).add(e.body["beneficiary"])
        elif e.kind == "dealloc":
            res = e.body["resource"]
            held.get(res, set()).discard(e.body["beneficiary"])
    return {res: sorted(bens) for res, bens in held.items() if bens}


def _check_capacity_and_exclusivity(
    entries: Sequence[LogEntry], report: VerifyReport
) -> None:
    """I1: no resource over capacity; no beneficiary holds 2 units in a group."""
    capacity: dict[str, int] = {}
    group: dict[str, str | None] = {}
    for e in entries:
        if e.kind == "resource":
            capacity[e.body["id"]] = e.body["capacity"]
            group[e.body["id"]] = e.body.get("group")

    manifest = report.manifest
    over = [
        f"{res}: {len(bens)} > cap {capacity.get(res, 0)}"
        for res, bens in manifest.items()
        if len(bens) > capacity.get(res, 0)
    ]
    report.add(
        "I1.capacity",
        not over,
        "no resource over capacity" if not over else "; ".join(over),
    )

    # exclusivity: a beneficiary may hold at most one unit per non-null group
    seen: dict[tuple[str, str], str] = {}
    clashes: list[str] = []
    for res, bens in sorted(manifest.items()):
        g = group.get(res)
        if g is None:
            continue
        for b in bens:
            key = (g, b)
            if key in seen:
                clashes.append(f"{b} holds {seen[key]} and {res} (group {g})")
            else:
                seen[key] = res
    report.add(
        "I1.exclusivity",
        not clashes,
        "no beneficiary double-allocated in a group" if not clashes else "; ".join(clashes),
    )


def _check_reveals(entries: Sequence[LogEntry], report: VerifyReport) -> None:
    """I4: accepted reveals re-derive their digest; rejects genuinely mismatch."""
    digests: dict[str, str] = {}
    for e in entries:
        if e.kind == "commit":
            digests[e.body["commitment_id"]] = e.body["digest"]

    bad_accept: list[str] = []
    bad_reject: list[str] = []
    n_ok = n_rej = 0
    for e in entries:
        if e.kind == "reveal_ok":
            n_ok += 1
            cid = e.body["commitment_id"]
            derived = commitment_digest(e.body["claim"], e.body["nonce"])
            if derived != digests.get(cid):
                bad_accept.append(cid)
        elif e.kind == "reveal_reject":
            n_rej += 1
            cid = e.body["commitment_id"]
            derived = commitment_digest(e.body["claim"], e.body["nonce"])
            if derived == digests.get(cid):
                bad_reject.append(cid)  # was rejected but actually matches — wrong

    report.add(
        "I4.accepted_reveals_match",
        not bad_accept,
        f"{n_ok} accepted reveals re-derive their commitment"
        if not bad_accept
        else "mismatched accepted reveals: " + ", ".join(bad_accept),
    )
    report.add(
        "I4.rejected_reveals_mismatch",
        not bad_reject,
        f"{n_rej} rejected reveals genuinely fail to match"
        if not bad_reject
        else "rejected reveals that actually match: " + ", ".join(bad_reject),
    )


def _check_rulings_cite(entries: Sequence[LogEntry], report: VerifyReport) -> None:
    """I3: every ruling in the log cites at least one source."""
    uncited: list[str] = []
    n = 0
    for e in entries:
        if e.kind == "ruling":
            n += 1
            body = e.body.get("body", {})
            cites = [c for c in body.get("citations", []) if str(c).strip()]
            if not cites:
                uncited.append(e.body.get("ruling_id", f"seq{e.seq}"))
    report.add(
        "I3.rulings_cite_source",
        not uncited,
        f"all {n} rulings cite >= 1 source" if not uncited else "uncited: " + ", ".join(uncited),
    )


def verify_log(
    source: Storage | ChainLog | Iterable[LogEntry] | str,
    *,
    domain_checks: Sequence[DomainCheck] = (),
) -> VerifyReport:
    """Re-derive the chain and re-check invariants I1, I3, I4, I5 (+ domain).

    ``source`` may be a run.db path, a ``Storage``/``ChainLog``, or an entry
    iterable. Returns a :class:`VerifyReport`; ``report.ok`` is the exit gate.
    """
    entries = _load_entries(source)
    report = VerifyReport(ok=True, chain_length=len(entries))

    chain_ok, chain_detail = verify_chain(entries)
    report.add("chain", chain_ok, chain_detail)

    # I5 — replay reproduces the recorded manifest
    replayed = replay_manifest(entries)
    report.manifest = replayed
    report.manifest_hash = hash_obj(replayed)
    recorded = next((e for e in reversed(entries) if e.kind == "manifest"), None)
    if recorded is None:
        report.add("I5.replay", False, "no manifest entry in log")
    else:
        rec_manifest = recorded.body["manifest"]
        rec_hash = recorded.body["manifest_hash"]
        match = replayed == rec_manifest and report.manifest_hash == rec_hash
        report.add(
            "I5.replay",
            match,
            "replay reproduces the recorded manifest byte-for-byte"
            if match
            else f"replay hash {report.manifest_hash[:12]} != recorded {rec_hash[:12]}",
        )

    _check_capacity_and_exclusivity(entries, report)
    _check_rulings_cite(entries, report)
    _check_reveals(entries, report)

    for check in domain_checks:
        name, ok, detail = check(entries)
        report.add(name, ok, detail)

    return report
