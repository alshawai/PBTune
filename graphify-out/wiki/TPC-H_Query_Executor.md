# TPC-H Query Executor

> 25 nodes · cohesion 0.11

## Key Concepts

- **Population** (40 connections) — `src/tuner/core/population.py`
- **.train_generation()** (9 connections) — `src/tuner/core/population.py`
- **.rescue_dead_workers()** (5 connections) — `src/tuner/core/population.py`
- **._choose_diverse_resample_config()** (4 connections) — `src/tuner/core/population.py`
- **.evaluate_generation()** (4 connections) — `src/tuner/core/population.py`
- **.exploit_and_explore()** (4 connections) — `src/tuner/core/population.py`
- **.setup_worker_instances()** (4 connections) — `src/tuner/core/population.py`
- **._finalize_scores()** (3 connections) — `src/tuner/core/population.py`
- **.update_metric_ranges_if_needed()** (3 connections) — `src/tuner/core/population.py`
- **_config_change_ratio()** (2 connections) — `src/tuner/core/population.py`
- **_invoke_optional_worker_callback()** (2 connections) — `src/tuner/core/population.py`
- **.setup_snapshots()** (2 connections) — `src/tuner/core/population.py`
- **.should_stop()** (2 connections) — `src/tuner/core/population.py`
- **Population Class for Population Based Training (PBT) ===========================** (1 connections) — `src/tuner/core/population.py`
- **Manages a population of Workers for Population Based Training.      The Populati** (1 connections) — `src/tuner/core/population.py`
- **Assign PostgreSQL instance configurations to workers.          Parameters** (1 connections) — `src/tuner/core/population.py`
- **Register snapshot configuration for database restoration during training.** (1 connections) — `src/tuner/core/population.py`
- **Evaluate all workers in the current generation.          Executes the evaluation** (1 connections) — `src/tuner/core/population.py`
- **Pick and remove the most-diverse candidate from a shared candidate pool.** (1 connections) — `src/tuner/core/population.py`
- **Immediately rescue dead workers by exploiting alive configs.          Rescue flo** (1 connections) — `src/tuner/core/population.py`
- **Update metric normalization ranges after initial exploration phase.          Thi** (1 connections) — `src/tuner/core/population.py`
- **Perform exploit-explore step on poor-performing workers.          Delegates to e** (1 connections) — `src/tuner/core/population.py`
- **Execute one complete PBT generation.          This is the main training loop met** (1 connections) — `src/tuner/core/population.py`
- **Check if training should stop early.          Stops if:         1. Maximum gener** (1 connections) — `src/tuner/core/population.py`
- **Finalize scoring for the current generation.          1. Expand ranges if satura** (1 connections) — `src/tuner/core/population.py`

## Relationships

- [[PBT Worker Core]] (64 shared connections)
- [[Metric Config Recalibration]] (8 shared connections)
- [[Population Initialization]] (7 shared connections)
- [[Benchmark Orchestrator]] (6 shared connections)
- [[Database Config & Connection]] (4 shared connections)
- [[CLI Argument Parsing]] (3 shared connections)
- [[BO Baseline & Workload]] (1 shared connections)
- [[Cross-Module Rationale]] (1 shared connections)
- [[Knob Space Configuration]] (1 shared connections)
- [[Evaluator Fault Injection]] (1 shared connections)

## Source Files

- `src/tuner/core/population.py`

## Audit Trail

- EXTRACTED: 72 (75%)
- INFERRED: 24 (25%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*