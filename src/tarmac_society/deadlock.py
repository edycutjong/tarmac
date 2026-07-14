"""Mechanical deadlock detection.

Two triggers (formalized in docs/SPEC-CLAIMS.md):

1. **Wait-for cycle** — agent A's blocked claim waits on resource holders;
   if the directed wait-for graph contains a cycle, everyone in it is stuck
   *by construction* and no amount of further talking resolves it.
2. **Contested streak** — a resource that stays contested (blocked claims,
   or an open challenge against a granted claim) for >= ``contested_rounds``
   consecutive rounds.

Detection is purely mechanical — no LLM involvement — which is what makes
mediation *triggerable* rather than vibes-based.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .ledger import ClaimLedger
from .schemas import ClaimStatus, Deadlock

__all__ = ["DeadlockDetector", "find_cycles"]


def find_cycles(edges: Mapping[str, Iterable[str]]) -> list[list[str]]:
    """Strongly connected components of size >= 2, plus self-loops.

    Iterative Tarjan; deterministic output (nodes visited in sorted order,
    each SCC returned sorted). Returns a list of node groups, each of which
    is a genuine cycle set in the wait-for graph.
    """
    graph: dict[str, list[str]] = {n: sorted(set(edges.get(n, ()))) for n in sorted(edges)}
    for targets in list(graph.values()):
        for t in targets:
            graph.setdefault(t, [])

    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    counter = 0
    sccs: list[list[str]] = []

    for root in sorted(graph):
        if root in index_of:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node, child_i = work[-1]
            if child_i == 0:
                index_of[node] = lowlink[node] = counter
                counter += 1
                stack.append(node)
                on_stack[node] = True
            advanced = False
            children = graph[node]
            while child_i < len(children):
                child = children[child_i]
                child_i += 1
                if child not in index_of:
                    work[-1] = (node, child_i)
                    work.append((child, 0))
                    advanced = True
                    break
                if on_stack.get(child):
                    lowlink[node] = min(lowlink[node], index_of[child])
            if advanced:
                continue
            work[-1] = (node, len(children))
            if lowlink[node] == index_of[node]:
                comp = []
                while True:
                    n = stack.pop()
                    on_stack[n] = False
                    comp.append(n)
                    if n == node:
                        break
                if len(comp) >= 2 or node in graph[node]:
                    sccs.append(sorted(comp))
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
    return sorted(sccs)


@dataclass
class DeadlockDetector:
    """Stateful scanner run once per round after positions are filed."""

    contested_rounds: int = 2
    _streaks: dict[str, int] = field(default_factory=dict)
    _fired: set[str] = field(default_factory=set)
    _seq: int = 0

    def _next_id(self) -> str:
        self._seq += 1
        return f"d-{self._seq:02d}"

    def reset_resource(self, resource: str) -> None:
        """Called after a ruling touches ``resource`` — its streak restarts."""
        self._streaks.pop(resource, None)

    def scan(
        self,
        ledger: ClaimLedger,
        open_contests: list[dict[str, Any]],
        round_: int,
    ) -> list[Deadlock]:
        """Detect deadlocks for this round (cycles first, then streaks)."""
        blocked = ledger.blocked_claims()
        deadlocks: list[Deadlock] = []

        # ---- wait-for graph: blocked claimant -> holders of that resource
        edges: dict[str, set[str]] = {}
        involved: dict[str, dict[str, Any]] = {}
        for rec in blocked:
            res = rec.proposal.resource
            claimant = rec.proposal.agent
            holders = ledger.holder_agents(res)
            for holder, claim_ids in holders.items():
                if holder == claimant:
                    continue
                edges.setdefault(claimant, set()).add(holder)
                info = involved.setdefault(claimant, {"resources": set(), "claims": set()})
                info["resources"].add(res)
                info["claims"].add(rec.id)
                hinfo = involved.setdefault(holder, {"resources": set(), "claims": set()})
                hinfo["resources"].add(res)
                hinfo["claims"].update(claim_ids)

        cycle_resources: set[str] = set()
        for comp in find_cycles(edges):
            resources = sorted(
                {r for a in comp for r in involved.get(a, {}).get("resources", set())}
            )
            claims = sorted({c for a in comp for c in involved.get(a, {}).get("claims", set())})
            key = f"cycle:{','.join(comp)}:{','.join(claims)}"
            if key in self._fired:
                continue
            self._fired.add(key)
            cycle_resources.update(resources)
            deadlocks.append(
                Deadlock(
                    id=self._next_id(),
                    kind="cycle",
                    round=round_,
                    resources=resources,
                    agents=comp,
                    claims=claims,
                    detail=f"wait-for cycle among {', '.join(comp)}",
                )
            )

        # ---- contested streaks
        contested_now: dict[str, dict[str, set[str]]] = {}
        for rec in blocked:
            entry = contested_now.setdefault(
                rec.proposal.resource, {"agents": set(), "claims": set()}
            )
            entry["agents"].add(rec.proposal.agent)
            entry["claims"].add(rec.id)
            for holder, claim_ids in ledger.holder_agents(rec.proposal.resource).items():
                entry["agents"].add(holder)
                entry["claims"].update(claim_ids)
        for contest in open_contests:
            target = contest["target_claim"]
            try:
                target_rec = ledger.claim(target)
            except Exception:
                continue
            if target_rec.status != ClaimStatus.GRANTED:
                continue
            entry = contested_now.setdefault(
                target_rec.proposal.resource, {"agents": set(), "claims": set()}
            )
            entry["agents"].add(contest["agent"])
            entry["agents"].add(target_rec.proposal.agent)
            entry["claims"].add(target)

        for res in list(self._streaks):
            if res not in contested_now:
                self._streaks.pop(res)
        for res, entry in sorted(contested_now.items()):
            self._streaks[res] = self._streaks.get(res, 0) + 1
            if res in cycle_resources:
                continue  # already escalating via a cycle deadlock this round
            if self._streaks[res] >= self.contested_rounds:
                key = f"contested:{res}:{','.join(sorted(entry['claims']))}"
                if key in self._fired:
                    continue
                self._fired.add(key)
                deadlocks.append(
                    Deadlock(
                        id=self._next_id(),
                        kind="contested",
                        round=round_,
                        resources=[res],
                        agents=sorted(entry["agents"]),
                        claims=sorted(entry["claims"]),
                        detail=f"{res} contested for {self._streaks[res]} consecutive rounds",
                    )
                )
        return deadlocks
