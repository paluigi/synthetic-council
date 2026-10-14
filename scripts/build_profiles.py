#!/usr/bin/env python
"""Build GC member profiles: Tavily per-trait + Wikipedia + verified facts →
one reviewable markdown per member (data/processed/member_profiles/{Person}.md).

Usage:
    uv run python scripts/build_profiles.py [--only "Philip R. Lane"] [--rebuild] [--no-llm]

Review-safe: existing profile files are NOT overwritten unless --rebuild (or
--only names a member), so your manual edits survive re-runs. Raw evidence is
archived under data/raw/profiles/ and re-used across runs.

Requires TAVILY_API_KEY in .env.local for the Tavily step (missing key
degrades to Wikipedia + facts only). --no-llm assembles cards from verbatim
evidence without any chat-model call.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import polars as pl  # noqa: E402

from synthetic_council.config import RAW_DIR  # noqa: E402
from synthetic_council.member_profiles import synthesis, tavily  # noqa: E402


def load_env_local() -> None:
    if os.environ.get("TAVILY_API_KEY"):
        return
    env_local = Path(".env.local")
    if env_local.exists():
        for line in env_local.read_text().splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--only", action="append", default=None, help="rebuild only this member (repeatable)"
    )
    ap.add_argument("--rebuild", action="store_true", help="overwrite existing profile files")
    ap.add_argument("--no-llm", action="store_true", help="verbatim assembly, no chat model")
    args = ap.parse_args(argv)

    load_env_local()

    memberships = pl.read_parquet(RAW_DIR / "gc_memberships_wayback.parquet")
    facts = synthesis.member_facts(memberships)
    persons = sorted(facts)
    if args.only:
        persons = [p for p in persons if p in args.only]
        missing = [p for p in args.only if p not in facts]
        if missing:
            print(f"unknown members (not in memberships): {missing}", file=sys.stderr)
            return 1

    synthesis.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    print(f"{'member':40s} {'file':12s} traits-with-evidence")
    for person in persons:
        out = synthesis.PROFILES_DIR / f"{person}.md"
        if out.exists() and not args.rebuild and not args.only:
            ev_dir = RAW_DIR / "profiles" / "tavily" / tavily.slug(person)
            have = [t for t in tavily.TRAITS if (ev_dir / f"{t}.json").exists()] or ["(raw only)"]
            print(f"{person:40s} {'kept':12s} {', '.join(have)}")
            continue
        try:
            synthesis.build_profile(person, facts[person], use_llm=not args.no_llm)
            print(f"{person:40s} {'built':12s}")
        except Exception as exc:
            print(f"{person:40s} {'FAILED':12s} {exc}", file=sys.stderr)
    print(f"\nprofiles: {synthesis.PROFILES_DIR} — review them before running simulations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
