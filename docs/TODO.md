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
- [x] Voting rights: accounts asterisks → per-meeting voting table (2015+) —
  `mpa_extract/participants.py`; asterisk = NO voting right (Art 10.2
  rotation), verified 2015-01-22 (4 no-vote) and 2024-12-12 (5 no-vote, 21
  voters)
- [x] Accounts-based attendance cross-check (2015+) — same module; 59/2,366
  rows flagged `in_memberships=false` (alternates, Wayback gaps, pre-accession
  observers)
- [x] Monetary policy accounts pipeline (2015 →): 96 accounts as markdown,
  participants, CE proposals (deterministic-first + LLM fallback), decision
  mode (Jev over LLM-curated verbatim evidence; Bruegel/Trichet categories)
- [x] Simulation engine, restructured CE-anchored (`feat/mpa-accounts-and-simulation`):
  verbatim CE proposal → 2 rounds by the others → CE reply → final positions →
  President's decision + modal-voter cross-check → per-member alignment vs
  simulated AND real decisions (Jev) → simulated decision mode (Jev +
  deterministic). Message archive per meeting (messages.jsonl, transcript.md).
  Two experiment arms (ea_only / country memos) + compare_arms analysis.
- [x] Member profiles: Tavily per-trait (stable traits only) + Wikipedia +
  verified facts, one reviewable .md per member (scripts/build_profiles.py)
- [x] Strict-window speech stance sections (speech after previous GC only;
  section omitted when none qualifies)
- [x] NCB governor speeches: harvested directly from NCB websites
  (bundesbank, banque-de-france, bde, suomenpankki, nbs, eestipank) + ECB
  blog via foedb + Banca d'Italia governor speeches (HTML entry pages
  2025→, PDFs 2015–24) — `collectors/ncb_websites.py`
  (sitemap-first, JSON-LD parsing, tenure-based speaker matching, pypdf for
  PDFs). Remaining gaps documented in docs/data-sources/ncb-websites.md
  (JS-rendered sites: BE/IE/SI/LV/BG/LU/HR/AT; bot-blocked: NL/PT/GR/LT;
  unreachable: CY/MT; stale-sitemap: ES)
- [ ] Hawkishness priors from speeches (Hansen–Kazinnik style LLM classification)
- [ ] CI on push (pytest + ruff)
