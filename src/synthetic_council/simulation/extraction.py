"""Position extraction via Jev (TypeSafe) on the Vercel AI Gateway.

One parallel ``experimental_evaluate`` request per extraction returns, in a
single call:

- ``position``  — ChoiceQuestion over {raise, hold, lower} with the **full
  probability distribution** over the three options;
- ``conviction`` — ScoreQuestion on a 3-level commitment rubric, returned as a
  fractional probability-weighted score (0 = tentative, 2 = fully committed).

Note on TypeSafe's native confidence statistic: empirically it saturates at 1.0
for choice questions even on deliberately hedged statements, so it is recorded
for provenance but the conviction score is the usable confidence measure.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from synthetic_council.simulation.config import SimulationConfig

POSITION_CRITERIA = {
    "raise": "raise the policy rate",
    "hold": "keep the policy rate unchanged",
    "lower": "lower the policy rate",
}

CONVICTION_RUBRIC = [
    "tentative, easily swayed by colleagues' arguments",
    "moderately firm",
    "fully committed, unwavering",
]

EXTRACTION_INSTRUCTIONS = (
    "You are analysing a statement by a member of the ECB Governing Council, "
    "given their briefing memo. Answer two questions about `statement`."
)


@dataclass
class PositionExtraction:
    """Result of one Jev extraction (pre- or post-discussion)."""

    position: str
    probabilities: dict[str, float] = field(default_factory=dict)
    conviction: float | None = None  # fractional score, 0..2
    typesafe_confidence: dict[str, Any] = field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def margin(self) -> float | None:
        """Chosen-option probability minus best alternative (decision margin)."""
        if not self.probabilities:
            return None
        sorted_p = sorted(self.probabilities.values(), reverse=True)
        return sorted_p[0] - sorted_p[1] if len(sorted_p) > 1 else sorted_p[0]


def _parse_usage(item: Any) -> tuple[int | None, int | None]:
    usage = getattr(item, "usage", None)
    if usage is None:
        return None, None
    return getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)


async def extract_position(
    statement: str,
    memo: str,
    cfg: SimulationConfig,
) -> PositionExtraction:
    """One Jev call: position choice + conviction score (parallel questions)."""
    from ai import get_model
    from ai.ops import ChoiceQuestion, ScoreQuestion, experimental_evaluate

    item = None
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            item = await experimental_evaluate(
                get_model(cfg.extractor_model),
                state={"memo": memo, "statement": statement},
                questions={
                    "position": ChoiceQuestion(
                        instructions=EXTRACTION_INSTRUCTIONS
                        + " Q1: what policy stance does the speaker take?",
                        criteria=POSITION_CRITERIA,
                    ),
                    "conviction": ScoreQuestion(
                        instructions=EXTRACTION_INSTRUCTIONS
                        + " Q2: how strongly does the speaker commit to their "
                        "stated stance?",
                        criteria=CONVICTION_RUBRIC,
                    ),
                },
            )
            break
        except Exception as exc:  # transient gateway errors
            last_exc = exc
            if attempt == 2:
                raise
            await asyncio.sleep(2**attempt * 2)
    assert item is not None, f"extraction failed: {last_exc}"

    answers = item.value.answers
    ts_meta = (item.provider_metadata or {}).get("typesafe") or {}
    input_tokens, output_tokens = _parse_usage(item)

    return PositionExtraction(
        position=answers["position"].choice,
        probabilities=dict(answers["position"].probabilities or {}),
        conviction=answers["conviction"].score,
        typesafe_confidence=dict(ts_meta.get("confidence", {})),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        raw=item.model_dump(mode="json"),
    )


async def _bounded(stmt: str, memo: str, cfg: SimulationConfig, sem: asyncio.Semaphore):
    async with sem:
        return await extract_position(stmt, memo, cfg)


async def extract_positions(
    pairs: list[tuple[str, str]],
    cfg: SimulationConfig,
    max_concurrent: int | None = None,
) -> list[PositionExtraction]:
    """Extract positions for many (statement, memo) pairs, bounded concurrency."""
    sem = asyncio.Semaphore(max_concurrent or cfg.max_concurrent_extractions)
    tasks = [_bounded(stmt, memo, cfg, sem) for stmt, memo in pairs]
    return list(await asyncio.gather(*tasks))
