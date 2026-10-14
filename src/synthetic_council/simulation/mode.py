"""Simulated decision mode: unanimity / consensus / majority (point 4).

Two independent measurements, both saved:

- **Jev** — one ChoiceQuestion over the simulated closing evidence (each
  member's final stance with its probability, plus the President's decision
  statement), using the *same Bruegel/Trichet criteria as the ground-truth
  extraction* in ``mpa_extract.decision_mode`` so the comparison is symmetric;
- **deterministic aggregation** — a threshold mapping over the per-agent
  P(aligned with the simulated decision): unanimity if every member is
  aligned with p >= ``unanimity_min_p``; consensus if the aligned share >=
  ``consensus_share`` and no member falls below ``consensus_min_p``;
  otherwise majority.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from synthetic_council.jev import jev_choice
from synthetic_council.mpa_extract.decision_mode import (
    DECISION_MODE_CRITERIA,
    DECISION_MODE_INSTRUCTIONS,
    PROMPT_VERSION,
)
from synthetic_council.simulation.agent import Agent
from synthetic_council.simulation.alignment import AlignmentResult
from synthetic_council.simulation.config import SimulationConfig
from synthetic_council.simulation.decision import Decision


@dataclass
class ModeResult:
    jev_label: str
    jev_probabilities: dict[str, float] = field(default_factory=dict)
    deterministic_label: str | None = None
    aligned_probs: dict[str, float] = field(default_factory=dict)
    prompt_version: str = PROMPT_VERSION


def deterministic_mode(
    agents: list[Agent], alignment: list[AlignmentResult], cfg: SimulationConfig
) -> tuple[str, dict[str, float]]:
    """Threshold mapping over per-agent P(aligned with the decision)."""
    probs: dict[str, float] = {}
    for al in alignment:
        p = al.p_aligned_sim.get("aligned")
        if p is None:  # missing distribution — treat as unknown, excluded
            continue
        probs[al.person] = p
    if not probs:
        return "unknown", probs
    values = list(probs.values())
    share = sum(1 for p in values if p >= 0.5) / len(values)
    if all(p >= cfg.unanimity_min_p for p in values):
        return "unanimity", probs
    if share >= cfg.consensus_share and all(p >= cfg.consensus_min_p for p in values):
        return "consensus", probs
    return "majority", probs


async def run_mode(
    agents: list[Agent],
    decision: Decision,
    alignment: list[AlignmentResult],
    cfg: SimulationConfig,
) -> ModeResult:
    det_label, probs = deterministic_mode(agents, alignment, cfg)
    evidence_lines = []
    for a in agents:
        ext = a.final_extraction
        al = next((x for x in alignment if x.person == a.person), None)
        p_al = f"{al.p_aligned_sim.get('aligned', float('nan')):.2f}" if al else "n/a"
        stance = ext.position if ext else "?"
        stance_p = ""
        if ext and ext.probabilities.get(stance) is not None:
            stance_p = f" (p={ext.probabilities[stance]:.2f})"
        evidence_lines.append(
            f"{a.person} ({'voting' if a.voting_right else 'non-voting'}): "
            f"position {stance}{stance_p}, P(aligned with decision)={p_al}"
        )
    state = {
        "decision_statement": decision.statement,
        "member_positions": "\n".join(evidence_lines),
    }
    res = await jev_choice(
        state=state,
        criteria=DECISION_MODE_CRITERIA,
        instructions=(
            DECISION_MODE_INSTRUCTIONS
            + " Here the evidence is the simulated council's final positions "
            "and alignment probabilities rather than account quotes."
        ),
        model=cfg.extractor_model,
    )
    if cfg.recorder is not None:
        cfg.recorder.record_jev(
            stage="decision-mode",
            state=state,
            criteria=DECISION_MODE_CRITERIA,
            outcome=res.outcome,
            probabilities=res.probabilities,
            model=cfg.extractor_model,
        )
    return ModeResult(
        jev_label=res.outcome,
        jev_probabilities=res.probabilities,
        deterministic_label=det_label,
        aligned_probs=probs,
    )


def mode_to_dict(m: ModeResult) -> dict:
    return {
        "jev_label": m.jev_label,
        "jev_probabilities": m.jev_probabilities,
        "deterministic_label": m.deterministic_label,
        "aligned_probs": m.aligned_probs,
        "prompt_version": m.prompt_version,
    }
