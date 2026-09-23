# synthetic-council

Agent-based simulations of the ECB Governing Council (GC) for interest rate
decisions. The repository contains (i) the **data foundation**: collectors and
curated datasets reconstructing, for every GC monetary policy meeting since
1999, the decision and rate levels, the roster and voting rights (from the
monetary policy accounts), the macroeconomic information available *at that
time* (true vintages where they exist), per-member speeches and web-evidence
member profiles; and (ii) the **simulation engine**: a CE-anchored LLM-agent
deliberation with Jev position/alignment/mode measurement, run as a two-arm
experiment (euro-area-only vs +country memos) over the accounts era.

## Layout

```
src/synthetic_council/
  config.py                 endpoints, paths, constants
  collectors/
    foedb.py                ECB publications database (all press releases 1992→)
    rates.py                daily key rates + rate-change table
    decisions.py            GC decision calendar 1999→ (announcement dates)
    members.py              GC membership tenures from Wayback snapshots
    persons.py              name canonicalisation / person ids
    speeches.py             official ECB speeches dataset + last-speech join
    macro.py                PEEI vintages, RTD HICP vintages, CISS, sentiment
    macro_panel.py          as-of macro panel per meeting
    projections.py          ECB/Eurosystem staff projections (MPD)
  accounts.py              monetary policy accounts (2015 →, markdown)
    ncb_speeches.py         BIS speeches hub harvest (governors)
  mpa_extract/              accounts extraction: participants + voting
    rights, decision mode (Jev over LLM-curated evidence), CE proposal
  speech_union.py           strict-window latest-speech join (ECB + BIS)
  member_profiles/          Tavily per-trait + Wikipedia profiles,
    speech-stance memo sections, synthesis
  memo.py                   per-member markdown briefing builder (2 arms)
  jev.py                    shared Jev choice helper (gateway)
  simulation/               LLM-agent council simulation (see below)
    agent.py                roster from accounts participants + memos
    generator.py            statement backends: Vercel AI Gateway | OpenAI-compatible
    extraction.py           Jev position+conviction extraction (gateway)
    discussion.py           CE proposal -> 2 rounds -> CE reply -> finals
    decision.py             President's decision + modal-voter cross-check
    alignment.py            per-member {aligned, non_aligned} vs sim+real
    mode.py                 simulated unanimity/consensus/majority
    recorder.py             message archive (messages.jsonl, transcript.md)
    runner.py               meeting orchestration, payloads
  analysis/compare_arms.py  two-arm experiment comparison
data/
  raw/                      source dumps (parquet; accounts/ as markdown)
  processed/                curated datasets + memos_ea/ + memos_ea_country/
docs/
  data-sources/             per-source explainer documents (reproducibility)
  literature-review.md      OpenAlex-compiled review
references/references.bib   curated bibliography with abstracts
```

## Reproducing

```bash
uv sync
uv run python -m synthetic_council.collectors.foedb
uv run python -m synthetic_council.collectors.decisions
uv run python -m synthetic_council.collectors.macro_panel
uv run python -m synthetic_council.collectors.projections
uv run python -m synthetic_council.memo
# memberships + speeches via scripts (see docs/data-sources/)
```

Every dataset documents its endpoints, discovery path and verification in
`docs/data-sources/`. No API keys required.

## Datasets (verified 2026-09-23)

| dataset | rows | span | source |
|---|---|---|---|
| `gc_decisions.parquet` | 319 meetings | 1999-03 → 2026-09 | ECB foedb (URL-slug dates, all eras) + SDMX FM |
| `gc_memberships_wayback.parquet` | 93 tenures / 87 persons | 2004 → 2026 | Wayback GC pages (3 layouts) |
| `accounts/` + `accounts_index.parquet` | 96 accounts (93 rate meetings) | 2015-01 → 2026-07 | ECB foedb enumeration + HTML→markdown |
| `account_participants.parquet` | 2,366 meeting×member rows | 2015 → 2026 | accounts attendees + voting rights (asterisks) |
| `account_ce_proposal.parquet` | 96 proposals (69 deterministic) | 2015 → 2026 | accounts §1 (regex; LLM fallback flagged) |
| `account_decision_mode.parquet` | unanimity/consensus/majority + probs | 2015 → 2026 | Jev over LLM-curated §2 evidence |
| `speeches.parquet` | 3,052 (ECB) + 47 (BIS) + NCB-web/blog harvest | 1997 → 2026-09 | ECB CSV + BIS hub + NCB websites + ECB blog |
| `latest_speech_per_meeting.parquet` | strict window (see speech_union) | 2015 → 2026 | union of all speech corpora |
| `macro_asof_panel.parquet` | ~39.5k rows | 1999 → 2026; EA vintages 2001+; country HICP/GDP/unemp (21 geos) | ECB RTD + PEEI + Eurostat + CISS + DG-ECFIN |
| `staff_projections.parquet` | 161,066 | 2000 → 2026 | ECB MPD |
| `memos_ea/` + `memos_ea_country/` | 5,200 briefings each | 2004 → 2026 | assembled (experiment arms) |
| `member_profiles/` | one reviewable .md per member | — | Tavily + Wikipedia + facts |

Rate levels note: the MRO column splices the fixed-rate tender rate (MRR_FR)
with the minimum bid rate (MRR_MBR) for the variable-rate-tender era
2000-06-28 → 2008-10-14 — see `src/synthetic_council/collectors/rates.py`.

## Council simulation (CE-anchored, two arms)

`synthetic_council.simulation` anchors each meeting to the Chief Economist's
**verbatim proposal** from the real account, then:

1. **Two discussion rounds** — every non-CE member reacts to the proposal
   (round 1: memo-grounded, persona-aware) and responds to colleagues
   (round 2, full transcript visible).
2. **CE reply** — the Chief Economist always replies to the discussion,
   defending or adjusting the proposal.
3. **Final summary positions** — every member ends with an explicit
   "Position: raise/hold/lower" line.
4. **Jev extraction** — position {raise, hold, lower} with the **full
   probability distribution** plus a conviction rubric (0 tentative … 2
   committed), on the round-1 reactions ("before") and the final statements
   ("after"); per-member deltas and the council balance are recorded.
5. **Decision** — the President formulates the decision; a modal-voter
   cross-check (voting rights from the accounts) is recorded alongside.
6. **Alignment** — per member, Jev {aligned, non_aligned} probabilities
   against the simulated decision AND the real ECB decision.
7. **Simulated decision mode** — unanimity/consensus/majority measured twice:
   Jev with the same criteria as the ground-truth extraction, and a
   deterministic aggregation of the alignment probabilities.

Every generator/Jev message is archived per meeting
(`{out}/{arm}/{date}/messages.jsonl` + `transcript.md` + `payload.json`) for
inspection and reuse. Statements come from the gateway (`--generator
gateway`, default `openai/gpt-4o-mini`) **or any OpenAI-compatible
endpoint** (`--generator openai_compat`); extraction is always Jev on the
gateway. Full methodology: `docs/methods-simulation.md`.

```bash
# one meeting, one arm:
uv run python scripts/run_simulation.py 2022-07-21 --arm ea_only

# full two-arm experiment over the anchored meetings (resumable):
uv run python scripts/run_simulation.py --all --arm ea_only
uv run python scripts/run_simulation.py --all --arm country

# comparison report (alignment delta, mode confusion, direction accuracy):
uv run python -m synthetic_council.analysis.compare_arms

# validate inputs without API calls:
uv run python scripts/run_simulation.py --all --arm ea_only --dry-run

# live 3-agent pipeline check on synthetic memos (no dataset needed):
uv run python scripts/simulation_smoke_live.py
```

Requires `AI_GATEWAY_API_KEY` in `.env.local` (never committed);
`TAVILY_API_KEY` additionally for profile building.

## Reproducing everything

```bash
uv sync
uv run python scripts/collect_all.py            # keyless pipeline (~30-40 min)
# LLM-dependent steps (keys in .env.local, see .env.example):
uv run python -m synthetic_council.mpa_extract.decision_mode
uv run python scripts/build_profiles.py
uv run python -m synthetic_council.member_profiles.speech_sections
uv run pytest                                   # 56 tests
uv run ruff check src/ scripts/ tests/
```

The pipeline is deterministic given upstream sources; each collector prints its
own verification stats (meeting counts, vintage spans, tenure counts).

## Status & next steps

See `docs/TODO.md` and `docs/QUESTIONS.md`.
