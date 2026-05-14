# Logger Formatters

> 26 nodes · cohesion 0.10

## Key Concepts

- **PopulationConfig** (21 connections) — `src/tuner/core/population.py`
- **_MetricConfigStub** (12 connections) — `tests/unit/core/test_population_saturation_rescoring.py`
- **test_finalize_scores_always_rescores_workers()** (7 connections) — `tests/unit/core/test_population_saturation_rescoring.py`
- **test_finalize_scores_grounds_best_to_current()** (7 connections) — `tests/unit/core/test_population_saturation_rescoring.py`
- **test_finalize_scores_overwrites_best_if_worse()** (7 connections) — `tests/unit/core/test_population_saturation_rescoring.py`
- **test_record_generation_not_converged_after_all_dead_resample()** (6 connections) — `tests/unit/core/test_dead_rescue_convergence_and_restart.py`
- **test_saturation_detection_expands_ranges_for_high_latency_low_throughput()** (6 connections) — `tests/unit/core/test_dead_rescue_convergence_and_restart.py`
- **test_train_generation_raises_when_snapshot_restore_and_rebuild_fail()** (6 connections) — `tests/unit/core/test_dead_rescue_convergence_and_restart.py`
- **test_train_generation_rebuilds_worker_when_snapshot_restore_fails()** (6 connections) — `tests/unit/core/test_dead_rescue_convergence_and_restart.py`
- **test_population_saturation_rescoring.py** (6 connections) — `tests/unit/core/test_population_saturation_rescoring.py`
- **test_should_stop_ignores_no_improvement_when_disabled()** (4 connections) — `tests/unit/core/test_dead_rescue_convergence_and_restart.py`
- **test_warm_start_seeds_workers_full()** (4 connections) — `tests/unit/core/test_warm_start.py`
- **test_warm_start_seeds_workers_partial()** (4 connections) — `tests/unit/core/test_warm_start.py`
- **Configuration for Population initialization and behavior.      Parameters     --** (1 connections) — `src/tuner/core/population.py`
- **No-improvement patience should be ignored when explicitly disabled.** (1 connections) — `tests/unit/core/test_dead_rescue_convergence_and_restart.py`
- **Generation should attempt clean-slate rebuild when snapshot restore fails.** (1 connections) — `tests/unit/core/test_dead_rescue_convergence_and_restart.py`
- **Generation should abort only if both restore and rebuild fail for a worker.** (1 connections) — `tests/unit/core/test_dead_rescue_convergence_and_restart.py`
- **Out-of-bounds in the opposite direction should also trigger expansion.** (1 connections) — `tests/unit/core/test_dead_rescue_convergence_and_restart.py`
- **All-dead rescue/resample generations should never be marked as converged.** (1 connections) — `tests/unit/core/test_dead_rescue_convergence_and_restart.py`
- **Tests for population saturation handling and rescoring behavior.** (1 connections) — `tests/unit/core/test_population_saturation_rescoring.py`
- **Even when no range expansion is needed, workers should be rescored.** (1 connections) — `tests/unit/core/test_population_saturation_rescoring.py`
- **Stub metric config used to verify rescoring logic.** (1 connections) — `tests/unit/core/test_population_saturation_rescoring.py`
- **When ranges expand, population should rescore workers and ground historical best** (1 connections) — `tests/unit/core/test_population_saturation_rescoring.py`
- **If the rescored historical best is worse than the current best, it should be ove** (1 connections) — `tests/unit/core/test_population_saturation_rescoring.py`
- **Test partial seeding of workers in population initialize()** (1 connections) — `tests/unit/core/test_warm_start.py`
- *... and 1 more nodes in this community*

## Relationships

- [[Metric Config Recalibration]] (54 shared connections)
- [[Population Initialization]] (18 shared connections)
- [[PBT Worker Core]] (12 shared connections)
- [[Evaluator Fault Injection]] (9 shared connections)
- [[Database Config & Connection]] (8 shared connections)
- [[Cross-Module Rationale]] (2 shared connections)
- [[Evolution Algorithms]] (2 shared connections)
- [[BO Baseline & Workload]] (1 shared connections)
- [[Knob Space Configuration]] (1 shared connections)
- [[DB Connection Reuse]] (1 shared connections)
- [[TPC-H Star Schema Queries]] (1 shared connections)

## Source Files

- `src/tuner/core/population.py`
- `tests/unit/core/test_dead_rescue_convergence_and_restart.py`
- `tests/unit/core/test_population_saturation_rescoring.py`
- `tests/unit/core/test_warm_start.py`

## Audit Trail

- EXTRACTED: 55 (50%)
- INFERRED: 54 (50%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*