"""Meeting-room booking — the SAME protocol, ~20 lines, zero airline code.

Proof that ``tarmac-society`` is domain-agnostic: revocable claims on a
row-locked ledger + mechanical deadlock detection, reused for a completely
different resource-contention problem. Run me: ``python examples/meeting_rooms.py``.
"""

from tarmac_society import ClaimLedger, ClaimProposal, ClaimStatus, DeadlockDetector

ledger = ClaimLedger()
ledger.register_resource("room:Aspen@9am", capacity=1, group="9am")  # one booking / slot
ledger.register_resource("room:Birch@9am", capacity=1, group="9am")


def book(team: str, room: str) -> ClaimStatus:
    rec = ledger.submit_plain(
        ClaimProposal(agent=team, resource=room, qty=1, beneficiaries=[team],
                      basis=f"{team} needs a 9am room"),
        round_=1,
    )
    return rec.status


print("design books Aspen:", book("design", "room:Aspen@9am"))   # -> granted
print("sales  books Aspen:", book("sales", "room:Aspen@9am"))    # -> blocked (taken)

# the deadlock detector flags the contention mechanically — no LLM, no vibes
deadlocks = DeadlockDetector(contested_rounds=1).scan(ledger, open_contests=[], round_=1)
print("contended slots  :", [d.resources for d in deadlocks])

# ...so the loser simply reroutes to the free room. Same ledger, same physics.
print("sales books Birch:", book("sales", "room:Birch@9am"))     # -> granted
print("final schedule   :", ledger.manifest())
