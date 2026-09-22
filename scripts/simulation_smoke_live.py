#!/usr/bin/env python
"""Live smoke test: 3-agent mini-council on one synthetic memo.

Verifies the full pipeline against the real gateway: opening statements
(gateway generator) -> Jev extraction -> discussion round 2 -> final
statements -> Jev extraction -> deltas. Not part of the test suite (needs
AI_GATEWAY_API_KEY); run manually:

    uv run python scripts/simulation_smoke_live.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

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

## Your most recent speech before the meeting

No ECB-website speech on record in the 120 days before the meeting.
"""

AGENTS = [
    ("Christine Lagarde", "ECB President", ""),
    ("Klaus Liesman", "Governor, Bundesbank", "**Country:** Germany\n"),
    ("Pierluigi Testoni", "Governor, Banca d'Italia", "**Country:** Italy\n"),
]


def make_memos(tmp: Path) -> Path:
    d = tmp / "memos"
    d.mkdir(parents=True, exist_ok=True)
    for person, role, country_line in AGENTS:
        (d / f"2022-07-21_{person.replace(' ', '_')}.md").write_text(
            MEMO.format(person=person, role=role, country_line=country_line),
            encoding="utf-8",
        )
    return d


def load_env_local() -> None:
    env = Path(".env.local")
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


async def main() -> int:
    load_env_local()
    if not os.environ.get("AI_GATEWAY_API_KEY"):
        print("AI_GATEWAY_API_KEY not set", file=sys.stderr)
        return 2

    cfg = SimulationConfig(memos_dir=make_memos(Path("/tmp/sim_smoke")), n_rounds=2)
    payload = await run_meeting("2022-07-21", cfg)

    for a in payload["agents"]:
        d = a["deltas"]
        print(f"\n== {a['person']} ({a['role']}) ==")
        print(f"  opening  : {a['initial_statement'][:220]}...")
        print(f"  pre-extr : {d['position_before']} probs={d['probabilities_before']} "
              f"conviction={d['conviction_before']}")
        print(f"  final    : {a['final_statement'][:220]}...")
        print(f"  post-extr: {d['position_after']} probs={d['probabilities_after']} "
              f"conviction={d['conviction_after']}")
        print(f"  changed={d['position_changed']} dConviction={d['conviction_delta']}")

    print("\nConsensus:", payload["consensus"])
    print("Turns:", len(payload["turns"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
