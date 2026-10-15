"""Branch an experiment from a saved payload: re-measure without regenerating.

Reads an existing ``payload.json`` (whose agents carry every saved
statement) and re-runs the measurement stages — position extraction,
decision, alignment, decision mode — under the CURRENT ``inference.toml``
(e.g. a different judge model or sampling settings), writing
``payload_variant.json`` plus its own ``messages.jsonl`` ledger next to
the original. No generator calls are made: the saved statements are
reused verbatim, so a judge swap costs only judge calls.

Usage:
    uv run python -m synthetic_council.simulation.replay \\
        data/processed/simulations/ea_only/2022-07-21/payload.json
    # or point at a meeting directory; --arm/--out relocate the variant.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from synthetic_council.simulation.agent import Agent
from synthetic_council.simulation.alignment import run_alignment
from synthetic_council.simulation.config import SimulationConfig
from synthetic_council.simulation.decision import Decision
from synthetic_council.simulation.extraction import extract_positions
from synthetic_council.simulation.mode import run_mode
from synthetic_council.simulation.recorder import Recorder


def _agent_from_saved(a: dict, meeting_date: str) -> Agent:
    """Rebuild an agent with its saved statements; the memo file is re-read
    so the judge sees exactly what a fresh run would see."""
    agent = Agent(
        person=a["person"],
        meeting_date=meeting_date,
        role=a.get("role") or "",
        country=a.get("country"),
        memo_path=Path(a["memo"]) if a.get("memo") else Path("/dev/null"),
    )
    agent.is_chief_economist = bool(a.get("is_chief_economist"))
    agent.voting_right = bool(a.get("voting_right"))
    agent.initial_statement = a.get("initial_statement") or ""
    agent.final_statement = a.get("final_statement") or ""
    try:
        agent.memo_text = agent.memo_path.read_text(encoding="utf-8")[:20_000]
    except OSError:
        agent.memo_text = ""
    return agent


async def replay_meeting(payload_path: Path, cfg: SimulationConfig) -> Path:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    date = payload["meeting_date"]
    out_dir = payload_path.parent
    recorder = Recorder(
        out_dir=out_dir,
        meta={
            "arm": payload.get("arm", cfg.arm),
            "meeting_date": date,
            "replay_of": str(payload_path),
        },
    )
    cfg.recorder = recorder

    agents = [_agent_from_saved(a, date) for a in payload["agents"]]
    proposal = payload["ce_proposal"]["text"]

    # 1. re-extract positions on the SAVED statements
    reacting = [a for a in agents if not a.is_chief_economist]
    pre = await extract_positions([(a.initial_statement, a.memo_text) for a in reacting], cfg)
    for agent, ext in zip(reacting, pre, strict=True):
        agent.initial_extraction = ext
    post = await extract_positions([(a.final_statement, a.memo_text) for a in agents], cfg)
    for agent, ext in zip(agents, post, strict=True):
        agent.final_extraction = ext

    # 2. decision: reuse the saved President's statement verbatim (the
    # generator's output is part of the branch point, not the re-measured
    # part); only the modal cross-check refreshes from new extractions
    saved_decision = payload["decision"]
    decision = Decision(
        president=saved_decision["president"],
        statement=saved_decision["statement"],
        stance=saved_decision["stance"],
        modal_stance_voters=None,
        voter_counts={},
        agrees_with_modal=None,
    )
    decision.modal_stance_voters, decision.voter_counts = _modal_voters(agents, decision)

    # 3-4. alignment + mode re-measured
    alignment = await run_alignment(agents, proposal, decision, date, cfg)
    mode = await run_mode(agents, decision, alignment, cfg)

    variant = dict(payload)
    variant["settings"] = cfg.settings()
    variant["replay_of"] = str(payload_path)
    variant["agents"] = [
        {**a_saved, **_updated_agent_fields(a_live)}
        for a_saved, a_live in zip(payload["agents"], agents, strict=True)
    ]
    variant["decision"] = {
        "president": decision.president,
        "statement": decision.statement,
        "stance": decision.stance,
        "modal_stance_voters": decision.modal_stance_voters,
        "voter_counts": decision.voter_counts,
        "agrees_with_modal": decision.agrees_with_modal,
    }
    from synthetic_council.simulation.mode import mode_to_dict
    from synthetic_council.simulation.runner import _consensus

    variant["mode"] = mode_to_dict(mode)
    variant["consensus"] = _consensus(agents)

    out = out_dir / "payload_variant.json"
    out.write_text(json.dumps(variant, indent=2, ensure_ascii=False), encoding="utf-8")
    recorder.flush()
    cfg.recorder = None
    return out


def _updated_agent_fields(a: Agent) -> dict:
    from synthetic_council.simulation.runner import _deltas

    return {
        "extraction_initial": asdict(a.initial_extraction) if a.initial_extraction else None,
        "extraction_final": asdict(a.final_extraction) if a.final_extraction else None,
        "deltas": _deltas(a) if a.initial_extraction and a.final_extraction else None,
    }


def _modal_voters(agents: list[Agent], decision: Decision):
    counts = {"raise": 0, "hold": 0, "lower": 0}
    for a in agents:
        if a.voting_right and a.final_extraction is not None:
            counts[a.final_extraction.position] += 1
    modal = max(counts, key=lambda k: counts[k]) if any(counts.values()) else None
    decision.agrees_with_modal = (
        None if decision.stance is None or modal is None else decision.stance == modal
    )
    return modal, counts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("payload", type=Path, help="payload.json (or its meeting directory)")
    p.add_argument("--arm", default=None, help="override cfg.arm for role resolution")
    args = p.parse_args(argv)
    path = args.payload
    if path.is_dir():
        path = path / "payload.json"
    if not path.exists():
        raise SystemExit(f"payload not found: {path}")

    cfg = SimulationConfig(arm=args.arm or "ea_only")
    missing = cfg.inference.check_keys()
    if missing:
        raise SystemExit(f"missing API keys: {', '.join(missing)}")

    out = asyncio.run(replay_meeting(path, cfg))
    print(f"variant written: {out}")
    print(
        f"judge: {cfg.inference.role('extractor').model_id} "
        f"(config {cfg.inference.config_sha256[:8]})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
