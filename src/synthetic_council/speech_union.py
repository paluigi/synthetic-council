"""Union speech corpus (ECB CSV + BIS hub + NCB websites + ECB blog) +
strict-window latest-speech join.

For each GC monetary policy meeting and each member attending it, the
member's latest speech delivered **after the previous monetary policy meeting
and on/before the current one** (window = ``(prev_gc, gc]``). A speech can
qualify for at most one meeting, which keeps the extracted stance sections
stable and reusable. When no speech falls in the window the member simply has
no speech section in that meeting's memo.

Roster source: ``account_participants`` (2015+, attendance as recorded in the
accounts) with a memberships fallback for pre-2015 meetings.

Output: data/processed/latest_speech_per_meeting.parquet (+ .csv without text)
"""

from __future__ import annotations

import polars as pl

from synthetic_council.collectors.persons import canonicalise
from synthetic_council.config import PROCESSED_DIR, RAW_DIR

OUT_PATH = PROCESSED_DIR / "latest_speech_per_meeting.parquet"
ECB_PATH = RAW_DIR / "speeches" / "speeches.parquet"
BIS_PATH = RAW_DIR / "speeches" / "bis_speeches.parquet"
NCB_WEB_PATH = RAW_DIR / "speeches" / "ncb_web_speeches.parquet"


def union_corpus() -> pl.DataFrame:
    """ECB CSV + BIS hub + NCB websites + ECB blog, normalised to one schema."""
    ecb = (
        pl.read_parquet(ECB_PATH)
        .with_columns(
            pl.col("speakers").str.split("|").list.first().alias("speaker"),
            pl.lit("ecb").alias("source"),
            pl.lit("").alias("url"),
        )
        .with_columns(
            pl.col("speaker").map_elements(canonicalise, return_dtype=pl.String),
            pl.col("date").cast(pl.String).str.to_date("%Y-%m-%d"),
        )
    )
    frames = [ecb.select("date", "speaker", "title", "contents", "source", "url")]
    for path, default_source in ((BIS_PATH, "bis"), (NCB_WEB_PATH, None)):
        if not path.exists():
            continue
        other = pl.read_parquet(path)
        if "source" not in other.columns:
            other = other.with_columns(pl.lit(default_source).alias("source"))
        other = other.with_columns(
            pl.col("speaker").map_elements(canonicalise, return_dtype=pl.String),
            pl.col("date").cast(pl.String).str.to_date("%Y-%m-%d"),
        )
        frames.append(other.select("date", "speaker", "title", "contents", "source", "url"))
    df = pl.concat(frames, how="vertical")
    return (
        df.filter(pl.col("contents").str.len_bytes() > 200)
        .unique(subset=["speaker", "date", "title"])
        .sort("date")
    )


def _roster(memberships: pl.DataFrame) -> pl.DataFrame:
    """(meeting_date, person) pairs: accounts participants when available,
    else Wayback memberships active at the meeting date (±45d grace)."""
    parts_path = PROCESSED_DIR / "account_participants.parquet"
    if parts_path.exists():
        return pl.read_parquet(parts_path).select(
            pl.col("meeting_date").cast(pl.String).alias("meeting_date"), "person"
        )
    df = memberships.with_columns(
        pl.col("start").str.to_date("%Y-%m-%d").alias("_start"),
        pl.col("end").str.to_date("%Y-%m-%d").alias("_end"),
    )
    rows = []
    meets = (
        pl.read_parquet(PROCESSED_DIR / "gc_decisions.parquet")
        .select(pl.col("announcement_date").cast(pl.String))
        .to_series()
        .to_list()
    )
    for d in meets:
        dd = pl.lit(d).str.to_date("%Y-%m-%d")
        active = df.filter(
            (pl.col("_start") - pl.duration(days=45) <= dd)
            & (pl.col("_end") + pl.duration(days=45) >= dd)
        )
        for p in active["person"].unique().to_list():
            rows.append({"meeting_date": d, "person": p})
    return pl.DataFrame(rows, schema={"meeting_date": pl.String, "person": pl.String})


def build(corpus: pl.DataFrame | None = None) -> pl.DataFrame:
    corpus = corpus if corpus is not None else union_corpus()
    meetings = (
        pl.read_parquet(PROCESSED_DIR / "gc_decisions.parquet")
        .select(pl.col("announcement_date").cast(pl.String).alias("meeting_date"))
        .sort("meeting_date")["meeting_date"]
        .to_list()
    )
    memberships = pl.read_parquet(RAW_DIR / "gc_memberships_wayback.parquet")
    roster = _roster(memberships)

    speech = corpus.select("speaker", "date", "title", "contents", "source", "url")
    rows = []
    for i, d in enumerate(meetings):
        prev = meetings[i - 1] if i else None
        win = speech.filter(
            (pl.col("date") <= pl.lit(d).str.to_date("%Y-%m-%d"))
            & (pl.col("date") > pl.lit(prev).str.to_date("%Y-%m-%d") if prev else pl.lit(True))
        )
        if not win.height:
            continue
        latest = win.sort("date", descending=True).unique(subset=["speaker"], keep="first")
        att = roster.filter(pl.col("meeting_date") == d)
        joined = att.join(latest, left_on="person", right_on="speaker", how="inner")
        for r in joined.iter_rows(named=True):
            rows.append(
                {
                    "meeting_date": d,
                    "person": r["person"],
                    "speech_date": str(r["date"]),
                    "title": r["title"],
                    "source": r["source"],
                    "url": r["url"],
                    "n_chars": len(r["contents"] or ""),
                    "contents": r["contents"],
                }
            )
    df = pl.DataFrame(rows).sort("meeting_date", "person")
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.write_parquet(OUT_PATH)
    df.drop("contents").write_csv(OUT_PATH.with_suffix(".csv"))
    return df


if __name__ == "__main__":
    df = build()
    print(
        f"latest-speech links: {df.height} over {df['meeting_date'].n_unique()} meetings; "
        f"sources={df.group_by('source').len().rows()}"
    )
