# ECB Monetary policy accounts

## What they are

The "accounts" of the Governing Council's monetary policy meetings — published
~4 weeks after each meeting since February 2015 (96 accounts as of 2026-09).
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
conclusion), 2025-06-25 (non-rate meeting). 2019-04-10 has no attendees block
at all (upstream); flagged in the participants extractor.

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
(2015), `**Members**` with bullet lists and trailing `\*` (2016–2020),
`#### Members` with bullets (2024+). **The asterisk marks members NOT holding
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
  (deterministic | llm | failed). 69/96 deterministic; the rest (mostly
  2015–2017 package proposals with no "<CE> proposed" sentence) use the LLM
  fallback at runtime. Direction validated: every non-null deterministic
  hike/cut matches the real decision except 3 PEPP-pace edge cases.
- `data/processed/account_decision_mode.parquet` — unanimity/consensus/
  majority probabilities (Jev) over LLM-curated verbatim evidence quotes
  stored in `data/processed/decision_mode_evidence/`.

Chief Economist tenures (proposal speaker verified against all accounts):
Peter Praet → 2019-05-31, Philip R. Lane 2019-06-01 →.
