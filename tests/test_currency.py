"""Credibility currency: cost, refund, premium, burn, void."""

from __future__ import annotations

import pytest

from tarmac_society import CredibilityBank, CurrencyError, SQLiteStorage
from tarmac_society.schemas import Position


def _bank(**kw):
    st = SQLiteStorage(":memory:")
    return CredibilityBank(st, {"alice": 50, "bob": 30}, contest_cost=10, premium=5, **kw)


def _block(agent, target="c-1"):
    return Position(agent=agent, stance="block", target_claim=target, argument="x", citations=["r"])


def test_initial_balances():
    bank = _bank()
    assert bank.balance("alice") == 50
    assert bank.balances() == {"alice": 50, "bob": 30}


def test_unknown_agent_balance_raises():
    bank = _bank()
    with pytest.raises(CurrencyError):
        bank.balance("carol")


def test_can_contest_threshold():
    bank = _bank()
    assert bank.can_contest("alice") is True
    # drain bob below cost
    bank.open_contest(_block("bob", "c-x"), 1)
    bank.open_contest(_block("bob", "c-y"), 1)
    bank.open_contest(_block("bob", "c-z"), 1)  # 30 -> 0
    assert bank.can_contest("bob") is False


def test_open_contest_charges_cost():
    bank = _bank()
    cid = bank.open_contest(_block("alice"), 1)
    assert cid is not None
    assert bank.balance("alice") == 40


def test_open_contest_non_block_raises():
    bank = _bank()
    pos = Position(agent="alice", stance="support", target_claim="c-1", argument="x")
    with pytest.raises(CurrencyError):
        bank.open_contest(pos, 1)


def test_open_contest_insufficient_budget_returns_none():
    st = SQLiteStorage(":memory:")
    bank = CredibilityBank(st, {"poor": 5}, contest_cost=10)
    assert bank.open_contest(_block("poor"), 1) is None
    assert bank.balance("poor") == 5


def test_duplicate_contest_not_double_charged():
    bank = _bank()
    c1 = bank.open_contest(_block("alice", "c-9"), 1)
    c2 = bank.open_contest(_block("alice", "c-9"), 1)  # same target
    assert c1 == c2
    assert bank.balance("alice") == 40  # charged once


def test_settle_won_refunds_cost_plus_premium():
    bank = _bank()
    cid = bank.open_contest(_block("alice"), 1)
    bank.settle(cid, won=True, round_=2)
    assert bank.balance("alice") == 55  # 50 - 10 + 15


def test_settle_lost_burns_stake():
    bank = _bank()
    cid = bank.open_contest(_block("alice"), 1)
    bank.settle(cid, won=False, round_=2)
    assert bank.balance("alice") == 40  # stake gone


def test_settle_twice_raises():
    bank = _bank()
    cid = bank.open_contest(_block("alice"), 1)
    bank.settle(cid, won=True, round_=2)
    with pytest.raises(CurrencyError):
        bank.settle(cid, won=True, round_=3)


def test_void_contest_refunds_without_premium():
    bank = _bank()
    cid = bank.open_contest(_block("alice"), 1)
    bank.void_contest(cid, 2)
    assert bank.balance("alice") == 50  # exactly the stake back


def test_open_contests_listing():
    bank = _bank()
    bank.open_contest(_block("alice", "c-1"), 1)
    contests = bank.open_contests()
    assert contests[0]["agent"] == "alice" and contests[0]["target_claim"] == "c-1"


def test_snapshot_and_curve():
    bank = _bank()
    bank.snapshot(1)
    bank.open_contest(_block("alice"), 2)
    bank.snapshot(2)
    curve = bank.curve()
    rounds = {r for r, _a, _b in curve}
    assert rounds == {1, 2}


def test_spend_summary_accounts_burn_and_premium():
    bank = _bank()
    won = bank.open_contest(_block("alice", "c-1"), 1)
    lost = bank.open_contest(_block("bob", "c-2"), 1)
    bank.settle(won, won=True, round_=2)
    bank.settle(lost, won=False, round_=2)
    summary = bank.spend_summary()
    assert summary["alice"]["premium"] == 5
    assert summary["bob"]["burned"] == 10
    assert bank.total_spend() == 10  # only bob's stake burned
    assert bank.total_staked() == 20  # both stakes placed


def test_negative_budget_rejected():
    st = SQLiteStorage(":memory:")
    with pytest.raises(CurrencyError):
        CredibilityBank(st, {"a": -1})


def test_bad_params_rejected():
    st = SQLiteStorage(":memory:")
    with pytest.raises(CurrencyError):
        CredibilityBank(st, {"a": 5}, contest_cost=0)


def test_reopened_bank_continues_contest_sequence():
    """A second CredibilityBank over the same storage must not reuse ids."""
    st = SQLiteStorage(":memory:")
    bank1 = CredibilityBank(st, {"alice": 50})
    cid1 = bank1.open_contest(_block("alice", "c-1"), 1)
    assert cid1 == "x-01"

    bank2 = CredibilityBank(st, {"alice": 50})
    cid2 = bank2.open_contest(_block("alice", "c-2"), 1)
    assert cid2 == "x-02"  # continues, does not collide with x-01


def test_reopened_bank_ignores_malformed_contest_ids():
    st = SQLiteStorage(":memory:")
    bank1 = CredibilityBank(st, {"alice": 50})
    # simulate a foreign/corrupted contest id that won't parse as "<prefix>-<int>"
    st.execute(
        "INSERT INTO contests(id, agent, target_claim, round_opened, status, cost, position)"
        " VALUES (?,?,?,?,'open',?,?)",
        ("not-a-number", "alice", "c-9", 1, 10, "{}"),
    )
    bank2 = CredibilityBank(st, {"alice": 50})
    assert bank2._seq == 0  # malformed id ignored, sequence unaffected
    assert bank1 is not None


def test_settle_unknown_contest_raises():
    bank = _bank()
    with pytest.raises(CurrencyError):
        bank.settle("x-999", won=True, round_=1)


def test_void_unknown_contest_raises():
    bank = _bank()
    with pytest.raises(CurrencyError):
        bank.void_contest("x-999", 1)


def test_void_already_closed_contest_raises():
    bank = _bank()
    cid = bank.open_contest(_block("alice"), 1)
    bank.settle(cid, won=True, round_=2)
    with pytest.raises(CurrencyError):
        bank.void_contest(cid, 3)
