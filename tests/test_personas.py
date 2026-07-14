"""Persona wiring: priority order, policy table, private/public view split."""

from __future__ import annotations

from tarmac_society.mediator import AgentView, MediatorView
from tarmac_society.schemas import ClaimProposal, ClaimRecord, ClaimStatus, Deadlock
from tarmac_society.tarmac.personas import (
    AGENT_ORDER,
    PERSONAS,
    CrewLegalityPolicy,
    DutyManagerPolicy,
    GateGroundPolicy,
    HotelPolicy,
    _my_claim_on,
    build_policies,
    build_private_views,
    pax_priority,
    public_scenario,
)


def _pax(flags, elite=False):
    return {"flags": flags, "elite": elite}


def test_pax_priority_ordering():
    assert pax_priority(_pax(["MED"])) < pax_priority(_pax(["UM"]))
    assert pax_priority(_pax(["UM"])) < pax_priority(_pax(["WCHR"]))
    assert pax_priority(_pax(["WCHR"])) < pax_priority(_pax(["TC"]))
    assert pax_priority(_pax(["TC"])) < pax_priority(_pax([], elite=True))
    assert pax_priority(_pax([], elite=True)) < pax_priority(_pax([]))


def test_agent_order_has_five_personas():
    assert len(AGENT_ORDER) == 5
    assert set(AGENT_ORDER) == set(PERSONAS)


def test_personas_have_system_prompts():
    for name, spec in PERSONAS.items():
        assert spec.name == name
        assert spec.system_prompt and spec.objective


def test_build_policies_returns_five(scenario):
    policies = build_policies(scenario)
    assert set(policies) == set(AGENT_ORDER)
    for name, pol in policies.items():
        assert pol.name == name


def test_private_views_keys(scenario):
    views = build_private_views(scenario)
    assert "__mediator__" in views
    assert set(views["advocate"]["special_needs"]) == set(
        p["id"] for p in scenario["pax"] if any(f in ("MED", "UM", "WCHR") for f in p["flags"])
    )
    assert len(views["crew_legality"]["duty"]) == 4
    assert "connection_risk" in views["rebooking"]


def test_mediator_holds_full_ground_truth(scenario):
    views = build_private_views(scenario)
    assert views["__mediator__"]["connection_risk"]  # full scenario present


def test_public_scenario_strips_private_tables(scenario):
    pub = public_scenario(scenario)
    assert "connection_risk" not in pub
    # crew duty clocks are private -> public crews carry only ids
    assert all(set(c) == {"id"} for c in pub["crews"])
    # passengers keep public labels but not the risk table
    assert all("legal_flights" in p for p in pub["pax"])


def test_public_scenario_pax_count(scenario):
    assert len(public_scenario(scenario)["pax"]) == 180


# --------------------------------------------------------------------------
# Direct view construction for branches the storm_dfw fixture never exercises
# --------------------------------------------------------------------------
def _rec(claim_id, agent, resource, beneficiaries, status, revocable=True):
    return ClaimRecord(
        id=claim_id,
        proposal=ClaimProposal(
            agent=agent, resource=resource, qty=len(beneficiaries),
            beneficiaries=beneficiaries, basis="test", revocable=revocable,
        ),
        status=status,
        round_committed=1,
        holders=list(beneficiaries) if status == ClaimStatus.GRANTED else [],
    )


def _agent_view(agent="x", granted_claims=None, blocked_claims=None, my_granted=None,
                 my_blocked=None, open_contests=None, resources=None, private=None):
    granted_claims = granted_claims or []
    blocked_claims = blocked_claims or []
    return AgentView(
        round=1,
        agent=agent,
        resources=resources or {},
        granted_claims=granted_claims,
        blocked_claims=blocked_claims,
        my_granted=my_granted if my_granted is not None else
            [c for c in granted_claims if c.proposal.agent == agent],
        my_blocked=my_blocked if my_blocked is not None else
            [c for c in blocked_claims if c.proposal.agent == agent],
        open_contests=open_contests or [],
        rulings=[],
        balances={},
        scenario={"pax": []},
        private=private or {},
    )


def _mediator_view(private, resources, blocked_claims=None, granted_claims=None,
                    open_contests=None):
    return MediatorView(
        round=1,
        agent="__mediator__",
        resources=resources,
        granted_claims=granted_claims or [],
        blocked_claims=blocked_claims or [],
        my_granted=[],
        my_blocked=[],
        open_contests=open_contests or [],
        rulings=[],
        balances={},
        scenario={"pax": []},
        private=private,
        positions=[],
        deadlocks=[],
    )


def test_my_claim_on_returns_none_when_no_match():
    view = _agent_view(
        agent="x",
        granted_claims=[_rec("c-1", "x", "seat:OTHER", ["p"], ClaimStatus.GRANTED)],
    )
    assert _my_claim_on(view, "seat:TARGET") is None


def test_crew_legality_skips_own_and_legal_and_unknown_duty_claims():
    """CrewLegalityPolicy never proposes crew: claims itself and only blocks
    claims it can prove illegal; these are defensive branches the engineered
    storm (always duty-illegal) never triggers naturally."""
    granted = [
        _rec("c-own", "crew_legality", "crew:X", ["CREW-A"], ClaimStatus.GRANTED),
        _rec("c-legal", "rebooking", "crew:Y", ["CREW-A"], ClaimStatus.GRANTED),
        _rec("c-unknown", "rebooking", "crew:Z", ["CREW-UNKNOWN"], ClaimStatus.GRANTED),
    ]
    view = _agent_view(
        agent="crew_legality",
        granted_claims=granted,
        private={"duty": {"CREW-A": 500}, "ferry_required_min": 215},
    )
    positions = CrewLegalityPolicy().respond(view)
    assert positions == []  # own claim skipped, CREW-A legal, CREW-UNKNOWN's duty unknown


def test_gate_ground_skips_own_crew_claim():
    """gate_ground never proposes crew: claims itself; defensive branch only."""
    view = _agent_view(
        agent="gate_ground",
        granted_claims=[_rec("c-1", "gate_ground", "crew:FERRY-1", ["CREW-A"], ClaimStatus.GRANTED)],
    )
    assert GateGroundPolicy().respond(view) == []


def test_hotel_skips_non_hotel_granted_claims():
    """hotel only ever proposes hotel:block claims; defensive branch only."""
    view = _agent_view(
        agent="hotel",
        my_granted=[_rec("c-1", "hotel", "seat:QW441", ["P1"], ClaimStatus.GRANTED)],
    )
    assert HotelPolicy().respond(view) == []


def test_rule_seats_skips_unbumpable_candidate_and_leaves_claim_blocked():
    """A bump candidate with no lawful alternative flight must be skipped
    (not sacrificed), leaving the blocked claim genuinely unresolved."""
    pax = [
        {"id": "MED-02", "flags": ["MED"], "elite": False},
        {"id": "GEN-1", "flags": [], "elite": False, "legal_flights": ["QW441"]},
    ]
    blocked = [_rec("c-blocked-1", "advocate", "seat:QW441", ["MED-02"], ClaimStatus.BLOCKED)]
    granted = [_rec("c-granted-1", "rebooking", "seat:QW441", ["GEN-1"], ClaimStatus.GRANTED)]
    view = _mediator_view(
        private={"pax": pax},
        resources={"seat:QW441": {"capacity": 1, "group": "seat", "free": 0}},
        blocked_claims=blocked,
        granted_claims=granted,
        open_contests=[{"agent": "advocate", "target_claim": "c-blocked-1",
                         "round_opened": 1, "cost": 10}],
    )
    dl = Deadlock(id="d-1", kind="contested", round=1, resources=["seat:QW441"],
                  agents=["advocate", "rebooking"], claims=["c-blocked-1", "c-granted-1"])
    ruling = DutyManagerPolicy()._rule_seats(dl, view)
    assert ruling.ops == []  # no lawful bump found -> nothing revoked or granted
    assert "stays blocked" in ruling.decision
    assert "advocate" in ruling.losers  # the unresolved challenger loses this round


def test_fiat_grants_available_seat_capacity_full_and_partial():
    """fiat()'s grant-blocked-claims branch: the storm scenario's round-1 cap
    never leaves free seat capacity behind, so this path needs a direct test."""
    pax = [{"id": f"P{i}", "flags": [], "elite": False} for i in range(1, 6)]
    blocked = [
        _rec("c-b1", "advocate", "seat:QW441", ["P1", "P2"], ClaimStatus.BLOCKED),
        _rec("c-b2", "rebooking", "seat:QW519", ["P3", "P4", "P5"], ClaimStatus.BLOCKED),
    ]
    view = _mediator_view(
        private={"pax": pax, "crews": [], "ferry": {"block_min": 170, "brief_min": 45}},
        resources={"seat:QW441": {"free": 3}, "seat:QW519": {"free": 1}},
        blocked_claims=blocked,
    )
    ruling = DutyManagerPolicy().fiat(view)
    grant_ops = {op.claim_id: op for op in ruling.ops if op.op == "grant"}
    assert grant_ops["c-b1"].beneficiaries is None  # full grant: 2 needed, 3 free
    assert grant_ops["c-b2"].beneficiaries == ["P3"]  # partial grant: 3 needed, 1 free
    assert {"advocate", "rebooking"} <= set(ruling.winners)
