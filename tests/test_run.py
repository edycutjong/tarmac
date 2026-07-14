"""Orchestrator: resource registration + condition wiring."""

from __future__ import annotations

import pytest

from tarmac_society import ClaimLedger
from tarmac_society.tarmac.run import (
    CONDITIONS,
    FERRY_CREW_RESOURCE,
    HOTEL_RESOURCE,
    build_society,
    register_resources,
    run_society,
)


def test_register_resources_creates_all_scarce_pools(scenario):
    lg = ClaimLedger()
    register_resources(lg, scenario)
    res = lg.resources()
    assert res["seat:QW441"]["capacity"] == 9
    assert res["seat:QW441"]["group"] == "seat"
    assert FERRY_CREW_RESOURCE in res
    assert res[HOTEL_RESOURCE]["capacity"] == 60
    assert sum(1 for r in res if r.startswith("gate:")) == 6


def test_conditions_tuple():
    assert CONDITIONS == ("society", "society_minus_mediator", "single")


def test_run_society_quiesces(scenario):
    b = run_society(scenario, 7, condition="society")
    assert b.result.quiescent is True
    assert b.condition == "society"
    assert b.bank is not None


def test_run_society_minus_mediator_runs_to_cap(scenario):
    b = run_society(scenario, 7, condition="society_minus_mediator")
    assert b.result.quiescent is False  # no adjudication -> no clean finish
    assert b.result.rulings == []


def test_run_society_rejects_single_condition(scenario):
    with pytest.raises(ValueError):
        run_society(scenario, 7, condition="single")


def test_build_society_returns_runnable(scenario):
    society = build_society(scenario, 7, with_mediator=True)
    result = society.run()
    assert result.rounds_used >= 1
    assert society._tarmac.condition == "society"


def test_run_society_is_deterministic(scenario):
    a = run_society(scenario, 7, condition="society")
    b = run_society(scenario, 7, condition="society")
    assert a.result.manifest_hash == b.result.manifest_hash
    assert a.result.chain_head == b.result.chain_head
