"""Synthetic regulation & carrier-policy passages (FAR/DOT-style).

All passages are synthetic paraphrases written for this simulator (carrier
code QW is fictional); ids are stable so positions/rulings cite them and
signed rulings embed their SHA-256 hashes.
"""

from __future__ import annotations

from ..qwen.citations import Passage, RegLibrary

__all__ = ["PASSAGES", "build_reg_library"]

PASSAGES: list[Passage] = [
    Passage(
        id="far117.11",
        title="FAR 117.11 — Flight duty period limits",
        text=(
            "No certificate holder may assign and no flightcrew member may accept "
            "an assignment if the flight duty period would exceed the applicable "
            "limit in Table B. A ferry or repositioning sector counts in full "
            "toward the flight duty period. There is no commercial-necessity "
            "extension: a sector that cannot be completed within the remaining "
            "duty window may not depart."
        ),
    ),
    Passage(
        id="duty_table.B",
        title="Duty Table B — Remaining-duty arithmetic",
        text=(
            "Required duty for a sector = block time + 45 minutes report/brief. "
            "A crew is legal for the sector only if remaining duty minutes >= "
            "required duty minutes. Rest resets are not permitted inside an "
            "irregular-operations recovery window."
        ),
    ),
    Passage(
        id="dot-259.4",
        title="DOT 259.4 — Re-accommodation priority for protected passengers",
        text=(
            "In irregular operations, carriers must re-accommodate passengers "
            "with disabilities, unaccompanied minors, and passengers with "
            "documented medical transport needs ahead of general re-booking "
            "priority, including ahead of elite-status and fare-class ordering."
        ),
    ),
    Passage(
        id="um-policy.4",
        title="QW UM-4 — Unaccompanied minor handling",
        text=(
            "An unaccompanied minor must be re-booked on a same-day NONSTOP "
            "flight departing before the 22:00 escort curfew. Connections and "
            "overnight holds require guardian consent and are prohibited during "
            "irregular operations."
        ),
    ),
    Passage(
        id="med-policy.2",
        title="QW MED-2 — Medical shipment couriers",
        text=(
            "A courier transporting time-critical medical material (e.g. organ "
            "transport) holds absolute re-accommodation priority onto any flight "
            "whose arrival precedes the documented viability deadline. Fare class "
            "is not a factor."
        ),
    ),
    Passage(
        id="wchr-policy.1",
        title="QW WCHR-1 — Wheelchair passenger service level",
        text=(
            "Wheelchair passengers displaced by cancellation must be re-booked on "
            "same-day nonstop service with boarding assistance. Routing a WCHR "
            "passenger through a connection to free a nonstop seat is a service "
            "failure."
        ),
    ),
    Passage(
        id="conx-policy.7",
        title="QW CONX-7 — International connection protection",
        text=(
            "Passengers holding same-ticket international connections must be "
            "prioritized onto the earliest arrival that protects the connection "
            "cutoff, subordinate only to DOT 259.4 protected categories."
        ),
    ),
    Passage(
        id="hotel-policy.3",
        title="QW HTL-3 — Distressed passenger lodging",
        text=(
            "The station may issue lodging up to the contracted distressed-block "
            "size. Rooms must be released back to the block as soon as a "
            "passenger is confirmed on a departure, and protected categories "
            "receive rooms first."
        ),
    ),
    Passage(
        id="gate-ops.5",
        title="QW GATE-5 — Turnaround and gate minima",
        text=(
            "A departure requires an assigned gate and a minimum 40-minute "
            "turnaround. Extra sections without a gate assignment and turnaround "
            "slot may not be scheduled."
        ),
    ),
    Passage(
        id="fareclass-policy.1",
        title="QW FARE-1 — Standard re-booking order",
        text=(
            "Absent overriding regulation, standby and re-booking order is elite "
            "tier descending, then fare class F, J, W, Y, then time of original "
            "booking."
        ),
    ),
]


def build_reg_library() -> RegLibrary:
    return RegLibrary(PASSAGES)
