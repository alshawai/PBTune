# ADR-002: Feature-Driven Scoring v2

- Status: Accepted
- Date: 2026-04-28

## Context

The repository originally used fixed metric weights per workload class. That approach was simple, but it could not express differences within a benchmark family, especially when Sysbench and template workloads exhibit materially different read/write ratios, concurrency, and tail-latency sensitivity.

The project also needed one scoring path that could be reused by three consumers:

- online tuning during Population-Based Training,
- post-hoc rescoring for historical sessions, and
- evaluation comparisons between default and tuned configurations.

Keeping separate scoring formulas for those paths would create interpretation drift and make the results harder to defend in research reporting.

## Decision

We adopt a feature-driven composite scoring model as the canonical scoring-v2 implementation.

1. Workload shape is represented by an explicit feature vector instead of benchmark name alone.
2. Metric importance is computed from workload features with policy-controlled floors and bounded weights.
3. Raw metrics are normalized through a robust utility normalizer before scoring.
4. A reliability gate suppresses reward for failed or unstable runs.
5. The same scorer and normalizer stack is used by tuning, rescoring, and evaluation.

The legacy static policy remains available as `fixed_v1` for compatibility with existing sessions and for incremental rollout.

## Consequences

Positive:

- Scoring can distinguish workload modes that share the same benchmark family.
- Tuning and evaluation now share the same interpretation of metric values.
- Compatibility mode preserves historical reproducibility.
- The policy metadata makes scoring decisions auditable in saved session artifacts.

Trade-offs:

- The persisted session schema is larger because it now stores scoring metadata.
- Feature extraction and normalization add implementation complexity.
- Historical score values are not directly comparable across score versions unless the version metadata is consulted.

## Alternatives Considered

1. Keep fixed benchmark weights and only widen normalization bounds.

   Rejected because it does not capture workload-specific behavior inside the same benchmark family.

2. Use a black-box learned scoring model.

   Rejected because it weakens interpretability and reproducibility for the research workflow.

3. Use multi-objective selection instead of scalar scoring.

   Rejected because it would require a larger rewrite of the exploit/explore ranking pipeline.

## Migration Notes

Existing sessions without scoring-v2 metadata continue to load with compatibility defaults. New sessions should persist `scoring_policy`, `scoring_policy_version`, `metric_reference_version`, `workload_features`, and `normalization_metadata` so that downstream consumers can rescore results without guesswork.
