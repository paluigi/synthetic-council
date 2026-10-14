#!/usr/bin/env python
"""Live smoke test: 3-agent mini-council on one synthetic memo.

Verifies the full restructured pipeline against the configured endpoints
(inference.toml): CE proposal anchor -> round-1 reactions -> round-2
responses -> CE reply -> final statements -> position extraction ->
President's decision -> alignment (sim+real) -> simulated decision mode
-> call ledger + settings block. Not part of the test suite (needs the
endpoint API keys in .env.local); run manually:

    uv run python scripts/simulation_smoke_live.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import polars as pl

from synthetic_council.simulation.config import SimulationConfig
from synthetic_council.simulation.runner import run_meeting

MEMO = """# Briefing memo — {person}

**Role:** {role}
{country_line}**Meeting:** GC monetary policy meeting, 2022-07-21
**Current rates:** MRO 0.5% · DFR 0.0% · MLF 0.75%

## Euro area economic situation (data available at the meeting)

| Indicator | Reference | Value |
|---|---|---|
| HICP inflation (y/y %) | 2022-06 | 8.64 |
| HICP core — ex energy, food, alcohol & tobacco (y/y %) | 2022-06 | 3.70 |
| Real GDP growth (y/y %) | 2022-Q1 | 5.39 |
| Unemployment rate (%) | 2022-05 | 6.80 |
| Consumer confidence | 2022-07-01 | -26.10 |

## Latest staff projections available at the meeting

Projection round: **2022-06-01** (ECB/Eurosystem staff).

| Item | Unit | 2022 | 2023 | 2024 |
|---|---|---|---|---|
| HICP inflation | % | 6.80 | 3.50 | 2.10 |
| Real GDP growth | % | 2.80 | 2.10 | 2.10 |
| Unemployment rate | % of labour force | 6.80 | 6.80 | 6.70 |
"""

AGENTS = [
    # person, role, country, ce
    ("Christine Lagarde", "ECB President", None, False),
    ("Klaus Liesman", "Governor, Deutsche Bundesbank", "DE", False),
    ("Philip R. Lane", "ECB Executive Board member", None, True),
]

PROPOSAL = (
    "Mr Lane therefore proposed that the three key ECB interest rates be "
    "raised by 50 basis points, reflecting the continued strength of "
    "underlying inflation and the tightening labour market."
)


def make_inputs(tmp: Path) -> SimulationConfig:
    d = tmp / "memos_ea"
    d.mkdir(parents=True, exist_ok=True)
    for person, role, country, _ in AGENTS:
        (d / f"2022-07-21_{person.replace(' ', '_')}.md").write_text(
            MEMO.format(
                person=person,
                role=role,
                country_line=f"**Country:** {country}\n" if country else "",
            ),
            encoding="utf-8",
        )
    pl.DataFrame(
        {
            "meeting_date": ["2022-07-21"] * len(AGENTS),
            "person": [a[0] for a in AGENTS],
            "role": [a[1] for a in AGENTS],
            "country": [a[2] for a in AGENTS],
            "replacing": [None] * len(AGENTS),
            "voting_right": [True] * len(AGENTS),
            "is_chief_economist": [a[3] for a in AGENTS],
            "in_memberships": [True] * len(AGENTS),
        }
    ).write_parquet(tmp / "participants.parquet")
    pl.DataFrame(
        {
            "meeting_date": ["2022-07-21"],
            "chief_economist": ["Philip R. Lane"],
            "proposal_speaker": ["Mr Lane"],
            "proposal_text": [PROPOSAL],
            "proposed_direction": ["hike"],
            "proposed_bp": [50],
            "ce_mismatch": [False],
            "extraction_method": ["deterministic"],
            "source_span": ["..."],
        }
    ).write_parquet(tmp / "ce_proposal.parquet")
    pl.DataFrame(
        {
            "announcement_date": ["2022-06-09", "2022-07-21"],
            "decision": ["hold", "change"],
            "rate_effective_date": [None, "2022-07-27"],
            "mro": [-0.50, 0.50],
            "dfr": [-0.75, 0.00],
            "mlf": [0.25, 0.75],
            "press_release_url": ["", ""],
            "published_at": [None, None],
        }
    ).write_parquet(tmp / "gc_decisions.parquet")
    return SimulationConfig(
        arm="ea_only",
        memos_dir=d,
        out_root=tmp / "simulations",
        participants_path=tmp / "participants.parquet",
        ce_proposal_path=tmp / "ce_proposal.parquet",
        gc_decisions_path=tmp / "gc_decisions.parquet",
        n_rounds=2,
    )


def load_env_local() -> None:
    env = Path(".env.local")
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


async def main() -> int:
    load_env_local()
    tmp = Path("/tmp/sim_smoke")
    cfg = make_inputs(tmp)
    missing = cfg.inference.check_keys()
    if missing:
        print(f"missing API keys (put them in .env.local): {', '.join(missing)}", file=sys.stderr)
        return 2
    print(f"generator: {cfg.inference.role('generator').model_id}")
    print(f"extractor: {cfg.inference.role('extractor').model_id}")
    payload = await run_meeting("2022-07-21", cfg)

    print(f"settings block: {json.dumps(payload['settings'], default=str)[:400]}")

    print(f"CE proposal anchor: {payload['ce_proposal']['text'][:120]}...")
    for a in payload["agents"]:
        d = a["deltas"]
        if not d:
            print(f"\n== {a['person']} (Chief Economist) ==")
            print(f"  final: {a['final_statement'][:220]}...")
            continue
        print(f"\n== {a['person']} ({a['role']}) ==")
        print(f"  reaction : {a['initial_statement'][:220]}...")
        print(
            f"  pre-extr : {d['position_before']} probs={d['probabilities_before']} "
            f"conviction={d['conviction_before']}"
        )
        print(f"  final    : {a['final_statement'][:220]}...")
        print(
            f"  post-extr: {d['position_after']} probs={d['probabilities_after']} "
            f"conviction={d['conviction_after']}"
        )
        print(f"  changed={d['position_changed']} dConviction={d['conviction_delta']}")
        print(f"  alignment sim={a['alignment']['sim']} real={a['alignment']['real']}")

    print(
        "\nDecision:",
        payload["decision"]["stance"],
        "| voters' mode:",
        payload["decision"]["modal_stance_voters"],
    )
    print(
        "Mode (jev/deterministic):",
        payload["mode"]["jev_label"],
        "/",
        payload["mode"]["deterministic_label"],
    )
    print("Consensus:", payload["consensus"])
    print("Turns:", len(payload["turns"]))
    print("Archive:", cfg.out_root / "ea_only" / "2022-07-21")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
