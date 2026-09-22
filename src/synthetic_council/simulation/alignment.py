"""Post-discussion alignment of each member with the decision (point 3).

One parallel Jev request per member and per reference decision:

- ``sim``  — the decision the *simulated* council took (the President's
  decision statement): internal cohesion, feeding the unanimity/consensus/
  majority aggregation;
- ``real`` — the *actual* ECB decision for that meeting (direction and size
  from gc_decisions): fidelity to history.

Both references' full {aligned, non_aligned} probability distributions are
saved for every member.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from synthetic_council.jev import jev_choice
from synthetic_council.simulation.agent import Agent
from synthetic_council.simulation.config import SimulationConfig
from synthetic_council.simulation.decision import Decision

ALIGNMENT_CRITERIA = {
    "aligned": (
        "the member's stated position endorses the same policy action as the "
        "decision — same direction (and, where a size is stated, same size); "
        "support with reservations still counts as aligned unless the member "
        "states a different preferred action"
    ),
    "non_aligned": (
        "the member's stated position prefers a different policy action than "
        "the decision — a different direction, a different size, or open "
        "opposition to what was decided"
    ),
}

_INSTRUCTIONS = (
    "You are given: the Chief Economist's rate proposal, a decision on the "
    "policy rate, and one Governing Council member's final statement. Decide "
    "whether the member's position is aligned with that decision."
)


@dataclass
class AlignmentResult:
    person: str
    p_aligned_sim: dict[str, float]
    p_aligned_real: dict[str, float]


def real_decision_text(meeting_date: str, cfg: SimulationConfig) -> str:
    """One-sentence real decision for the meeting (direction + size)."""
    import polars as pl

    df = pl.read_parquet(cfg.gc_decisions_path).with_columns(
        pl.col("announcement_date").cast(pl.String)
    )
    rows = df.sort("announcement_date")
    dates = rows["announcement_date"].to_list()
    if meeting_date not in dates:
        return ""
    i = dates.index(meeting_date)
    cur = rows.row(i, named=True)
    mro = cur["mro"]
    dfr = cur["dfr"]
    mlf = cur["mlf"]
    if i > 0:
        prev = rows.row(i - 1, named=True)
        delta = (mro or 0) - (prev["mro"] or 0)
    else:
        delta = 0
    direction = cur["decision"]
    if direction == "change" and mro is not None:
        sign = "raise" if delta > 0 else "lower"
        bp = int(round(abs(delta) * 100))
        return (
            f"The ECB decided to {sign} the three key ECB interest rates by "
            f"{bp} basis points (MRO {mro}%, deposit facility {dfr}%, "
            f"marginal lending {mlf}% after the decision)."
        )
    return (
        "The ECB decided to maintain the three key ECB interest rates "
        f"unchanged (MRO {mro}%, deposit facility {dfr}%, marginal "
        f"lending {mlf}%)."
    )


async def _align_one(
    agent: Agent,
    proposal: str,
    decision_text: str,
    reference: str,
    cfg: SimulationConfig,
    sem: asyncio.Semaphore,
) -> dict[str, float]:
    async with sem:
        res = await jev_choice(
            state={
                "proposal": proposal,
                "reference_decision": decision_text,
                "member": agent.person,
                "member_final_statement": agent.final_statement,
            },
            criteria=ALIGNMENT_CRITERIA,
            instructions=_INSTRUCTIONS,
            model=cfg.extractor_model,
        )
    if cfg.recorder is not None:
        cfg.recorder.record_jev(
            stage=f"alignment-{reference}",
            state={"member": agent.person, "reference_decision": decision_text},
            criteria=ALIGNMENT_CRITERIA,
            outcome=res.outcome,
            probabilities=res.probabilities,
            model=cfg.extractor_model,
            agent=agent.person,
        )
    return res.probabilities


async def run_alignment(
    agents: list[Agent],
    proposal: str,
    decision: Decision,
    meeting_date: str,
    cfg: SimulationConfig,
) -> list[AlignmentResult]:
    """Alignment probabilities for every member against both references."""
    real_text = real_decision_text(meeting_date, cfg)
    sim_text = decision.statement
    sem = asyncio.Semaphore(cfg.max_concurrent_extractions)
    sim_probs, real_probs = await asyncio.gather(
        asyncio.gather(*[_align_one(a, proposal, sim_text, "sim", cfg, sem) for a in agents]),
        asyncio.gather(*[_align_one(a, proposal, real_text, "real", cfg, sem) for a in agents]),
    )
    return [
        AlignmentResult(person=a.person, p_aligned_sim=sp, p_aligned_real=rp)
        for a, sp, rp in zip(agents, sim_probs, real_probs, strict=True)
    ]
