"""Shared fixtures for the tarmac-society test suite."""

from __future__ import annotations

import pytest

from tarmac_society import ChainLog, ClaimLedger, ClaimProposal, CredibilityBank, SQLiteStorage
from tarmac_society.tarmac.seed import generate


@pytest.fixture
def storage() -> SQLiteStorage:
    return SQLiteStorage(":memory:")


@pytest.fixture
def ledger() -> ClaimLedger:
    """A bare ledger with two small seat-like resources in group 'seat'."""
    lg = ClaimLedger()
    lg.register_resource("seat:A", 2, group="seat")
    lg.register_resource("seat:B", 1, group="seat")
    lg.register_resource("room", 3, group="room")
    return lg


@pytest.fixture
def chained_ledger():
    """Ledger + chain log sharing one storage, with genesis written."""
    st = SQLiteStorage(":memory:")
    log = ChainLog(st)
    log.genesis({"scenario": "test", "seed": 0, "condition": "unit"})
    lg = ClaimLedger(storage=st, chainlog=log)
    lg.register_resource("seat:A", 2, group="seat")
    lg.register_resource("seat:B", 1, group="seat")
    return lg, log, st


@pytest.fixture
def bank(storage) -> CredibilityBank:
    return CredibilityBank(storage, {"alice": 50, "bob": 50}, contest_cost=10, premium=5)


@pytest.fixture
def proposal() -> ClaimProposal:
    return ClaimProposal(
        agent="alice", resource="seat:A", qty=2, beneficiaries=["p1", "p2"], basis="test"
    )


@pytest.fixture(scope="session")
def scenario() -> dict:
    return generate("storm_dfw", 7)


@pytest.fixture(scope="session")
def scenario_5() -> dict:
    return generate("storm_dfw", 5)
