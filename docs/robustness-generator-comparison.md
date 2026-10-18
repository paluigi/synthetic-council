# Cross-generator robustness: Muse-Spark vs GLM-5.3-Flash

The same two-arm experiment (94 ECB Governing Council meetings, 2015-01-22 →
2026-09-10; arms `ea_only` vs `country` country-annex memos; Clef extractor on
OpenRouter's decisions API in both) was run twice, once per generator:

| | Experiment 1 (Muse) | Experiment 2 (GLM) |
|---|---|---|
| Generator | `meta-models/Muse-Spark-Open-816B-A42B-promo` (Together, free promo) | `zai-org/GLM-5.3-Flash` (Featherless) |
| Reasoning settings | default (heavy reasoning; `max_tokens=16000`) | `reasoning_effort="low"`, `max_tokens=2500` |
| Temperature | 0.7 | 0.7 |
| Extractor | `cloudflare/clef` (decisions API, unchanged) | `cloudflare/clef` (decisions API, unchanged) |
| Wall time | ~23.5 h (incl. endpoint outage windows and re-run sweeps) | ~9.5 h (zero failed meetings) |
| Generator calls | 14,058 | 14,058 |
| Generator tokens (in/out) | 75.4 M / 44.9 M | 109.4 M / 11.2 M |
| Cost | ≈ $2 (Clef only; Muse promo is free) | $24.23 total ($16.39 in + $5.58 out + $2.25 Clef) |
| Payloads | `data/processed/simulations/` | `data/processed/simulations_glm/` |
| Report | `data/experiments/comparison/report.md` | `data/experiments/comparison_glm/report.md` |

Muse's run needed `--allow-mixed`: the generator retry budget was raised
mid-experiment (6→8 attempts) while riding out Together 5xx bursts. The only
varying field is that retry knob (verified by diffing the settings blocks);
model, temperature, prompts, and arms are constant. GLM's run has a single
clean settings block per arm.

## The treatment effect (country annex) is a robust null

The paired arm contrast (Δ P(aligned with simulated decision),
country − ea_only):

| Generator | mean Δ | bootstrap 95% CI | sign test p |
|---|---|---|---|
| Muse-Spark | +0.0086 | [−0.0065, +0.0262] | 0.918 |
| GLM-5.3-Flash | −0.0143 | [−0.0268, −0.0038] | 0.079 |

The two point estimates are tiny, of opposite sign, and each is economically
negligible (|Δ| ≤ 1.5 pp of alignment probability). The correct reading for
the paper: **the country annex has no stable effect on deliberation cohesion**;
the point estimate is generator-sensitive noise around zero. One experiment
alone (GLM's CI excludes zero) would have tempted a "small negative effect"
claim — the replication reveals that as an apparatus artifact, which is
precisely what a generator-robustness check is for.

## What replicates across generators

- **Decision-direction accuracy** (simulated President's raise/hold/lower vs
  the real decision): 95.7% (GLM, both arms), 95.7% / 97.9% (Muse). The
  CE-anchored design tracks real decisions almost perfectly regardless of the
  generator.
- **Simulated decisions themselves**: the two generators produce the same
  decision stance in **95.7%** (`ea_only`) and **97.9%** (`country`) of the 94
  meetings. The disagreements concentrate in the close calls of 2019–2022.
- **Decision-mode confusion structure**: match rates vs the accounts-derived
  ground truth are similar (Muse 50%/48%, GLM 46%/47%), and in every
  generator × arm cell the dominant error is the same: consensus-grounded
  meetings simulated as unanimity (Muse 24/39 and 23/39; GLM 28/39 and
  27/39). The deterministic aggregator shows the same skew. This is a
  property of the deliberation design (the synthetic council converges more
  readily than the real one), not of any model — see the discussion point
  below.
- **Episode difficulty**: 2019/2020/2022 are the weakest years for alignment
  and direction accuracy under both generators.

## What is generator-sensitive

- **Arm-delta sign** (above).
- **Alignment levels**: GLM deliberations are slightly more cohesive
  (mean P(aligned) 0.944–0.959 vs Muse's 0.916–0.925).
- **Decision-mode labels**: inter-generator agreement on the simulated mode
  (unanimity/consensus/majority) is only 58.5%–63.8%, versus 96–98% on the
  decision stance — the mode measurement sits near category boundaries and
  should be treated as the softest outcome in the design.

## Reporting discipline

The two experiments are never pooled. Each within-experiment arm contrast is
internally valid (the generator is part of the apparatus, held fixed within
the experiment); cross-generator statements are descriptive robustness
evidence only. All per-meeting payloads carry full LLM call ledgers
(`messages.jsonl`, inputs/outputs/params per call) for both experiments, so
any meeting can be inspected or branched (`simulation/replay.py`).

## Cost note (for the replication package)

Input tokens dominate and are model-independent (same prompts): ≈ 75 M per
two-arm experiment (GLM tokenizer counted 109 M — +45% vs the Together
tokenizer on identical prompts; budget with the provider's own tokenizer).
Output is the model-dependent margin: Muse's hidden reasoning cost 44.9 M
output tokens; GLM at `reasoning_effort="low"` needed 11.2 M. If the generator
reasons by default, set a token cap and/or a low reasoning effort *before*
launching: the reasoning overhead is invisible in the transcript but fully
billed.

Generated 2026-10-18 by the GLM-5.3 (Z.ai) agent session, from the two
experiment ledgers and reports; agreement statistics computed with
`polars` over `data/experiments/{comparison,comparison_glm}/per_meeting.parquet`.
