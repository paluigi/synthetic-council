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

## Message archive

Every generator and Jev request/response is logged per meeting to
`{out}/{arm}/{date}/messages.jsonl` (stage, agent, round, prompts,
response, model, usage, timestamp) plus a human-readable `transcript.md`
(proposal → rounds → CE reply → final positions → decision). Payload JSON
carries the structured results. The archive makes runs inspectable and
re-usable as inputs to other experiments without re-running.

## Comparison (`analysis/compare_arms.py`)

Per meeting × arm: mean/min P(aligned_sim), share aligned, simulated mode
(both methods), simulated decision stance vs the real direction. Report:
(a) alignment delta between arms, paired by meeting (exact sign test +
bootstrap 95% CI); (b) decision-mode match rates + 3×3 confusion matrices
vs the accounts ground truth; (c) direction accuracy per arm; (d) yearly
episode breakdown.

## Jev semantics

`typesafe-ai/jev` via the Vercel AI Gateway (`ai` package,
`experimental_evaluate` + `ChoiceQuestion`): returns the chosen outcome
with a full calibrated probability distribution over the criteria. TypeSafe's
native confidence statistic saturates at 1.0 for choices (documented in
`simulation/extraction.py`), so the position *conviction* rubric
(ScoreQuestion, 0–2) is the usable confidence measure for statements; the
choice probabilities themselves are the usable measure everywhere else.
Prompt versions are recorded in every output (`prompt_version` columns,
payload fields).
