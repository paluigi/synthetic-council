"""Position extraction via the ``extractor`` role (config-driven).

One measurement per extraction asks the judge (Clef via OpenRouter's
native decisions API by default — see inference.toml) two typed questions
in a single request:

- ``position``  — choice over {raise, hold, lower}: the member's policy
  stance with the **full probability distribution**;
- ``conviction`` — score over a 3-level commitment rubric, returned as a
  fractional probability-weighted score (0 = tentative, 2 = committed).

Roles without a native decisions API (``api = "chat"``) emulate the same
measurement with the choice-v1 prompted-JSON protocol — the answers share
one shape either way. Per-answer confidence from the native API is kept
in ``raw`` for provenance; the conviction score remains the usable
confidence measure.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from synthetic_council.inference import Client, decision
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
    "given their briefing memo. Judge the `statement` inside `state`."
)

QUESTIONS = {
    "position": {
        "type": "choice",
        "instructions": EXTRACTION_INSTRUCTIONS + " What policy stance does the speaker take?",
        "criteria": POSITION_CRITERIA,
    },
    "conviction": {
        "type": "score",
        "instructions": (
            EXTRACTION_INSTRUCTIONS + " How strongly does the speaker commit to their stance?"
        ),
        "criteria": CONVICTION_RUBRIC,
    },
}


@dataclass
class PositionExtraction:
    """Result of one extraction (pre- or post-discussion)."""

    position: str
    probabilities: dict[str, float] = field(default_factory=dict)
    conviction: float | None = None  # fractional score, 0..2
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


async def extract_position(
    statement: str,
    memo: str,
    cfg: SimulationConfig,
    client: Client | None = None,
) -> PositionExtraction:
    """One extractor measurement: position distribution + conviction score."""
    own = client is None
    client = client or Client(cfg.inference)
    role = cfg.inference.role("extractor")
    state = {"memo": memo, "statement": statement}
    on_event = cfg.recorder.record_event if cfg.recorder is not None else None
    try:
        attempts = int(role.params.get("retries", 3))
        last_bad = ""
        for attempt in range(attempts):
            try:
                answers, usage = await decision(
                    client,
                    "extractor",
                    state,
                    QUESTIONS,
                    context={"stage": "position-extraction"},
                    on_event=on_event,
                )
            except RuntimeError as exc:
                last_bad = str(exc)
                await asyncio.sleep(2 ** min(attempt, 4))
                continue
            pos_answer = answers.get("position") or {}
            con_answer = answers.get("conviction") or {}
            probs = {k: float(v) for k, v in (pos_answer.get("probabilities") or {}).items()}
            if not probs or set(probs) != set(POSITION_CRITERIA):
                last_bad = json.dumps(answers)[:200]
                continue
            position = pos_answer.get("choice") or max(probs, key=probs.get)  # type: ignore[arg-type]
            conviction = float(con_answer["score"]) if con_answer.get("score") is not None else None
            return PositionExtraction(
                position=position,
                probabilities=probs,
                conviction=conviction,
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                raw={
                    "endpoint": role.endpoint,
                    "model": role.model,
                    "api": role.api,
                    "answers": answers,
                },
            )
        raise RuntimeError(
            f"position extraction via {role.model_id} returned no valid "
            f"answers after {attempts} attempts; last: {last_bad!r}"
        )
    finally:
        if own:
            await client.aclose()


async def _bounded(
    stmt: str, memo: str, cfg: SimulationConfig, sem: asyncio.Semaphore, client: Client
):
    async with sem:
        return await extract_position(stmt, memo, cfg, client)


async def extract_positions(
    pairs: list[tuple[str, str]],
    cfg: SimulationConfig,
    max_concurrent: int | None = None,
) -> list[PositionExtraction]:
    """Extract positions for many (statement, memo) pairs, bounded concurrency."""
    sem = asyncio.Semaphore(max_concurrent or cfg.max_concurrent_extractions)
    client = Client(cfg.inference)
    try:
        tasks = [_bounded(stmt, memo, cfg, sem, client) for stmt, memo in pairs]
        return list(await asyncio.gather(*tasks))
    finally:
        await client.aclose()
