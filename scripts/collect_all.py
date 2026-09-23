"""End-to-end data collection pipeline.

Runs every collector in dependency order and writes all datasets under data/.
Usage: uv run python scripts/collect_all.py [--members-only|--skip-members]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import polars as pl  # noqa: E402

from synthetic_council.collectors import foedb, projections  # noqa: E402
from synthetic_council.config import PROCESSED_DIR, RAW_DIR  # noqa: E402


def step(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def main() -> None:
    skip_members = "--skip-members" in sys.argv

    step("1/9 foedb publications database (all ECB press releases since 1992)")
    res = foedb.collect()
    print(
        f"records={res.n_records} mopo={res.mopo_releases} "
        f"span={res.first_decision}..{res.last_decision}"
    )

    step("2/9 speeches: ECB CSV + BIS hub + NCB websites + ECB blog + CBS")
    from synthetic_council.collectors import cbs_speeches, ncb_speeches, ncb_websites, speeches

    sp = speeches.collect()
    print(sp)
    print(ncb_speeches.collect())
    bis_df = ncb_speeches.collect_from_sitemap()
    print(
        f"bis speeches={bis_df.height} "
        f"persons={bis_df['speaker'].n_unique() if bis_df.height else 0}"
    )
    ncb_df = ncb_websites.collect()
    print(
        f"ncb-web speeches={ncb_df.height} "
        f"persons={ncb_df['speaker'].n_unique() if ncb_df.height else 0}"
    )
    cbs_df = cbs_speeches.build()
    print(
        f"cbs speeches={cbs_df.height} persons={cbs_df['speaker'].n_unique()} "
        f"translated={cbs_df['translated'].sum()}"
    )

    step("3/9 memberships: Wayback GC-members page snapshots")
    if not skip_members:
        from synthetic_council.collectors import members

        snaps = members.wayback_snapshots(1999, 2026)
        print(f"snapshots: {len(snaps)}")
        rows = members.build_memberships(snaps)
        df = pl.DataFrame([r.__dict__ for r in rows])
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        df.write_parquet(RAW_DIR / "gc_memberships_wayback.parquet")
        df.write_csv(PROCESSED_DIR / "gc_memberships.csv")
        print(f"tenures={df.height} persons={df['person'].n_unique()}")
    else:
        print("skipped")

    step("4/9 decision calendar + rates")
    from synthetic_council.collectors import decisions

    dec = decisions.collect()
    print(
        f"meetings={dec.height} "
        f"span={dec['announcement_date'].min()}..{dec['announcement_date'].max()}"
    )

    step("5/9 monetary policy accounts (2015 →, markdown + index)")
    from synthetic_council.collectors import accounts

    acc = accounts.collect()
    print(
        f"accounts={acc.n_accounts} rate_meetings={acc.n_rate_meetings} "
        f"span={acc.first_meeting}..{acc.last_meeting} "
        f"date_mismatches={acc.n_date_mismatches}"
    )

    step("6/9 macro as-of panel (RTD/PEEI vintages, CISS, sentiment)")
    from synthetic_council.collectors import macro_panel

    panel = macro_panel.collect(dec)
    print(f"panel rows={panel.height}")

    step("6b/9 accounts extraction: participants + CE proposals (deterministic)")
    from synthetic_council.mpa_extract import ce_proposal, participants

    print(participants.extract())
    ce_proposal.extract()  # LLM fallback engages only if a key is configured

    step("6c/9 strict-window latest-speech join (ECB + BIS union)")
    from synthetic_council import speech_union

    sj = speech_union.build()
    print(f"latest-speech links={sj.height} over {sj['meeting_date'].n_unique()} meetings")

    step("7/9 staff projections (MPD)")
    proj = projections.collect()
    print(proj)

    step("8/9 memos — both experiment arms (EA-only / EA + country annex)")
    from synthetic_council import memo

    memberships = pl.read_parquet(RAW_DIR / "gc_memberships_wayback.parquet")
    for annex, name in ((False, "memos_ea"), (True, "memos_ea_country")):
        res = memo.build_memos(
            dec,
            memberships,
            panel,
            pl.read_parquet(RAW_DIR / "mpd_projections.parquet"),
            Path(f"data/processed/{name}"),
            country_annex=annex,
        )
        print(res)
    print("\nAll keyless datasets written under data/. Done.")
    print("LLM-dependent steps (run separately; need keys in .env.local):")
    print("  uv run python -m synthetic_council.mpa_extract.decision_mode")
    print("  uv run python scripts/build_profiles.py")
    print("  uv run python -m synthetic_council.member_profiles.speech_sections")


if __name__ == "__main__":
    main()
