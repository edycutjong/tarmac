"""Storage abstraction for the claim ledger. SQLite is the default backend.

The protocol code talks to a tiny ``Storage`` interface (execute / query /
transaction). ``SQLiteStorage`` is the shipped implementation with real
locking semantics:

- mutations run inside ``BEGIN IMMEDIATE`` transactions, which take the
  SQLite RESERVED lock up-front — two writers cannot interleave a
  check-then-insert (the classic double-claim race);
- an in-process ``threading.RLock`` serializes threads sharing a connection;
- capacity is *also* enforced by a database trigger, so even a buggy caller
  cannot over-allocate a resource (invariant **I1** is a constraint, not a
  convention).

A Postgres backend (e.g. row-level ``SELECT ... FOR UPDATE``) can implement
the same interface; nothing above this module knows it is SQLite.
"""

from __future__ import annotations

import abc
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

__all__ = ["Storage", "SQLiteStorage", "IntegrityViolation"]


class IntegrityViolation(Exception):
    """A storage-level constraint (capacity, uniqueness) refused a mutation."""


class Storage(abc.ABC):
    """Minimal storage interface the ledger/chain-log require."""

    @abc.abstractmethod
    def executescript(self, script: str) -> None: ...

    @abc.abstractmethod
    def execute(self, sql: str, params: Sequence[Any] = ()) -> None: ...

    @abc.abstractmethod
    def query(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]: ...

    @abc.abstractmethod
    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Serialized read-modify-write scope (writer lock held throughout)."""
        ...

    def close(self) -> None:  # pragma: no cover - trivial default
        pass


class SQLiteStorage(Storage):
    """SQLite-backed storage with immediate-mode write transactions."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path,
            check_same_thread=False,
            isolation_level=None,  # explicit transaction control
            timeout=30.0,
        )
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._depth = 0

    # -- Storage interface -------------------------------------------------
    def executescript(self, script: str) -> None:
        with self._lock:
            self._conn.executescript(script)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        with self._lock:
            try:
                self._conn.execute(sql, tuple(params))
            except sqlite3.IntegrityError as exc:
                raise IntegrityViolation(str(exc)) from exc

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """``BEGIN IMMEDIATE`` write transaction; re-entrant for nesting."""
        with self._lock:
            if self._depth > 0:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            self._depth = 1
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")
            finally:
                self._depth = 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
