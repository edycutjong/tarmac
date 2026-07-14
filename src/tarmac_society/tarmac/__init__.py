"""Tarmac — the airline-IRROPS scenario built on the tarmac-society protocol.

Everything airline-specific lives here: the seeded ``storm_dfw`` generator,
the ten synthetic FAR/DOT-style regulation passages, the five persona role
agents + Duty-Manager mediator (with deterministic offline policies), the
orchestrator that wires a scenario into a :class:`~tarmac_society.Society`,
the single-planner baseline, and the outcome metrics.
"""

from __future__ import annotations

from .baseline import PLANNER, commercial_key, run_single_planner
from .metrics import (
    SAME_DAY_ARR_CUTOFF,
    compute_metrics,
    crew_violations,
    flight_protects,
    seat_assignment,
    special_needs_sla,
)
from .personas import (
    AGENT_ORDER,
    PERSONAS,
    DutyManagerPolicy,
    build_policies,
    build_private_views,
    pax_priority,
    public_scenario,
)
from .regs import PASSAGES, build_reg_library
from .run import (
    CONDITIONS,
    DEFAULT_BUDGET,
    RunBundle,
    build_society,
    register_resources,
    run_society,
)
from .scenario import (
    SUPPLY_SHORTFALL,
    ferry_is_illegal_for_all_crews,
    ferry_required_duty,
    flight_by_id,
    pax_by_id,
    seat_resource,
    special_needs_ids,
    tc_ids,
    validate_scenario,
)
from .seed import (
    FIXTURE_BASENAME,
    SCENARIOS,
    generate,
    load_fixture,
    write_fixture,
)

__all__ = [
    # seed / scenario
    "generate",
    "write_fixture",
    "load_fixture",
    "SCENARIOS",
    "FIXTURE_BASENAME",
    "validate_scenario",
    "flight_by_id",
    "pax_by_id",
    "seat_resource",
    "special_needs_ids",
    "tc_ids",
    "ferry_required_duty",
    "ferry_is_illegal_for_all_crews",
    "SUPPLY_SHORTFALL",
    # personas / regs
    "PERSONAS",
    "AGENT_ORDER",
    "DutyManagerPolicy",
    "build_policies",
    "build_private_views",
    "public_scenario",
    "pax_priority",
    "PASSAGES",
    "build_reg_library",
    # orchestration
    "CONDITIONS",
    "DEFAULT_BUDGET",
    "RunBundle",
    "build_society",
    "run_society",
    "register_resources",
    # baseline
    "PLANNER",
    "commercial_key",
    "run_single_planner",
    # metrics
    "SAME_DAY_ARR_CUTOFF",
    "compute_metrics",
    "crew_violations",
    "flight_protects",
    "seat_assignment",
    "special_needs_sla",
]
