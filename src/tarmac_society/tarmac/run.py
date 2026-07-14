"""Wire a ``storm_dfw`` scenario into a runnable :class:`Society`.

This is the airline orchestrator: it registers the scenario's scarce
resources on a fresh ledger (seats, the ferry crew slot, gates, the hotel
block), builds the five persona role-agents + the Duty-Manager mediator on a
transport (``FakeQwen`` offline, ``LiveQwen`` behind ``DASHSCOPE_API_KEY``),
shares one SQLite backend across ledger/bank/chain-log so the whole run is a
single replayable ``run.db``, and runs the round loop.

Three ablation conditions are expressed here: ``society`` (5 agents +
mediator), ``society_minus_mediator`` (5 agents, no adjudication — contested
claims are quarantined at the round cap), and — via
:mod:`tarmac_society.tarmac.baseline` — ``single`` (one greedy planner).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from random import Random
from typing import Any

from ..chainlog import ChainLog
from ..currency import CredibilityBank
from ..deadlock import DeadlockDetector
from ..ledger import ClaimLedger
from ..qwen import RegLibrary
from ..qwen.transport import FakeQwen, QwenTransport, RoleAgent, TransportMediator
from ..signing import KeyPair, keypair_from_seed
from ..society import RunResult, Society
from ..storage import SQLiteStorage
from .personas import (
    AGENT_ORDER,
    PERSONAS,
    DutyManagerPolicy,
    build_policies,
    build_private_views,
    public_scenario,
)
from .regs import build_reg_library
from .scenario import seat_resource

__all__ = [
    "CONDITIONS",
    "DEFAULT_BUDGET",
    "RunBundle",
    "register_resources",
    "build_society",
    "run_society",
]

CONDITIONS = ("society", "society_minus_mediator", "single")
DEFAULT_BUDGET = 100
FERRY_CREW_RESOURCE = "crew:FERRY-1"
HOTEL_RESOURCE = "hotel:block"


@dataclass
class RunBundle:
    """Everything a metrics/verify pass needs after a run."""

    condition: str
    seed: int
    result: RunResult
    ledger: ClaimLedger
    bank: CredibilityBank
    chainlog: ChainLog
    storage: SQLiteStorage
    scenario: dict[str, Any]
    reglib: RegLibrary


def register_resources(ledger: ClaimLedger, scenario: dict[str, Any]) -> None:
    """Register every scarce resource of the storm as a ledger capacity.

    - ``seat:<flight>`` — one exclusivity group ``seat`` so a passenger holds
      at most one seat across all flights;
    - ``crew:FERRY-1`` — the single extra-section crew slot (the ferry trap);
    - ``gate:<gate>`` — one departure per gate;
    - ``hotel:block`` — the distressed-lodging block (group ``hotel``).
    """
    for flight in scenario["flights"]:
        ledger.register_resource(seat_resource(flight["id"]), flight["seats_free"], group="seat")
    ledger.register_resource(FERRY_CREW_RESOURCE, 1, group=None)
    for gate in scenario["gates"]:
        ledger.register_resource(f"gate:{gate}", 1, group=None)
    ledger.register_resource(HOTEL_RESOURCE, scenario["hotel_block"], group="hotel")


def build_society(
    scenario: dict[str, Any],
    seed: int,
    *,
    with_mediator: bool = True,
    transport: QwenTransport | None = None,
    max_rounds: int = 6,
    budget: int = DEFAULT_BUDGET,
    db_path: str | Path = ":memory:",
) -> Society:
    """Assemble a ready-to-run society over a shared SQLite backend.

    ``transport`` defaults to the offline :class:`FakeQwen` (deterministic
    policy agents); pass a :class:`LiveQwen` for the DashScope path. Returns
    the :class:`Society` with its pre-built :class:`RunBundle` stashed on
    ``society._tarmac`` (result unset until ``run()``); :func:`run_society`
    is the helper that runs it and returns the populated bundle.
    """
    storage = SQLiteStorage(db_path)
    chainlog = ChainLog(storage)
    chainlog.genesis(
        {
            "scenario": scenario["name"],
            "seed": scenario["seed"],
            "run_seed": seed,
            "condition": "society" if with_mediator else "society_minus_mediator",
            "agents": list(AGENT_ORDER),
        }
    )
    # a seeded rng makes commitment nonces (hence the chain) reproducible
    rng = Random(f"tarmac:ledger:{scenario['name']}:{seed}:{with_mediator}")
    ledger = ClaimLedger(storage=storage, chainlog=chainlog, rng=rng)
    register_resources(ledger, scenario)

    bank = CredibilityBank(
        storage=storage,
        budgets={name: budget for name in AGENT_ORDER},
        chainlog=chainlog,
    )

    if transport is None:
        transport = FakeQwen(build_policies(scenario), DutyManagerPolicy())

    agents = [RoleAgent(PERSONAS[name], transport) for name in AGENT_ORDER]
    mediator = TransportMediator(transport) if with_mediator else None

    reglib = build_reg_library()
    keypair: KeyPair = keypair_from_seed(f"tarmac:{scenario['name']}:{seed}")

    society = Society(
        agents=agents,
        mediator=mediator,
        ledger=ledger,
        bank=bank,
        detector=DeadlockDetector(contested_rounds=2),
        chainlog=chainlog,
        keypair=keypair,
        scenario=public_scenario(scenario),
        private_views=build_private_views(scenario),
        citation_resolver=reglib.resolver(),
        max_rounds=max_rounds,
    )
    # stash for run_society
    society._tarmac = RunBundle(  # type: ignore[attr-defined]
        condition="society" if with_mediator else "society_minus_mediator",
        seed=seed,
        result=None,  # type: ignore[arg-type]
        ledger=ledger,
        bank=bank,
        chainlog=chainlog,
        storage=storage,
        scenario=scenario,
        reglib=reglib,
    )
    return society


def run_society(
    scenario: dict[str, Any],
    seed: int,
    *,
    condition: str = "society",
    transport: QwenTransport | None = None,
    max_rounds: int = 6,
    db_path: str | Path = ":memory:",
) -> RunBundle:
    """Build and run a society condition; return the fully populated bundle.

    ``condition`` is ``"society"`` or ``"society_minus_mediator"``. The
    ``"single"`` baseline is a different engine — see
    :func:`tarmac_society.tarmac.baseline.run_single_planner`.
    """
    if condition not in ("society", "society_minus_mediator"):
        raise ValueError(
            f"run_society handles society conditions only, got {condition!r}; "
            "use baseline.run_single_planner for 'single'"
        )
    society = build_society(
        scenario,
        seed,
        with_mediator=(condition == "society"),
        transport=transport,
        max_rounds=max_rounds,
        db_path=db_path,
    )
    result = society.run()
    bundle: RunBundle = society._tarmac  # type: ignore[attr-defined]
    bundle.result = result
    return bundle
