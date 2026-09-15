# ADR-007: Workload-Feature Policy for Post-Hoc Evaluation

- Status: Accepted
- Date: 2026-09-15

## Context

Under the `feature_driven_v2` scoring policy, the composite score is `S = G × Σ(wᵢ × uᵢ)` where the metric weights `w` are a pure function of the workload-feature vector and the policy's base logits (`CompositeScorer._resolve_weights`, `FeatureDrivenWeightModel.compute_weights`). The quantile normaliser stays workload-conditioned through `workload_type`, but the *weights* see only the feature vector. Two different feature vectors are therefore two different rubrics over the same raw metrics.

That makes the feature vector an experimental parameter of any post-hoc comparison, and the arms of a comparison do not agree on one.

**PBT moves its vector while it searches, by design.** `WorkloadFeatureRefiner` blends runtime observations into `metric_config.workload_features` in place once per generation (EMA, α = 0.7, with a 15 % soft floor against the static prior). This is deliberate: the original PBT formulation adapts its own objective as the population learns, and we exercise that property for database tuning — the score is the signal PBT optimises, so letting it move is a feature of the method, not a defect. The consequence for bookkeeping is that the vector a PBT session *ends* with is a property of that run's search path.

**BO and LHS never move theirs.** Only `Population.train_generation` calls the refiner. A BO or LHS session persists the static prior that `build_workload_bundle` extracted at setup, unchanged.

**A default arm has no session at all**, so it has no vector to contribute.

Measured on a real pair of extensive `oltp_read_write` sessions (`trace_20260913_0033` PBT vs `trace_20260913_0141` BO):

| feature | PBT (end of session) | BO (static prior) |
| --- | --- | --- |
| `concurrency_pressure` | 0.1180 | 0.5 |
| `tail_latency_sensitivity` | 0.1347 | 0.55 |

Worth recording for whoever next tunes the refiner: on that session both features ended within about 0.05 of their 15 % floors (0.075 and 0.0825). The activation scales are `min(1, throughput_CV / 0.20)` and `min(1, tail_amplification / 10.0)`, and a healthy run sits near zero on both — a stable worker's throughput CV is well under 0.20, and a 3× tail amplification maps to 0.3. A one-directional EMA toward near-zero signals therefore decays both features rather than tracking a workload change. Whether those divisors are the right activation scales is a question for the refinement mechanism itself; it is out of scope here, and this ADR's decision makes it irrelevant to published comparisons either way.

The evaluation package previously passed `workload_features=None` at both rescore call sites, with a code comment asserting this "falls back to the workload-type-conditioned base prior in `create_metric_config`". **No such prior exists.** `OLTP_METRIC_CONFIG`, `OLAP_METRIC_CONFIG` and `MIXED_METRIC_CONFIG` never set `workload_features`; the dataclass default is `{}`, and `create_metric_config` resolves `custom_weights.get("workload_features", base) or {}` to `{}` either way. The evaluation was therefore scoring every arm with an **empty** vector, i.e. the policy's bare base logits — symmetric across arms, but stripped of workload conditioning. Since the weights are the only workload-conditioned part of the rubric that depends on features, TPC-H and sysbench were graded identically:

| metric | TPC-H prior | empty vector | distortion |
| --- | --- | --- | --- |
| `scan_efficiency` | 0.2241 | 0.0194 | 11.6× under-weighted |
| `tail_amplification` | 0.0813 | 0.0320 | 2.5× under-weighted |
| `throughput` | 0.1449 | 0.2552 | 1.76× over-weighted |
| `latency_p95` | 0.1609 | 0.2552 | 1.59× over-weighted |

For sysbench `oltp_read_write` the same comparison gives `latency_p99` 0.2364 → 0.1634 (1.45× under) and `throughput` 0.1707 → 0.2552 (1.5× over).

Re-scoring the recorded runs of `multi_arm_comparison_20260913_053831` under four candidate vectors (evaluation prior, empty, PBT's session vector, BO's session vector) did **not** change the ranking — `bo > default > pbt`, 10/10 paired wins in every case — but did move score magnitudes (PBT's mean 24.25 → 27.79). The defect is one of reporting fidelity and workload conditioning, not of a reversed published result.

One further asymmetry sat in the same code path: `_run_single` computed each run's intermediate score with `session.workload_features` — the PBT session's end-of-run vector — and applied it to the BO and default arms too. Those values were overwritten by the final global rescore, but they were logged as if authoritative.

## Decision

Post-hoc evaluation scores every arm with **one workload-feature vector that belongs to the evaluation, not to any arm**. The vector is re-extracted by `WorkloadFeatureExtractor` from the evaluation's *own effective benchmark parameters* — the CLI → session → default precedence already resolved by `ComparisonRunner._resolve_effective_benchmark_params`. This policy is identified as `eval_static_prior`, version `1.0`, and lives in `src/evaluation/feature_policy.py`.

Four consequences of that rule:

1. **Symmetry.** `run()` (two-arm) and `run_multi_arm()` (n-arm) pass the same resolved vector to `rescore_metrics_globally`, and thread it down through `_run_paired_comparisons` / `_run_multi_arm_repetitions` into `_run_single`, so logged per-run scores and final rescored scores are computed on one rubric. No arm is graded on a vector derived from its own session.

2. **The prior is the one every tuner started from.** Deriving it from benchmark parameters rather than inventing a third vector means it is, by construction, what `build_workload_bundle` extracts at the start of a tuning run. This is asserted directly: `test_sysbench_prior_matches_tuning_bundle_prior` and `test_tpch_prior_matches_tuning_bundle_prior` compare the evaluation's vector against the tuning bundle's for the same parameters and require exact equality. It was also confirmed empirically before the change — the recomputed prior reproduced BO's persisted vector on all ten features.

3. **Evaluation parameters, not session parameters.** When a CLI override makes the evaluation's workload differ from the tuning session's, the rubric follows the evaluation, because that is the workload actually being measured. Both arms are measured under the same overridden conditions, so symmetry holds regardless.

4. **Provenance is recorded.** `scoring_metadata` in every comparison JSON now carries `workload_feature_policy`, `workload_feature_policy_version`, `workload_feature_source` and `workload_feature_inputs` alongside the resolved `workload_features` vector, so the rubric can be recomputed from the output alone. Multi-arm `session_scoring_metadata` also restores each arm's persisted `workload_features` (the two-arm path already recorded it), so a reader can see what each tuner actually optimised against versus what the comparison graded.

Session vectors that diverge from the evaluation prior are **logged, never scored**. The evaluation prints per-arm divergence (`describe_feature_divergence`) so the reader can see which parts of a tuner's own rubric the comparison does not reproduce. For a PBT arm this divergence is expected, and it is interpretive context rather than a warning.

This decision is scoped to post-hoc evaluation. It changes nothing about how a tuner scores during a session: PBT continues to move its features mid-run, because that movement is the mechanism, and the score is the signal it optimises. The evaluation score is guidance and insight; the load-bearing claims of a comparison rest on the raw endpoints — throughput, latency percentiles, error rate — which no feature vector can reweight.

## Consequences

Positive:

- The head-to-head rubric is workload-conditioned again. TPC-H comparisons weight `scan_efficiency` at 0.2241 instead of 0.0194.
- The rubric is reproducible from the comparison JSON without either session file.
- The rubric is independent of every arm's search path, so adding or removing an arm cannot change how the remaining arms are scored.
- Logged intermediate scores and final rescored scores agree.
- A reader can see, per arm, where the tuning rubric and the evaluation rubric differ.

Trade-offs:

- Evaluation scores are not directly comparable to the `best_score` recorded in a PBT session, because PBT's end-of-run rubric differs from the evaluation's by construction. The divergence log makes this visible rather than silent; `session_scoring_metadata` preserves both vectors.
- Comparison JSONs written before this change carry `"workload_features": {}` and no policy field. Absence of `workload_feature_policy` identifies them as scored under the feature-blind rubric; their scores are not comparable to post-ADR runs, though their raw metrics are.
- The policy adds a small surface (`feature_policy.py`) and one more parameter threaded through the run loops.

## Alternatives considered

1. **Keep `workload_features=None` and document it.** Symmetric and simple, and it needs no new code. Rejected because it is not workload-conditioned: one rubric would grade sysbench and TPC-H identically, with the single most OLAP-relevant metric suppressed 11.6×. Symmetry across arms was never the part that was broken.

2. **Grade each arm with its own session's vector.** Maximally faithful to what each tuner optimised. Rejected because the resulting scores are not on a common scale, which breaks the paired Wilcoxon design the comparison rests on — and the default arm has no vector at all.

3. **Average or otherwise pool the arms' vectors.** Rejected as non-reproducible: the rubric would depend on which arms happened to be included, so the same PBT session would score differently in a two-arm and a three-arm comparison.

4. **Persist a static/refined split in the session JSON and use the recorded static half.** Would tie the rubric to the session rather than to what was measured, and no existing trace carries the split, so every recorded session would become unevaluable under the new policy. Deriving the prior from benchmark parameters obtains the same vector without a schema change.

## Related

- [ADR-002: Feature-Driven Scoring v2](ADR-002-feature-driven-scoring-v2.md) — the policy whose weights this vector drives.
- [feature-driven-scoring](../feature-driven-scoring.md) — canonical reference for the scoring-v2 architecture.
- [evaluation-suite](../evaluation-suite.md) — the comparison pipeline this policy applies to.
- Issue #146 — the divergence report that prompted this decision.
