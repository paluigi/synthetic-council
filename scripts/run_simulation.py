#!/usr/bin/env python
"""CLI: run the council simulation for meeting dates.

Usage:
    # explicit dates
    uv run python scripts/run_simulation.py 2022-07-21 2022-09-08 [--rounds 2]

    # every meeting that has memos (one command = full run)
    uv run python scripts/run_simulation.py --all

    # all meetings from a year onward (e.g. the true-vintage era)
    uv run python scripts/run_simulation.py --all --from 2015

Options: [--generator gateway|openai_compat] [--generator-model openai/gpt-4o-mini]
[--out data/processed/simulations] [--resume/--no-resume]

Resume: a meeting is skipped when its output JSON already exists; delete the
file to re-run that meeting. Enabled by default for --all, off for explicit
dates.

Requires AI_GATEWAY_API_KEY in .env.local (extractor, always Jev via the
Vercel AI Gateway). For --generator openai_compat also set
OPENAI_COMPAT_BASE_URL, OPENAI_COMPAT_API_KEY (optional) and
OPENAI_COMPAT_MODEL.

Note: memos are not committed to the repository (data/ stays untracked);
build them first with `uv run python -m synthetic_council.memo` (needs the
collected datasets) or point --memos-dir at an existing memos/ directory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from synthetic_council.simulation.config import SimulationConfig

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("dates", nargs="*", help="meeting dates, YYYY-MM-DD")
    p.add_argument(
        "--all",
        action="store_true",
        help="run every meeting found in --memos-dir (cheapest first: oldest date)",
    )
    p.add_argument(
        "--from",
        dest="from_date",
        default=None,
        help="with --all: only meetings on/after this date (YYYY-MM-DD)",
    )
    p.add_argument("--memos-dir", type=Path, default=None)
    p.add_argument("--rounds", type=int, default=2)
    p.add_argument(
        "--generator",
        choices=["gateway", "openai_compat"],
        default="gateway",
    )
    p.add_argument("--generator-model", default="openai/gpt-4o-mini")
    p.add_argument("--extractor-model", default="typesafe-ai/jev")
    p.add_argument("--out", type=Path, default=Path("data/processed/simulations"))
    p.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        default=None,
        help="skip meetings whose output JSON already exists (default: on for --all)",
    )
    p.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="re-run meetings even if their output JSON exists",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="validate config + memos, then exit without any API calls",
    )
    return p.parse_args(argv)


def load_env_local() -> None:
    if os.environ.get("AI_GATEWAY_API_KEY"):
        return
    env_local = Path(".env.local")
    if env_local.exists():
        for line in env_local.read_text().splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def resolve_dates(
    args: argparse.Namespace, memos_dir: Path
) -> tuple[list[str], list[str]]:
    """Return (dates_to_run, skipped_already_done)."""
    from synthetic_council.simulation.runner import meeting_memos

    if args.all:
        dates = sorted(
            {
                m.group("date")
                for path in memos_dir.glob("*.md")
                if (m := re.match(r"^(?P<date>\d{4}-\d{2}-\d{2})_", path.name))
            }
        )
        if args.from_date:
            dates = [d for d in dates if d >= args.from_date]
        if not dates:
            raise FileNotFoundError(f"no meeting memos found in {memos_dir}")
    else:
        dates = args.dates
        bad = [d for d in dates if not _DATE_RE.match(d)]
        if bad:
            raise ValueError(f"meeting dates must be YYYY-MM-DD, got {bad}")
        if not dates:
            raise SystemExit(
                "no dates given: pass explicit dates, --all, or --all --from YYYY-MM-DD"
            )

    resume = args.resume if args.resume is not None else args.all
    to_run: list[str] = []
    skipped: list[str] = []
    for d in dates:
        meeting_memos(memos_dir, d)  # validates memos exist
        out = args.out / f"simulation_{d}.json"
        (skipped if (resume and out.exists()) else to_run).append(d)
    return to_run, skipped


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    load_env_local()
    if not os.environ.get("AI_GATEWAY_API_KEY"):
        print("AI_GATEWAY_API_KEY is not set (put it in .env.local)", file=sys.stderr)
        return 2

    cfg = SimulationConfig(
        memos_dir=args.memos_dir if args.memos_dir else Path("data/processed/memos"),
        generator_backend=args.generator,
        generator_model=args.generator_model,
        extractor_model=args.extractor_model,
        n_rounds=args.rounds,
    )

    try:
        to_run, skipped = resolve_dates(args, cfg.memos_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if skipped:
        print(f"resume: {len(skipped)} meetings already done (skipped)")
    n_memos = {d: len(list(cfg.memos_dir.glob(f"{d}_*.md"))) for d in to_run[:3]}
    for d, n in n_memos.items():
        print(f"{d}: {n} member memos found")
    if len(to_run) > 3:
        print(f"... {len(to_run) - 3} more meetings queued")

    if args.dry_run:
        print(
            f"dry-run OK: {len(to_run)} meetings to run, {len(skipped)} skipped; "
            "no API calls made"
        )
        return 0

    from synthetic_council.simulation.runner import run_simulation

    written = asyncio.run(run_simulation(to_run, cfg, args.out))
    for path in written:
        payload = json.loads(path.read_text())
        cons = payload["consensus"]
        changed = [
            a["person"] for a in payload["agents"] if a["deltas"]["position_changed"]
        ]
        print(
            f"{payload['meeting_date']}: {payload['n_agents']} agents, "
            f"{payload['n_rounds']} rounds; balance {cons['before']} -> "
            f"{cons['after']}; position changes: {changed or 'none'}"
        )
        print(f"  -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
