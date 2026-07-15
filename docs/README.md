# Documentation

This documentation is organised by **reader intent** using the [Diátaxis framework](https://diataxis.fr/). Pick the entry point that matches what you need to do right now:

| If you want to… | Read |
| --- | --- |
| **Learn the system** from a blank slate | [getting-started/](getting-started/) |
| **Get a specific job done** with a recipe | [guides/](guides/) |
| **Look up** a flag, schema, or table | [reference/](reference/) |
| **Understand how it works** under the hood | [architecture/](architecture/) |
| **Position this work** in the literature | [research/](research/) |

---

## getting-started/ — Tutorials

Learning-oriented. Read in order; each builds on the previous.

- [setup](getting-started/setup.md) — install dependencies, configure `.env`, build sysbench 1.1.0
- [quickstart](getting-started/quickstart.md) — run your first PBT session end-to-end and inspect the results

## guides/ — How-to

Task-oriented. Assumes you know what you're trying to do.

- [evaluation-runbook](guides/evaluation-runbook.md) — reproduce a default-vs-tuned comparison
- [bo-baseline](guides/bo-baseline.md) — run the SMAC3 Bayesian-Optimisation baseline
- [pbt-vs-bo-comparison](guides/pbt-vs-bo-comparison.md) — produce the multi-arm comparison artefacts
- [visualization](guides/visualization.md) — generate publication figures from result trees
- [adding-knobs](guides/adding-knobs.md) — add a new tunable PostgreSQL knob to the search space
- [adding-workloads](guides/adding-workloads.md) — author and use a custom JSON/YAML workload template
- [scalpel-rollout](guides/scalpel-rollout.md) — run SCALPEL on a single workload, fan it out via `--all-workloads`, and debug degenerate results

## reference/ — Reference

Information-oriented. For looking things up, not for reading top-to-bottom.

- [cli](reference/cli.md) — consolidated CLI flag reference across `src.tuner.main`, `src.evaluation`, `src.scripts.bo_baseline`, `src.scripts.pbt_vs_bo_comarison`, `src.visualization`
- [session-json-schema](reference/session-json-schema.md) — schema of the PBT/BO session JSON artefacts and the comparison JSON
- [benchmarking](reference/benchmarking.md) — dual-evaluation strategy and `SchemaProvider` protocol
- [autotuning-knob-policy](reference/autotuning-knob-policy.md) — per-knob tuning rationale and safety classification
- [metrics-validation](reference/metrics-validation.md) — academic validation of the multi-objective scoring formulation
- [scalpel-diagnostics](reference/scalpel-diagnostics.md) — every field SCALPEL writes to `data_driven_tiers.json` and the `scalpel_diagnostics.json` sibling

## architecture/ — Explanation

Understanding-oriented. The "how it works and why" set. Read in the listed order if onboarding.

- [overview](architecture/overview.md) — top-level system map tying everything together
- [pbt-core](architecture/pbt-core.md) — Worker, Evolution, Population, dead-worker rescue
- [generation-barriers](architecture/generation-barriers.md) — B1–B17 lockstep mechanism for measurement fairness
- [workload-orchestrator](architecture/workload-orchestrator.md) — per-worker evaluation flow, restart policy, executor selection
- [environment-backends](architecture/environment-backends.md) — Docker vs bare-metal, CPU subsets, snapshots, instance cloning
- [performance-evaluation](architecture/performance-evaluation.md) — `PerformanceMetrics` and the scoring integration
- [feature-driven-scoring](architecture/feature-driven-scoring.md) — scoring-v2 pipeline, policies, normalisation, outlier filtering
- [configuration-management](architecture/configuration-management.md) — `KnobSpace`, tier CSVs, `KnobApplicator`, `verify()` read-back
- [hardware-aware-normalization](architecture/hardware-aware-normalization.md) — fractional encoding, per-worker resource slicing
- [postgresql-connection-and-knobs](architecture/postgresql-connection-and-knobs.md) — connection layer, knob retrieval, tuning metadata, policy filter
- [evaluation-suite](architecture/evaluation-suite.md) — `ComparisonRunner`, paired statistical methodology, multi-arm comparisons
- [knob-importance-analysis](architecture/knob-importance-analysis.md) — fANOVA + TreeSHAP + SCALPEL tier generation
- [scalpel](architecture/scalpel.md) — SCALPEL pipeline (significance gate, Lorenz coverage, group-clustered stability, DBA-prior audit)
- [bo-baseline](architecture/bo-baseline.md) — SMAC3 surrogate selection, Pilot+Freeze normalisation, read-back parity, parallel ask-tell
- [visualization](architecture/visualization.md) — figure-framework design rationale (auto-discovery, loader/renderer split, theme ownership)

### architecture/decisions/ — ADRs

Design decisions with context, alternatives, and consequences.

- [ADR-001 — Sysbench multi-workload support](architecture/decisions/ADR-001-sysbench-multi-workload.md)
- [ADR-002 — Feature-driven scoring v2](architecture/decisions/ADR-002-feature-driven-scoring-v2.md)
- [ADR-003 — Lockstep generation barriers](architecture/decisions/ADR-003-lockstep-generation-barriers.md)
- [ADR-004 — Docker CPU subset isolation](architecture/decisions/ADR-004-docker-cpu-subset-isolation.md)
- [ADR-005 — SCALPEL tier generation](architecture/decisions/ADR-005-scalpel-tier-generation.md)

## research/ — Research positioning

Where this work sits in the literature and where it could go next.

- [algorithm-comparison](research/algorithm-comparison.md)
- [competitive-analysis](research/competitive-analysis.md)
- [cross-workload-transfer](research/cross-workload-transfer.md) — future-work scope (population archive, knob-importance transfer)
- [mysql-integration](research/mysql-integration.md) — MySQL extension roadmap

---

## Top-level project docs

- [Project README](../README.md)
- [Contributing Guide](../CONTRIBUTING.md)
- [Workload Format Guide](../workloads/README.md)
