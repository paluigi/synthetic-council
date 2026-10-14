# Press-coverage speech layer (TinyFish)

`collectors/press_speeches.py` fills the strict-window speech gaps for
2024→ meetings that neither BIS nor CBS cover (PT interviews, LU, one-off
silences): for every GC meeting from 2024-01, every participating member
with **no speech in the (prev_gc, gc] window** gets a news search, and
usable articles are stored as a `press` source.

## Why press rows are a separate evidence class

Articles quote 1–3 paragraphs of the member's remarks plus analyst
framing — not the verbatim speech. The memo section generated from them
(speech_sections, prompt `ss-press-v1`) states this explicitly, separates
the member's own quoted words from journalistic framing, and says so in
one sentence when an article carries no substantive monetary remarks.
Press never displaces a real speech: `speech_union` uses press only for
speakers with no non-press speech in the window, and rows colliding with
a primary source on (speaker, date) are dropped at collection.

## Pipeline

1. **Search** (`GET api.search.tinyfish.ai`, free; `TINYFISH_API_KEY`
   from the environment — export `.env.local`). Native date-range
   enforcement: `after_date=prev_gc`, `before_date=gc`. Live-verified
   choices (2026-10): unquoted short queries (`{person} ECB`, then
   `{surname} ECB rate`) — the exact-phrase/`OR` variants halved recall —
   and both `domain_type=news` and `web`: the web index is far richer for
   small-country governors (Centeno: 0 news vs 10 web results in the
   pilot window). Raw responses archived per query under
   `data/raw/speeches/press_searches/`.
2. **Candidate gates** (before spending a fetch): domain blocklist
   (x.com, youtube, facebook, …); a policy signal in title+snippet
   (`rate/inflation/monetary/interest/easing/tightening/hike` + a few
   non-English stems — "ECB" alone is NOT a signal, which keeps court
   and scandal coverage out).
3. **Fetch** (`POST api.fetch.tinyfish.ai`, `{"urls": [...]}` → markdown
   text + ISO `published_date`). Up to 6 per (member, window), keep ≤2.
   The article's own `published_date` (fallback: a date embedded in the
   URL) is the authoritative window clip — undated articles are dropped.
   Reuters/Bloomberg direct pages usually refuse (401/403); syndicated
   copies are fetched opportunistically, failures skipped.
4. **Subject check** (`_is_subject`): the headline names the member
   (surname present, no other GC member headlining earlier,
   first-name-compatible tokens — "ECB's Knot" passes, "Mark Knot"
   doesn't), or — for wire stories that headline the news instead of the
   speaker — the member is named at the top of the body (first name
   adjacent to the surname, or the full name in the head).
5. Output `data/raw/speeches/press_speeches.parquet` (corpus schema +
   `source='press'`, `publisher`, `language`). Resume index
   `data/raw/speeches/press_fetched.parquet` — url → outcome, plus
   `search:{person}:{gc}` markers for fully processed (member, window)
   pairs; flushes at window granularity.

## Result (2026-10 full run)

90 articles by 29 members over 2024-01 → 2026-07 (~1,000 searches, ~240
(member, window) pairs processed; ≤2 kept per pair). Concentrated exactly
where BIS/CBS lack coverage: Wunsch 10, Kažimír 9, Kazāks 8, Šimkus 6,
Vujčić 5, Reinesch 5 (Luxembourg finally covered), Moulin 4, Holzmann 3,
Kocher 3, Centeno 2. Strict-window join 1,304 → 1,374 links (+70 press,
which only ever fill uncovered members). Publisher mix: Bloomberg/Yahoo
syndications (the originals are paywalled to fetchers), national outlets,
international wire syndication. Most rows English; a handful flagged
da/de/fr/hr/ms/nl via the `language` column.

## Caveats

- Press ≠ verbatim: partial quotes + framing; sections are labelled.
- Fetches of the same article can be flaky (consent walls occasionally
  return truncated text); such articles are lost for that run — delete
  the pair's entries in `press_fetched.parquet` to retry.
- Foreign-language syndications (Danish, Turkish) pass the gates when a
  policy stem matches; the `language` column flags them downstream.
- Wire-frequency: `collect_all.py` runs this step only when
  `TINYFISH_API_KEY` is set.

## Memo integration

`speech_sections` branches on `source == 'press'` (prompt
`ss-press-v1`); the memo template is unchanged — the section body itself
carries the press-coverage caveat, keeping simulation prompts stable.
After collecting press rows: rebuild the join
(`python -m synthetic_council.speech_union`), then sections
(`python -m synthetic_council.member_profiles.speech_sections`, needs
`AI_GATEWAY_API_KEY`), then memos (`python -m synthetic_council.memo`).
