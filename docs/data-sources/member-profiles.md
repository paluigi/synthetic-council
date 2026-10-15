# Member profiles and speeches (Tavily, Wikipedia, BIS)

## Member profiles (`scripts/build_profiles.py`)

One reviewable markdown per GC member
(`data/processed/member_profiles/{Person}.md`) built from three streams:

1. **Verified facts** — roles/tenures/country from `gc_memberships_wayback`
   plus the Chief Economist tenure table (`mpa_extract/ce_tenure.py`).
2. **Tavily per-trait Q&A** — simple, single-purpose queries, one per
   *stable, time-invariant* trait (the profile is built once per member, not
   per meeting — no era- or meeting-specific questions such as dissents or
   views on negative rates). Default traits (editable in
   `member_profiles/tavily.py`): monetary policy stance (hawkish/dovish),
   economist career background, economic thinking and intellectual
   formation, approach to the ECB price stability mandate. The raw answer
   AND sources are archived verbatim per (member, trait) under
   `data/raw/profiles/tavily/{slug}/{trait}.json`.
3. **Wikipedia** — search (role context for disambiguation) + plaintext
   extract, archived under `data/raw/profiles/wikipedia/{slug}.json`.

The synthesised body is LLM-written but strictly grounded (quote key
phrases, per-section source tags, conflicts resolved toward
Wikipedia/verified facts); `--no-llm` assembles verbatim evidence instead.
Re-runs do NOT overwrite existing profiles (manual review edits survive);
`--rebuild` or `--only "Name"` regenerates. Requires `TAVILY_API_KEY` in
`.env.local` for the Tavily step; a missing key degrades to Wikipedia +
facts.

## Speech-grounded policy orientation (`member_profiles/speech_stance.py`)

Deliberately NOT a numeric hawkishness prior: each member's revealed
policy orientation (hawkish/dovish leanings as actually expressed, the
themes they lean on, evolution over time) is distilled by ONE
generator-role call over an evenly-spread sample of up to 8 of their OWN
speeches (union corpus; press coverage excluded — not their verbatim
voice) and embedded as the profile section
`## Policy orientation — from your own speeches`, which the memo carries
via `profile_text`. The section is second-person, grounded only in the
excerpts, quotes key phrases, and says so plainly when the excerpts are
too thin to characterise an orientation. Raw sections archived per
member under `data/processed/profiles/speech_stance/`; the call under
`data/processed/llm_calls/speech_stance.jsonl`. Embedding is idempotent
and review-safe: only the section's own block is replaced, the rest of
the profile file is preserved verbatim. Run:
`python -m synthetic_council.member_profiles.speech_stance [Name ...]`.

## GLM-5.3 web-search fill pass (2026-10-15) — replication package

Tavily evidence proved too thin for ~50 members (empty trait sections or
bare stubs). Those profiles were completed by GLM-5.3 (Z.ai) via directed
web searches (Wikipedia, ECB/NCB bios, Reuters/Bloomberg/Bruegel
profiles), with every raw search result archived verbatim per member
under `data/raw/profiles/websearch/{slug}/NN-source.md` (query,
timestamp, retrieved content, source URLs). This groundwork does NOT
carry a reproducibility guarantee — search results are time-dependent —
so every touched profile carries a provenance footer naming the
generator; the profiles ship as-is in the replication package.

The same pass: merged duplicate identities created by Wayback snapshot
seams (Philip R. Lane; Georgios Provopoulos, whose 2008–14 governorship
was split at a "George A." spelling; Pablo Hernández de Cos — tenures
healed to one continuous span each, richer trait sections carried over);
added the missing persons.py aliases (including the mononym account
spellings "Rimšēvičs", "Demarco", "Vujčić", "Radev", "Delgado",
"Dolenc", "Kālis", "Švaljek", "Žumer Šujica", "Välimäki") and rebuilt
`account_participants.parquet` so every participant resolves to exactly
one canonical identity; created profiles for the five participants that
had none (Margarita Delgado, Māris Kālis, Tuomas Välimäki, Sandra
Švaljek, Tina Žumer — mostly alternates/acting governors); deleted the
"To be appointed" roster artifact. `tests/test_profile_coverage.py`
now asserts every distinct participant has exactly one canonical
profile and no non-canonical files exist.

## Governor speeches (`collectors/ncb_speeches.py`)

The ECB all-speeches CSV covers Executive Board speakers only; NCB
governors publish on their own banks' sites. We harvest the **BIS speeches
hub** (`bis.org/speeches`): per-member keyword queries (exact name, then
surname) with author-matched cards, plus a walk of the default listing;
full texts are read from each speech page (`text__component` blocks).

**Coverage caveat (verified 2026-09):** the redesigned BIS hub migrated only
a subset of the old central-bankers'-speeches database — e.g. just a handful
of Jens Weidmann's 2014-21 speeches and one by Ignazio Visco (47 speeches by
our 88 members in total; de Cos 35, Wellink 10, Panetta 1, Visco 1).
Per-author pages exist (`/author/{slug}`) but list nothing more. Direct
NCB-website harvesting remains future work (docs/TODO.md). The pipeline
degrades gracefully: members without a qualifying speech simply get no
speech section in that meeting's memo.

## Strict-window speech join (`speech_union.py`)

For each meeting and member: the latest speech delivered **after the
previous monetary policy meeting and on/before the current one** (window
`(prev_gc, gc]`). A speech qualifies for at most one meeting, so the
extracted stance sections (`member_profiles/speech_sections.py`, one LLM
call per (member, meeting), saved under `data/processed/speech_sections/`)
are stable and reusable across memo rebuilds. Current join: 646 (member,
meeting) links over 92 meetings — 412 ECB CSV + 71 IT + 50 FI + 38 ECB blog +
30 FR + 25 DE + 16 BIS + 4 EE; governor links went from 83 (49 meetings) to
266 once NCB websites were harvested (see `ncb-websites.md`).

## Decision-mode evidence (`mpa_extract/decision_mode.py`)

LLM-curated verbatim quotes from §2 of each account (auditable against the
archived markdown in `data/processed/decision_mode_evidence/{date}.json`),
classified by Jev into unanimity/consensus/majority with the Bruegel/Trichet
definitions in the criteria ("large majority" → majority). A deterministic
keyword harvest is the fallback when the LLM output cannot be parsed.
