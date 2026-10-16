# ECB Monetary policy accounts

## What they are

The "accounts" of the Governing Council's monetary policy meetings — published
~4 weeks after each meeting since February 2015 (97 accounts as of 2026-10,
through the 2026-09-10 meeting).
Each account has §1 "Review of financial, economic and monetary developments
and policy options" (closing with the Chief Economist's proposal) and §2
"Governing Council's discussion and monetary policy decisions", followed by an
attendees block with voting-rights asterisks.

## Enumeration (deterministic)

The accounts are NOT listed on a server-rendered index page. They are
enumerated from the **foedb publications database** (see `ecb-foedb.md`):
every record whose document URLs point under `/press/accounts/` (type facet
20; titles "Account of the monetary policy meeting" in 2015–2020,
"Meeting of …" from 2021). English URL patterns:

- 2015–2020: `/press/accounts/{year}/html/mg{YYMMDD}.en.html`
- 2021→: `/press/accounts/{year}/html/ecb.mg{YYMMDD}~{hash}.en.html`
  (occasionally `ecb.mg{YYMMDD}_1~…` when two are published the same day)

The slug carries the **publication** date, not the meeting date.

## Meeting-date parsing (pitfalls verified)

The decision-day date is parsed from the document, in this priority:

1. the opening sentence "held in {venue} on Wednesday and Thursday,
   11-12 December 2024" — **venue varies** (Frankfurt am Main, Nicosia);
2. the `# Meeting of …` heading / foedb title ("Meeting of 22-23 July 2026");
3. first date range after the leading lines.

The leading bare-date line at the top of `<main>` is the *publication* date
and must never win (this caused 20 misparses before the fix). Decision day =
the last day of the meeting range ("3-5 June 2025" → 2025-06-05).

## Non-rate meetings

Three accounts cover meetings without a rate decision and are flagged
`is_rate_meeting=false` in the index (excluded from the decision-anchored
pipeline): 2020-03-18 (PEPP emergency meeting), 2021-07-07 (strategy-review
conclusion), 2025-06-25 (non-rate meeting). 2019-04-10 once parsed to zero
participants because its attendees block uses a bare `Members` heading line
(no `##`/`**` marker) — the heading regex now accepts that variant, so all
97 accounts parse.

## Calendar cross-fix (decisions.py)

Cross-validating the accounts against `gc_decisions` exposed two bugs in the
decision calendar, now fixed:

- 2016–2018 press releases also carry evening batch timestamps: the URL slug
  (`ecb.mp170427.en.html`) is authoritative for ALL eras (`_slug_date` now
  matches `.mp` slugs too) — e.g. April 2017 announcement is the 27th, not
  the 26th;
- the foedb corpus is missing the December 2016 "Monetary policy decisions"
  release entirely (GC meeting 7-8 December 2016, hold); patched via
  `MISSING_RELEASES` with documented provenance.

## Attendees & voting rights

Layouts differ by era: `### Members` with plain lines and `(\*)` markers
(2015), `**Members**` with bullet lists and trailing `\*` (2016–2020), the
bare `Members` line (e.g. 2019-04-10), `#### Members` with bullets (2024+).
**The asterisk marks members NOT holding
a voting right that month** under the rotation system (Article 10.2 ESCB
Statute; footnote captured verbatim per meeting) — Executive Board members
never carry it. Alternates appear as "Mr Demarco, temporarily replacing Mr
Scicluna", "Alternate to Mr Jazbec", ", Deputy/Vice/Acting Governor of {bank}"
(with country inferred from the bank name), ", via teleconference".
Roles/countries are cross-checked against `gc_memberships_wayback` (±45d
grace); rows that match no tenure are flagged `in_memberships=false`
(alternates, Wayback gaps, pre-accession observers such as Bulgaria's Radev
in late-2025 accounts).

## Output

- `data/raw/accounts/{meeting_date}_account.md` — front matter (source_url,
  publication_date, meeting_date) + `<main>` converted with markdownify
  (asterisks preserved as `\*`).
- `data/raw/accounts_index.parquet` — per account: meeting_date,
  publication_date, title, url, file, is_rate_meeting, date_check.
- `data/processed/account_participants.parquet` — per (meeting, member):
  person, role, country, voting_right, replacing, is_chief_economist,
  in_memberships. Verified: 2024-12-12 has 26 attendees / 21 voting (the
  correct rotation math).
- `data/processed/account_ce_proposal.parquet` — Chief Economist proposal
  text (verbatim), speaker, direction (hike/cut/hold), bp, method
  (deterministic | llm | manual-glm53 | failed). 70/97 extract
  deterministically (the modern "<CE> proposed" phrasings). 25 more were
  filled manually (GLM-5.3, 2026-10-16, `extraction_method='manual-glm53'`):
  verbatim quotes from the accounts — the 2015–16 old-format meetings anchor
  on the formal decision line ("on a proposal from the President…", speaker
  recorded as such, `ce_mismatch=true`), the post-2017 ones quote the
  proposal passages the deterministic patterns miss (colon-lists, members'
  summaries). Every quote is fragment-verified against the account markdown.
  Only the two non-rate meetings (2021-07-07, 2025-06-25) remain `failed` —
  nothing to anchor. Direction validated: every non-null hike/cut matches
  the real decision except documented edge cases (2015-12-03 deposit-rate
  cut with MRO unchanged; 2022-06-09 July-hike signal at a hold meeting).
- `data/processed/account_decision_mode.parquet` — unanimity/consensus/
  majority probabilities (Jev) over LLM-curated verbatim evidence quotes
  stored in `data/processed/decision_mode_evidence/`.

Chief Economist tenures (proposal speaker verified against all accounts):
Peter Praet → 2019-05-31, Philip R. Lane 2019-06-01 →.
