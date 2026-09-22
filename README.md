# synthetic-council

Agent-based simulations of the ECB Governing Council (GC) for interest rate
decisions. This repository currently contains the **data foundation**: collectors
and curated datasets that reconstruct, for every GC monetary policy meeting since
1999, (i) the decision and rate levels, (ii) who sat on the Council and in what
role, (iii) the macroeconomic information available *at that time* (true vintages
where they exist), (iv) each member's last public speech before the meeting, and
(v) per-member briefing memos suitable as LLM-agent inputs.

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
  memo.py                   per-member markdown briefing builder
  simulation/               LLM-agent council simulation (see below)
    config.py               SimulationConfig (generator/extractor/rounds knobs)
    agent.py                per-member agent built from the briefing memo
    generator.py            statement backends: Vercel AI Gateway | OpenAI-compatible
    extraction.py           Jev position+conviction extraction (gateway)
    discussion.py           multi-round discussion + final statements
    runner.py               meeting orchestration, deltas, consensus
data/
  raw/                      source dumps (parquet)
  processed/                curated datasets + memos/
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

## Datasets (verified 2026-09-19)

| dataset | rows | span | source |
|---|---|---|---|
| `gc_decisions.parquet` | 318 meetings | 1999-03 → 2026-07 | ECB foedb (URL-slug dates) + SDMX FM |
| `gc_memberships_wayback.parquet` | 93 tenures / 87 persons | 2004 → 2026 | Wayback GC pages (3 layouts) |
| `speeches.parquet` | 3,052 | 1997 → 2026-07 | ECB speeches CSV |
| `last_speech_before_meeting.parquet` | 1,888 meeting×speaker links | 1999 → 2026 | idem |
| `macro_asof_panel.parquet` | ~39.5k rows | 1999 → 2026; EA vintages 2001+; country HICP/GDP/unemp (21 geos) | ECB RTD + PEEI + Eurostat + CISS + DG-ECFIN |
| `staff_projections.parquet` | 154,686 | 2000 → 2026 | ECB MPD |
| `memos/` | 5,175 briefings (all meetings 2004→2026) | — | assembled |

Rate levels note: the MRO column splices the fixed-rate tender rate (MRR_FR)
with the minimum bid rate (MRR_MBR) for the variable-rate-tender era
2000-06-28 → 2008-10-14 — see `src/synthetic_council/collectors/rates.py`.

## Council simulation

`synthetic_council.simulation` turns the briefing memos into an LLM-agent
Governing Council:

1. **Opening statements** — each member reads their own memo (euro area vintage
   macro + staff projections + country annex and last speech for governors) and
   states their motivation first, decision last.
2. **Pre-discussion extraction** — Jev (`typesafe-ai/jev` via the Vercel AI
   Gateway) classifies each statement in **one parallel request**: position
   {raise, hold, lower} with the **full probability distribution**, plus a
   conviction score (0 = tentative … 2 = fully committed). TypeSafe's native
   confidence statistic is recorded but saturates at 1.0 for choices, so the
   conviction score is the usable confidence measure.
3. **Discussion** — at least 2 rounds (`--rounds`); every member speaks in every
   round seeing the transcript so far, reacting to colleagues without having to
   conform.
4. **Final statements + post-discussion extraction** — same Jev extraction on
   the closing statements; per-member deltas (position flips, conviction and
   probability shifts) and the council's before/after balance are written to
   `data/processed/simulations/simulation_<date>.json`.

Statements and extraction are separate backends: statements come from the
gateway (`--generator gateway`, default `openai/gpt-4o-mini`) **or any
OpenAI-compatible endpoint** (`--generator openai_compat` with
`OPENAI_COMPAT_BASE_URL` / `OPENAI_COMPAT_API_KEY` / `OPENAI_COMPAT_MODEL`);
extraction is always Jev on the gateway.

```bash
# build memos first (see Reproducing), then one meeting:
uv run python scripts/run_simulation.py 2022-07-21 --rounds 2

# EVERY meeting that has memos, oldest first, resumable (re-running skips
# meetings whose simulation_<date>.json already exists):
uv run python scripts/run_simulation.py --all

# restrict to the true-vintage era onward:
uv run python scripts/run_simulation.py --all --from 2015

# validate config + memos without API calls:
uv run python scripts/run_simulation.py --all --dry-run

# live 3-agent pipeline check on synthetic memos (no dataset needed):
uv run python scripts/simulation_smoke_live.py
```

Requires `AI_GATEWAY_API_KEY` in `.env.local` (never committed).

## Reproducing everything

```bash
uv sync
uv run python scripts/collect_all.py            # full pipeline (~30-40 min, no keys)
uv run pytest                                   # 35 tests
uv run ruff check src/ scripts/ tests/
```

The pipeline is deterministic given upstream sources; each collector prints its
own verification stats (meeting counts, vintage spans, tenure counts).

## Status & next steps

See `docs/TODO.md` and `docs/QUESTIONS.md`.
