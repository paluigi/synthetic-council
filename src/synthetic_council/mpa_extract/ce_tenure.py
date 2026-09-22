"""Chief Economist tenure table for the accounts era (2015 → present).

The Chief Economist is the Executive Board member whose proposal opens the
rate discussion in every account ("Mr/Ms <CE> therefore proposed …").
Tenures here are verified against the proposal speaker observed across all
accounts by ``mpa_extract.ce_proposal`` (which flags any disagreement).
"""

from __future__ import annotations

CHIEF_ECONOMISTS: list[tuple[str, str, str]] = [
    # (person, start, end) — end inclusive
    ("Peter Praet", "2012-06-01", "2019-05-31"),
    ("Philip R. Lane", "2019-06-01", "2027-05-31"),
]


def chief_economist_on(day: str) -> str | None:
    """Chief Economist active on an ISO date, per the tenure table."""
    for person, start, end in CHIEF_ECONOMISTS:
        if start <= day <= end:
            return person
    return None
