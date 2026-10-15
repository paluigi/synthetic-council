"""Meeting-level orchestration and result serialisation.

Pipeline per meeting (see package docstring):

1. load the roster (accounts participants ∩ arm memos) and the CE proposal
   anchor (verbatim from the accounts extraction);
2. ``run_discussion`` — proposal injection, two rounds of reactions by the
   non-CE members, then the CE's reply;
3. pre-discussion Jev extraction on the round-1 reactions;
4. ``run_final_statements`` + post-discussion extraction;
5. ``run_decision`` — the President's decision statement + modal-voter
   cross-check;
6. ``run_alignment`` — per member, {aligned, non_aligned} probabilities
   against the simulated AND the real decision;
7. ``run_mode`` — simulated unanimity/consensus/majority (Jev, same criteria
   as the ground-truth extraction, plus the deterministic aggregation).

Every generator/Jev request and response is archived (messages.jsonl) and a
human-readable transcript.md is written alongside the payload JSON.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict
from pathlib import Path

import polars as pl

from synthetic_council.simulation.agent import Agent, load_roster
from synthetic_council.simulation.alignment import AlignmentResult, run_alignment
from synthetic_council.simulation.config import SimulationConfig
from synthetic_council.simulation.decision import Decision, run_decision
from synthetic_council.simulation.discussion import (
    DiscussionResult,
    first_round_statements,
    run_discussion,
    run_final_statements,
)
from synthetic_council.simulation.extraction import PositionExtraction, extract_positions
from synthetic_council.simulation.generator import BaseGenerator, build_generator
from synthetic_council.simulation.mode import ModeResult, mode_to_dict, run_mode
from synthetic_council.simulation.recorder import Recorder

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def meeting_memos(memos_dir: Path, date: str) -> list[Path]:
    """Memo paths for one meeting date."""
    paths = sorted(memos_dir.glob(f"{date}_*.md"))
    if not paths:
        raise FileNotFoundError(
            f"no memos for meeting {date} in {memos_dir} "
            "(build them first: uv run python -m synthetic_council.memo)"
        )
    return paths


def load_ce_proposal(date: str, cfg: SimulationConfig) -> dict:
    """The verbatim proposal anchor for one meeting."""
    df = pl.read_parquet(cfg.ce_proposal_path).filter(pl.col("meeting_date") == date)
    if not df.height:
        raise FileNotFoundError(
            f"no CE proposal for {date} — run "
            "`uv run python -m synthetic_council.mpa_extract.ce_proposal` first"
        )
    return df.row(0, named=True)


async def run_meeting(
    date: str,
    cfg: SimulationConfig,
    generator: BaseGenerator | None = None,
) -> dict:
    """Full pipeline for one meeting; returns the JSON-serialisable payload."""
    own_generator = generator is None
    out_dir = cfg.out_root / cfg.arm / date
    recorder = Recorder(out_dir=out_dir, meta={"arm": cfg.arm, "meeting_date": date})
    cfg.recorder = recorder
    gen = generator or build_generator(cfg, on_event=recorder.record_event)
    try:
        agents, skipped = load_roster(date, cfg)
        if skipped:
            print(f"WARNING {date}: participants without memos (skipped): {skipped}")
        prop = load_ce_proposal(date, cfg)
        proposal = prop["proposal_text"]
        if not proposal:
            raise ValueError(
                f"empty CE proposal text for {date} (extraction_method="
                f"{prop['extraction_method']}) — re-run the extraction with the "
                "LLM fallback enabled"
            )

        # 1-2. proposal -> discussion rounds -> CE reply
        discussion = await run_discussion(agents, proposal, prop["chief_economist"], gen, cfg)
        first_round_statements(agents, discussion)

        # 3. pre-discussion extraction on the round-1 reactions (parallel)
        reacting = [a for a in agents if not a.is_chief_economist]
        pre = await extract_positions([(a.initial_statement, a.memo_text) for a in reacting], cfg)
        for agent, ext in zip(reacting, pre, strict=True):
            agent.initial_extraction = ext

        # 4. final statements + post-discussion extraction (parallel)
        await run_final_statements(agents, proposal, discussion, gen, cfg)
        post = await extract_positions([(a.final_statement, a.memo_text) for a in agents], cfg)
        for agent, ext in zip(agents, post, strict=True):
            agent.final_extraction = ext

        # 5. decision (President + modal-voter cross-check)
        decision = await run_decision(agents, discussion, gen, cfg)

        # 6. alignment vs simulated and real decisions
        alignment = await run_alignment(agents, proposal, decision, date, cfg)

        # 7. simulated decision mode
        mode = await run_mode(agents, decision, alignment, cfg)

        payload = _meeting_payload(
            date, cfg, prop, agents, discussion, decision, alignment, mode, skipped
        )
        out = out_dir / "payload.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        recorder.flush()
        recorder.write_transcript(_transcript_blocks(date, discussion, agents, decision))
        return payload
    finally:
        cfg.recorder = None
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
    date: str,
    cfg: SimulationConfig,
    prop: dict,
    agents: list[Agent],
    discussion: DiscussionResult,
    decision: Decision,
    alignment: list[AlignmentResult],
    mode: ModeResult,
    skipped: list[str],
) -> dict:
    align_by_person = {a.person: a for a in alignment}
    return {
        "meeting_date": date,
        "arm": cfg.arm,
        "settings": cfg.settings(),
        "n_agents": len(agents),
        "n_rounds": max(t.round_no for t in discussion.turns if t.round_no > 0),
        "ce_proposal": {
            "chief_economist": prop["chief_economist"],
            "text": prop["proposal_text"],
            "direction": prop["proposed_direction"],
            "bp": prop["proposed_bp"],
            "extraction_method": prop["extraction_method"],
        },
        "agents": [
            {
                "person": a.person,
                "role": a.role,
                "country": a.country,
                "voting_right": a.voting_right,
                "is_chief_economist": a.is_chief_economist,
                "memo": str(a.memo_path),
                "initial_statement": a.initial_statement,
                "final_statement": a.final_statement,
                "extraction_initial": asdict(a.initial_extraction)
                if a.initial_extraction
                else None,
                "extraction_final": asdict(a.final_extraction) if a.final_extraction else None,
                "deltas": _deltas(a) if a.initial_extraction and a.final_extraction else None,
                "alignment": {
                    "sim": align_by_person[a.person].p_aligned_sim,
                    "real": align_by_person[a.person].p_aligned_real,
                }
                if a.person in align_by_person
                else None,
            }
            for a in agents
        ],
        "decision": {
            "president": decision.president,
            "statement": decision.statement,
            "stance": decision.stance,
            "modal_stance_voters": decision.modal_stance_voters,
            "voter_counts": decision.voter_counts,
            "agrees_with_modal": decision.agrees_with_modal,
        },
        "mode": mode_to_dict(mode),
        "consensus": _consensus(agents),
        "turns": [asdict(t) | {"label": t.label} for t in discussion.turns],
        "skipped_participants": skipped,
    }


def _transcript_blocks(
    date: str, discussion: DiscussionResult, agents: list[Agent], decision: Decision
) -> list[str]:
    blocks = [f"# Simulated Governing Council — {date}"]
    for t in discussion.turns:
        blocks.append(f"## {t.label} — {t.person}\n\n{t.text}")
    blocks.append("## Final positions")
    for a in agents:
        blocks.append(f"### {a.person}\n\n{a.final_statement}")
    blocks.append(f"## Decision — {decision.president}\n\n{decision.statement}")
    return blocks


async def run_simulation(dates: list[str], cfg: SimulationConfig) -> list[Path]:
    """Run several meetings sequentially, one payload per meeting directory."""
    for d in dates:
        if not _DATE_RE.match(d):
            raise ValueError(f"meeting date must be YYYY-MM-DD, got {d!r}")
    written: list[Path] = []
    for d in dates:
        payload = await run_meeting(d, cfg)
        out = cfg.out_root / cfg.arm / d / "payload.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        written.append(out)
    return written


def run_simulation_sync(dates: list[str], cfg: SimulationConfig) -> list[Path]:
    """Blocking entry point (CLI)."""
    return asyncio.run(run_simulation(dates, cfg))
