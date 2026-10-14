"""The decision step: the President formulates the council's decision.

Two recorded layers:

- **President statement (LLM)** — the chair closes the meeting and formulates
  the monetary policy decision, taking the discussion and the Chief
  Economist's proposal into account;
- **deterministic cross-check** — the modal final stance
  (raise/hold/lower) of the voting-right members; disagreement with the
  President's stance is flagged, not resolved silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from synthetic_council.simulation.agent import Agent
from synthetic_council.simulation.config import SimulationConfig
from synthetic_council.simulation.discussion import DiscussionResult
from synthetic_council.simulation.generator import BaseGenerator

PRESIDENT_DECISION_PROMPT = """You are {president}, President of the ECB, chairing \
today's monetary policy meeting. The Chief Economist's proposal:

\"\"\"{proposal}\"\"\"

The Governing Council has discussed it (transcript tail):

{transcript}

Each member's final position:

{positions}

Task: close the meeting. Formulate the Governing Council's monetary policy \
decision as a short statement (2-4 sentences) — what was decided on the key \
interest rates (direction and, if changed, size), grounded in the discussion. \
End with one explicit line: "Decision: raise the rate", "Decision: hold the \
rate", or "Decision: lower the rate"."""


@dataclass
class Decision:
    president: str
    statement: str
    stance: str | None  # raise / hold / lower, parsed from the statement
    modal_stance_voters: str | None  # deterministic cross-check
    voter_counts: dict[str, int]
    agrees_with_modal: bool | None


def _stance_of(text: str) -> str | None:
    m = re.search(r"Decision:\s*(raise|hold|lower)", text, re.IGNORECASE)
    return m.group(1).lower() if m else None


def _modal_voter_stance(agents: list[Agent]) -> tuple[str | None, dict[str, int]]:
    counts = {"raise": 0, "hold": 0, "lower": 0}
    for a in agents:
        if a.final_extraction is not None and a.voting_right:
            counts[a.final_extraction.position] += 1
    if not any(counts.values()):
        return None, counts
    return max(counts, key=lambda k: counts[k]), counts


async def run_decision(
    agents: list[Agent],
    discussion: DiscussionResult,
    generator: BaseGenerator,
    cfg: SimulationConfig,
) -> Decision:
    presidents = [
        a for a in agents if "president" in a.role.lower() and "vice" not in a.role.lower()
    ]
    president = presidents[0] if presidents else None
    modal, counts = _modal_voter_stance(agents)
    positions = "\n".join(
        f"- {a.person} ({'voting' if a.voting_right else 'non-voting'}): "
        f"{a.final_extraction.position if a.final_extraction else '?'}"
        f" — {a.final_statement[-300:]}"
        for a in agents
    )
    if president is None:
        # no president on the roster: fall back to the deterministic rule alone
        statement = (
            "No president on the roster; decision taken by the modal position "
            f"of voting members: {modal}."
        )
        if cfg.recorder is not None:
            cfg.recorder.record(
                stage="decision",
                system="",
                user=positions,
                response=statement,
                model="(deterministic)",
                agent=None,
                round_no=None,
            )
        return Decision(
            president="(none)",
            statement=statement,
            stance=modal,
            modal_stance_voters=modal,
            voter_counts=counts,
            agrees_with_modal=None,
        )

    system = PRESIDENT_DECISION_PROMPT.format(
        president=president.person,
        proposal=discussion.proposal,
        transcript=discussion.transcript(cfg.max_transcript_chars)[-12_000:],
        positions=positions[:12_000],
    )
    text = await generator.generate(
        system, "Close the meeting now.", context={"stage": "decision", "agent": president.person}
    )
    stance = _stance_of(text)
    return Decision(
        president=president.person,
        statement=text,
        stance=stance,
        modal_stance_voters=modal,
        voter_counts=counts,
        agrees_with_modal=None if stance is None or modal is None else stance == modal,
    )
