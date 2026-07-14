"""Mechanical deadlock detection: cycles + contested streaks."""

from __future__ import annotations

from tarmac_society import ClaimLedger, ClaimProposal, DeadlockDetector, find_cycles


def _claim(agent, res, ben):
    return ClaimProposal(agent=agent, resource=res, qty=1, beneficiaries=[ben], basis="b")


# ---- find_cycles ---------------------------------------------------------
def test_no_cycle_in_dag():
    assert find_cycles({"a": ["b"], "b": ["c"]}) == []


def test_self_loop_is_a_cycle():
    assert find_cycles({"a": ["a"]}) == [["a"]]


def test_two_cycle():
    assert find_cycles({"a": ["b"], "b": ["a"]}) == [["a", "b"]]


def test_three_cycle():
    assert find_cycles({"a": ["b"], "b": ["c"], "c": ["a"]}) == [["a", "b", "c"]]


def test_disjoint_cycles():
    edges = {"a": ["b"], "b": ["a"], "x": ["y"], "y": ["x"]}
    assert find_cycles(edges) == [["a", "b"], ["x", "y"]]


def test_empty_graph():
    assert find_cycles({}) == []


def test_cycle_output_is_sorted_and_deterministic():
    edges = {"c": ["a"], "a": ["b"], "b": ["c"]}
    assert find_cycles(edges) == [["a", "b", "c"]]


# ---- DeadlockDetector.scan ----------------------------------------------
def _wait_for_cycle_ledger():
    lg = ClaimLedger()
    lg.register_resource("X", 1)
    lg.register_resource("Y", 1)
    lg.submit_plain(_claim("b", "X", "bx"), 1)  # b holds X
    lg.submit_plain(_claim("a", "Y", "ay"), 1)  # a holds Y
    lg.submit_plain(_claim("a", "X", "ax"), 1)  # a blocked on X (b holds)
    lg.submit_plain(_claim("b", "Y", "by"), 1)  # b blocked on Y (a holds)
    return lg


def test_scan_detects_wait_for_cycle():
    lg = _wait_for_cycle_ledger()
    det = DeadlockDetector()
    dls = det.scan(lg, [], 1)
    cycles = [d for d in dls if d.kind == "cycle"]
    assert len(cycles) == 1
    assert set(cycles[0].agents) == {"a", "b"}
    assert set(cycles[0].resources) == {"X", "Y"}


def test_scan_dedups_same_cycle():
    lg = _wait_for_cycle_ledger()
    det = DeadlockDetector()
    det.scan(lg, [], 1)
    again = det.scan(lg, [], 2)
    assert [d for d in again if d.kind == "cycle"] == []  # already fired


def test_contested_streak_fires_after_two_rounds():
    lg = ClaimLedger()
    lg.register_resource("R", 1)
    lg.submit_plain(_claim("a", "R", "ar"), 1)  # a holds R
    lg.submit_plain(_claim("b", "R", "br"), 1)  # b blocked on R
    det = DeadlockDetector(contested_rounds=2)
    first = det.scan(lg, [], 1)
    second = det.scan(lg, [], 2)
    assert [d for d in first if d.kind == "contested"] == []
    contested = [d for d in second if d.kind == "contested"]
    assert len(contested) == 1 and contested[0].resources == ["R"]


def test_reset_resource_restarts_streak():
    lg = ClaimLedger()
    lg.register_resource("R", 1)
    lg.submit_plain(_claim("a", "R", "ar"), 1)
    lg.submit_plain(_claim("b", "R", "br"), 1)
    det = DeadlockDetector(contested_rounds=2)
    det.scan(lg, [], 1)
    det.reset_resource("R")
    # streak reset -> next scan is only the 1st again, no contested deadlock
    assert [d for d in det.scan(lg, [], 2) if d.kind == "contested"] == []


def test_contest_against_granted_claim_counts_as_contested():
    lg = ClaimLedger()
    lg.register_resource("R", 2)
    g = lg.submit_plain(_claim("a", "R", "ar"), 1)  # granted
    open_contests = [{"agent": "b", "target_claim": g.id, "round_opened": 1, "cost": 10}]
    det = DeadlockDetector(contested_rounds=2)
    det.scan(lg, open_contests, 1)
    dls = det.scan(lg, open_contests, 2)
    assert any(d.kind == "contested" and g.id in d.claims for d in dls)


def test_deadlock_ids_are_unique():
    lg = _wait_for_cycle_ledger()
    lg.register_resource("Z", 1)
    lg.submit_plain(_claim("c", "Z", "cz"), 1)
    det = DeadlockDetector()
    dls = det.scan(lg, [], 1)
    ids = [d.id for d in dls]
    assert len(ids) == len(set(ids))


def test_scan_ignores_open_contest_with_unknown_target_claim():
    lg = ClaimLedger()
    lg.register_resource("R", 1)
    lg.submit_plain(_claim("a", "R", "ar"), 1)
    open_contests = [{"agent": "b", "target_claim": "c-999", "round_opened": 1, "cost": 10}]
    det = DeadlockDetector(contested_rounds=1)
    dls = det.scan(lg, open_contests, 1)  # must not crash on a phantom target claim
    assert all("c-999" not in d.claims for d in dls)


def test_scan_ignores_open_contest_on_non_granted_target():
    lg = ClaimLedger()
    lg.register_resource("R", 1)
    g = lg.submit_plain(_claim("a", "R", "ar"), 1)  # granted, then voided below
    lg.void_claim(g.id, "test", 1)
    open_contests = [{"agent": "b", "target_claim": g.id, "round_opened": 1, "cost": 10}]
    det = DeadlockDetector(contested_rounds=1)
    dls = det.scan(lg, open_contests, 1)
    assert all(g.id not in d.claims for d in dls)


def test_streak_forgotten_when_resource_no_longer_contested():
    lg = ClaimLedger()
    lg.register_resource("R", 1)
    lg.submit_plain(_claim("a", "R", "ar"), 1)  # granted
    blocked = lg.submit_plain(_claim("b", "R", "br"), 1)  # blocked -> contested
    det = DeadlockDetector(contested_rounds=3)
    det.scan(lg, [], 1)
    assert det._streaks.get("R") == 1
    lg.withdraw_claim("b", blocked.id, 2)  # no longer blocked/contested
    det.scan(lg, [], 2)
    assert "R" not in det._streaks  # streak forgotten, not just paused
