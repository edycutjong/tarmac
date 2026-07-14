"""Mediator interface + view types shared by agents and mediators.

A ``Mediator`` is only invoked when the deadlock detector fires; its
``Ruling`` is binding, must cite at least one source (I3), and is signed by
the orchestrator before it is applied. ``fiat`` is the round-cap safety
valve: if the society hits ``max_rounds`` without quiescing, the mediator
issues one final ruling resolving everything still contested.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from .schemas import ClaimProposal, ClaimRecord, Deadlock, Position, Ruling

__all__ = ["AgentView", "MediatorView", "Agent", "Mediator"]


@dataclass
class AgentView:
    """Everything one agent may see at decision time.

    ``scenario`` is the shared public prefix (identical for all agents —
    in live mode this is the context-cached prompt prefix); ``private`` is
    the persona's own information slice. Claims are public once revealed.
    """

    round: int
    agent: str
    resources: dict[str, dict[str, Any]]  # id -> {capacity, group, free}
    granted_claims: list[ClaimRecord]
    blocked_claims: list[ClaimRecord]
    my_granted: list[ClaimRecord]
    my_blocked: list[ClaimRecord]
    open_contests: list[dict[str, Any]]
    rulings: list[dict[str, Any]]
    balances: dict[str, int]
    scenario: dict[str, Any]
    private: dict[str, Any] = field(default_factory=dict)

    def free(self, resource: str) -> int:
        return self.resources.get(resource, {}).get("free", 0)

    def allocated_beneficiaries(self, group: str | None = None) -> set[str]:
        """Beneficiaries currently holding any granted allocation (optionally by group)."""
        out: set[str] = set()
        for rec in self.granted_claims:
            if group is not None:
                res_group = self.resources.get(rec.proposal.resource, {}).get("group")
                if res_group != group:
                    continue
            out.update(rec.holders)
        return out


@dataclass
class MediatorView(AgentView):
    """The mediator additionally sees the position papers per deadlock."""

    positions: list[Position] = field(default_factory=list)
    deadlocks: list[Deadlock] = field(default_factory=list)


class Agent(abc.ABC):
    """A society member: proposes claims, then takes positions on contests."""

    name: str

    @abc.abstractmethod
    def propose(self, view: AgentView) -> list[ClaimProposal]:
        """New claims to seal-and-commit this round (may be empty)."""

    @abc.abstractmethod
    def respond(self, view: AgentView) -> list[Position]:
        """Position papers: block (opens a paid contest), support, or yield."""


class Mediator(abc.ABC):
    """Adjudicates mechanically detected deadlocks with binding rulings."""

    name: str = "mediator"

    @abc.abstractmethod
    def rule(self, deadlock: Deadlock, positions: list[Position], view: MediatorView) -> Ruling:
        ...

    @abc.abstractmethod
    def fiat(self, view: MediatorView) -> Ruling:
        """Round-cap final resolution of everything still contested."""
