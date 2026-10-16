"""Council agents: one per Governing Council member present at a meeting.

Roster: the accounts-extracted participants (attendance + voting rights + CE
flag, 2015+) intersected with the arm's memo set; pre-2015 or missing
participant rows fall back to the memo-filename roster (flagged, no voting
rights). Each agent carries its briefing memo and — when built — its
web-evidence member profile as persona background.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from synthetic_council.memo import profile_text
from synthetic_council.mpa_extract.ce_tenure import chief_economist_on
from synthetic_council.simulation.config import SimulationConfig

if TYPE_CHECKING:
    from synthetic_council.simulation.extraction import PositionExtraction

MEMO_FILENAME_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<person>.+)\.md$")

REACTION_PROMPT = """You are {person}, {role_line} attending today's ECB \
Governing Council monetary policy meeting on {meeting_date}.

The Chief Economist ({chief_economist}) has just presented the proposal that \
will frame today's rate decision:

\"\"\"{proposal}\"\"\"

Your briefing memo follows at the end of this message. It contains the euro \
area situation with the data available at the meeting, the latest staff \
projections, and — where applicable — your own country's situation and an \
extract of your own most recent public speech. Your persona background from \
your member profile is included.

Task: give your opening reaction to the Chief Economist's proposal.

Requirements:
1. Motivation FIRST: ground your argument in the memo's data (inflation, \
growth, labour market, projections, your country's situation where relevant, \
your own past positioning) and in your profile's policy orientation.
2. State clearly whether you support the proposal as made, support it with \
reservations, or oppose it — and if you oppose it, what you would prefer \
instead (a different size, direction, or timing).
3. Stay in character: argue from your mandate and your public record, not as \
an AI. Be concise: at most {max_chars} words.
"""

PERSONA_BLOCK = """=== YOUR MEMBER PROFILE (persona background) ===

{profile}

=== END OF PROFILE ===
"""


@dataclass
class Agent:
    """One council member at one meeting."""

    person: str
    meeting_date: str
    role: str
    country: str | None
    memo_path: Path
    memo_text: str = field(repr=False, default="")
    initial_statement: str = field(repr=False, default="")
    initial_extraction: PositionExtraction | None = field(repr=False, default=None)
    final_statement: str = field(repr=False, default="")
    final_extraction: PositionExtraction | None = field(repr=False, default=None)
    voting_right: bool = True
    is_chief_economist: bool = False
    profile: str = field(repr=False, default="")
    roster_source: str = "participants"

    @property
    def role_line(self) -> str:
        if self.is_chief_economist:
            return "Executive Board member and Chief Economist of the ECB"
        if self.country and self.role.lower().startswith("governor"):
            return f"Governor of the national central bank of {self.country}"
        return self.role

    def reaction_prompt(
        self, cfg: SimulationConfig, chief_economist: str, proposal: str
    ) -> tuple[str, str]:
        """Round-1 system/user prompts: the opening reaction to the proposal."""
        system = REACTION_PROMPT.format(
            person=self.person,
            role_line=self.role_line,
            meeting_date=self.meeting_date,
            chief_economist=chief_economist,
            proposal=proposal,
            max_chars=cfg.max_statements_chars,
        )
        user = self._memo_user_message()
        if self.profile:
            user = PERSONA_BLOCK.format(profile=self.profile) + "\n\n" + user
        return system, user

    def _memo_user_message(self) -> str:
        return (
            "=== YOUR BRIEFING MEMO ===\n\n"
            f"{self.memo_text}\n\n"
            "=== END OF MEMO ===\n\n"
            "Give your opening reaction to the proposal now."
        )


def _memo_header_fields(memo_text: str) -> tuple[str, str | None]:
    """Parse '**Role:** ...' and '**Country:** ...' from a memo header."""
    role = country = None
    for line in memo_text.splitlines()[:12]:
        m_role = re.match(r"\*\*Role:\*\*\s*(.+)", line)
        if m_role:
            role = m_role.group(1).strip()
        m_country = re.match(r"\*\*Country:\*\*\s*(.+)", line)
        if m_country:
            country = m_country.group(1).strip()
    return role or "Governing Council member", country


def load_agents(memo_paths: list[Path]) -> list[Agent]:
    """Load agents from explicit memo paths (pre-2015 / fallback rosters)."""
    agents: list[Agent] = []
    for path in sorted(memo_paths):
        m = MEMO_FILENAME_RE.match(path.name)
        if not m:
            raise ValueError(f"memo filename does not match <date>_<person>.md: {path}")
        memo_text = path.read_text(encoding="utf-8")
        role, country = _memo_header_fields(memo_text)
        person = m.group("person").replace("_", " ")
        ce = chief_economist_on(m.group("date"))
        agents.append(
            Agent(
                person=person,
                meeting_date=m.group("date"),
                role=role,
                country=country,
                memo_path=path,
                memo_text=memo_text,
                is_chief_economist=bool(ce and person.endswith(ce.split()[-1])),
                profile=profile_text(person),
                roster_source="memos_fallback",
            )
        )
    return agents


def load_roster(date: str, cfg: SimulationConfig) -> tuple[list[Agent], list[str]]:
    """Agents for one meeting: participants ∩ memos (with skipped-participant
    log); memo-filename fallback when no participants exist for the date."""
    memos_dir = cfg.resolved_memos_dir()
    parts = (
        pl.read_parquet(cfg.participants_path).filter(pl.col("meeting_date") == date)
        if cfg.participants_path.exists()
        else None
    )
    agents: list[Agent] = []
    skipped: list[str] = []
    for r in parts.sort("person").iter_rows(named=True) if parts is not None else []:
        path = memos_dir / f"{date}_{r['person'].replace(' ', '_')}.md"
        if not path.exists():
            skipped.append(r["person"])  # participant without a memo (gap)
            continue
        agents.append(
            Agent(
                person=r["person"],
                meeting_date=date,
                role=r["role"],
                country=r["country"],
                memo_path=path,
                memo_text=path.read_text(encoding="utf-8"),
                voting_right=bool(r["voting_right"]),
                is_chief_economist=bool(r["is_chief_economist"]),
                profile=profile_text(r["person"]),
            )
        )
    if agents:
        return agents, skipped
    paths = sorted(memos_dir.glob(f"{date}_*.md"))
    if not paths:
        raise FileNotFoundError(
            f"no memos for meeting {date} in {memos_dir} "
            "(build them first: uv run python -m synthetic_council.memo)"
        )
    print(
        f"WARNING {date}: no account participants for this date — falling back "
        "to the memo-filename roster (roles/countries from memo headers, "
        "voting_right defaults to True for everyone)"
    )
    return load_agents(paths), []
