"""Hash-chained decision log.

Every ledger mutation, commitment, reveal, position, deadlock and ruling
extends a SHA-256 chain:

    hash_n = SHA256( prev_hash || canonical({seq, round, kind, body}) )

The genesis hash binds the run header (scenario, seed, condition), so a log
is only valid *for the run it claims to describe*. ``verify_chain`` re-derives
the whole chain; ``tarmac verify-log`` builds on it (see ``verify.py``).

Entries carry logical time only (round + sequence) — no wall clock — so two
runs with identical inputs produce byte-identical chains.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .canonical import canonical_bytes, sha256_hex
from .storage import Storage

__all__ = ["LogEntry", "ChainLog", "verify_chain", "GENESIS_KIND"]

GENESIS_KIND = "genesis"

SCHEMA = """
CREATE TABLE IF NOT EXISTS log_chain (
    seq       INTEGER PRIMARY KEY,
    round     INTEGER NOT NULL,
    kind      TEXT    NOT NULL,
    body      TEXT    NOT NULL,   -- canonical JSON
    prev_hash TEXT    NOT NULL,
    hash      TEXT    NOT NULL
);
"""


@dataclass(frozen=True)
class LogEntry:
    seq: int
    round: int
    kind: str
    body: dict[str, Any]
    prev_hash: str
    hash: str


def _entry_hash(prev_hash: str, seq: int, round_: int, kind: str, body: Mapping[str, Any]) -> str:
    envelope = {"seq": seq, "round": round_, "kind": kind, "body": dict(body)}
    return sha256_hex(prev_hash.encode("utf-8") + canonical_bytes(envelope))


class ChainLog:
    """Append-only hash chain over a ``Storage`` backend."""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        storage.executescript(SCHEMA)
        row = storage.query("SELECT seq, hash FROM log_chain ORDER BY seq DESC LIMIT 1")
        if row:
            self._seq, self._head = row[0][0], row[0][1]
        else:
            self._seq, self._head = -1, ""

    # -- writing ------------------------------------------------------------
    def genesis(self, header: Mapping[str, Any]) -> LogEntry:
        """First entry; binds the run header. Must be called exactly once."""
        if self._seq >= 0:
            raise RuntimeError("chain already has a genesis entry")
        return self.append(GENESIS_KIND, dict(header), round_=0)

    def append(self, kind: str, body: Mapping[str, Any], round_: int) -> LogEntry:
        if self._seq < 0 and kind != GENESIS_KIND:
            raise RuntimeError("append before genesis")
        import json

        seq = self._seq + 1
        prev = self._head
        h = _entry_hash(prev, seq, round_, kind, body)
        body_text = canonical_bytes(dict(body)).decode("utf-8")
        with self.storage.transaction():
            self.storage.execute(
                "INSERT INTO log_chain(seq, round, kind, body, prev_hash, hash) VALUES (?,?,?,?,?,?)",
                (seq, round_, kind, body_text, prev, h),
            )
        self._seq, self._head = seq, h
        entry = LogEntry(seq=seq, round=round_, kind=kind, body=json.loads(body_text), prev_hash=prev, hash=h)
        return entry

    # -- reading ------------------------------------------------------------
    @property
    def head(self) -> str:
        return self._head

    @property
    def length(self) -> int:
        return self._seq + 1

    def entries(self, kind: str | None = None) -> list[LogEntry]:
        import json

        if kind is None:
            rows = self.storage.query(
                "SELECT seq, round, kind, body, prev_hash, hash FROM log_chain ORDER BY seq"
            )
        else:
            rows = self.storage.query(
                "SELECT seq, round, kind, body, prev_hash, hash FROM log_chain WHERE kind=? ORDER BY seq",
                (kind,),
            )
        return [
            LogEntry(seq=r[0], round=r[1], kind=r[2], body=json.loads(r[3]), prev_hash=r[4], hash=r[5])
            for r in rows
        ]


def verify_chain(entries: Iterable[LogEntry]) -> tuple[bool, str]:
    """Re-derive the hash chain. Returns ``(ok, detail)``.

    Checks: contiguous sequence from 0, first entry is genesis, each
    ``prev_hash`` links to the previous entry, and each ``hash`` re-derives
    from its content.
    """
    prev_hash = ""
    expected_seq = 0
    any_entry = False
    for e in entries:
        any_entry = True
        if e.seq != expected_seq:
            return False, f"sequence gap at {e.seq} (expected {expected_seq})"
        if e.seq == 0 and e.kind != GENESIS_KIND:
            return False, "first entry is not genesis"
        if e.prev_hash != prev_hash:
            return False, f"broken link at seq {e.seq}"
        derived = _entry_hash(e.prev_hash, e.seq, e.round, e.kind, e.body)
        if derived != e.hash:
            return False, f"hash mismatch at seq {e.seq}"
        prev_hash = e.hash
        expected_seq += 1
    if not any_entry:
        return False, "empty chain"
    return True, f"chain ok ({expected_seq} entries, head {prev_hash[:16]}...)"
