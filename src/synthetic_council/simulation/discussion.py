"""Multi-round council discussion.

Every member speaks once per round (``cfg.n_rounds >= 2``); each statement sees
the full transcript built so far. The generator backend produces the words;
Jev extraction of positions happens once before (opening statements) and once
after (final statements) the discussion — not per turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from synthetic_council.simulation.agent import Agent
from synthetic_council.simulation.config import SimulationConfig
from synthetic_council.simulation.generator import BaseGenerator

DISCUSSION_TURN_PROMPT = """You are {person}, {role_line}, in round {round_no} of \
{n_rounds} of the ECB Governing Council discussion on the policy rate.

So far in the discussion:

{transcript}

Task: speak once more. Respond directly to colleagues — especially those whose \
position or arguments differ from yours. You may defend your position, concede \
a point, or adjust your stance if a colleague's argument from the data convinces \
you; do not change position just to conform. Ground every claim in the economic \
data from your briefing memo. Be concise: at most {max_chars} words."""

FINAL_STATEMENT_PROMPT = """You are {person}, {role_line}. The discussion is over; \
you must now state your final position for the rate decision.

The full discussion transcript:

{transcript}

Task: write your closing statement, accounting for the arguments made by your \
colleagues.

Requirements:
1. Weigh the discussion: acknowledge the strongest point made against your view \
and say why it does — or now does — move you.
2. Motivation first, grounded in your briefing memo's data.
3. Decision LAST, one explicit line: "Position: raise the rate", "Position: hold \
the rate", or "Position: lower the rate".
4. Stay in character. At most {max_chars} words of motivation before the final line.
"""


@dataclass
class DiscussionTurn:
    round_no: int  # 1-based
    person: str
    text: str


@dataclass
class DiscussionResult:
    turns: list[DiscussionTurn] = field(default_factory=list)

    def transcript(self, max_chars: int) -> str:
        """Transcript as seen by agents (tail-truncated to fit context)."""
        blocks = [
            f"[Round {t.round_no}] {t.person}: {t.text}" for t in self.turns
        ]
        text = "\n\n".join(blocks)
        if len(text) <= max_chars:
            return text
        return "…[earlier turns truncated]…\n" + text[-max_chars:]


def _transcript_for(
    agent: Agent, result: DiscussionResult, cfg: SimulationConfig
) -> str:
    text = result.transcript(cfg.max_transcript_chars)
    if agent.initial_statement and not result.turns:
        text = f"[Opening statement] {agent.person}: {agent.initial_statement}\n\n" + text
    return text


async def run_discussion(
    agents: list[Agent],
    generator: BaseGenerator,
    cfg: SimulationConfig,
) -> DiscussionResult:
    """Run >= 2 sequential rounds; every agent speaks in every round."""
    if cfg.n_rounds < 2:
        raise ValueError("the discussion needs at least 2 rounds (cfg.n_rounds >= 2)")

    result = DiscussionResult()
    for round_no in range(1, cfg.n_rounds + 1):
        for agent in agents:  # alphabetical by person (load_agents sorts memos)
            if round_no == 1:
                system, user = agent.initial_prompt(cfg)
            else:
                system = DISCUSSION_TURN_PROMPT.format(
                    person=agent.person,
                    role_line=agent.role_line,
                    round_no=round_no,
                    n_rounds=cfg.n_rounds,
                    transcript=_transcript_for(agent, result, cfg),
                    max_chars=cfg.max_statements_chars,
                )
                user = "Give your statement for this round now."
            text = await generator.generate(system, user)
            result.turns.append(DiscussionTurn(round_no, agent.person, text))

    return result


async def run_final_statements(
    agents: list[Agent],
    discussion: DiscussionResult,
    generator: BaseGenerator,
    cfg: SimulationConfig,
) -> None:
    """After the discussion, each agent produces a final in-character statement."""
    for agent in agents:
        system = FINAL_STATEMENT_PROMPT.format(
            person=agent.person,
            role_line=agent.role_line,
            transcript=discussion.transcript(cfg.max_transcript_chars),
            max_chars=cfg.max_statements_chars,
        )
        agent.final_statement = await generator.generate(
            system, "Give your final statement now."
        )
