# TODO

## Done
- [x] GC decision calendar 1999→2026 — announcement dates from foedb; **pre-2015
  off-by-one timestamps fixed via URL slugs** (2008-10-08 coordinated cut et al.),
  16 documented dates verified
- [x] Daily key rates + rate-change cross-check
- [x] Membership tenures (94 tenures / 88 persons, 2004→) — three page layouts,
  name canonicalisation, junk filtered at source, **handover overlaps clamped**
  (attendee counts verified: 19 in 2004 → 28 in 2026)
- [x] Official ECB speeches (3,052) + last-speech-before-meeting join (1,888)
- [x] As-of macro panel: **EA HICP + GDP true vintages via ECB RTD
  (2001-01→)**, CISS daily, EA unemployment, country+EA sentiment; growth
  computed per release event (`vintage_growth.py`)
- [x] Staff projections (MPD): **round codes {W,G,S,A}=Mar/Jun/Sep/Dec decoded
  and verified**; **item codes fixed to official CL_PD_ITEM** (HIC/YER/URX —
  DDR=domestic demand, PCR=private consumption); 318/318 archive cells match
- [x] Memo builder (88 memos, correct projections)
- [x] Literature review + references.bib (95 entries)
- [x] Data-source explainers (foedb, PEEI, RTD/MPD) incl. all pitfalls
- [x] Regression tests for every review fix (12 tests total)
- [x] One-command reproduction: `scripts/collect_all.py`

## Known limitations (documented, deliberate)
- [ ] 1999–2000 (44 meetings): no true HICP/GDP vintages anywhere we probed
  (ECB RTD history starts 2001-01-03). Options: start sample 2001+, or fill with
  first-available vintage flagged as approximate. Needs user decision.
- [ ] 1999–2004 (99 meetings): no GC membership records (earliest Wayback
  snapshot 2004-07). Seed from ECB Annual Report appendices (~25 persons) if the
  simulation must start pre-2004. Needs user decision.
- [ ] Country GDP vintages: not on Eurostat (PEEI = EA/EU only); RTD = EA only.
  Country memos carry sentiment (+unemployment/IP vintages can be added from
  PEEI `ei_lm_m_vtg`/`ei_is_m_vtg`).

## Next (proposed)
- [ ] Join PEEI country unemployment/IP vintages into the panel
- [ ] Voting rights: accounts asterisks → per-meeting voting table (2015+)
- [ ] Accounts-based attendance cross-check (2015+)
- [ ] Hawkishness priors from speeches (Hansen–Kazinnik style LLM classification)
- [x] Simulation engine (`src/synthetic_council/simulation/`, branch
  `feat/council-simulation`): memo-driven agents, Jev position+conviction
  extraction (single parallel request, full probability distribution saved),
  >=2-round discussion, pre/post deltas + consensus per meeting. Configurable
  statement backend (gateway or any OpenAI-compatible API).
- [ ] CI on push (pytest + ruff)
