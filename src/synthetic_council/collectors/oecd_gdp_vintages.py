"""Collector: OECD MEI GDP vintages (STES revisions dataflow) — country memos.

Eurostat publishes no country-level GDP vintages (PEEI = EA/EU aggregates
only; ``ei_na_q_vtg`` likewise), so the country ``gdp_yoy`` rows previously
came from latest-revised Eurostat data + a release-calendar mask. The OECD's
Short-term Economic Statistics **revisions** dataflow closes that gap: it
re-disseminates, per MEI *edition* month (199902 →), the full then-published
history of quarterly GDP volume levels — a true archive of the OECD's
snapshot of the national data, one vintage per edition.

- Measure ``B1GQ_Q`` (GDP, volume), unit ``XDC`` (national currency, levels),
  activity ``_T``: quarterly *levels*; the y/y growth used by the panel is
  computed WITHIN an edition (level ratio vs the same quarter of the previous
  year in the same edition), never across editions. Each edition republishes
  the full history on a consistent base, so within-edition ratios are clean.
- Coverage: 17 of the 20 euro-area geos (every OECD member; CY/MT/HR are not
  OECD members and stay on the Eurostat latest-revised + calendar-mask path).
- Availability: an edition is visible at a meeting only from the 1st of the
  month AFTER its label month (editions publish within their label month;
  the +1 month rule is conservative — no look-ahead, at the cost of hiding a
  mid-month edition from a late-month meeting).
- OECD editions lag the national flash by ~1-2 months, so the Eurostat
  release-calendar path remains the splice for the newest quarter (see
  ``country_macro._gdp_oecd_splice``) — same policy as the EA RTD load lag.

Harvest shape: ONE wildcard request (trailing-dot key leaves EDITION free)
returns every edition for all 17 areas at once (~490k rows), cached to
``data/raw/oecd_gdp_vintages/all_editions.csv``; re-runs skip the download
when the cache exists.

API pitfalls (verified live 2026-10-16):
- The endpoint sits behind Cloudflare bot management with a rolling window
  quota: sustained per-edition requesting (even with 8-20 s pauses) degrades
  into long HTTP 429 cycles that also swallow curl. The single wildcard
  request is the polite path; retries use exponential backoff
  (45 s → ×1.6, ceiling 420 s).
- The response is SDMX-CSV v2 ONLY with the right Accept header; the first
  column is STRUCTURE (``DATAFLOW`` on data rows), 11 columns.
- Batched multi-area keys work (all 17 areas in the one request).
- Non-XDC units (GR/IX/PS/PT_*) 404 for this measure.
- The EDITION codelist pre-announces future months (2028xx); the wildcard
  only returns editions that have data, so no ceiling filter is needed.

Docs: ``docs/data-sources/oecd-stes-revisions.md``.
"""

from __future__ import annotations

import datetime as dt
import io
import time

import httpx
import polars as pl

from synthetic_council.config import PROCESSED_DIR, RAW_DIR, USER_AGENT

OECD_REST = "https://sdmx.oecd.org/public/rest"
DATAFLOW = "OECD.SDD.STES,DSD_STES_REVISIONS@DF_STES_REVISIONS,4.0"
CSV2_ACCEPT = "application/vnd.sdmx.data+csv; charset=utf-8; version=2; labels=id"

# OECD REF_AREA (ISO3) -> Eurostat geo (ISO2). GRC -> EL is the one trap.
OECD_TO_ESTAT = {
    "AUT": "AT",
    "BEL": "BE",
    "DEU": "DE",
    "ESP": "ES",
    "EST": "EE",
    "FIN": "FI",
    "FRA": "FR",
    "GRC": "EL",
    "IRL": "IE",
    "ITA": "IT",
    "LTU": "LT",
    "LUX": "LU",
    "LVA": "LV",
    "NLD": "NL",
    "PRT": "PT",
    "SVK": "SK",
    "SVN": "SI",
}

RAW_ALL_CSV = RAW_DIR / "oecd_gdp_vintages" / "all_editions.csv"
PROCESSED_PARQUET = PROCESSED_DIR / "oecd_gdp_vintages.parquet"

# rate-limit handling (verified live; see module docstring)
BACKOFF_START = 45.0
BACKOFF_FACTOR = 1.6
BACKOFF_CEILING = 420.0
BACKOFF_MAX_ATTEMPTS = 12


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=300, follow_redirects=True)


def edition_to_avail(edition: str) -> dt.date:
    """Edition YYYYMM -> first day of the FOLLOWING month.

    The edition is published within its label month; using the 1st of the
    next month is conservative (never lets a meeting see an edition that may
    not have been out yet).
    """
    y, m = int(edition[:4]), int(edition[4:6])
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return dt.date(y, m, 1)


def fetch_all_editions(client: httpx.Client) -> pl.DataFrame:
    """Every edition, every area, one request: geo | edition | period | level.

    The trailing dot on the key leaves EDITION free (wildcard). Raises only
    after the backoff budget is exhausted.
    """
    areas = "+".join(OECD_TO_ESTAT)
    url = f"{OECD_REST}/data/{DATAFLOW}/{areas}.Q.B1GQ_Q.XDC._T."
    backoff = BACKOFF_START
    for attempt in range(BACKOFF_MAX_ATTEMPTS):
        r = client.get(url, params={"detail": "dataonly"}, headers={"Accept": CSV2_ACCEPT})
        if r.status_code in (429, 500, 502, 503, 504):
            if attempt == BACKOFF_MAX_ATTEMPTS - 1:
                r.raise_for_status()
            print(f"  wildcard harvest: HTTP {r.status_code}, backoff {backoff:.0f}s", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * BACKOFF_FACTOR, BACKOFF_CEILING)
            continue
        r.raise_for_status()
        df = pl.read_csv(io.BytesIO(r.content), infer_schema=False)
        return (
            df.select(
                pl.col("REF_AREA").replace_strict(OECD_TO_ESTAT, default=None).alias("geo"),
                pl.col("EDITION").cast(pl.String).alias("edition"),
                pl.col("TIME_PERIOD").alias("period"),
                pl.col("OBS_VALUE").cast(pl.Float64, strict=False).alias("level"),
            )
            .filter(pl.col("geo").is_not_null() & pl.col("level").is_not_null())
            .unique(subset=["edition", "geo", "period"], keep="first")
        )
    raise RuntimeError("unreachable: backoff loop exhausted")


def download_all() -> None:
    """Fetch the wildcard archive to the raw cache (skipped when present)."""
    RAW_ALL_CSV.parent.mkdir(parents=True, exist_ok=True)
    if RAW_ALL_CSV.exists():
        print(f"raw cache present: {RAW_ALL_CSV}")
        return
    df = fetch_all_editions(_client())
    df.write_csv(RAW_ALL_CSV)
    print(f"wildcard harvest: {df.height} rows over {df['edition'].n_unique()} editions")


def _quarter_idx(col: str) -> pl.Expr:
    """'2022-Q1' -> absolute quarter index (year*4 + q - 1)."""
    return pl.col(col).str.slice(0, 4).cast(pl.Int32) * 4 + pl.col(col).str.slice(6, 1).cast(
        pl.Int32
    ) - 1


def build_vintages() -> pl.DataFrame:
    """Raw edition snapshots -> within-edition y/y vintage panel.

    Output: edition | avail_from (date) | geo | period | value (y/y %),
    one row per (edition, geo, quarter) whose year-earlier quarter exists in
    the SAME edition (levels never leave their edition — cross-edition ratios
    would compare different bases/revisions).
    """
    if not RAW_ALL_CSV.exists():
        raise FileNotFoundError(
            f"{RAW_ALL_CSV} missing - run python -m synthetic_council.collectors.oecd_gdp_vintages"
        )
    levels = pl.read_csv(RAW_ALL_CSV, schema_overrides={"edition": pl.String}).with_columns(
        _quarter_idx("period").alias("qi"),
        pl.date(
            pl.col("edition").str.slice(0, 4).cast(pl.Int32),
            pl.col("edition").str.slice(4, 2).cast(pl.Int32),
            1,
        )
        .dt.offset_by("1mo")
        .alias("avail_from"),
    )
    prev = levels.select("edition", "geo", "qi", pl.col("level").alias("level_prev"))
    yoy = (
        levels.with_columns((pl.col("qi") - 4).alias("qi_prev"))
        .join(prev, left_on=["edition", "geo", "qi_prev"], right_on=["edition", "geo", "qi"])
        .filter(pl.col("level_prev") > 0)
        .with_columns(((pl.col("level") / pl.col("level_prev") - 1) * 100).alias("value"))
        .select("edition", "avail_from", "geo", "period", "value")
        .unique(subset=["edition", "geo", "period"], keep="first")
    )
    return yoy.sort("edition", "geo", "period")


def load_vintages() -> pl.DataFrame | None:
    if PROCESSED_PARQUET.exists():
        return pl.read_parquet(PROCESSED_PARQUET)
    return None


def asof_gdp(dates: pl.DataFrame, vtg: pl.DataFrame | None = None) -> pl.DataFrame:
    """Per (meeting date, geo): the newest quarter's y/y as seen in the newest
    MEI edition published (conservatively) before the meeting.

    Returns the panel-shaped rows WITHOUT the indicator column (the caller
    adds ``gdp_yoy``); empty when no vintage data is available.
    """
    if vtg is None:
        vtg = load_vintages()
    if vtg is None or vtg.height == 0:
        print(
            "NOTE: OECD GDP vintages not found - country gdp_yoy falls back to "
            "latest-revised Eurostat + release calendar. Run: "
            "uv run python -m synthetic_council.collectors.oecd_gdp_vintages"
        )
        return pl.DataFrame(
            schema={"announcement_date": pl.Date, "geo": pl.String,
                    "ref_period": pl.String, "value": pl.Float64}
        )
    ed_meta = vtg.select("edition", "avail_from").unique()
    latest_q = (
        vtg.group_by("edition", "geo")
        .agg(pl.col("period").max().alias("ref_period"))
        .join(ed_meta, on="edition")
        .select("geo", "avail_from", "ref_period")
        .sort("avail_from")
    )
    grid = dates.join(latest_q.select("geo").unique(), how="cross").sort("announcement_date")
    visible = (
        grid.join_asof(
            latest_q.sort("avail_from"),
            left_on="announcement_date",
            right_on="avail_from",
            by="geo",
            strategy="backward",
        )
        .filter(pl.col("ref_period").is_not_null())
    )
    vals = vtg.select("geo", "avail_from", "period", "value")
    return visible.join(
        vals,
        left_on=["geo", "avail_from", "ref_period"],
        right_on=["geo", "avail_from", "period"],
        how="inner",
    ).select("announcement_date", "geo", "ref_period", "value")


def verification(vtg: pl.DataFrame) -> None:
    by_geo = vtg.group_by("geo").agg(
        pl.col("period").min().alias("first_q"), pl.col("period").max().alias("last_q")
    )
    print(f"editions: {vtg['edition'].n_unique()}")
    print(f"rows: {vtg.height}")
    for r in sorted(by_geo.iter_rows(named=True), key=lambda r: r["geo"]):
        print(f"  {r['geo']}: {r['first_q']}..{r['last_q']}")
    suspicious = vtg.filter(pl.col("value").abs() > 25)
    print(f"rows with |y/y| > 25% (rebase-break canaries): {suspicious.height}")
    if suspicious.height:
        print(suspicious.group_by("geo").len().sort("geo"))


def collect() -> pl.DataFrame:
    download_all()
    vtg = build_vintages()
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    vtg.write_parquet(PROCESSED_PARQUET)
    verification(vtg)
    return vtg


if __name__ == "__main__":
    collect()
