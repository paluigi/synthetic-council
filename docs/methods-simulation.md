# Simulation methodology: CE-anchored deliberation and the two-arm experiment

## Meeting flow (per meeting × arm)

1. **CE proposal anchor.** The Chief Economist's proposal, extracted
   verbatim from the monetary policy account
   (`account_ce_proposal.parquet`), opens the meeting. No LLM call — the
   simulation is anchored to what was actually proposed. Meetings without a
   usable anchor (failed extraction, LLM fallback not yet run) are excluded
   rather than simulated on a fabricated proposal.
2. **Two discussion rounds.** Every non-CE member reacts to the proposal
   (round 1, memo-grounded, persona-aware), then responds to colleagues
   (round 2, full transcript visible, tail-truncated to
   `max_transcript_chars`). The CE does not speak in these rounds.
3. **CE reply.** The Chief Economist (LLM, in character) always replies to
   the discussion — defends, concedes, or adjusts the proposal.
4. **Final summary positions.** Every member states a final position ending
   with an explicit "Position: raise/hold/lower" line.
5. **Decision.** The President (LLM) formulates the decision statement
   ("Decision: …"); a deterministic cross-check (modal final stance of
   voting-right members, per the accounts' voting-rights table) is recorded
   alongside and disagreement is flagged.
6. **Alignment (Jev).** Per member, `{aligned, non_aligned}` probabilities
   against TWO references: the simulated decision (internal cohesion) and
   the real ECB decision built from `gc_decisions` (direction + size).
7. **Simulated decision mode.** (i) Jev on the simulated closing evidence
   with the SAME criteria as the ground-truth extraction
   (`mpa_extract.decision_mode`), so the comparison is symmetric; (ii) a
   deterministic threshold aggregation of per-agent P(aligned_sim):
   unanimity if every member ≥ 0.85; consensus if the aligned share ≥ 0.70
   and no member < 0.20; else majority (thresholds in `SimulationConfig`).

## Experiment arms

- `ea_only` — every member receives the euro-area-only memo
  (`data/processed/memos_ea/`).
- `country` — national governors additionally receive their country annex
  (`data/processed/memos_ea_country/`); the euro-area section is identical
  in both arms and Executive Board memos are identical by construction.

Identical generator settings and speaking order (alphabetical, from the
sorted roster) across arms.

## Message ledger

Every LLM call is logged per meeting to `{out}/{arm}/{date}/messages.jsonl`
as a complete `CallEvent`: call id, timestamp, kind (generator/choice),
role, endpoint, model, sampling parameters, **full inputs** (system+user,
or state+questions+rendered prompt), **full outputs** (content, or
outcome+probabilities+answers), token usage and latency — plus a
human-readable `transcript.md` (proposal → rounds → CE reply → final
positions → decision). Payload JSON carries the structured results and a
top-level `settings` block (see below). Any archived call can be replayed
verbatim, so experiments can branch off any step:
`simulation/replay.py` re-runs extraction → decision cross-check →
alignment → mode over a saved payload's statements under the current
`inference.toml` (e.g. a different judge), writing `payload_variant.json`
+ its own ledger with **no generator calls**.

## Comparison (`analysis/compare_arms.py`)

Per meeting × arm: mean/min P(aligned_sim), share aligned, simulated mode
(both methods), simulated decision stance vs the real direction. Report:
(a) alignment delta between arms, paired by meeting (exact sign test +
bootstrap 95% CI); (b) decision-mode match rates + 3×3 confusion matrices
vs the accounts ground truth; (c) direction accuracy per arm; (d) yearly
episode breakdown. The report header stamps the run settings
(generator/extractor ids, sampling parameters, inference.toml hash, git
sha); a settings-drift guard hard-fails when an arm mixes payloads from
different settings (override: `--allow-mixed`), and `--diff-runs A B`
prints the exact settings deltas between two payloads (e.g. an original
vs a replay variant).

## Measurement-model semantics

Measurement (positions, conviction, alignment, decision modes) runs on
the **extractor role** of `inference.toml` — `cloudflare/clef`,
Cloudflare's open-source 27B decision model, via OpenRouter's native
decisions API (`/api/alpha/decisions`, `api = "decisions"`). That API is
Jev-shaped: typed questions (choice over criteria with full probability
distributions; score over a rubric with a legend and fractional score)
answered in one request, with per-answer confidence (kept in `raw` for
provenance). The position *conviction* rubric (0–2, from the score
question) remains the usable confidence measure for statements; the
choice probabilities themselves are the usable measure everywhere else.
Roles without a native decisions API (`api = "chat"`, any
OpenAI-compatible endpoint) emulate the same measurement with the
`choice-v1` prompted-JSON protocol — parsed, validated, normalised —
so consumers are agnostic to the backend.

Legacy field names (`mode.jev_label`, `JevChoice`) are kept so pre-2026
payloads stay readable; they now mean "choice-model label/result".

## Run settings (comparability)

Every payload records a `settings` block: the full inference roles
(endpoint, model, api, sampling parameters), `choice_version`, the prompt
versions in play (speech sections ss-v1/ss-press-v1, decision mode
dm-v1), the simulation shape (rounds, truncation caps, concurrency,
aggregation thresholds), the sha256 of `inference.toml` and the git
commit — everything that determines the outcome, in one diffable block.
