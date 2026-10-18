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

## Next (proposed)
- [x] Country GDP vintages: OECD STES revisions dataflow (`oecd_gdp_vintages.py`)
  — true vintages per MEI edition (199902→), 17 of 20 geos (every OECD
  member; CY/MT/HR stay latest-revised + calendar mask); within-edition y/y
  from XDC volume levels; Eurostat flash splice for the newest quarter (same
  policy as the EA RTD load lag). Closes the previously documented "country
  GDP vintages not on Eurostat" limitation — see
  `docs/data-sources/oecd-stes-revisions.md`.
- [x] Join PEEI country unemployment/IP vintages into the panel —
  unemployment was already true-vintage (2021+); industrial production
  y/y added from `ei_is_m_vtg` (CA, NACE B-D excl. construction, RT12 =
  12-month rate; true vintages 2021+, `sts_inpr_m` latest-revised with a
  t+40d availability mask before) — `collectors/country_macro.py`;
  panel now hicp/core/gdp/unemp/ip_yoy, 21 geos, ~31.7k rows; memo
  country table gains the IP row
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
- [x] CBS dataset (Campiglio et al. 2025) as speech source for the
  unharvestable NCBs (JS-rendered / bot-blocked / unreachable), 2015–2023
  only: 1,482 governor speeches by 37 members after tolerant name
  matching (CSV deletes diacritics), (person, day) dedup vs primary
  sources — `collectors/cbs_speeches.py`; strict-window join 647 → 1,187
  links over 92 meetings. 2024→ for those NCBs remains open (dataset ends
  2023-12)
- [x] BIS hub sitemap recrawl (2024→): the redesigned hub's listing is a
  curated ~750-speech subset and its keyword search is broken, but
  bis.org's sitemap lists every speech page — 383 new full texts by 28
  members incl. all gap-country governors and the new generation
  (Knot 32, Makhlouf 47, Stournaras 26, Escrivá 12, Sleijpen 11, Radev 7,
  Holzmann 6, Vasle 4, Šimkus 3, Wunsch 3, Vujčić 3 …) —
  `collectors/ncb_speeches.py::collect_from_sitemap`; post-tenure purge
  for legacy listing-walk rows (ex-governors speaking as BIS management).
  Join 1,187 → 1,304 links. Remaining for the news layer (TinyFish, key
  pending): PT/LU interviews and one-off gaps
- [x] TinyFish press layer built, live-validated and fully run
  (`collectors/press_speeches.py` + `docs/data-sources/press-tinyfish.md`):
  search (news+web, date-bounded) → policy-signal gate → fetch with
  published-date window clip → subject check (headline or body-head) →
  `press` corpus rows that only fill gaps in the strict-window join;
  press-framed memo-section prompt (`ss-press-v1`). Full run: 109 articles
  by 33 members (2024→2026-09), join now carries 80 press links (LU
  covered for the first time). speech_sections + memos rebuilt with the
  generator key, so press sections are in the experiment.
  (sitemap-first, JSON-LD parsing, tenure-based speaker matching, pypdf for
  PDFs). Remaining gaps documented in docs/data-sources/ncb-websites.md
  (JS-rendered sites: BE/IE/SI/LV/BG/LU/HR/AT; bot-blocked: NL/PT/GR/LT;
  unreachable: CY/MT; stale-sitemap: ES)
- [x] Provider-agnostic inference (`inference.toml` + `inference.py`):
  Vercel gateway + Jev removed (`ai[vercel]` dependency dropped); the
  extractor role is `cloudflare/clef` on OpenRouter's native
  `/api/alpha/decisions` API (full distributions + score rubrics in one
  request; live-validated), the generator role is
  `zai-org/GLM-5.3-Flash` on Featherless; any chat model can emulate the
  measurement via the `choice-v1` protocol. Every LLM call archived as a
  full CallEvent (inputs, outputs, model, params, usage, latency) —
  per-meeting `messages.jsonl` ledger + per-context JSONL sinks for
  utility calls; payload `settings` block (roles, params, prompt
  versions, config hash, git sha) with compare_arms drift detection and
  `--diff-runs`; `simulation/replay.py` branches a saved payload under a
  different judge with zero generator calls. Generator live-validated
  (all 1,409 speech sections generated via Featherless GLM-5.3-Flash).
  Remaining: the full two-arm run itself.
- [x] Member-profile fill pass (GLM-5.3, 2026-10-15): ~50 thin profiles
  completed via directed web search (Wikipedia, ECB/NCB bios, press
  profiles) — raw results archived under
  data/raw/profiles/websearch/{slug}/, provenance footers name the
  generator, shipped in the replication package; duplicate identities
  merged with tenures healed (Lane, Provopoulos, de Cos) + missing
  aliases for account mononym spellings; 5 participant gaps given
  profiles (Delgado, Kālis, Välimäki, Švaljek, Žumer); "To be
  appointed" artifact removed; coverage test guarantees one canonical
  profile per participant (tests/test_profile_coverage.py)
- [x] Hawkishness reflected in profiles, not estimated as a numeric prior:
  `member_profiles/speech_stance.py` distils each member's revealed
  policy orientation (hawk/dove leanings, themes, evolution) from a spread
  sample of their OWN speeches into the profile section "Policy
  orientation — from your own speeches", which memos embed via
  profile_text — qualitative Hansen–Kazinnik-style evidence inside the
  persona, per design decision (no standalone prior); calls archived,
  embedding idempotent and review-safe
- [x] Pre-run review fixes (2026-10-16): 2019-04-10 attendees parse (bare
  `Members` heading variant — was silently falling back to the Wayback
  roster with a "To be appointed" agent and fabricated voting rights);
  load_roster warns on fallback and the payload carries `roster_source`;
  speech-sections index upserts on subset runs and records `reused` vs
  the generating model honestly; the memo persona keeps the verified
  `**Roles:**` line; Knot's speech-grounded orientation moved inside the
  2,500-char cap; chat-API decision emulation archives one CallEvent per
  question; replay variants refresh per-agent alignment; dead artifacts
  removed (last_speech_before_meeting.parquet, legacy recorder shapes)
- [x] CE anchors completed (2026-10-16): 95/97 meetings anchored. The 25
  formerly-failed extractions filled manually (GLM-5.3,
  `extraction_method='manual-glm53'`) with verbatim, fragment-verified
  account quotes — 2015–16 old-format meetings anchor on the formal
  decision line (proposal attributed to the President, `ce_mismatch`
  flagged); only the two non-rate meetings stay unanchored. Run set is
  now 94 meetings, 2015-01-22 → 2026-09-10
- [x] 2026-09-10 meeting added end-to-end: foedb refresh → account
  fetched (97 total) → participants (27 attendees) → deterministic CE
  anchor (Lane, +25bp hike) → speeches for the new window (ECB CSV to
  2026-09-30, BIS +17 to 2026-10-13, press +19 to 109) → macro panel →
  join/sections/memos for both arms
- [x] EA industrial production pre-2021: `sts_inpr_m` aggregate (EA19 /
  EA20 → EA, overlap resolved to EA20) spliced under the PEEI true
  vintages — EA ip_yoy now spans 1999→2026 in the panel
- [x] Decision-mode ground truth built (97 meetings,
  `account_decision_mode.parquet`): verbatim evidence curation +
  unanimity/consensus/majority classification, so compare_arms'
  mode-vs-ground-truth sections populate
- CI on push: not planned (user decision, 2026-10-16)
- [x] Experiment 2 under GLM-5.3-Flash (reasoning_effort=low): both arms
  94/94, zero failures, $24.23 exact; comparison in
  data/experiments/comparison_glm/. Cross-generator robustness note
  (Muse vs GLM: robust null arm effect, 96-98% decision agreement,
  generator-sensitive mode labels) persisted as
  docs/robustness-generator-comparison.md
