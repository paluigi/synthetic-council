# OECD STES revisions — country GDP vintages (MEI editions)

**Purpose**: true GDP vintages for the country rows of `macro_asof_panel`
(the `gdp_yoy` indicator). Eurostat publishes no country-level GDP vintages
(PEEI `ei_na_q_vtg` is EA/EU aggregates only), so country GDP was previously
latest-revised Eurostat + a release-calendar mask. The OECD's Short-term
Economic Statistics **revisions** dataflow closes that gap with an archive of
every MEI *edition* since 1999-02: each edition carries the full
then-published history of quarterly GDP volume levels, as re-disseminated by
the OECD from the national data of the time.

Module: `src/synthetic_council/collectors/oecd_gdp_vintages.py`

## Endpoint

| item | value |
|---|---|
| provider | OECD SDMX 2.1 REST, no key required |
| dataflow | `OECD.SDD.STES,DSD_STES_REVISIONS@DF_STES_REVISIONS,4.0` |
| base URL | `https://sdmx.oecd.org/public/rest/data/` |
| key | `{REF_AREAS}.Q.B1GQ_Q.XDC._T.` — all 17 areas batched, trailing dot = EDITION wildcard → **one request for the whole archive** |
| dimensions | `REF_AREA.FREQ.MEASURE.UNIT_MEASURE.ACTIVITY.EDITION` |
| measure | `B1GQ_Q` = GDP, volume; unit `XDC` = national currency **levels** (`GR/IX/PS/PT_*` 404 for this measure) |
| params | `?detail=dataonly` + `Accept: application/vnd.sdmx.data+csv; charset=utf-8; version=2; labels=id` |
| harvest | 431k raw rows, 333 editions 199902 → 202610 (collected 2026-10-16) → 411,776 within-edition y/y rows |
| coverage | 17 of the 20 euro-area geos: every OECD member (all but CY, MT, HR); first y/y quarter per geo: FR 1950-Q1, EL/ES/IT 1971-Q1, FI 1976-Q1, NL 1978-Q1, BE 1981-Q1, PT 1989-Q1, SK 1991-Q1, DE 1992-Q1, AT 1965-Q1, EE/IE/LT/LU/LV/SI 1996-Q1 |

## Vintage semantics

- **Edition, not revision-date**: the vintage unit is the MEI edition
  (YYYYMM). Each edition request returns the full published history of
  quarterly GDP volume levels *as of that edition* (~110-190 quarters per
  country), so y/y growth is computed **within an edition** (level ratio vs
  the same quarter of the previous year in the same edition) — never across
  editions, which would compare different bases/revisions.
- **As-of mapping**: an edition is visible at a meeting only from the 1st of
  the month AFTER its label month (editions publish within their label
  month). Conservative by design: a mid-month edition is hidden from a
  late-month meeting rather than risk look-ahead. At meeting *d* the panel
  shows the newest quarter of the newest visible edition.
- **What the vintage is**: a true vintage of the OECD's re-dissemination —
  the OECD's snapshot of the then-current national data, typically the first
  or second national release. One rung below a genuine first-release archive
  in the project's vintage hierarchy, and vastly better than latest-revised.
  Verified: edition 202204 gives 2021-Q4 y/y DE +1.8, FR +5.4, IT +6.2,
  ES +5.5 — the national releases current in spring 2022, not today's
  revised Eurostat values.
- **Lag vs the national flash**: OECD editions trail the flash by ~1-2
  months (edition 202204 does not yet contain 2022-Q1; edition 202608 →
  2026-Q2). The Eurostat release-calendar path (flash ≈ quarter end + 31d)
  therefore becomes visible EARLIER for the newest quarter; see the splice
  below.

## Splice with the Eurostat release-calendar path

`country_macro._gdp_oecd_splice` merges the OECD as-of rows with the
previous latest-revised + calendar-mask rows using the same policy as the EA
RTD load lag in `macro_panel.py`: keep the row with the newest `ref_period`
per (meeting, geo); on equal periods the OECD vintage row wins; the
Eurostat row survives only when (a) no OECD edition is visible at the
meeting (pre-1999-03 meetings, and CY/MT/HR which are not OECD members) or
(b) its masked flash quarter is strictly newer than the newest quarter in
the visible OECD edition — for a just-published quarter the latest-revised
Eurostat value IS the first vintage.

## API pitfalls (verified live 2026-10-16)

1. **The endpoint sits behind Cloudflare bot management with a rolling
   window quota.** Per-edition harvesting (333 requests, even with 8-20 s
   pauses) degrades into sustained HTTP 429 cycles that also swallow curl
   from the same IP for many minutes. The collector therefore makes ONE
   **wildcard request** — trailing-dot key `…/AREAS.Q.B1GQ_Q.XDC._T.`
   leaves the EDITION dimension free — which returns every edition for all
   17 areas at once (~431k rows, one ~40 MB download), cached to
   `data/raw/oecd_gdp_vintages/all_editions.csv` (re-runs skip the
   download). Retries use exponential backoff (45 s → ×1.6, ceiling 420 s).
2. **SDMX-CSV v2 only with the right Accept header**; a plain
   `Accept: text/csv` yields parse errors. The response's first column is
   `STRUCTURE` (constant `DATAFLOW` on data rows) with 11 columns; the
   EDITION column is all-numeric so CSV readers must pin it to string.
3. **Batched multi-area keys** work (`DEU+FRA+…+SVN` in one key).
4. **Middle-wildcard keys 403** ("Not enough key values in query") — only a
   TRAILING empty dimension is accepted as wildcard.
5. **The EDITION codelist pre-announces future months** (2028xx at
   collection time); the wildcard only returns editions that have data, so
   no ceiling filter is needed.
6. **Non-XDC units 404** for `B1GQ_Q` (GR growth rates, IX indices, PS,
   PT_* ratios): use XDC levels and compute y/y within the edition.

## Secondary measures (present, deliberately unused)

The same dataflow carries 51 measures keyed by edition — `CP` (consumer
prices), `PP` (producer prices), `UNEMP`, `EMP`, GDP expenditure components
(`P51G_Q`, `P6_Q`, `P7_Q`), `B1GQ_V`/`B1GQ_D` (nominal/deflator). `CP` and
`UNEMP` are national-definition series (national CPI ≠ HICP; national
unemployment ≠ harmonised LFS), so mixing them with the panel's Eurostat
HICP/LFS series would create definition breaks — they are NOT used. The GDP
expenditure components are definition-compatible and vintage-correct if the
memos ever need them.

## Verification anchors

- Edition 202204 (visible from 2022-05-01), 2021-Q4 y/y: DE 1.78, FR 5.41,
  IT 6.21, ES 5.54 — match the national releases current in spring 2022
  (Destatis ~1.8, INSEE 5.4, Istat 6.2, INE ~5.5).
- Rebuilt panel (2026-10-16): **2022-07-21** rows show the edition-202206
  vintage, not today's revised values — DE 2022-Q1 3.76 (was 3.70
  latest-revised), FR 4.47, IT 6.25, ES 6.41. **2011-04-07**: FR 2010-Q4
  1.47, DE 3.98 — the INSEE/Destatis releases of that week. **2008-10-08**
  (coordinated cut): FR 2008-Q2 1.15. **2020-06-04**: IT 2020-Q1 −4.81.
  First meetings 1999-03-04/18 resolve to the 199902 edition (DE 1998-Q4
  0.9).
- EA aggregate rows are untouched (RTD path); CY stays on the Eurostat path;
  2026-07-23 DE shows 2026-Q1 0.34 (edition 202606 — the 2026-Q2 flash was
  not yet published at that meeting).
- Revision materiality (5,422 OECD-vintage panel rows vs today's Eurostat
  latest-revised): mean |Δ| 0.27 pp, median 0.04 pp, 16.5% of rows > 0.5 pp,
  max 9.7 pp; most-revised geos IE 0.44 / BE 0.35 / EL 0.33 / IT 0.30 /
  LV 0.29 — the known heavy revisers.
- Within-edition level series show no rebase breaks: the largest q/q level
  jumps in edition 202606 are the 2020 COVID quarters (DE ±9%), i.e. genuine
  swings, not base-year splices. The |y/y| > 25% canary flags 203 rows, all
  Ireland 2015 (+25–28% y/y, the multinational-inversion revision) — real
  data, not breaks.
