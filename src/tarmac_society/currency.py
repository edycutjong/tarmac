"""Credibility currency — the economic engine that bounds argument.

Each agent holds a contest budget ``V_i`` (integer points). Filing a
blocking position against another agent's granted claim opens a *contest*
and costs ``c`` immediately. When a mediator ruling lands:

- the winner's stake is refunded **plus a premium** ``p`` (credibility is
  minted for being right),
- the loser's stake is **burned**.

Contests left unadjudicated at finalization are voided and the stake is
returned (no premium). Because every contest has a hard price and budgets
are finite, argument length is bounded *economically* — an agent literally
cannot afford to block forever — and the per-round balance snapshots give
the bench its contest-spend curves.

Formally: agent utility = objective score − Σ stakes + refunds + premiums.
"""

from __future__ import annotations

import json
from typing import Any

from .chainlog import ChainLog
from .schemas import Position
from .storage import Storage

__all__ = ["CredibilityBank", "CurrencyError"]


class CurrencyError(Exception):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS balances (
    agent   TEXT PRIMARY KEY,
    balance INTEGER NOT NULL CHECK (balance >= 0)
);
CREATE TABLE IF NOT EXISTS contests (
    id           TEXT PRIMARY KEY,
    agent        TEXT NOT NULL,
    target_claim TEXT NOT NULL,
    round_opened INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',  -- open | won | lost | void
    cost         INTEGER NOT NULL,
    position     TEXT NOT NULL                  -- JSON of the blocking Position
);
CREATE TABLE IF NOT EXISTS spend_curve (
    round   INTEGER NOT NULL,
    agent   TEXT NOT NULL,
    balance INTEGER NOT NULL,
    PRIMARY KEY (round, agent)
);
"""


class CredibilityBank:
    def __init__(
        self,
        storage: Storage,
        budgets: dict[str, int],
        contest_cost: int = 10,
        premium: int = 5,
        chainlog: ChainLog | None = None,
    ) -> None:
        if contest_cost <= 0 or premium < 0:
            raise CurrencyError("contest_cost must be > 0 and premium >= 0")
        self.storage = storage
        self.contest_cost = contest_cost
        self.premium = premium
        self.log = chainlog
        self._seq = 0
        storage.executescript(SCHEMA)
        with storage.transaction():
            for agent, v in sorted(budgets.items()):
                if v < 0:
                    raise CurrencyError(f"negative budget for {agent}")
                if not storage.query("SELECT 1 FROM balances WHERE agent=?", (agent,)):
                    storage.execute(
                        "INSERT INTO balances(agent, balance) VALUES (?,?)", (agent, v)
                    )
        rows = storage.query("SELECT id FROM contests")
        for (cid,) in rows:
            try:
                self._seq = max(self._seq, int(str(cid).split("-")[1]))
            except (IndexError, ValueError):
                pass

    def _emit(self, kind: str, body: dict[str, Any], round_: int) -> None:
        if self.log is not None:
            self.log.append(kind, body, round_)

    # ------------------------------------------------------------- balances
    def balance(self, agent: str) -> int:
        rows = self.storage.query("SELECT balance FROM balances WHERE agent=?", (agent,))
        if not rows:
            raise CurrencyError(f"unknown agent {agent!r}")
        return rows[0][0]

    def balances(self) -> dict[str, int]:
        return {a: b for a, b in self.storage.query("SELECT agent, balance FROM balances ORDER BY agent")}

    def can_contest(self, agent: str) -> bool:
        return self.balance(agent) >= self.contest_cost

    # ------------------------------------------------------------- contests
    def open_contest(self, position: Position, round_: int) -> str | None:
        """Charge the stake and open a contest; None if the agent cannot afford it."""
        if position.stance != "block":
            raise CurrencyError("only blocking positions open contests")
        agent = position.agent
        if not self.can_contest(agent):
            self._emit(
                "contest_declined",
                {"agent": agent, "target_claim": position.target_claim, "reason": "insufficient budget"},
                round_,
            )
            return None
        dup = self.storage.query(
            "SELECT id FROM contests WHERE agent=? AND target_claim=? AND status='open'",
            (agent, position.target_claim),
        )
        if dup:
            return dup[0][0]  # one open contest per (agent, target)
        self._seq += 1
        cid = f"x-{self._seq:02d}"
        with self.storage.transaction():
            self.storage.execute(
                "UPDATE balances SET balance = balance - ? WHERE agent=?",
                (self.contest_cost, agent),
            )
            self.storage.execute(
                "INSERT INTO contests(id, agent, target_claim, round_opened, status, cost, position)"
                " VALUES (?,?,?,?,'open',?,?)",
                (
                    cid,
                    agent,
                    position.target_claim,
                    round_,
                    self.contest_cost,
                    json.dumps(position.model_dump(mode="json"), sort_keys=True),
                ),
            )
        self._emit(
            "contest_opened",
            {
                "contest_id": cid,
                "agent": agent,
                "target_claim": position.target_claim,
                "cost": self.contest_cost,
            },
            round_,
        )
        return cid

    def open_contests(self) -> list[dict[str, Any]]:
        rows = self.storage.query(
            "SELECT id, agent, target_claim, round_opened, cost, position FROM contests"
            " WHERE status='open' ORDER BY id"
        )
        return [
            {
                "id": r[0],
                "agent": r[1],
                "target_claim": r[2],
                "round_opened": r[3],
                "cost": r[4],
                "position": json.loads(r[5]),
            }
            for r in rows
        ]

    def settle(self, contest_id: str, won: bool, round_: int) -> None:
        rows = self.storage.query(
            "SELECT agent, cost, status FROM contests WHERE id=?", (contest_id,)
        )
        if not rows:
            raise CurrencyError(f"unknown contest {contest_id!r}")
        agent, cost, status = rows[0]
        if status != "open":
            raise CurrencyError(f"contest {contest_id} already {status}")
        with self.storage.transaction():
            if won:
                self.storage.execute(
                    "UPDATE balances SET balance = balance + ? WHERE agent=?",
                    (cost + self.premium, agent),
                )
            self.storage.execute(
                "UPDATE contests SET status=? WHERE id=?",
                ("won" if won else "lost", contest_id),
            )
        self._emit(
            "contest_settled",
            {
                "contest_id": contest_id,
                "agent": agent,
                "won": won,
                "refund": cost + self.premium if won else 0,
                "burned": 0 if won else cost,
            },
            round_,
        )

    def void_contest(self, contest_id: str, round_: int) -> None:
        """Unadjudicated at finalization: stake returned, no premium."""
        rows = self.storage.query(
            "SELECT agent, cost, status FROM contests WHERE id=?", (contest_id,)
        )
        if not rows:
            raise CurrencyError(f"unknown contest {contest_id!r}")
        agent, cost, status = rows[0]
        if status != "open":
            raise CurrencyError(f"contest {contest_id} already {status}")
        with self.storage.transaction():
            self.storage.execute(
                "UPDATE balances SET balance = balance + ? WHERE agent=?", (cost, agent)
            )
            self.storage.execute("UPDATE contests SET status='void' WHERE id=?", (contest_id,))
        self._emit("contest_void", {"contest_id": contest_id, "agent": agent, "refund": cost}, round_)

    # ------------------------------------------------------------ reporting
    def snapshot(self, round_: int) -> None:
        with self.storage.transaction():
            for agent, bal in sorted(self.balances().items()):
                self.storage.execute(
                    "INSERT OR REPLACE INTO spend_curve(round, agent, balance) VALUES (?,?,?)",
                    (round_, agent, bal),
                )

    def curve(self) -> list[tuple[int, str, int]]:
        return [
            (r, a, b)
            for r, a, b in self.storage.query(
                "SELECT round, agent, balance FROM spend_curve ORDER BY round, agent"
            )
        ]

    def spend_summary(self) -> dict[str, dict[str, int]]:
        """Per agent: stakes paid, refunds, premiums earned, burned, final balance."""
        out: dict[str, dict[str, int]] = {
            a: {"staked": 0, "refunded": 0, "premium": 0, "burned": 0, "balance": b}
            for a, b in self.balances().items()
        }
        for agent, cost, status in self.storage.query(
            "SELECT agent, cost, status FROM contests"
        ):
            rec = out.setdefault(
                agent, {"staked": 0, "refunded": 0, "premium": 0, "burned": 0, "balance": 0}
            )
            rec["staked"] += cost
            if status == "won":
                rec["refunded"] += cost
                rec["premium"] += self.premium
            elif status == "lost":
                rec["burned"] += cost
            elif status == "void":
                rec["refunded"] += cost
        return out

    def total_spend(self) -> int:
        """Net credibility burned across the run (the bench's contest_spend)."""
        return sum(v["burned"] for v in self.spend_summary().values())

    def total_staked(self) -> int:
        return sum(v["staked"] for v in self.spend_summary().values())
