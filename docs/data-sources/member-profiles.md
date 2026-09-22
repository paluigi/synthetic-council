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
are stable and reusable across memo rebuilds. Current join: 453 (member,
meeting) links over 92 meetings (437 ECB + 16 BIS).

## Decision-mode evidence (`mpa_extract/decision_mode.py`)

LLM-curated verbatim quotes from §2 of each account (auditable against the
archived markdown in `data/processed/decision_mode_evidence/{date}.json`),
classified by Jev into unanimity/consensus/majority with the Bruegel/Trichet
definitions in the criteria ("large majority" → majority). A deterministic
keyword harvest is the fallback when the LLM output cannot be parsed.
