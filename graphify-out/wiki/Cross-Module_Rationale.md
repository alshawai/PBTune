# Cross-Module Rationale

> 33 nodes · cohesion 0.06

## Key Concepts

- **.__init__()** (82 connections) — `src/tuner/main.py`
- **._build_output_dir()** (4 connections) — `src/tuner/main.py`
- **._derive_restore_api_timeout()** (3 connections) — `src/utils/environments/docker.py`
- **._derive_restore_ready_timeout()** (3 connections) — `src/utils/environments/docker.py`
- **._derive_snapshot_timeout()** (3 connections) — `src/utils/environments/docker.py`
- **._compute_active_weights()** (3 connections) — `src/utils/scoring/scorer.py`
- **._normalize_snapshot_identifier()** (3 connections) — `src/tuner/main.py`
- **Initialize WorkloadOrchestrator.          Parameters         ----------** (1 connections) — `src/tuner/benchmark/orchestrator.py`
- **Initialize template workload executor.          Parameters         ----------** (1 connections) — `src/tuner/benchmark/workload.py`
- **Initialize BO baseline runner.          Parameters         ----------         co** (1 connections) — `src/scripts/bo_baseline/runner.py`
- **Initialize knob space.          Parameters         ----------         knob_defin** (1 connections) — `src/tuner/config/knob_space.py`
- **Initialize a Population instance.          Parameters         ----------** (1 connections) — `src/tuner/core/population.py`
- **Initialize bare metal environment with configuration.** (1 connections) — `src/utils/environments/bare_metal.py`
- **Initialize the environment manager.          Parameters         ----------** (1 connections) — `src/utils/environments/base.py`
- **Initialize Docker environment with configuration.** (1 connections) — `src/utils/environments/docker.py`
- **Derive an appropriate Docker commit timeout from the benchmark type.          Re** (1 connections) — `src/utils/environments/docker.py`
- **Derive startup timeout after snapshot restore.          Restoring from an image** (1 connections) — `src/utils/environments/docker.py`
- **Derive Docker API timeout used by snapshot restore operations.          Snapshot** (1 connections) — `src/utils/environments/docker.py`
- **Initialize ComparisonRunner with configuration.** (1 connections) — `src/evaluation/runner.py`
- **Initialize the knob retriever.          Parameters         ----------         co** (1 connections) — `src/knobs/retrieval.py`
- **Initialize HTMLFormatter with optional module name display.          Parameters** (1 connections) — `src/utils/logger/formatters.py`
- **Initialize HTMLFileHandler with HTML header.** (1 connections) — `src/utils/logger/formatters.py`
- **Initialize colored formatter.          Parameters         ----------         sho** (1 connections) — `src/utils/logger/formatters.py`
- **Parameters         ----------         lower_quantile : float             Lower a** (1 connections) — `src/utils/scoring/normalization.py`
- **Parameters         ----------         policy : ScoringPolicySpec             The** (1 connections) — `src/utils/scoring/scorer.py`
- *... and 8 more nodes in this community*

## Relationships

- [[TPC-H Query Executor]] (5 shared connections)
- [[BO Baseline & Workload]] (5 shared connections)
- [[Tuner Config Tests]] (5 shared connections)
- [[Sysbench Executor Tests]] (4 shared connections)
- [[PBT vs BO Comparison]] (3 shared connections)
- [[Bare Metal Tests]] (3 shared connections)
- [[TPC-H Star Schema Queries]] (2 shared connections)
- [[Scoring & Weight Policies]] (2 shared connections)
- [[Benchmark Orchestrator]] (2 shared connections)
- [[Visualization Plotting]] (2 shared connections)
- [[Metric Config Recalibration]] (2 shared connections)
- [[Knob Space Configuration]] (2 shared connections)

## Source Files

- `src/benchmarks/sysbench/executor.py`
- `src/benchmarks/tpch/executor.py`
- `src/evaluation/runner.py`
- `src/knobs/retrieval.py`
- `src/scripts/bo_baseline/runner.py`
- `src/tuner/benchmark/orchestrator.py`
- `src/tuner/benchmark/workload.py`
- `src/tuner/config/knob_space.py`
- `src/tuner/core/population.py`
- `src/tuner/main.py`
- `src/utils/applicator.py`
- `src/utils/environments/bare_metal.py`
- `src/utils/environments/base.py`
- `src/utils/environments/docker.py`
- `src/utils/logger/formatters.py`
- `src/utils/scoring/normalization.py`
- `src/utils/scoring/scorer.py`
- `src/utils/scoring/weights.py`

## Audit Trail

- EXTRACTED: 109 (86%)
- INFERRED: 18 (14%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*