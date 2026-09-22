"""Council agents: one per Governing Council member present at a meeting."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from synthetic_council.simulation.config import SimulationConfig

if TYPE_CHECKING:
    from synthetic_council.simulation.extraction import PositionExtraction

MEMO_FILENAME_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<person>.+)\.md$"
)

INITIAL_STATEMENT_PROMPT = """You are {person}, {role_line} attending today's ECB \
Governing Council monetary policy meeting on {meeting_date}.

Your briefing memo follows at the end of this message. It contains the euro area
situation with the data available at the meeting, the latest staff projections,
and — where applicable — your own country's situation and an excerpt of your own
most recent public speech.

Task: write your opening position statement for the rate-setting discussion.

Requirements:
1. Motivation FIRST: ground your argument in the memo's data (inflation, growth,
   labour market, projections, your country's situation where relevant, your own
   past statements).
2. Decision LAST: end with an explicit one-line position — "Position: raise \
the rate", "Position: hold the rate", or "Position: lower the rate" \
(raise / hold / lower, exactly one of the three).
3. Stay in character: argue from your mandate and your public record, not as an
   AI. Be concise: at most {max_chars} words of motivation before the final line.
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

    @property
    def role_line(self) -> str:
        if self.country and self.role.lower().startswith("governor"):
            return f"Governor of the national central bank of {self.country}"
        return self.role

    def initial_prompt(self, cfg: SimulationConfig) -> tuple[str, str]:
        system = INITIAL_STATEMENT_PROMPT.format(
            person=self.person,
            role_line=self.role_line,
            meeting_date=self.meeting_date,
            max_chars=cfg.max_statements_chars,
        )
        user = (
            "=== YOUR BRIEFING MEMO ===\n\n"
            f"{self.memo_text}\n\n"
            "=== END OF MEMO ===\n\n"
            "Write your opening position statement now."
        )
        return system, user


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
    """Load agents from explicit memo paths (validation happens in the runner)."""
    agents: list[Agent] = []
    for path in sorted(memo_paths):
        m = MEMO_FILENAME_RE.match(path.name)
        if not m:
            raise ValueError(f"memo filename does not match <date>_<person>.md: {path}")
        memo_text = path.read_text(encoding="utf-8")
        role, country = _memo_header_fields(memo_text)
        agents.append(
            Agent(
                person=m.group("person").replace("_", " "),
                meeting_date=m.group("date"),
                role=role,
                country=country,
                memo_path=path,
                memo_text=memo_text,
            )
        )
    return agents
