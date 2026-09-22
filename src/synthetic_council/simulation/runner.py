"""Meeting-level orchestration and result serialisation.

Pipeline per meeting (see package docstring):

1. ``run_discussion`` — round 1 statements are the members' opening positions
   (memo-driven); rounds 2..n react to the transcript.
2. Pre-discussion Jev extraction on the opening statements.
3. ``run_final_statements`` — each member's closing statement.
4. Post-discussion Jev extraction + per-member deltas and council consensus.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict
from pathlib import Path

from synthetic_council.simulation.agent import Agent, load_agents
from synthetic_council.simulation.config import SimulationConfig
from synthetic_council.simulation.discussion import (
    DiscussionResult,
    run_discussion,
    run_final_statements,
)
from synthetic_council.simulation.extraction import PositionExtraction, extract_positions
from synthetic_council.simulation.generator import BaseGenerator, build_generator


def meeting_memos(memos_dir: Path, date: str) -> list[Path]:
    """Memo paths for one meeting date."""
    paths = sorted(memos_dir.glob(f"{date}_*.md"))
    if not paths:
        raise FileNotFoundError(
            f"no memos for meeting {date} in {memos_dir} "
            "(build them first: uv run python -m synthetic_council.memo)"
        )
    return paths


async def run_meeting(
    date: str,
    cfg: SimulationConfig,
    generator: BaseGenerator | None = None,
) -> dict:
    """Full pipeline for one meeting; returns the JSON-serialisable payload."""
    own_generator = generator is None
    gen = generator or build_generator(cfg)
    try:
        agents = load_agents(meeting_memos(cfg.memos_dir, date))

        # 1. discussion (round 1 = opening statements, rounds 2+ = reactions)
        discussion = await run_discussion(agents, gen, cfg)
        for agent, turn in zip(agents, discussion.turns[: len(agents)], strict=True):
            agent.initial_statement = turn.text

        # 2. pre-discussion extraction (parallel, one Jev call per member)
        pre = await extract_positions(
            [(a.initial_statement, a.memo_text) for a in agents], cfg
        )
        for agent, ext in zip(agents, pre, strict=True):
            agent.initial_extraction = ext

        # 3. final statements accounting for the discussion
        await run_final_statements(agents, discussion, gen, cfg)

        # 4. post-discussion extraction (parallel)
        post = await extract_positions(
            [(a.final_statement, a.memo_text) for a in agents], cfg
        )
        for agent, ext in zip(agents, post, strict=True):
            agent.final_extraction = ext

        return _meeting_payload(date, agents, discussion)
    finally:
        if own_generator:
            await gen.aclose()


def _deltas(agent: Agent) -> dict:
    pre: PositionExtraction = agent.initial_extraction  # type: ignore[assignment]
    post: PositionExtraction = agent.final_extraction  # type: ignore[assignment]
    return {
        "position_changed": pre.position != post.position,
        "position_before": pre.position,
        "position_after": post.position,
        "conviction_before": pre.conviction,
        "conviction_after": post.conviction,
        "conviction_delta": None
        if pre.conviction is None or post.conviction is None
        else round(post.conviction - pre.conviction, 4),
        "margin_before": pre.margin,
        "margin_after": post.margin,
        "probabilities_before": pre.probabilities,
        "probabilities_after": post.probabilities,
    }


def _consensus(agents: list[Agent]) -> dict:
    """Position balance of the council, before and after the discussion."""

    def tally(attr: str) -> dict[str, int]:
        counts = {"raise": 0, "hold": 0, "lower": 0}
        for a in agents:
            ext: PositionExtraction | None = getattr(a, attr)
            if ext is not None:
                counts[ext.position] += 1
        return counts

    pre, post = tally("initial_extraction"), tally("final_extraction")
    n_changed = sum(
        1
        for a in agents
        if a.initial_extraction is not None
        and a.final_extraction is not None
        and a.initial_extraction.position != a.final_extraction.position
    )
    return {
        "before": pre,
        "after": post,
        "majority_before": max(pre, key=lambda k: pre[k]),
        "majority_after": max(post, key=lambda k: post[k]),
        "n_changed": n_changed,
    }


def _meeting_payload(
    date: str, agents: list[Agent], discussion: DiscussionResult
) -> dict:
    return {
        "meeting_date": date,
        "n_agents": len(agents),
        "n_rounds": max(t.round_no for t in discussion.turns),
        "agents": [
            {
                "person": a.person,
                "role": a.role,
                "country": a.country,
                "memo": str(a.memo_path),
                "initial_statement": a.initial_statement,
                "final_statement": a.final_statement,
                "extraction_initial": asdict(a.initial_extraction),
                "extraction_final": asdict(a.final_extraction),
                "deltas": _deltas(a),
            }
            for a in agents
        ],
        "consensus": _consensus(agents),
        "turns": [asdict(t) for t in discussion.turns],
    }


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


async def run_simulation(
    dates: list[str], cfg: SimulationConfig, out_dir: Path
) -> list[Path]:
    """Run several meetings sequentially, writing one JSON payload each."""
    for d in dates:
        if not _DATE_RE.match(d):
            raise ValueError(f"meeting date must be YYYY-MM-DD, got {d!r}")
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for d in dates:
        payload = await run_meeting(d, cfg)
        out = out_dir / f"simulation_{d}.json"
        out.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        written.append(out)
    return written


def run_simulation_sync(
    dates: list[str], cfg: SimulationConfig, out_dir: Path
) -> list[Path]:
    """Blocking entry point (CLI)."""
    return asyncio.run(run_simulation(dates, cfg, out_dir))
