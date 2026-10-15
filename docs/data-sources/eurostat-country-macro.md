# Country-level macro data (governor memos)

**Purpose**: per-country GDP growth, headline + core HICP, and unemployment for
the national-central-bank governors' memos — the country counterpart of the
EA vintage panel (`macro_asof_panel`). Merged into the same long panel
(`announcement_date | geo | indicator | ref_period | value`).

Module: `src/synthetic_council/collectors/country_macro.py`

## Sources

| indicator | source | series | span |
|---|---|---|---|
| hicp_yoy | Eurostat `prc_hicp_minr` | `M.RCH_A.TOTAL.{EA+20}` | 1996-01 → |
| hicp_core | Eurostat `prc_hicp_minr` | `M.RCH_A.TOT_X_NRG_FOOD.{EA+20}` | 1996-01 → |
| gdp_yoy | Eurostat `namq_10_gdp` | `Q.CLV_PCH_SM.SCA.B1GQ.{EA+20}` | 1975-Q1 → |
| unemp (2021-01 →) | Eurostat `ei_lm_m_vtg` | **true vintages** (revdate) | 1983-01 → |
| unemp (pre-2021) | Eurostat `une_rt_m` | latest-revised `PC_ACT SA T TOTAL` | 1983-01 → |
| ip_yoy (2021-01 →) | Eurostat `ei_is_m_vtg` | **true vintages** (revdate); `CA`, NACE `B-D` excl. construction, unit `RT12` = 12-month rate of change | 1980-01 → |
| ip_yoy (pre-2021) | Eurostat `sts_inpr_m` | latest-revised `PRD B-D CA PCH_SM` (y/y %) | 1980-01 → |

- `EA` = euro area **changing composition** (matches ECB `S0`/`U2`
  convention; EA19/EA20 are fixed rosters and are never used).
- Core definition (ECB): HICP all-items **excluding energy, food, alcohol
  and tobacco** (`TOT_X_NRG_FOOD`). This is the ECB's preferred core measure
  used in staff projections and the Monthly Bulletin. Note the distinction
  from the narrower Eurostat aggregate `TOT_X_NRG_FOOD_NP` (excluding only
  energy and unprocessed/fresh food) — deliberately NOT used here.
- **Dataset history**: `prc_hicp_manr` (the older monthly-rates dataset) was
  **discontinued with 2025-12 data**; `prc_hicp_minr` (HICP monthly *index*
  new release, `coicop18` classification) is the successor and carries
  `RCH_A` (annual rate of change) directly. Dimension is `coicop18`
  (not `coicop`), and `TOTAL` (not `CP00`) is the all-items code.
- GDP: chain-linked volumes, percentage change vs same quarter of previous
  year, seasonally adjusted.

## API pitfalls (verified 2026-08-22)

1. **`format=CSV` always returns HTTP 406** on the Eurostat SDMX 2.1
   dissemination API. Use `format=TSV` (optionally `compressed=true`) or
   `format=JSON`.
2. **TSV bulk queries silently ignore dimension filters** —
   `?format=TSV&coicop=CP00` returns the full dataset. Filters work only in
   the **key path** (`prc_hicp_manr/M.RCH_A.CP00.DE`) — except for datasets
   that reject key queries entirely (`une_rt_m` 400s on any key with more
   than one dimension fixed; use bulk + client-side filter).
3. `prc_hicp_manr` was **discontinued** (data ends 2025-12; announced
   2026-01). Successor `prc_hicp_minr` is current (2026-07 live) and carries
   both headline (`TOTAL`) and core (`TOT_X_NRG_FOOD`) y/y via unit
   `RCH_A`. The PEEI flash release `ei_cphi_m` is an alternative but starts
   later and is flash-oriented.
4. Geo lists in the key path use `+`: `...TOTAL.EA+AT+BE+...` (one request
   for all 21 geos).

## Release-calendar availability model (no look-ahead)

Latest-revised series carry no release dates, so first-publication timing is
modelled from the Eurostat release calendar:

| indicator | first release | avail_from |
|---|---|---|
| HICP (ei_cphi_m) | flash end of ref month; final ~mid m+1 | month end + 17d |
| GDP QNA (namq) | flash ~t+30d after quarter end | quarter end + 31d |
| unemployment (une_rt_m) | monthly news release ~start of m+2 | month end + 32d |
| industrial production (sts_inpr_m) | first estimate ~t+38/45d | month end + 40d |

A value is included at meeting date *d* only if `avail_from <= d`
(`join_asof` backward on date, by geo). This prevents intra-quarter look-ahead
(e.g. Q2 GDP flash released 29 July is NOT visible at the 21 July 2022 GC
meeting; the panel correctly shows 2022-Q1 there).

The same m+2 timing applies to the EA unemployment series in
`macro_panel.py` (LFSI publishes ~1 month after the reference month).

## Vintage hierarchy (per user data-rigour policy)

- **True vintages** where they exist online: unemployment 2021-01 → via
  `ei_lm_m_vtg` (revdate dimension; value for period p at meeting d = latest
  revdate <= d; vintages store only changes, so missing = unchanged).
- **Latest-revised + availability calendar** elsewhere (country HICP/GDP all
  eras, unemployment pre-2021): honest about the limitation; Eurostat hosts
  no country HICP/GDP vintages before 2014 (and none at all for HICP/GDP via
  the public API — `ei_na_q_vtg` covers EA/EU aggregates only).
- EA aggregates use true RTD vintages (2001+; see `ecb-rtd-mpd.md`).

## EA splice (RTD load lag)

The EA rows in `macro_asof_panel` come from true ECB RTD vintages, but RTD
loads with a lag (as of 2026-08: last vintage 2026-06-10, so June 2026 HICP —
published 2026-07-17 — was absent). For meetings where the Eurostat release
is strictly newer than the newest RTD vintage, the Eurostat value is spliced
in (for a just-published period the Eurostat value IS the first vintage); on
equal `ref_period` the RTD/LFSI row wins. Verified: 2026-07-23 memo shows
HICP 2026-06 = 2.8, GDP 2026-Q1 = 0.33 (RTD), unemp 2026-05 = 6.3.

## Verification anchors

- 2022-07-21: DE hicp 8.3, core 4.2, GDP 2022-Q1 3.7, unemp 2.8 (2022-05,
  vintage) — all match Eurostat/Euro-indicators releases of that week.
- 2026-07-23: DE hicp 2.4 (2026-06), core 2.3, GDP 2026-Q1 0.6, unemp 3.8 —
  matches current Eurostat data.
- 2011-04-07: FR hicp 2.2 (2011-02), DE core 0.9 — matches April 2011
  Euro-indicator releases.
- `prc_hicp_minr` DE 2026-07 core (`TOT_X_NRG_FOOD`) = 2.6 — matches the
  Eurostat release.
