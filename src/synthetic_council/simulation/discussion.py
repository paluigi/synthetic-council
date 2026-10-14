"""The council discussion, structured around the Chief Economist's proposal.

Fixed flow (no config switches):

1. **CE proposal** — injected verbatim from the accounts extraction
   (``mpa_extract.ce_proposal``); no LLM call. This anchors the meeting to
   what was actually proposed.
2. **Rounds 1..n (default 2)** — every non-CE member reacts to the proposal
   (round 1) and then responds to colleagues (rounds 2+), each seeing the
   transcript so far. The Chief Economist does not speak in these rounds.
3. **CE reply** — the Chief Economist (LLM, in character) always gets a turn
   to reply to the discussion, defending or adjusting the proposal.
4. **Final summary positions** — every member states a final position
   relative to the proposal (``run_final_statements``).

Jev position extraction anchors on the round-1 reactions ("before") and the
final statements ("after"), preserving the pre/post delta analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from synthetic_council.simulation.agent import Agent
from synthetic_council.simulation.config import SimulationConfig
from synthetic_council.simulation.generator import BaseGenerator

DISCUSSION_TURN_PROMPT = """You are {person}, {role_line}, in round {round_no} of \
{n_rounds} of the ECB Governing Council discussion that followed the Chief \
Economist's rate proposal.

The Chief Economist's proposal:

\"\"\"{proposal}\"\"\"

So far in the discussion:

{transcript}

Task: speak once more. Respond directly to colleagues — especially those whose \
position or arguments differ from yours. You may defend your position, concede \
a point, or adjust your stance if a colleague's argument from the data convinces \
you; do not change position just to conform. Ground every claim in the economic \
data from your briefing memo. Be concise: at most {max_chars} words."""

CE_REPLY_PROMPT = """You are {person}, Executive Board member and Chief Economist of \
the ECB. You opened today's monetary policy meeting with your rate proposal:

\"\"\"{proposal}\"\"\"

The Governing Council has now discussed it for {n_rounds} round(s):

{transcript}

Task: reply to the discussion on behalf of your proposal. Defend it where you \
remain convinced, acknowledge the strongest counter-arguments, and say clearly \
whether you maintain the proposal unchanged, or would adjust it (and how). \
Stay in character; be concise: at most {max_chars} words."""

FINAL_STATEMENT_PROMPT = """You are {person}, {role_line}. The discussion is over; \
you must now state your final position for the rate decision.

The Chief Economist's proposal:

\"\"\"{proposal}\"\"\"

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
    round_no: int  # 1-based; 0 marks the injected CE proposal, -1 the CE reply
    person: str
    text: str

    @property
    def label(self) -> str:
        if self.round_no == 0:
            return "Proposal"
        if self.round_no == -1:
            return "Chief Economist's reply"
        return f"Round {self.round_no}"


@dataclass
class DiscussionResult:
    proposal: str = ""
    chief_economist: str = ""
    turns: list[DiscussionTurn] = field(default_factory=list)

    def transcript(self, max_chars: int) -> str:
        """Transcript as seen by agents (tail-truncated to fit context)."""
        blocks = [f"[{t.label}] {t.person}: {t.text}" for t in self.turns]
        text = "\n\n".join(blocks)
        if len(text) <= max_chars:
            return text
        return "…[earlier turns truncated]…\n" + text[-max_chars:]


async def _recorded_generate(
    gen: BaseGenerator,
    cfg: SimulationConfig,
    system: str,
    user: str,
    *,
    stage: str,
    agent: str | None,
    round_no: int | None,
) -> str:
    # the generator emits its own full CallEvent (prompts, response, model,
    # params, usage, latency) through on_event -> Recorder.record_event
    return await gen.generate(
        system, user, context={"stage": stage, "agent": agent, "round": round_no}
    )


async def run_discussion(
    agents: list[Agent],
    proposal: str,
    chief_economist: str,
    generator: BaseGenerator,
    cfg: SimulationConfig,
) -> DiscussionResult:
    """Proposal injection -> non-CE discussion rounds -> CE reply."""
    if cfg.n_rounds < 2:
        raise ValueError("the discussion needs at least 2 rounds (cfg.n_rounds >= 2)")
    ce_agents = [a for a in agents if a.is_chief_economist]
    others = [a for a in agents if not a.is_chief_economist]
    if not ce_agents:
        raise ValueError("roster has no Chief Economist — cannot anchor the proposal")
    ce = ce_agents[0]

    result = DiscussionResult(proposal=proposal, chief_economist=ce.person)
    result.turns.append(DiscussionTurn(0, ce.person, proposal))

    for round_no in range(1, cfg.n_rounds + 1):
        for agent in others:  # alphabetical by person (roster is sorted)
            if round_no == 1:
                system, user = agent.reaction_prompt(cfg, ce.person, proposal)
            else:
                system = DISCUSSION_TURN_PROMPT.format(
                    person=agent.person,
                    role_line=agent.role_line,
                    round_no=round_no,
                    n_rounds=cfg.n_rounds,
                    proposal=proposal,
                    transcript=result.transcript(cfg.max_transcript_chars),
                    max_chars=cfg.max_statements_chars,
                )
                user = "Give your statement for this round now."
            text = await _recorded_generate(
                generator,
                cfg,
                system,
                user,
                stage=f"round-{round_no}",
                agent=agent.person,
                round_no=round_no,
            )
            result.turns.append(DiscussionTurn(round_no, agent.person, text))

    # the CE always replies to the discussion
    ce_system = CE_REPLY_PROMPT.format(
        person=ce.person,
        proposal=proposal,
        n_rounds=cfg.n_rounds,
        transcript=result.transcript(cfg.max_transcript_chars),
        max_chars=cfg.max_statements_chars,
    )
    ce_text = await _recorded_generate(
        generator,
        cfg,
        ce_system,
        "Give your reply to the discussion now.",
        stage="ce-reply",
        agent=ce.person,
        round_no=-1,
    )
    result.turns.append(DiscussionTurn(-1, ce.person, ce_text))
    return result


def first_round_statements(agents: list[Agent], discussion: DiscussionResult) -> None:
    """Attach each non-CE agent's round-1 statement as its initial statement
    (the pre-discussion anchor for Jev position extraction)."""
    for a in agents:
        if a.is_chief_economist:
            continue
        for t in discussion.turns:
            if t.round_no == 1 and t.person == a.person:
                a.initial_statement = t.text
                break
        else:
            a.initial_statement = ""


async def run_final_statements(
    agents: list[Agent],
    proposal: str,
    discussion: DiscussionResult,
    generator: BaseGenerator,
    cfg: SimulationConfig,
) -> None:
    """After the discussion, each agent produces a final in-character statement."""
    for agent in agents:
        system = FINAL_STATEMENT_PROMPT.format(
            person=agent.person,
            role_line=agent.role_line,
            proposal=proposal,
            transcript=discussion.transcript(cfg.max_transcript_chars),
            max_chars=cfg.max_statements_chars,
        )
        agent.final_statement = await _recorded_generate(
            generator,
            cfg,
            system,
            "Give your final statement now.",
            stage="final-statement",
            agent=agent.person,
            round_no=None,
        )
