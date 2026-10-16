"""Collector: country-level macro indicators for governor memos.

Sources (all key-based SDMX TSV queries, geo list in the key path; note CSV
format 406s and query-param filtering is silently ignored for TSV bulk — see
docs/data-sources/eurostat-country-macro.md):

- Headline + core HICP (monthly y/y): Eurostat prc_hicp_manr
  TOTAL (all-items) + TOT_X_NRG_FOOD (core: ex energy, food, alcohol &
  geo = EA (changing composition) + 20 EA countries, 1997-01 ->.
- Real GDP growth (quarterly y/y): OECD MEI edition vintages
  (oecd_gdp_vintages.py; true vintages 1999 -> for the 17 OECD-member EA
  countries) spliced with Eurostat namq_10_gdp CLV_PCH_SM SCA B1GQ
  latest-revised + release-calendar mask (CY/MT/HR always; the newest flash
  quarter elsewhere — see _gdp_oecd_splice).
- Unemployment rate: Eurostat ei_lm_m_vtg TRUE VINTAGES (revdate dimension,
  2021-01 ->) where available; une_rt_m latest-revised for earlier dates
  (no online vintages pre-2021).
- Industrial production y/y: Eurostat ei_is_m_vtg TRUE VINTAGES (s_adj=CA,
  unit=RT12 = 12-month rate of change, NACE B-D excl. construction,
  2021-01 ->) where available; sts_inpr_m PRD B-D CA PCH_SM latest-revised
  for earlier dates.

Release-lag model (Eurostat release calendar; doc has per-source detail):
- HICP flash ~end of reference month, final mid-next-month  -> month m-1 at m
- GDP QNA t+30/65d                                             -> quarter q-1 at q
- Unemployment monthly news release ~m+1                       -> month m-1 at m
- Industrial production first estimate ~t+40d                 -> month m-1/m-2
"""

from __future__ import annotations

import gzip
import io
import re
from dataclasses import dataclass

import httpx
import polars as pl

from synthetic_council.config import USER_AGENT

ESTAT_SDMX = "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data"

# Eurostat "EA" = euro area changing composition (EA19/EA20 are fixed rosters);
# consistent with ECB S0/U2 usage elsewhere in this project.
EA_GEOS = [
    "EA",
    "AT",
    "BE",
    "CY",
    "DE",
    "EE",
    "ES",
    "FI",
    "FR",
    "EL",
    "HR",
    "IE",
    "IT",
    "LT",
    "LU",
    "LV",
    "MT",
    "NL",
    "PT",
    "SI",
    "SK",
]


@dataclass
class CountryMacroResult:
    n_rows: int
    indicators: list[str]


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=600, follow_redirects=True)


def _get_tsv(client: httpx.Client, path: str) -> pl.DataFrame:
    """Key-based TSV query -> tidy long frame (dims parsed from the key col)."""
    r = client.get(f"{ESTAT_SDMX}/{path}", params={"format": "TSV"})
    r.raise_for_status()
    raw = r.content
    if raw[:2] == b"\x1f\x8b":  # gzip magic
        raw = gzip.decompress(raw)
    df = pl.read_csv(io.BytesIO(raw), separator="\t", infer_schema=False)
    if df.height == 0:
        return pl.DataFrame(schema={"geo": pl.String, "period": pl.String, "value": pl.Float64})
    key_col = df.columns[0]
    dim_names = [d.split("\\")[0] for d in key_col.split(",")]
    records = []
    for row_i, key_str in enumerate(df[key_col].to_list()):
        dims = dict(zip(dim_names, key_str.split(","), strict=False))
        for col in df.columns[1:]:
            v = df[col][row_i]
            v = str(v).strip() if v is not None else ""
            m = re.match(r"^(-?\d+(?:\.\d+)?)", v)
            if not m:
                continue
            records.append({**dims, "period": col.strip(), "value": float(m.group(1))})
    return pl.DataFrame(records).with_columns(pl.col("value").cast(pl.Float64))


def _month_idx(col: str) -> pl.Expr:
    return pl.col(col).str.slice(0, 4).cast(pl.Int32) * 12 + pl.col(col).str.slice(5, 2).cast(
        pl.Int32
    )


def _month_avail(col: str, days: int) -> pl.Expr:
    """Availability date of a monthly period: month end + `days`.

    HICP final mid next month (15), unemployment news release start of m+2 (32).
    """
    return (
        pl.col(col)
        .str.to_date("%Y-%m")
        .dt.offset_by("1mo")
        .dt.offset_by("-1d")
        .dt.offset_by(f"{days}d")
    )


def _quarter_avail(col: str, days: int) -> pl.Expr:
    """Availability date of a quarterly period: quarter end + `days`.

    QNA flash ~t+30d (31), so a quarter is visible only ~1 month after its end.
    """
    year = pl.col(col).str.slice(0, 4).cast(pl.Int32)
    q = pl.col(col).str.slice(6, 1).cast(pl.Int32)
    return (
        pl.date(year, q * 3 - 2, 1).dt.offset_by("1q").dt.offset_by("-1d").dt.offset_by(f"{days}d")
    )


def _asof_latest_published(
    series: pl.DataFrame,
    dates: pl.DataFrame,
    indicator: str,
    freq: str,
    avail_days: int,
) -> pl.DataFrame:
    """Latest published observation per (meeting date, geo) under a
    release-calendar availability model (avail_from <= meeting date).

    series: period (YYYY-MM or YYYY-Qq) | geo | value (latest-revised values).
    avail_days: days between period end and first publication.
    """
    avail = (
        _month_avail("period", avail_days) if freq == "M" else _quarter_avail("period", avail_days)
    )
    s = series.select("geo", "period", "value").with_columns(avail.alias("avail_from"))
    grid = dates.join(s.select("geo").unique(), how="cross").sort("announcement_date")
    return (
        grid.join_asof(
            s.sort("avail_from"),
            left_on="announcement_date",
            right_on="avail_from",
            by="geo",
            strategy="backward",
        )
        .filter(pl.col("value").is_not_null())
        .select(
            "announcement_date",
            pl.col("geo"),
            pl.lit(indicator).alias("indicator"),
            pl.col("period").alias("ref_period"),
            "value",
        )
    )


def fetch_country_hicp(client: httpx.Client) -> pl.DataFrame:
    """Country + EA headline and core HICP y/y (prc_hicp_minr).

    prc_hicp_minr M.RCH_A (annual rate of change): TOTAL = all-items headline;
    TOT_X_NRG_FOOD = core per the ECB definition (excluding energy, food,
    alcohol & tobacco — not the narrower Eurostat TOT_X_NRG_FOOD_NP which
    only excludes energy and unprocessed food).
    prc_hicp_minr replaced the discontinued prc_hicp_manr from 2026-01
    (prc_hicp_manr data ends 2025-12).
    """
    geos = "+".join(EA_GEOS)
    df = _get_tsv(client, f"prc_hicp_minr/M.RCH_A.TOTAL+TOT_X_NRG_FOOD.{geos}")
    ind_map = {"TOTAL": "hicp_yoy", "TOT_X_NRG_FOOD": "hicp_core"}
    return (
        df.with_columns(pl.col("coicop18").replace(ind_map, default=None).alias("indicator"))
        .filter(pl.col("indicator").is_not_null())
        .select("geo", "period", "value", "indicator")
    )


def fetch_country_gdp(client: httpx.Client) -> pl.DataFrame:
    """Country + EA real GDP growth y/y (namq_10_gdp CLV_PCH_SM SCA B1GQ)."""
    geos = "+".join(EA_GEOS)
    df = _get_tsv(client, f"namq_10_gdp/Q.CLV_PCH_SM.SCA.B1GQ.{geos}")
    return df.select(
        "geo",
        "period",
        pl.col("value").alias("value"),
        pl.lit("gdp_yoy").alias("indicator"),
    )


def _fetch_vtg(client: httpx.Client, dataset: str, keep: dict[str, str]) -> pl.DataFrame:
    """PEEI vintage dataset -> (revdate, geo, period, value) records.

    ``keep`` selects dimension values (e.g. s_adj/unit); rows whose geo is
    not in EA_GEOS are dropped. The vintage database stores only CHANGED
    series, so (revdate, period) cells are sparse by design — the as-of
    join carries the latest non-missing vintage forward.
    """
    r = client.get(f"{ESTAT_SDMX}/{dataset}", params={"format": "TSV", "compressed": "true"})
    r.raise_for_status()
    raw = gzip.decompress(r.content)
    df = pl.read_csv(io.BytesIO(raw), separator="\t", infer_schema=False)
    key_col = df.columns[0]
    dim_names = [d.split("\\")[0] for d in key_col.split(",")]
    records = []
    for row_i, key_str in enumerate(df[key_col].to_list()):
        dims = dict(zip(dim_names, key_str.split(","), strict=False))
        if any(dims.get(k) != v for k, v in keep.items()):
            continue
        if dims.get("geo") not in EA_GEOS:
            continue
        for col in df.columns[1:]:
            v = df[col][row_i]
            v = str(v).strip() if v is not None else ""
            m = re.match(r"^(-?\d+(?:\.\d+)?)", v)
            if not m:
                continue
            records.append(
                {
                    "revdate": dims["revdate"],
                    "geo": dims["geo"],
                    "period": col.strip(),
                    "value": float(m.group(1)),
                }
            )
    if not records:
        return pl.DataFrame(
            schema={
                "revdate": pl.String,
                "geo": pl.String,
                "period": pl.String,
                "value": pl.Float64,
            }
        )
    out = pl.DataFrame(records).with_columns(pl.col("value").cast(pl.Float64))
    return out.with_columns(pl.col("revdate").str.to_date("%Y-%m-%d"))


def fetch_country_unemp_vintages(client: httpx.Client) -> pl.DataFrame:
    """Unemployment true vintages (ei_lm_m_vtg): revdate | geo | period | value."""
    return _fetch_vtg(
        client,
        "ei_lm_m_vtg",
        {"unit": "PC_ACT", "s_adj": "SA", "sex": "T", "age": "TOTAL"},
    )


def fetch_country_ip_vintages(client: httpx.Client) -> pl.DataFrame:
    """Industrial production y/y true vintages (ei_is_m_vtg): the RT12 unit
    is the 12-month rate of change; CA (calendar-adjusted) is the only
    s_adj RT12 is published with. NACE B-D = industry excl. construction."""
    return _fetch_vtg(client, "ei_is_m_vtg", {"s_adj": "CA", "unit": "RT12", "nace_r2": "B-D"})


def fetch_country_ip_latest(client: httpx.Client) -> pl.DataFrame:
    """Industrial production y/y latest-revised (sts_inpr_m PRD B-D CA
    PCH_SM) — pre-2021 fallback (no online vintages before ei_is_m_vtg).

    The EA aggregate is disseminated as EA19 (pre-2020 composition) and EA20;
    both map to the panel's EA geo. Where the two series overlap in a period,
    EA20 wins (the composition the ECB's EA label refers to today)."""
    df = _get_tsv(client, "sts_inpr_m").filter(
        (pl.col("freq") == "M")
        & (pl.col("indic_bt") == "PRD")
        & (pl.col("nace_r2") == "B-D")
        & (pl.col("s_adj") == "CA")
        & (pl.col("unit") == "PCH_SM")
        & pl.col("geo").is_in([*EA_GEOS, "EA19", "EA20"])
    )
    ea20_periods = df.filter(pl.col("geo") == "EA20")["period"].unique()
    return (
        df.filter(~((pl.col("geo") == "EA19") & pl.col("period").is_in(ea20_periods)))
        .with_columns(
            pl.col("geo").replace_strict({"EA19": "EA", "EA20": "EA"}, default=pl.col("geo"))
        )
        .select("geo", "period", "value")
        .unique(subset=["geo", "period"], keep="first")
    )


def fetch_country_unemp_latest(client: httpx.Client) -> pl.DataFrame:
    """Unemployment latest-revised (une_rt_m bulk + client-side dim filter)."""
    df = _get_tsv(client, "une_rt_m")
    return df.filter(
        (pl.col("freq") == "M")
        & (pl.col("unit") == "PC_ACT")
        & (pl.col("s_adj") == "SA")
        & (pl.col("sex") == "T")
        & (pl.col("age") == "TOTAL")
        & (pl.col("geo").is_in(EA_GEOS))
    ).select("geo", "period", "value")


def _asof_vintage(vtg: pl.DataFrame, dates: pl.DataFrame, indicator: str) -> pl.DataFrame:
    """As-of values from true vintages.

    A vintage row (revdate, period) is visible at meeting d if revdate <= d;
    the vintage stores only CHANGES, so for each (geo, period) take the latest
    revdate <= d, then the latest published period.
    """
    vis = dates.join_where(vtg, pl.col("revdate") <= pl.col("announcement_date"))
    latest = vis.sort(
        ["announcement_date", "geo", "revdate", "period"], descending=[False, False, True, True]
    ).unique(subset=["announcement_date", "geo"], keep="first")
    return latest.select(
        "announcement_date",
        "geo",
        pl.lit(indicator).alias("indicator"),
        pl.col("period").alias("ref_period"),
        "value",
    )


# kept name for the unemployment tests/callers
def _asof_unemp_vintage(vtg: pl.DataFrame, dates: pl.DataFrame) -> pl.DataFrame:
    return _asof_vintage(vtg, dates, "unemp")


def _gdp_oecd_splice(oecd: pl.DataFrame, fallback_all: pl.DataFrame) -> pl.DataFrame:
    """Country GDP rows: OECD edition vintages, Eurostat path filling the gaps.

    The Eurostat latest-revised + release-calendar rows are used only where
    (a) no OECD edition is visible at the meeting (pre-1999-03 meetings, and
    CY/MT/HR which are not OECD members), or (b) the Eurostat flash quarter
    is strictly NEWER than the newest quarter in the visible OECD edition —
    OECD editions trail the national flash by ~1-2 months, and for a
    just-published quarter the latest-revised Eurostat value IS the first
    vintage (same splice policy as the EA RTD load lag in macro_panel.py).
    On equal ref_period the OECD vintage wins.
    """
    if oecd.height == 0:
        return fallback_all
    oecd = oecd.select("announcement_date", "geo", "indicator", "ref_period", "value")
    # concat + stable sort + keep-last implements the rule: the row with the
    # NEWEST ref_period survives, and on equal periods the OECD vintage row
    # (appended last; stable sort) wins over the latest-revised Eurostat row.
    # Same idiom as the EA RTD splice in macro_panel.py.
    return (
        pl.concat([fallback_all, oecd])
        .sort("ref_period")
        .unique(subset=["announcement_date", "geo", "indicator"], keep="last")
        .sort("announcement_date", "geo")
    )


def build_country_macro(meetings: pl.DataFrame) -> pl.DataFrame:
    """Long panel: announcement_date | geo | indicator | ref_period | value.

    Indicators: hicp_yoy, hicp_core, gdp_yoy, unemp and ip_yoy. True
    vintages: OECD MEI editions for GDP (1999+), PEEI for unemp/ip (2021+);
    else latest-revised with release-calendar lags.
    """
    from synthetic_council.collectors import oecd_gdp_vintages

    dates = meetings.select(pl.col("announcement_date")).unique().sort("announcement_date")
    with _client() as c:
        hicp = fetch_country_hicp(c)
        gdp = fetch_country_gdp(c)
        unemp_vtg = fetch_country_unemp_vintages(c)
        unemp_latest = fetch_country_unemp_latest(c)
        ip_vtg = fetch_country_ip_vintages(c)
        ip_latest = fetch_country_ip_latest(c)

    # OECD edition vintages (may be empty when the harvest has not run —
    # then gdp_yoy is latest-revised Eurostat + calendar mask everywhere)
    gdp_oecd = oecd_gdp_vintages.asof_gdp(dates).with_columns(
        pl.lit("gdp_yoy").alias("indicator")
    )

    out = [
        # HICP final released mid-next-month; use +17d to be safe for late
        # publishers and month-end meetings
        _asof_latest_published(
            hicp.filter(pl.col("indicator") == "hicp_yoy").drop("indicator"),
            dates,
            "hicp_yoy",
            freq="M",
            avail_days=17,
        ),
        _asof_latest_published(
            hicp.filter(pl.col("indicator") == "hicp_core").drop("indicator"),
            dates,
            "hicp_core",
            freq="M",
            avail_days=17,
        ),
        # GDP: OECD vintage value for the quarter as published in the newest
        # MEI edition visible at the meeting; QNA flash splice via
        # _gdp_oecd_splice when the Eurostat release-calendar quarter is newer
        _gdp_oecd_splice(
            gdp_oecd,
            _asof_latest_published(
                gdp.drop("indicator"),
                dates,
                "gdp_yoy",
                freq="Q",
                avail_days=31,
            ),
        ),
    ]

    # unemployment: true vintages where they exist (revdate <= d), else
    # latest-revised with the LFS monthly news release (~start of m+2)
    vint = _asof_vintage(unemp_vtg, dates, "unemp") if unemp_vtg.height else None
    fallback_all = _asof_latest_published(unemp_latest, dates, "unemp", freq="M", avail_days=32)
    if vint is not None and vint.height:
        fallback = fallback_all.join(
            vint.select("announcement_date", "geo"),
            on=["announcement_date", "geo"],
            how="anti",
        )
        out.append(pl.concat([vint, fallback]))
    else:
        out.append(fallback_all)

    # industrial production: true vintages where they exist, else
    # latest-revised with the STS first estimate (~t+40d after month end)
    ip_vint = _asof_vintage(ip_vtg, dates, "ip_yoy") if ip_vtg.height else None
    ip_fallback_all = _asof_latest_published(ip_latest, dates, "ip_yoy", freq="M", avail_days=40)
    if ip_vint is not None and ip_vint.height:
        ip_fallback = ip_fallback_all.join(
            ip_vint.select("announcement_date", "geo"),
            on=["announcement_date", "geo"],
            how="anti",
        )
        out.append(pl.concat([ip_vint, ip_fallback]))
    else:
        out.append(ip_fallback_all)

    panel = pl.concat(out)
    return panel.sort("announcement_date", "geo", "indicator")


def collect(meetings: pl.DataFrame) -> CountryMacroResult:
    from synthetic_council.config import PROCESSED_DIR

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    panel = build_country_macro(meetings)
    panel.write_parquet(PROCESSED_DIR / "country_macro_asof.parquet")
    return CountryMacroResult(
        n_rows=panel.height, indicators=sorted(panel["indicator"].unique().to_list())
    )


if __name__ == "__main__":
    m = pl.read_parquet(
        __import__("synthetic_council.config", fromlist=["PROCESSED_DIR"]).PROCESSED_DIR
        / "gc_decisions.parquet"
    )
    res = collect(m)
    print(res)
