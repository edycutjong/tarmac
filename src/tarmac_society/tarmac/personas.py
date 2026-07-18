"""The five role personas + Duty-Manager mediator.

Each persona has (a) a ``PersonaSpec`` with a system prompt for live Qwen
mode, and (b) a deterministic POLICY class — a rule-based implementation of
the persona's objective used by ``FakeQwen``. The policies are decision
functions over live ledger views (never canned transcripts): they claim,
collide, contest, concede and re-plan through exactly the same machinery
the LLM agents use.

Genuinely conflicting objectives (the deadlock is structural):

- **rebooking** fills seats by connection-risk throughput — wave 1 takes all
  nine QW441 seats and proposes the illegal ferry;
- **advocate** claims three QW441 seats for MED-02 (whose ONLY compliant
  flight is QW441), UM-07 and a wheelchair passenger — mathematically
  opposed to rebooking's plan;
- **crew_legality** vetoes the ferry with duty arithmetic;
- **gate_ground** claims gates and joins the ferry veto (no turnaround slot);
- **hotel** lodges the projected-stranded and releases rooms as pax get seats.
"""

from __future__ import annotations

from typing import Any

from ..mediator import AgentView, MediatorView
from ..qwen.transport import PersonaSpec
from ..schemas import ClaimProposal, ClaimRecord, Deadlock, Position, Ruling, RulingOp
from .scenario import ferry_required_duty, flight_by_id, pax_by_id, seat_resource

__all__ = [
    "PERSONAS",
    "AGENT_ORDER",
    "pax_priority",
    "build_policies",
    "build_private_views",
    "public_scenario",
    "DutyManagerPolicy",
]

AGENT_ORDER = ["rebooking", "crew_legality", "gate_ground", "hotel", "advocate"]

WAVE_1_QW519 = 15  # rebooking's dispatch-wave discipline


def pax_priority(p: dict[str, Any]) -> int:
    """0 = most protected. MED < UM < WCHR < TC < elite < general."""
    flags = p["flags"]
    if "MED" in flags:
        return 0
    if "UM" in flags:
        return 1
    if "WCHR" in flags:
        return 2
    if "TC" in flags:
        return 3
    if p["elite"]:
        return 4
    return 5


# --------------------------------------------------------------------------
# View helpers shared by policies (public info only unless noted)
# --------------------------------------------------------------------------
def _seated(view: AgentView) -> set[str]:
    return view.allocated_beneficiaries(group="seat")


def _my_pending_beneficiaries(view: AgentView) -> set[str]:
    """Pax already named by my blocked claims or held by my granted claims."""
    out: set[str] = set()
    for rec in view.my_blocked:
        out.update(rec.proposal.beneficiaries)
    for rec in view.my_granted:
        out.update(rec.holders)
    return out


def _my_claim_on(view: AgentView, resource: str) -> ClaimRecord | None:
    for rec in view.my_blocked + view.my_granted:
        if rec.proposal.resource == resource:
            return rec
    return None

def _my_blocked_on(view: AgentView, resource: str) -> ClaimRecord | None:
    for rec in view.my_blocked:
        if rec.proposal.resource == resource:
            return rec
    return None


def _rival_granted_on(view: AgentView, resource: str, me: str) -> list[ClaimRecord]:
    rivals = [
        rec
        for rec in view.granted_claims
        if rec.proposal.resource == resource and rec.proposal.agent != me and rec.holders
    ]
    rivals.sort(key=lambda r: (-len(r.holders), r.id))
    return rivals


def _i_contest(view: AgentView, me: str, target_claim: str) -> bool:
    return any(
        c["agent"] == me and c["target_claim"] == target_claim for c in view.open_contests
    )


def _roster(view: AgentView) -> list[dict[str, Any]]:
    return view.scenario["pax"]


# --------------------------------------------------------------------------
# Rebooking — throughput: fill every seat, highest connection-risk first
# --------------------------------------------------------------------------
class RebookingPolicy:
    name = "rebooking"

    def _candidates(
        self, view: AgentView, flight_id: str, taken: set[str] | None = None
    ) -> list[str]:
        risk = view.private["connection_risk"]
        seated = _seated(view)
        reserved = _my_pending_beneficiaries(view)
        taken = taken or set()
        cands = [
            p["id"]
            for p in _roster(view)
            if flight_id in p["legal_flights"]
            and p["id"] not in seated
            and p["id"] not in reserved
            and p["id"] not in taken
        ]
        cands.sort(key=lambda pid: (-risk[pid], pid))
        return cands

    def _claim(
        self,
        view: AgentView,
        flight_id: str,
        qty: int,
        basis: str,
        taken: set[str] | None = None,
    ) -> ClaimProposal | None:
        if qty <= 0:
            return None
        cands = self._candidates(view, flight_id, taken)[:qty]
        if not cands:
            return None
        if taken is not None:
            taken.update(cands)  # never seat the same pax on two flights in one round
        return ClaimProposal(
            agent=self.name,
            resource=seat_resource(flight_id),
            qty=len(cands),
            beneficiaries=cands,
            basis=basis,
        )

    def propose(self, view: AgentView) -> list[ClaimProposal]:
        proposals: list[ClaimProposal] = []
        taken: set[str] = set()  # pax already placed by an earlier claim THIS round
        if view.round == 1:
            # The "obvious" throughput move: crew an extra ferry section.
            crew_id = view.scenario["crews"][0]["id"]
            proposals.append(
                ClaimProposal(
                    agent=self.name,
                    resource="crew:FERRY-1",
                    qty=1,
                    beneficiaries=[crew_id],
                    basis="crew the FERRY-1 extra section: +50 seats of throughput",
                )
            )
            c = self._claim(
                view, "QW441", view.free(seat_resource("QW441")),
                "earliest DEN arrival for highest connection-risk pax", taken,
            )
            if c:
                proposals.append(c)
            c = self._claim(
                view, "QW519", min(WAVE_1_QW519, view.free(seat_resource("QW519"))),
                "wave 1: protect remaining connection-risk on QW519", taken,
            )
            if c:
                proposals.append(c)
            return proposals

        if view.round == 2:
            res519 = seat_resource("QW519")
            if _my_blocked_on(view, res519) is None:
                rivals_held = sum(len(r.holders) for r in _rival_granted_on(view, res519, self.name))
                target = view.free(res519) + rivals_held
                c = self._claim(
                    view, "QW519", target,
                    "QW519 should carry connection-risk pax; WCHR can be assisted on QW602", taken,
                )
                if c:
                    proposals.append(c)
            c = self._claim(view, "QW338", view.free(seat_resource("QW338")),
                            "fill the ORD connection bank", taken)
            if c:
                proposals.append(c)
            return proposals

        # round >= 3: fill everything that is still open
        for fid in ("QW519", "QW602", "QW338", "QW258", "QW777"):
            res = seat_resource(fid)
            if _my_blocked_on(view, res) is not None:
                continue  # pending claim; wait for adjudication
            c = self._claim(view, fid, view.free(res), "fill remaining seats by connection risk", taken)
            if c:
                proposals.append(c)
        return proposals

    def respond(self, view: AgentView) -> list[Position]:
        positions: list[Position] = []
        risk = view.private["connection_risk"]
        # Contest the advocate's WCHR hold on QW519 when my remainder wave is blocked.
        res519 = seat_resource("QW519")
        blocked_519 = _my_blocked_on(view, res519)
        if blocked_519 is not None:
            for rival in _rival_granted_on(view, res519, self.name):
                if _i_contest(view, self.name, rival.id):
                    continue
                mine441 = _my_claim_on(view, seat_resource("QW441"))
                bumpable = (
                    sorted(mine441.holders, key=lambda pid: (risk[pid], pid))[:3]
                    if mine441 and mine441.holders
                    else []
                )
                concession = (
                    "if granted the QW519 remainder I can bump my three lowest-connection-risk "
                    f"QW441 holders ({', '.join(bumpable)}) to QW519"
                    if bumpable
                    else None
                )
                positions.append(
                    Position(
                        agent=self.name,
                        stance="block",
                        target_claim=rival.id,
                        argument=(
                            f"QW519 is the last connection-protecting arrival; claim {rival.id} "
                            "parks non-connection pax on it while my blocked remainder wave "
                            f"({blocked_519.id}) strands ticketed connections"
                        ),
                        citations=["conx-policy.7", "fareclass-policy.1"],
                        concession=concession,
                    )
                )
                break
        # Defend my own claims that are under challenge.
        for contest in view.open_contests:
            target = contest["target_claim"]
            rec = next((r for r in view.my_granted if r.id == target), None)
            if rec is None:
                continue
            mine441 = rec if rec.proposal.resource == seat_resource("QW441") else None
            concession = None
            if mine441:
                bumpable = sorted(mine441.holders, key=lambda pid: (risk[pid], pid))[:3]
                concession = (
                    f"can concede {', '.join(bumpable)} to QW519 if the remainder wave is granted"
                )
            positions.append(
                Position(
                    agent=self.name,
                    stance="support",
                    target_claim=target,
                    argument=(
                        f"claim {target} maximizes protected connections per seat; "
                        "revoking it strands more pax overnight than it saves"
                    ),
                    citations=["conx-policy.7"],
                    concession=concession,
                )
            )
        return positions


# --------------------------------------------------------------------------
# Advocate — protected categories first (DOT 259.4)
# --------------------------------------------------------------------------
class AdvocatePolicy:
    name = "advocate"

    def propose(self, view: AgentView) -> list[ClaimProposal]:
        needs: dict[str, dict[str, Any]] = view.private["special_needs"]
        seated = _seated(view)
        proposals: list[ClaimProposal] = []

        if view.round == 1:
            wchr = sorted(pid for pid, n in needs.items() if n["kind"] == "WCHR")
            first_wave = [
                pid for pid, n in needs.items() if n["kind"] in ("MED", "UM")
            ]
            first_wave.sort(key=lambda pid: needs[pid]["kind"] != "MED")  # MED first
            first_wave.append(wchr[0])
            proposals.append(
                ClaimProposal(
                    agent=self.name,
                    resource=seat_resource("QW441"),
                    qty=len(first_wave),
                    beneficiaries=first_wave,
                    basis="DOT 259.4 protected categories on the only compliant arrival",
                )
            )
            proposals.append(
                ClaimProposal(
                    agent=self.name,
                    resource=seat_resource("QW519"),
                    qty=len(wchr) - 1,
                    beneficiaries=wchr[1:],
                    basis="same-day nonstop WCHR service (WCHR-1)",
                )
            )
            return proposals

        # Fallback waves: a special pax whose claim has been blocked for >=2
        # full rounds gets re-routed to the next SLA-compliant flight.
        for pid in sorted(needs):
            if pid in seated:
                continue
            pending = None
            for rec in view.my_blocked:
                if pid in rec.proposal.beneficiaries:
                    pending = rec
                    break
            if pending is not None and (view.round - pending.round_committed) < 2:
                continue
            for fid in needs[pid]["sla_flights"]:
                res = seat_resource(fid)
                if pending is not None and pending.proposal.resource == res:
                    continue  # already fighting for exactly this flight
                if view.free(res) >= 1:
                    proposals.append(
                        ClaimProposal(
                            agent=self.name,
                            resource=res,
                            qty=1,
                            beneficiaries=[pid],
                            basis=f"fallback SLA re-accommodation for {pid}",
                        )
                    )
                    break
        return proposals

    def respond(self, view: AgentView) -> list[Position]:
        needs = view.private["special_needs"]
        positions: list[Position] = []
        res441 = seat_resource("QW441")
        blocked441 = _my_blocked_on(view, res441)
        if blocked441 is not None:
            for rival in _rival_granted_on(view, res441, self.name):
                if _i_contest(view, self.name, rival.id):
                    continue
                detail = ", ".join(
                    f"{pid} ({needs[pid]['kind']})" for pid in blocked441.proposal.beneficiaries
                )
                positions.append(
                    Position(
                        agent=self.name,
                        stance="block",
                        target_claim=rival.id,
                        argument=(
                            f"claim {rival.id} fills QW441 with general re-booking while "
                            f"{detail} hold DOT 259.4 priority; QW441 is MED-02's only "
                            "deadline-compliant arrival and UM-07's earliest pre-curfew nonstop"
                        ),
                        citations=["dot-259.4", "med-policy.2", "um-policy.4"],
                        concession=(
                            "WCHR-2/WCHR-3 stay on QW519; I need exactly three QW441 seats"
                        ),
                    )
                )
                break
        return positions


# --------------------------------------------------------------------------
# Crew legality — duty clocks are physics
# --------------------------------------------------------------------------
class CrewLegalityPolicy:
    name = "crew_legality"

    def propose(self, view: AgentView) -> list[ClaimProposal]:
        return []

    def respond(self, view: AgentView) -> list[Position]:
        duty: dict[str, int] = view.private["duty"]
        required = view.private["ferry_required_min"]
        positions: list[Position] = []
        for rec in view.granted_claims:
            if not rec.proposal.resource.startswith("crew:"):
                continue
            if rec.proposal.agent == self.name:
                continue
            crew_id = rec.proposal.beneficiaries[0]
            remaining = duty.get(crew_id)
            if remaining is None or remaining >= required:
                continue
            if _i_contest(view, self.name, rec.id):
                continue
            positions.append(
                Position(
                    agent=self.name,
                    stance="block",
                    target_claim=rec.id,
                    argument=(
                        f"{crew_id} has {remaining} duty minutes remaining; "
                        f"{rec.proposal.resource.split(':', 1)[1]} requires {required} "
                        "(block + 45 brief). FAR 117.11 admits no commercial extension"
                    ),
                    citations=["far117.11", "duty_table.B"],
                    concession=None,
                )
            )
        # Lend weight to the advocate's protected-category claim while it is contested.
        adv_blocked = [
            r for r in view.blocked_claims
            if r.proposal.agent == "advocate" and r.proposal.resource == seat_resource("QW441")
        ]
        if adv_blocked and any(c["agent"] == "advocate" for c in view.open_contests):
            positions.append(
                Position(
                    agent=self.name,
                    stance="support",
                    target_claim=adv_blocked[0].id,
                    argument="protected-category priority is regulatory, not commercial",
                    citations=["dot-259.4"],
                )
            )
        return positions


# --------------------------------------------------------------------------
# Gate/Ground — turnaround feasibility
# --------------------------------------------------------------------------
class GateGroundPolicy:
    name = "gate_ground"

    def propose(self, view: AgentView) -> list[ClaimProposal]:
        gates: list[str] = view.private["gates"]
        flights = sorted(view.scenario["flights"], key=lambda f: (f["dep_min"], f["id"]))
        already = {
            rec.proposal.beneficiaries[0]
            for rec in view.my_granted + view.my_blocked
            if rec.proposal.resource.startswith("gate:")
        }
        proposals = []
        for gate, flight in zip(gates, flights):
            if flight["id"] in already:
                continue
            proposals.append(
                ClaimProposal(
                    agent=self.name,
                    resource=f"gate:{gate}",
                    qty=1,
                    beneficiaries=[flight["id"]],
                    basis=f"gate assignment for {flight['id']} departure",
                )
            )
        return proposals

    def respond(self, view: AgentView) -> list[Position]:
        positions = []
        for rec in view.granted_claims:
            if not rec.proposal.resource.startswith("crew:"):
                continue
            if rec.proposal.agent == self.name:
                continue
            if _i_contest(view, self.name, rec.id):
                continue
            positions.append(
                Position(
                    agent=self.name,
                    stance="block",
                    target_claim=rec.id,
                    argument=(
                        "all six gates are assigned to scheduled departures; an extra "
                        "section has no gate or 40-minute turnaround slot before its "
                        "proposed pushback"
                    ),
                    citations=["gate-ops.5"],
                    concession=None,
                )
            )
        return positions


# --------------------------------------------------------------------------
# Hotel — lodge the stranded, release rooms the moment pax get seats
# --------------------------------------------------------------------------
class HotelPolicy:
    name = "hotel"

    def propose(self, view: AgentView) -> list[ClaimProposal]:
        if view.round < 3:
            return []  # let the re-booking waves land first
        seated = _seated(view)
        roomed = view.allocated_beneficiaries(group="hotel")
        pax_map = {p["id"]: p for p in _roster(view)}
        candidates = [
            pid for pid in sorted(pax_map)
            if pid not in seated and pid not in roomed
        ]
        candidates.sort(key=lambda pid: (pax_priority(pax_map[pid]), pax_map[pid]["booking_order"]))
        qty = min(view.free("hotel:block"), len(candidates))
        if qty <= 0:
            return []
        return [
            ClaimProposal(
                agent=self.name,
                resource="hotel:block",
                qty=qty,
                beneficiaries=candidates[:qty],
                basis="distressed-passenger lodging, protected categories first",
            )
        ]

    def respond(self, view: AgentView) -> list[Position]:
        seated = _seated(view)
        positions = []
        for rec in view.my_granted:
            if rec.proposal.resource != "hotel:block":
                continue
            release = sorted(set(rec.holders) & seated)
            if not release:
                continue
            positions.append(
                Position(
                    agent=self.name,
                    stance="yield",
                    target_claim=rec.id,
                    argument=f"{len(release)} pax confirmed on departures; rooms returned to block",
                    citations=["hotel-policy.3"],
                    release=release,
                )
            )
        return positions


# --------------------------------------------------------------------------
# Duty-Manager mediator policy (offline stand-in for qwen3.7-max + thinking)
# --------------------------------------------------------------------------
class DutyManagerPolicy:
    """Deterministic adjudication: hard constraints, then protected priority."""

    BASE_CITATIONS = ["dot-259.4"]

    def _pax_map(self, view: MediatorView) -> dict[str, dict[str, Any]]:
        return pax_by_id(view.private)  # mediator holds the full scenario

    # ---------------------------------------------------------------- rule
    def rule(self, deadlock: Deadlock, positions: list[Position], view: MediatorView) -> Ruling:
        if any(res.startswith("crew:") for res in deadlock.resources):
            return self._rule_crew(deadlock, view)
        return self._rule_seats(deadlock, view)

    def _rule_crew(self, deadlock: Deadlock, view: MediatorView) -> Ruling:
        full = view.private
        required = ferry_required_duty(full)
        duty = {c["id"]: c["duty_remaining_min"] for c in full["crews"]}
        ops: list[RulingOp] = []
        winners: set[str] = set()
        losers: set[str] = set()
        details: list[str] = []
        for rec in view.granted_claims:
            if rec.id not in deadlock.claims or not rec.proposal.resource.startswith("crew:"):
                continue
            crew_id = rec.proposal.beneficiaries[0]
            remaining = duty.get(crew_id, 0)
            if remaining < required:
                ops.append(RulingOp(op="void", claim_id=rec.id))
                losers.add(rec.proposal.agent)
                details.append(
                    f"{crew_id}: {remaining} min remaining < {required} required — "
                    f"claim {rec.id} void"
                )
                for contest in view.open_contests:
                    if contest["target_claim"] == rec.id:
                        winners.add(contest["agent"])
            else:  # pragma: no cover - the storm never produces a legal ferry
                winners.add(rec.proposal.agent)
                details.append(f"{crew_id} legal for the sector; challenge dismissed")
                for contest in view.open_contests:
                    if contest["target_claim"] == rec.id:
                        losers.add(contest["agent"])
        return Ruling(
            deadlock_id=deadlock.id,
            decision="the extra section may not operate: " + "; ".join(details),
            rationale=(
                "Duty arithmetic is a hard constraint (Table B: block + 45 brief vs "
                "remaining duty). No commercial-necessity extension exists under FAR "
                "117.11; the throughput argument cannot outrank legality."
            ),
            citations=["far117.11", "duty_table.B"],
            ops=ops,
            winners=sorted(winners),
            losers=sorted(losers),
        )

    def _rule_seats(self, deadlock: Deadlock, view: MediatorView) -> Ruling:
        pax_map = self._pax_map(view)
        prio = lambda pid: pax_priority(pax_map[pid])  # noqa: E731

        blocked = [
            rec for rec in view.blocked_claims
            if rec.id in deadlock.claims and rec.proposal.resource.startswith("seat:")
        ]
        blocked.sort(key=lambda r: (min(prio(b) for b in r.proposal.beneficiaries), r.id))

        working_free = {
            res: info["free"] for res, info in view.resources.items() if res.startswith("seat:")
        }
        reserved: dict[str, int] = {}
        ops: list[RulingOp] = []
        winners: set[str] = set()
        losers: set[str] = set()
        details: list[str] = []
        touched_full: set[str] = set()  # claims fully revoked/voided

        def usable(res: str) -> int:
            return working_free.get(res, 0) - reserved.get(res, 0)

        def alternative_for(pid: str, exclude_res: str) -> str | None:
            for fid in pax_map[pid]["legal_flights"]:
                res = seat_resource(fid)
                if res != exclude_res and usable(res) >= 1:
                    return res
            return None

        for bc in blocked:
            res = bc.proposal.resource
            need = bc.proposal.qty
            best_prio = min(prio(b) for b in bc.proposal.beneficiaries)
            if usable(res) < need:
                shortfall = need - usable(res)
                candidates: list[tuple[int, str, str, str]] = []  # (prio, pid, claim, alt)
                for gc in view.granted_claims:
                    if (
                        gc.proposal.resource != res
                        or gc.proposal.agent == bc.proposal.agent
                        or not gc.proposal.revocable
                    ):
                        continue
                    for pid in sorted(gc.holders, reverse=True):
                        p_b = prio(pid)
                        if p_b <= best_prio:
                            continue  # never bump equal-or-more protected pax
                        alt = alternative_for(pid, res)
                        if alt is None:
                            continue
                        candidates.append((p_b, pid, gc.id, alt))
                # least protected first; stable id-desc within equal priority
                candidates.sort(key=lambda t: -t[0])
                victims = candidates[:shortfall]
                by_claim: dict[str, list[str]] = {}
                for p_b, pid, gcid, alt in victims:
                    by_claim.setdefault(gcid, []).append(pid)
                    working_free[res] = working_free.get(res, 0) + 1
                    reserved[alt] = reserved.get(alt, 0) + 1
                    details.append(f"bump {pid} from {res.split(':')[1]} (re-seat via {alt.split(':')[1]})")
                for gcid in sorted(by_claim):
                    victim_ids = sorted(by_claim[gcid])
                    ops.append(RulingOp(op="revoke", claim_id=gcid, beneficiaries=victim_ids))
                    owner = next(
                        g.proposal.agent for g in view.granted_claims if g.id == gcid
                    )
                    losers.add(owner)
            grant_n = min(need, usable(res))
            if grant_n <= 0:
                details.append(f"claim {bc.id} stays blocked: no lawful capacity on {res}")
                continue
            if grant_n == need:
                ops.append(RulingOp(op="grant", claim_id=bc.id))
            else:
                ops.append(
                    RulingOp(
                        op="grant",
                        claim_id=bc.id,
                        beneficiaries=bc.proposal.beneficiaries[:grant_n],
                    )
                )
                details.append(
                    f"claim {bc.id} granted {grant_n}/{need}; remainder must re-book elsewhere"
                )
            working_free[res] -= grant_n
            winners.add(bc.proposal.agent)

        for contest in view.open_contests:
            if contest["target_claim"] not in deadlock.claims:
                continue
            agent = contest["agent"]
            if agent in winners:
                continue
            if contest["target_claim"] in touched_full:
                # Unreachable today: touched_full stays empty because
                # _rule_seats only ever issues per-beneficiary revokes, never
                # a full revoke/void. Kept as a guard for future full-revoke
                # ops so their contesters are credited as winners.
                winners.add(agent)  # pragma: no cover
            else:
                losers.add(agent)

        citations = set(self.BASE_CITATIONS)
        kind_cite = {"MED": "med-policy.2", "UM": "um-policy.4", "WCHR": "wchr-policy.1", "TC": "conx-policy.7"}
        for bc in blocked:
            for pid in bc.proposal.beneficiaries:
                for flag in pax_map[pid]["flags"]:
                    if flag in kind_cite:
                        citations.add(kind_cite[flag])
        decision = "; ".join(details) if details else "no reallocation is lawful; positions dismissed"
        return Ruling(
            deadlock_id=deadlock.id,
            decision=decision,
            rationale=(
                "Protected categories (DOT 259.4) outrank fare-class and throughput "
                "ordering; bumped passengers were selected as the least protected "
                "holders with a lawful re-seat alternative, so no connection is "
                "sacrificed that a later flight can still protect."
            ),
            citations=sorted(citations),
            ops=ops,
            winners=sorted(winners),
            losers=sorted(losers),
        )

    # ---------------------------------------------------------------- fiat
    def fiat(self, view: MediatorView) -> Ruling:
        pax_map = self._pax_map(view)
        full = view.private
        required = ferry_required_duty(full)
        duty = {c["id"]: c["duty_remaining_min"] for c in full["crews"]}
        prio = lambda pid: pax_priority(pax_map[pid]) if pid in pax_map else 9  # noqa: E731

        ops: list[RulingOp] = []
        winners: set[str] = set()
        losers: set[str] = set()
        details: list[str] = []

        # 1. adjudicate every open challenge against crew claims (hard law)
        for contest in view.open_contests:
            target = contest["target_claim"]
            rec = next((r for r in view.granted_claims if r.id == target), None)
            if rec is None or not rec.proposal.resource.startswith("crew:"):
                continue
            crew_id = rec.proposal.beneficiaries[0]
            if duty.get(crew_id, 0) < required:
                if not any(op.claim_id == target for op in ops):
                    ops.append(RulingOp(op="void", claim_id=target))
                    losers.add(rec.proposal.agent)
                    details.append(f"{target} void: {crew_id} duty-illegal")
                winners.add(contest["agent"])
            else:  # pragma: no cover
                losers.add(contest["agent"])

        # 2. grant blocked seat claims by protection priority while capacity lasts
        working_free = {
            res: info["free"] for res, info in view.resources.items()
        }
        blocked = sorted(
            view.blocked_claims,
            key=lambda r: (min(prio(b) for b in r.proposal.beneficiaries), r.id),
        )
        for bc in blocked:
            res = bc.proposal.resource
            grant_n = min(bc.proposal.qty, working_free.get(res, 0))
            if grant_n <= 0:
                ops.append(RulingOp(op="void", claim_id=bc.id))
                details.append(f"{bc.id} void at cap: no capacity on {res}")
                continue
            if grant_n == bc.proposal.qty:
                ops.append(RulingOp(op="grant", claim_id=bc.id))
            else:
                ops.append(
                    RulingOp(op="grant", claim_id=bc.id,
                             beneficiaries=bc.proposal.beneficiaries[:grant_n])
                )
            working_free[res] -= grant_n
            winners.add(bc.proposal.agent)
            details.append(f"{bc.id} granted {grant_n}/{bc.proposal.qty} by fiat")

        return Ruling(
            deadlock_id="d-fiat",
            decision="round-cap fiat: " + ("; ".join(details) if details else "nothing left to resolve"),
            rationale=(
                "The society reached its round cap. Remaining capacity is assigned "
                "strictly by protection priority; duty-illegal assignments are void."
            ),
            citations=["dot-259.4", "far117.11"],
            ops=ops,
            winners=sorted(winners),
            losers=sorted(losers),
        )


# --------------------------------------------------------------------------
# Persona specs (live-mode prompts) + wiring builders
# --------------------------------------------------------------------------
_COMMON_RULES = (
    " You negotiate by emitting typed claims against a shared seat ledger and "
    "typed position papers on contested claims. Claims are sealed (committed) "
    "before reveal. A blocking position costs credibility and MUST cite at least "
    "one regulation/policy id. Work only from the state you are shown."
)

PERSONAS: dict[str, PersonaSpec] = {
    "rebooking": PersonaSpec(
        name="rebooking",
        display="Rebooking",
        objective="minimize total passenger delay; fill every recoverable seat",
        system_prompt=(
            "You are the Rebooking agent for QW airlines during a storm IRROPS event. "
            "Your private objective: maximize seats filled, protecting the highest "
            "connection-risk passengers first, using your private connection-risk table."
            + _COMMON_RULES
        ),
    ),
    "crew_legality": PersonaSpec(
        name="crew_legality",
        display="Crew Legality",
        objective="zero duty-time violations, whatever it costs throughput",
        system_prompt=(
            "You are the Crew-Legality agent. Your private objective: no crew may be "
            "assigned beyond its remaining duty minutes (you hold the private duty "
            "clocks). Veto any illegal assignment with the arithmetic, citing far117.11 "
            "and duty_table.B." + _COMMON_RULES
        ),
    ),
    "gate_ground": PersonaSpec(
        name="gate_ground",
        display="Gate & Ground",
        objective="every departure has a gate and a 40-minute turnaround",
        system_prompt=(
            "You are the Gate/Ground agent. Your private objective: assign the six "
            "gates to scheduled departures and block any extra section without a "
            "turnaround slot, citing gate-ops.5." + _COMMON_RULES
        ),
    ),
    "hotel": PersonaSpec(
        name="hotel",
        display="Hotel Desk",
        objective="lodge every stranded passenger within the 60-room block",
        system_prompt=(
            "You are the Hotel agent. Your private objective: claim rooms for "
            "projected-stranded passengers (protected categories first) and release "
            "rooms the moment a passenger is confirmed on a departure, citing "
            "hotel-policy.3." + _COMMON_RULES
        ),
    ),
    "advocate": PersonaSpec(
        name="advocate",
        display="Passenger Advocate",
        objective="protected passengers fly first: MED, UM, WCHR",
        system_prompt=(
            "You are the Passenger-Advocate agent. Your private objective: the medical "
            "courier, unaccompanied minor and wheelchair passengers meet their SLAs "
            "even at the cost of general throughput, citing dot-259.4, med-policy.2, "
            "um-policy.4 and wchr-policy.1." + _COMMON_RULES
        ),
    ),
}


def build_policies(scenario: dict[str, Any]) -> dict[str, Any]:
    """FakeQwen persona-policy table for a run (policies are stateless)."""
    return {
        "rebooking": RebookingPolicy(),
        "crew_legality": CrewLegalityPolicy(),
        "gate_ground": GateGroundPolicy(),
        "hotel": HotelPolicy(),
        "advocate": AdvocatePolicy(),
    }


def _sla_flights(scenario: dict[str, Any], p: dict[str, Any]) -> list[str]:
    fmap = flight_by_id(scenario)
    flags = p["flags"]
    out = []
    for fid in p["legal_flights"]:
        f = fmap[fid]
        if "MED" in flags and f["arr_min"] <= p["med_deadline_min"]:
            out.append(fid)
        elif "UM" in flags and f["nonstop"] and f["dep_min"] <= scenario["um_curfew_dep_min"]:
            out.append(fid)
        elif "WCHR" in flags and f["nonstop"] and f["arr_min"] <= scenario["wchr_arr_limit_min"]:
            out.append(fid)
    return sorted(out, key=lambda fid: fmap[fid]["arr_min"])


def build_private_views(scenario: dict[str, Any]) -> dict[str, dict[str, Any]]:
    special = {
        p["id"]: {
            "kind": next(f for f in p["flags"] if f in ("MED", "UM", "WCHR")),
            "sla_flights": _sla_flights(scenario, p),
            "med_deadline_min": p["med_deadline_min"],
        }
        for p in scenario["pax"]
        if any(f in ("MED", "UM", "WCHR") for f in p["flags"])
    }
    return {
        "rebooking": {
            "connection_risk": dict(scenario["connection_risk"]),
            "flexible_pax": sorted(p["id"] for p in scenario["pax"] if p["flexible"]),
        },
        "crew_legality": {
            "duty": {c["id"]: c["duty_remaining_min"] for c in scenario["crews"]},
            "ferry_required_min": ferry_required_duty(scenario),
        },
        "gate_ground": {"gates": list(scenario["gates"]), "turnaround_min": 40},
        "hotel": {"block": scenario["hotel_block"], "nightly_rate": 89},
        "advocate": {
            "special_needs": special,
            "um_curfew_dep_min": scenario["um_curfew_dep_min"],
            "wchr_arr_limit_min": scenario["wchr_arr_limit_min"],
        },
        "__mediator__": scenario,  # the Duty Manager holds ground truth
    }


def public_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    """The shared prefix every agent sees (private tables stripped)."""
    pub = {
        "name": scenario["name"],
        "seed": scenario["seed"],
        "airport": scenario["airport"],
        "disruption": scenario["disruption"],
        "flights": scenario["flights"],
        "ferry": {k: v for k, v in scenario["ferry"].items()},
        "crews": [{"id": c["id"]} for c in scenario["crews"]],  # clocks are private
        "gates": scenario["gates"],
        "hotel_block": scenario["hotel_block"],
        "pax": [
            {
                "id": p["id"],
                "name": p["name"],
                "flags": p["flags"],
                "fare_class": p["fare_class"],
                "elite": p["elite"],
                "booking_order": p["booking_order"],
                "flexible": p["flexible"],
                "legal_flights": p["legal_flights"],
            }
            for p in scenario["pax"]
        ],
    }
    return pub
