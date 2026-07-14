"""Concurrency: row-level locking must reject the double-claim race.

Two threads race for the last seat on a capacity-1 resource; exactly one may
win. The BEGIN IMMEDIATE write lock + in-process RLock serialize the
check-then-insert so capacity can never be exceeded (invariant I1).
"""

from __future__ import annotations

import threading

from tarmac_society import ClaimLedger, ClaimProposal, ClaimStatus


def _claim(agent, res, ben):
    return ClaimProposal(agent=agent, resource=res, qty=1, beneficiaries=[ben], basis="race")


def test_concurrent_last_seat_exactly_one_winner():
    lg = ClaimLedger()
    lg.register_resource("seat:LAST", 1, group="seat")
    results: dict[str, ClaimStatus] = {}
    start = threading.Barrier(2)

    def go(agent, ben):
        start.wait()
        rec = lg.submit_plain(_claim(agent, "seat:LAST", ben), 1)
        results[agent] = rec.status

    t1 = threading.Thread(target=go, args=("a", "p1"))
    t2 = threading.Thread(target=go, args=("b", "p2"))
    t1.start(); t2.start(); t1.join(); t2.join()

    granted = [a for a, s in results.items() if s == ClaimStatus.GRANTED]
    assert len(granted) == 1
    assert lg.free("seat:LAST") == 0


def test_many_threads_never_exceed_capacity():
    lg = ClaimLedger()
    lg.register_resource("seat:POOL", 5, group="seat")
    n = 20
    start = threading.Barrier(n)

    def go(i):
        start.wait()
        lg.submit_plain(_claim(f"a{i}", "seat:POOL", f"p{i}"), 1)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert lg.free("seat:POOL") == 0
    assert len(lg.manifest()["seat:POOL"]) == 5  # never 6+
