# Documentation Index

> Last reviewed: 2026-06-07

This index is a quick navigation map for the project documentation set.

## Architecture (start here)

- [PBT Core Components](./PBT_CORE_COMPONENTS.md) — Worker, Evolution, Population, lockstep barriers, dead-worker rescue
- [Generation Barriers](./GENERATION_BARRIERS.md) — B1–B17 lockstep mechanism for measurement fairness
- [Workload Orchestrator](./WORKLOAD_ORCHESTRATOR.md) — per-worker evaluation: apply / measure / score
- [Performance Evaluation](./PERFORMANCE_EVALUATION.md) — `PerformanceMetrics` + scoring integration
- [Configuration Management](./CONFIGURATION_MANAGEMENT.md) — `KnobSpace`, tier CSVs, `KnobApplicator`, `verify()` read-back
- [PostgreSQL Connection and Knobs](./POSTGRESQL_CONNECTION_AND_KNOBS.md) — connection layer, knob retrieval, tuning metadata, policy filter

## Scoring and Metrics

- [Feature-Driven Scoring](./FEATURE_DRIVEN_SCORING.md) — scoring-v2 pipeline, policies, weight model, normalisation, outlier filtering
- [Metrics Validation](./METRICS_VALIDATION.md) — academic justification for the multi-objective formulation
- [Hardware-Aware Normalization](./HARDWARE_AWARE_NORMALIZATION.md) — fractional encoding, per-worker resource slicing, Docker CPU subset enforcement

## Execution Layer

- [Environment Backends](./ENVIRONMENT_BACKENDS.md) — Docker vs bare-metal, CPU subsets, snapshot lifecycle, instance cloning
- [Benchmarking](./BENCHMARKING.md) — dual-evaluation strategy (external C-binaries vs JSON templates), SchemaProvider protocol
- [Environment Setup](./ENVIRONMENT_SETUP.md) — installation, `.env` configuration, sysbench 1.1.0 build instructions

## Evaluation and Comparison

- [Evaluation Suite](./EVALUATION_SUITE.md) — `ComparisonRunner`, paired statistical methodology, JSON schema, multi-arm comparisons
- [Evaluation Reproducibility Runbook](./EVALUATION_RUNBOOK.md) — canonical commands and reproducibility checklist
- [Bayesian Optimization Baseline](./BO_BASELINE.md) — SMAC3-based baseline, pilot+freeze normalisation, parallel ask-tell mode
- [PBT vs BO Comparison](./PBT_VS_BO_COMPARISON.md) — multi-arm comparison script and publication PDFs

## Analysis and Visualization

- [Knob Importance Analysis](./KNOB_IMPORTANCE_ANALYSIS.md) — fANOVA + TreeSHAP + Jenks tier generation, data-driven tier schema
- [Visualization](./VISUALIZATION.md) — figure registry, `PBTuneTheme`, loaders, plot modules, CLI
- [Autotuning Knob Policy](./AUTOTUNING_KNOB_POLICY.md) — per-knob tuning rationale and safety classification

## Research Positioning

- [Algorithm Comparison](./ALGORITHM_COMPARISON.md)
- [Competitive Analysis](./COMPETITIVE_ANALYSIS.md)
- [Cross-Workload Transfer](./CROSS_WORKLOAD_TRANSFER.md) — future-work scope (population archive, knob-importance transfer)
- [MySQL Integration Roadmap](./MYSQL_INTEGRATION.md)

## Architecture Decision Records

- [ADR-001 — Sysbench multi-workload support](./architecture/decisions/ADR-001-sysbench-multi-workload.md)
- [ADR-002 — Feature-driven scoring v2](./architecture/decisions/ADR-002-feature-driven-scoring-v2.md)
- [ADR-003 — Lockstep generation barriers](./architecture/decisions/ADR-003-lockstep-generation-barriers.md)
- [ADR-004 — Docker CPU subset isolation](./architecture/decisions/ADR-004-docker-cpu-subset-isolation.md)

## Related Top-Level Docs

- [Project README](../README.md)
- [Contributing Guide](../CONTRIBUTING.md)
- [Workload Format Guide](../workloads/README.md)
