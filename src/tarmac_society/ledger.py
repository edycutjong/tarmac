"""ClaimLedger — revocable claims on capacity-constrained resources.

The ledger is where negotiation gets *physics*: a claim is a typed mutation
attempt, not a sentence. Grants are atomic and all-or-nothing; capacity is
enforced by both a pre-check inside a write transaction and a database
trigger (invariant **I1**); exclusivity groups guarantee one beneficiary can
hold at most one unit per group (a passenger cannot be seated on two
flights). Sealed-bid claims flow commit → reveal → grant/blocked; a reveal
that does not match its commitment digest is rejected and logged
(invariant **I4**).

Every state change appends to the hash-chained decision log. Two low-level
event kinds — ``alloc`` / ``dealloc`` — carry the entire allocation state, so
``tarmac replay`` re-derives the final manifest from the log alone
(invariant **I5**).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from random import Random
from typing import Any

from .canonical import hash_obj
from .chainlog import ChainLog
from .commitment import commitment_digest, make_nonce, verify_commitment
from .schemas import ClaimProposal, ClaimRecord, ClaimStatus, RulingOp
from .storage import IntegrityViolation, SQLiteStorage, Storage

__all__ = ["ClaimLedger", "LedgerError"]


class LedgerError(Exception):
    """Protocol misuse (unknown resource/claim, illegal transition, ...)."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS resources (
    id         TEXT PRIMARY KEY,
    capacity   INTEGER NOT NULL CHECK (capacity >= 0),
    excl_group TEXT
);
CREATE TABLE IF NOT EXISTS allocations (
    resource_id TEXT NOT NULL,
    beneficiary TEXT NOT NULL,
    claim_id    TEXT NOT NULL,
    round       INTEGER NOT NULL,
    excl_group  TEXT,
    PRIMARY KEY (resource_id, beneficiary)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_alloc_group
    ON allocations(excl_group, beneficiary) WHERE excl_group IS NOT NULL;
CREATE TRIGGER IF NOT EXISTS trg_alloc_capacity
BEFORE INSERT ON allocations
BEGIN
    SELECT RAISE(ABORT, 'unknown resource')
     WHERE NOT EXISTS (SELECT 1 FROM resources WHERE id = NEW.resource_id);
    SELECT RAISE(ABORT, 'capacity exceeded')
     WHERE (SELECT COUNT(*) FROM allocations WHERE resource_id = NEW.resource_id)
        >= (SELECT capacity FROM resources WHERE id = NEW.resource_id);
END;
CREATE TABLE IF NOT EXISTS claims (
    id              TEXT PRIMARY KEY,
    agent           TEXT NOT NULL,
    resource_id     TEXT NOT NULL,
    qty             INTEGER NOT NULL,
    beneficiaries   TEXT NOT NULL,      -- JSON list
    basis           TEXT NOT NULL,
    revocable       INTEGER NOT NULL,
    payload         TEXT NOT NULL,      -- JSON dict
    status          TEXT NOT NULL,
    round_committed INTEGER NOT NULL,
    round_resolved  INTEGER,
    commitment_id   TEXT
);
CREATE TABLE IF NOT EXISTS commitments (
    id        TEXT PRIMARY KEY,
    agent     TEXT NOT NULL,
    digest    TEXT NOT NULL,
    round     INTEGER NOT NULL,
    revealed  INTEGER NOT NULL DEFAULT 0,
    ok        INTEGER,
    nonce_hex TEXT,
    claim_id  TEXT
);
"""


class ClaimLedger:
    """Shared, locked, chain-logged claim ledger (SQLite by default)."""

    def __init__(
        self,
        storage: Storage | None = None,
        chainlog: ChainLog | None = None,
        rng: Random | None = None,
    ) -> None:
        self.storage = storage or SQLiteStorage(":memory:")
        self.storage.executescript(SCHEMA)
        self.log = chainlog  # optional: pure-lib users may run without a chain
        self.rng = rng
        self._claim_seq = self._max_seq("claims", "c")
        self._commit_seq = self._max_seq("commitments", "m")

    # ------------------------------------------------------------------ utils
    def _max_seq(self, table: str, prefix: str) -> int:
        rows = self.storage.query(f"SELECT id FROM {table}")
        best = 0
        for (cid,) in rows:
            try:
                best = max(best, int(str(cid).split("-")[1]))
            except (IndexError, ValueError):
                continue
        return best

    def _next_claim_id(self) -> str:
        self._claim_seq += 1
        return f"c-{self._claim_seq:03d}"

    def _next_commit_id(self) -> str:
        self._commit_seq += 1
        return f"m-{self._commit_seq:03d}"

    def _emit(self, kind: str, body: dict[str, Any], round_: int) -> None:
        if self.log is not None:
            self.log.append(kind, body, round_)

    # -------------------------------------------------------------- resources
    def register_resource(self, resource_id: str, capacity: int, group: str | None = None) -> None:
        if capacity < 0:
            raise LedgerError("capacity must be >= 0")
        with self.storage.transaction():
            if self.storage.query("SELECT 1 FROM resources WHERE id=?", (resource_id,)):
                raise LedgerError(f"resource {resource_id!r} already registered")
            self.storage.execute(
                "INSERT INTO resources(id, capacity, excl_group) VALUES (?,?,?)",
                (resource_id, capacity, group),
            )
        self._emit("resource", {"id": resource_id, "capacity": capacity, "group": group}, 0)

    def resources(self) -> dict[str, dict[str, Any]]:
        rows = self.storage.query("SELECT id, capacity, excl_group FROM resources ORDER BY id")
        return {r[0]: {"capacity": r[1], "group": r[2]} for r in rows}

    def free(self, resource_id: str) -> int:
        row = self.storage.query("SELECT capacity FROM resources WHERE id=?", (resource_id,))
        if not row:
            raise LedgerError(f"unknown resource {resource_id!r}")
        used = self.storage.query(
            "SELECT COUNT(*) FROM allocations WHERE resource_id=?", (resource_id,)
        )[0][0]
        return row[0][0] - used

    # ----------------------------------------------------------- commitments
    def commit(self, agent: str, digest: str, round_: int) -> str:
        """Record a sealed bid. Only the digest is public."""
        cid = self._next_commit_id()
        with self.storage.transaction():
            self.storage.execute(
                "INSERT INTO commitments(id, agent, digest, round) VALUES (?,?,?,?)",
                (cid, agent, digest, round_),
            )
        self._emit("commit", {"commitment_id": cid, "agent": agent, "digest": digest}, round_)
        return cid

    def seal_and_commit(self, proposal: ClaimProposal, round_: int) -> tuple[str, str]:
        """Convenience: seal ``proposal`` (ledger rng), commit, return (commitment_id, nonce)."""
        nonce = make_nonce(self.rng)
        digest = commitment_digest(proposal.canonical_dict(), nonce)
        return self.commit(proposal.agent, digest, round_), nonce

    def reveal(
        self, commitment_id: str, proposal: ClaimProposal, nonce_hex: str, round_: int
    ) -> ClaimRecord:
        """Reveal a sealed claim. Mismatched reveals are rejected (I4)."""
        rows = self.storage.query(
            "SELECT agent, digest, revealed FROM commitments WHERE id=?", (commitment_id,)
        )
        if not rows:
            raise LedgerError(f"unknown commitment {commitment_id!r}")
        agent, digest, revealed = rows[0]
        if revealed:
            raise LedgerError(f"commitment {commitment_id!r} already revealed")
        if agent != proposal.agent:
            raise LedgerError("reveal agent does not match commitment agent")

        ok = verify_commitment(proposal.canonical_dict(), nonce_hex, digest)
        if not ok:
            with self.storage.transaction():
                self.storage.execute(
                    "UPDATE commitments SET revealed=1, ok=0, nonce_hex=? WHERE id=?",
                    (nonce_hex, commitment_id),
                )
            self._emit(
                "reveal_reject",
                {
                    "commitment_id": commitment_id,
                    "agent": proposal.agent,
                    "claim": proposal.canonical_dict(),
                    "nonce": nonce_hex,
                    "expected_digest": digest,
                },
                round_,
            )
            return ClaimRecord(
                id="",
                proposal=proposal,
                status=ClaimStatus.REVEAL_REJECTED,
                round_committed=round_,
                commitment_id=commitment_id,
            )

        claim_id = self._next_claim_id()
        with self.storage.transaction():
            self.storage.execute(
                "UPDATE commitments SET revealed=1, ok=1, nonce_hex=?, claim_id=? WHERE id=?",
                (nonce_hex, claim_id, commitment_id),
            )
        self._emit(
            "reveal_ok",
            {
                "commitment_id": commitment_id,
                "claim_id": claim_id,
                "agent": proposal.agent,
                "claim": proposal.canonical_dict(),
                "nonce": nonce_hex,
            },
            round_,
        )
        return self._insert_and_apply(claim_id, proposal, round_, commitment_id)

    def submit_plain(self, proposal: ClaimProposal, round_: int) -> ClaimRecord:
        """Unsealed claim path (single-planner baseline, simple reuse cases)."""
        claim_id = self._next_claim_id()
        self._emit(
            "claim_plain",
            {"claim_id": claim_id, "agent": proposal.agent, "claim": proposal.canonical_dict()},
            round_,
        )
        return self._insert_and_apply(claim_id, proposal, round_, None)

    # ------------------------------------------------------------ core apply
    def _insert_and_apply(
        self, claim_id: str, proposal: ClaimProposal, round_: int, commitment_id: str | None
    ) -> ClaimRecord:
        res = self.storage.query(
            "SELECT capacity, excl_group FROM resources WHERE id=?", (proposal.resource,)
        )
        if not res:
            raise LedgerError(f"unknown resource {proposal.resource!r}")
        group = res[0][1]

        with self.storage.transaction():
            self.storage.execute(
                "INSERT INTO claims(id, agent, resource_id, qty, beneficiaries, basis, revocable,"
                " payload, status, round_committed, commitment_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    claim_id,
                    proposal.agent,
                    proposal.resource,
                    proposal.qty,
                    json.dumps(proposal.beneficiaries),
                    proposal.basis,
                    int(proposal.revocable),
                    json.dumps(proposal.payload, sort_keys=True),
                    ClaimStatus.REVEALED.value,
                    round_,
                    commitment_id,
                ),
            )
            granted, reason = self._try_allocate(
                claim_id, proposal.resource, group, proposal.beneficiaries, round_
            )
            status = ClaimStatus.GRANTED if granted else ClaimStatus.BLOCKED
            self.storage.execute(
                "UPDATE claims SET status=?, round_resolved=? WHERE id=?",
                (status.value, round_ if granted else None, claim_id),
            )

        if granted:
            self._emit(
                "claim_granted",
                {
                    "claim_id": claim_id,
                    "agent": proposal.agent,
                    "resource": proposal.resource,
                    "beneficiaries": proposal.beneficiaries,
                },
                round_,
            )
            for b in proposal.beneficiaries:
                self._emit(
                    "alloc",
                    {"resource": proposal.resource, "beneficiary": b, "claim_id": claim_id},
                    round_,
                )
        else:
            self._emit(
                "claim_blocked",
                {
                    "claim_id": claim_id,
                    "agent": proposal.agent,
                    "resource": proposal.resource,
                    "qty": proposal.qty,
                    "reason": reason,
                },
                round_,
            )
        return self.claim(claim_id)

    def _try_allocate(
        self,
        claim_id: str,
        resource_id: str,
        group: str | None,
        beneficiaries: list[str],
        round_: int,
    ) -> tuple[bool, str]:
        """All-or-nothing allocation attempt. Must run inside a transaction."""
        capacity = self.storage.query(
            "SELECT capacity FROM resources WHERE id=?", (resource_id,)
        )[0][0]
        used = self.storage.query(
            "SELECT COUNT(*) FROM allocations WHERE resource_id=?", (resource_id,)
        )[0][0]
        if capacity - used < len(beneficiaries):
            return False, f"capacity ({capacity - used} free < {len(beneficiaries)} requested)"
        if group is not None:
            for b in beneficiaries:
                held = self.storage.query(
                    "SELECT resource_id FROM allocations WHERE excl_group=? AND beneficiary=?",
                    (group, b),
                )
                if held:
                    return False, f"beneficiary {b} already holds {held[0][0]} in group {group}"
        try:
            for b in beneficiaries:
                self.storage.execute(
                    "INSERT INTO allocations(resource_id, beneficiary, claim_id, round, excl_group)"
                    " VALUES (?,?,?,?,?)",
                    (resource_id, b, claim_id, round_, group),
                )
        except IntegrityViolation:  # trigger/unique caught the race
            raise  # transaction context rolls back; callers treat as fatal bug
        return True, "ok"

    # ------------------------------------------------------- release / rulings
    def release(
        self, agent: str, claim_id: str, beneficiaries: list[str] | None, round_: int
    ) -> None:
        """An agent voluntarily gives back (part of) its own granted claim."""
        rec = self.claim(claim_id)
        if rec.proposal.agent != agent:
            raise LedgerError(f"{agent} cannot release claim {claim_id} owned by {rec.proposal.agent}")
        if rec.status not in (ClaimStatus.GRANTED,):
            raise LedgerError(f"claim {claim_id} is {rec.status}, not granted")
        targets = beneficiaries if beneficiaries is not None else list(rec.holders)
        self._deallocate(claim_id, rec.proposal.resource, targets, round_)
        remaining = self.claim(claim_id).holders
        if not remaining:
            with self.storage.transaction():
                self.storage.execute(
                    "UPDATE claims SET status=?, round_resolved=? WHERE id=?",
                    (ClaimStatus.RELEASED.value, round_, claim_id),
                )
        self._emit("release", {"claim_id": claim_id, "agent": agent, "beneficiaries": targets}, round_)

    def _deallocate(
        self, claim_id: str, resource_id: str, beneficiaries: Iterable[str], round_: int
    ) -> None:
        with self.storage.transaction():
            for b in beneficiaries:
                rows = self.storage.query(
                    "SELECT 1 FROM allocations WHERE resource_id=? AND beneficiary=? AND claim_id=?",
                    (resource_id, b, claim_id),
                )
                if not rows:
                    raise LedgerError(
                        f"no allocation of {resource_id} to {b} under claim {claim_id}"
                    )
                self.storage.execute(
                    "DELETE FROM allocations WHERE resource_id=? AND beneficiary=? AND claim_id=?",
                    (resource_id, b, claim_id),
                )
        for b in beneficiaries:
            self._emit(
                "dealloc",
                {"resource": resource_id, "beneficiary": b, "claim_id": claim_id},
                round_,
            )

    def apply_ruling_ops(self, ops: list[RulingOp], ruling_id: str, round_: int) -> None:
        """Apply a mediator's binding ops in order. Constraints still hold (I1)."""
        for op in ops:
            rec = self.claim(op.claim_id)
            if op.op == "revoke":
                if rec.status != ClaimStatus.GRANTED:
                    raise LedgerError(f"cannot revoke {op.claim_id}: status {rec.status}")
                targets = op.beneficiaries if op.beneficiaries is not None else list(rec.holders)
                self._deallocate(op.claim_id, rec.proposal.resource, targets, round_)
                if not self.claim(op.claim_id).holders:
                    self._set_status(op.claim_id, ClaimStatus.REVOKED, round_)
            elif op.op == "grant":
                if rec.status != ClaimStatus.BLOCKED:
                    raise LedgerError(f"cannot grant {op.claim_id}: status {rec.status}")
                targets = op.beneficiaries if op.beneficiaries is not None else list(
                    rec.proposal.beneficiaries
                )
                group = self.resources()[rec.proposal.resource]["group"]
                with self.storage.transaction():
                    granted, reason = self._try_allocate(
                        op.claim_id, rec.proposal.resource, group, targets, round_
                    )
                    if not granted:
                        raise LedgerError(
                            f"ruling grant of {op.claim_id} violates constraints: {reason}"
                        )
                    self.storage.execute(
                        "UPDATE claims SET status=?, round_resolved=? WHERE id=?",
                        (ClaimStatus.GRANTED.value, round_, op.claim_id),
                    )
                for b in targets:
                    self._emit(
                        "alloc",
                        {"resource": rec.proposal.resource, "beneficiary": b, "claim_id": op.claim_id},
                        round_,
                    )
            elif op.op == "void":
                self.void_claim(op.claim_id, f"voided by ruling {ruling_id}", round_)
            self._emit(
                "ruling_op",
                {
                    "ruling_id": ruling_id,
                    "op": op.op,
                    "claim_id": op.claim_id,
                    "beneficiaries": op.beneficiaries,
                },
                round_,
            )

    def withdraw_claim(self, agent: str, claim_id: str, round_: int) -> None:
        """An agent withdraws its own *blocked* claim (a 'yield')."""
        rec = self.claim(claim_id)
        if rec.proposal.agent != agent:
            raise LedgerError(f"{agent} cannot withdraw claim {claim_id} owned by {rec.proposal.agent}")
        if rec.status != ClaimStatus.BLOCKED:
            raise LedgerError(f"only blocked claims can be withdrawn ({claim_id} is {rec.status})")
        self._set_status(claim_id, ClaimStatus.WITHDRAWN, round_)
        self._emit("withdraw", {"claim_id": claim_id, "agent": agent}, round_)

    def void_claim(self, claim_id: str, reason: str, round_: int) -> None:
        """Kill a claim (blocked or granted). Granted claims lose their allocations."""
        rec = self.claim(claim_id)
        if rec.status in (ClaimStatus.VOIDED, ClaimStatus.REVOKED, ClaimStatus.RELEASED):
            return
        if rec.status == ClaimStatus.GRANTED and rec.holders:
            self._deallocate(claim_id, rec.proposal.resource, list(rec.holders), round_)
        self._set_status(claim_id, ClaimStatus.VOIDED, round_)
        self._emit("void", {"claim_id": claim_id, "reason": reason}, round_)

    def _set_status(self, claim_id: str, status: ClaimStatus, round_: int) -> None:
        with self.storage.transaction():
            self.storage.execute(
                "UPDATE claims SET status=?, round_resolved=? WHERE id=?",
                (status.value, round_, claim_id),
            )

    # ---------------------------------------------------------------- queries
    def claim(self, claim_id: str) -> ClaimRecord:
        rows = self.storage.query(
            "SELECT id, agent, resource_id, qty, beneficiaries, basis, revocable, payload,"
            " status, round_committed, round_resolved, commitment_id FROM claims WHERE id=?",
            (claim_id,),
        )
        if not rows:
            raise LedgerError(f"unknown claim {claim_id!r}")
        r = rows[0]
        holders = [
            b[0]
            for b in self.storage.query(
                "SELECT beneficiary FROM allocations WHERE claim_id=? ORDER BY beneficiary",
                (claim_id,),
            )
        ]
        return ClaimRecord(
            id=r[0],
            proposal=ClaimProposal(
                agent=r[1],
                resource=r[2],
                qty=r[3],
                beneficiaries=json.loads(r[4]),
                basis=r[5],
                revocable=bool(r[6]),
                payload=json.loads(r[7]),
            ),
            status=ClaimStatus(r[8]),
            round_committed=r[9],
            round_resolved=r[10],
            commitment_id=r[11],
            holders=holders,
        )

    def claims_with_status(self, *statuses: ClaimStatus) -> list[ClaimRecord]:
        vals = tuple(s.value for s in statuses)
        q = ",".join("?" for _ in vals)
        rows = self.storage.query(
            f"SELECT id FROM claims WHERE status IN ({q}) ORDER BY id", vals
        )
        return [self.claim(r[0]) for r in rows]

    def blocked_claims(self) -> list[ClaimRecord]:
        return self.claims_with_status(ClaimStatus.BLOCKED)

    def granted_claims(self) -> list[ClaimRecord]:
        return self.claims_with_status(ClaimStatus.GRANTED)

    def allocations(self) -> dict[str, list[tuple[str, str]]]:
        """resource -> [(beneficiary, claim_id)], deterministic ordering."""
        rows = self.storage.query(
            "SELECT resource_id, beneficiary, claim_id FROM allocations"
            " ORDER BY resource_id, beneficiary"
        )
        out: dict[str, list[tuple[str, str]]] = {}
        for res, ben, cid in rows:
            out.setdefault(res, []).append((ben, cid))
        return out

    def holder_agents(self, resource_id: str) -> dict[str, list[str]]:
        """agent -> claim ids holding allocations on ``resource_id``."""
        rows = self.storage.query(
            "SELECT DISTINCT a.claim_id, c.agent FROM allocations a JOIN claims c"
            " ON a.claim_id = c.id WHERE a.resource_id=? ORDER BY a.claim_id",
            (resource_id,),
        )
        out: dict[str, list[str]] = {}
        for cid, agent in rows:
            out.setdefault(agent, []).append(cid)
        return out

    def beneficiary_resource(self, group: str, beneficiary: str) -> str | None:
        rows = self.storage.query(
            "SELECT resource_id FROM allocations WHERE excl_group=? AND beneficiary=?",
            (group, beneficiary),
        )
        return rows[0][0] if rows else None

    # ---------------------------------------------------------------- manifest
    def manifest(self) -> dict[str, list[str]]:
        """Final allocation state: resource -> sorted beneficiaries."""
        return {res: sorted(b for b, _ in pairs) for res, pairs in self.allocations().items()}

    def manifest_hash(self) -> str:
        return hash_obj(self.manifest())
