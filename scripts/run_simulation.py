#!/usr/bin/env python
"""CLI: run the council simulation for meeting dates (one experiment arm).

Usage:
    # explicit dates
    uv run python scripts/run_simulation.py 2022-07-21 2022-09-08 [--rounds 2]

    # every meeting that has memos + a CE proposal anchor (full run)
    uv run python scripts/run_simulation.py --all --arm ea_only
    uv run python scripts/run_simulation.py --all --arm country

    # all meetings from a year onward (e.g. the accounts era)
    uv run python scripts/run_simulation.py --all --from 2015

Options: [--arm ea_only|country] [--generator gateway|openai_compat]
[--generator-model openai/gpt-4o-mini] [--out data/processed/simulations]
[--resume/--no-resume]

Arms: ``ea_only`` gives every member the euro-area-only memo
(data/processed/memos_ea); ``country`` additionally gives national governors
their country annex (data/processed/memos_ea_country). Outputs land under
{out}/{arm}/{date}/ (payload.json, messages.jsonl, transcript.md).

Resume: a meeting is skipped when its payload.json already exists; delete
the directory to re-run. Enabled by default for --all, off for explicit
dates.

Requires AI_GATEWAY_API_KEY in .env.local (extractor, always Jev via the
Vercel AI Gateway; also the CE-proposal/decision-mode LLM fallbacks). For
--generator openai_compat also set OPENAI_COMPAT_BASE_URL,
OPENAI_COMPAT_API_KEY (optional) and OPENAI_COMPAT_MODEL.

Note: memos are not committed to the repository (data/ stays untracked);
build them first with `uv run python -m synthetic_council.memo` (needs the
collected datasets) — it writes both arm variants.
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
        "--arm",
        choices=["ea_only", "country"],
        default="ea_only",
        help="memo variant: euro-area only, or + country annex for governors",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="run every meeting with memos and a CE proposal anchor (oldest first)",
    )
    p.add_argument(
        "--from",
        dest="from_date",
        default=None,
        help="with --all: only meetings on/after this date (YYYY-MM-DD)",
    )
    p.add_argument("--memos-dir", type=Path, default=None, help="override the arm's memo dir")
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
        help="skip meetings whose payload.json already exists (default: on for --all)",
    )
    p.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="re-run meetings even if their payload.json exists",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="validate config + inputs, then exit without any API calls",
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


def resolve_dates(args: argparse.Namespace, cfg: SimulationConfig) -> tuple[list[str], list[str]]:
    """Return (dates_to_run, skipped_already_done)."""
    import polars as pl

    from synthetic_council.simulation.runner import load_ce_proposal

    if args.all:
        memos_dir = cfg.resolved_memos_dir()
        dates = sorted(
            {
                m.group("date")
                for path in memos_dir.glob("*.md")
                if (m := re.match(r"^(?P<date>\d{4}-\d{2}-\d{2})_", path.name))
            }
        )
        props = pl.read_parquet(cfg.ce_proposal_path).with_columns(
            pl.col("meeting_date").cast(pl.String)
        )
        anchored = set(
            props.filter(pl.col("proposal_text").str.len_chars() > 0)["meeting_date"].to_list()
        )
        dates = [d for d in dates if d in anchored]
        if args.from_date:
            dates = [d for d in dates if d >= args.from_date]
        if not dates:
            raise FileNotFoundError(
                f"no anchored meetings found in {memos_dir} (need memos + CE proposals)"
            )
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
        load_ce_proposal(d, cfg)  # validates the anchor exists
        done = (args.out / args.arm / d / "payload.json").exists()
        (skipped if (resume and done) else to_run).append(d)
    return to_run, skipped


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    load_env_local()
    if not os.environ.get("AI_GATEWAY_API_KEY") and not args.dry_run:
        print("AI_GATEWAY_API_KEY is not set (put it in .env.local)", file=sys.stderr)
        return 2

    cfg = SimulationConfig(
        arm=args.arm,
        memos_dir=args.memos_dir,
        out_root=args.out,
        generator_backend=args.generator,
        generator_model=args.generator_model,
        extractor_model=args.extractor_model,
        n_rounds=args.rounds,
    )

    try:
        to_run, skipped = resolve_dates(args, cfg)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if skipped:
        print(f"resume: {len(skipped)} meetings already done (skipped)")
    print(f"arm={cfg.arm} memos={cfg.resolved_memos_dir()}")
    for d in to_run[:3]:
        n = len(list(cfg.resolved_memos_dir().glob(f"{d}_*.md")))
        print(f"{d}: {n} member memos found")
    if len(to_run) > 3:
        print(f"... {len(to_run) - 3} more meetings queued")

    if args.dry_run:
        print(
            f"dry-run OK: {len(to_run)} meetings to run, {len(skipped)} skipped; no API calls made"
        )
        return 0

    from synthetic_council.simulation.runner import run_simulation

    written = asyncio.run(run_simulation(to_run, cfg))
    for path in written:
        payload = json.loads(path.read_text())
        cons = payload["consensus"]
        mode = payload["mode"]
        dec = payload["decision"]
        changed = [
            a["person"] for a in payload["agents"] if (a["deltas"] or {}).get("position_changed")
        ]
        print(
            f"{payload['meeting_date']} [{payload['arm']}]: {payload['n_agents']} agents; "
            f"stance {cons['majority_before']} -> {cons['majority_after']}; "
            f"decision={dec['stance']} (voters' mode: {dec['modal_stance_voters']}); "
            f"mode={mode['jev_label']}/{mode['deterministic_label']}; "
            f"position changes: {changed or 'none'}"
        )
        print(f"  -> {path.parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
