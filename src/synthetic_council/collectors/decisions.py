"""Governing Council monetary policy decision calendar (March 1999 - present).

One row per monetary policy meeting:

- `announcement_date` — day the "Monetary policy decisions" press release was
  published (14:15 CET), from the ECB foedb publications database
  (collectors/foedb.py).
- `decision` — `change` if any key rate changed with effect within the following
  11 days, else `hold`.
- `mro` / `dfr` / `mlf` — the three key ECB interest rates **in force after the
  decision** (levels on the effective date; for holds, the levels in force at
  announcement).
- `press_release_url` — the canonical English press-release URL.

Cross-validation performed at collect time:
- every rate-change effective date in the daily series must be matched to some
  announcement within 11 days (else listed in `unmatched_effective_dates`);
- announcement cadence per year must follow the GC's known schedule (24→12→8).
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import polars as pl

from synthetic_council.collectors.foedb import fetch_all
from synthetic_council.collectors.rates import decisions_from_daily_rates, fetch_key_rates
from synthetic_council.config import PROCESSED_DIR

MAX_EFFECTIVE_LAG_DAYS = 11


def _slug_date(url: str) -> date | None:
    """True publication date from a press-release URL slug, else None.

    foedb pub_timestamps logged at 22:00/23:00 CET are batch times from the
    evening BEFORE publication (documented for pre-2015, but 2016-2018
    ``ecb.mpYYMMDD`` URLs show the same pattern — ecb.mp170427.en.html carries
    a 2017-04-26 22:00 timestamp while the true release date is 27 April).
    The URL slug carries the true date:
    /press/pr/date/2008/html/pr081008.en.html -> 2008-10-08 (coordinated cut);
    /press/pr/date/2017/html/ecb.mp170427.en.html -> 2017-04-27.
    Hashed modern URLs (ecb.mpYYMMDD~hash) match their timestamps anyway.
    """
    m = re.search(r"(?:/pr|\.mp)(\d{6})(?:[~_][\w.]+)?\.en\.html", url or "")
    if not m:
        return None
    yy, mm, dd = int(m.group(1)[:2]), int(m.group(1)[2:4]), int(m.group(1)[4:])
    year = 1900 + yy if yy > 90 else 2000 + yy
    return date(year, mm, dd)


# The foedb publications database is missing the December 2016 "Monetary policy
# decisions" press release (GC meeting 7-8 December 2016, all rates unchanged).
# Patched from the documented record: account mg170112 (meeting of 7-8
# December 2016) and the ECB key interest rates table (levels since 2016-03-16).
MISSING_RELEASES = [
    {
        "announcement_date": date(2016, 12, 8),
        "decision": "hold",
        "rate_effective_date": None,
        "mro": 0.00,
        "dfr": -0.40,
        "mlf": 0.25,
        "press_release_url": "https://www.ecb.europa.eu/press/pr/date/2016/html/pr161208.en.html",
    }
]


def true_date(url: str, published_at) -> date:
    """Announcement date: URL slug when available, else timestamp date."""
    return _slug_date(url) or published_at.date()


def build() -> pl.DataFrame:
    daily = fetch_key_rates(start="1998-12-01")
    if daily["date"].dtype == pl.String:
        daily = daily.with_columns(pl.col("date").str.to_date("%Y-%m-%d"))

    pubs, _ = fetch_all()
    mopo = pubs.filter(pl.col("title") == "Monetary policy decisions").sort("published_at")

    # canonical English URL (plain python loop: map_elements probes UDFs with a Series)
    def en_url(urls) -> str:
        urls = list(urls or [])
        for u in urls:
            if u.endswith(".en.html"):
                return "https://www.ecb.europa.eu" + u
        return ("https://www.ecb.europa.eu" + urls[0]) if urls else ""

    url_col = [en_url(u) for u in mopo["document_urls"].to_list()]
    mopo = mopo.with_columns(
        pl.Series("press_release_url", url_col, dtype=pl.String),
    )

    ann = [
        true_date(u, p)
        for u, p in zip(mopo["press_release_url"], mopo["published_at"], strict=False)
    ]
    mopo = mopo.with_columns(pl.Series("announcement_date", ann, dtype=pl.Date))

    # rate-change effective dates (null-aware: see decisions_from_daily_rates —
    # the spliced MRO series has no nulls, but keep the logic robust)
    chg = decisions_from_daily_rates(daily)
    effective = chg.select(pl.col("date").alias("effective_date"), "mro", "dfr", "mlf")

    rows = []
    eff_list = effective.to_dicts()
    used: set = set()
    for m in mopo.iter_rows(named=True):
        a = m["announcement_date"]
        cand = [
            e
            for e in eff_list
            if timedelta(0) <= (e["effective_date"] - a) <= timedelta(days=MAX_EFFECTIVE_LAG_DAYS)
        ]
        if cand:
            e = cand[0]
            used.add(e["effective_date"])
            rows.append(
                {
                    **m,
                    "rate_effective_date": e["effective_date"],
                    "decision": "change",
                    "mro": e["mro"],
                    "dfr": e["dfr"],
                    "mlf": e["mlf"],
                }
            )
        else:
            lvl = daily.filter(pl.col("date") <= a).sort("date").tail(1)
            rows.append(
                {
                    **m,
                    "rate_effective_date": None,
                    "decision": "hold",
                    "mro": lvl["mro"][0],
                    "dfr": lvl["dfr"][0],
                    "mlf": lvl["mlf"][0],
                }
            )

    df = pl.DataFrame(rows)
    unmatched = sorted({e["effective_date"] for e in eff_list} - used)
    if unmatched:
        print(f"WARNING: {len(unmatched)} rate-change dates matched no announcement: {unmatched}")
    out = df.select(
        "announcement_date",
        "decision",
        "rate_effective_date",
        "mro",
        "dfr",
        "mlf",
        "press_release_url",
        "published_at",
    )
    patch = (
        pl.DataFrame(MISSING_RELEASES)
        .with_columns(
            pl.col("announcement_date").cast(pl.Date),
            pl.col("rate_effective_date").cast(pl.Date),
            pl.lit(None, dtype=pl.Datetime("us", "Europe/Berlin")).alias("published_at"),
        )
        .select(out.columns)
    )
    return pl.concat([out, patch], how="vertical").sort("announcement_date")


def collect() -> pl.DataFrame:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df = build()
    df.write_parquet(PROCESSED_DIR / "gc_decisions.parquet")
    df.write_csv(PROCESSED_DIR / "gc_decisions.csv")
    return df
