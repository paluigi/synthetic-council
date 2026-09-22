"""Shared Jev choice-question helper (typesafe-ai/jev via Vercel AI Gateway).

Wraps the ``ai`` package's ``experimental_evaluate`` + ``ChoiceQuestion``
pattern (as live-verified in ``simulation/extraction.py``) so every Jev
classification in the project — position extraction, decision mode,
alignment — goes through one retry-hardened code path and returns the full
probability distribution over the outcomes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

DEFAULT_JEV_MODEL = "typesafe-ai/jev"


@dataclass
class JevChoice:
    outcome: str
    probabilities: dict[str, float] = field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def margin(self) -> float | None:
        if not self.probabilities:
            return None
        p = sorted(self.probabilities.values(), reverse=True)
        return p[0] - p[1] if len(p) > 1 else p[0]


async def jev_choice(
    state: dict[str, Any],
    criteria: dict[str, str],
    instructions: str,
    model: str = DEFAULT_JEV_MODEL,
    attempts: int = 3,
) -> JevChoice:
    """One Jev call: a choice question over ``criteria`` given ``state``.

    Returns the chosen outcome plus the full probability distribution
    (calibrated per-outcome probabilities from the model).
    """
    from ai import get_model
    from ai.ops import ChoiceQuestion, experimental_evaluate

    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            item = await experimental_evaluate(
                get_model(model),
                state=state,
                questions={"choice": ChoiceQuestion(instructions=instructions, criteria=criteria)},
            )
            break
        except Exception as exc:  # transient gateway errors
            last_exc = exc
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(2**attempt * 2)
    else:  # pragma: no cover
        raise RuntimeError(f"jev_choice failed: {last_exc}")

    answer = item.value.answers["choice"]
    usage = getattr(item, "usage", None)
    input_tokens = getattr(usage, "input_tokens", None) if usage else None
    output_tokens = getattr(usage, "output_tokens", None) if usage else None
    return JevChoice(
        outcome=answer.choice,
        probabilities=dict(answer.probabilities or {}),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        raw=item.model_dump(mode="json"),
    )
