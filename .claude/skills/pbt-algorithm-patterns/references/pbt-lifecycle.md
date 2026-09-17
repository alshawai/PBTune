# PBT Lifecycle — Detailed Generation Data Flow

## Full Generation Cycle (from `BaseTuner.run()`)

`PBTTuner` has no `run()` of its own — it inherits the Template Method lifecycle from
`BaseTuner` (`src/tuners/base.py:681`) and supplies `step()` (`src/tuners/pbt/tuner.py:526`).

```
BaseTuner.run()
├── setup()                                    # bootstrap (timed separately)
│   └── environment.setup_instances(num_workers)
│       └── caches the worker-0 baseline snapshot for fast restarts
├── FOR generation IN range(max_rounds):
│   ├── PBTTuner.step(generation)
│   │   └── population.train_generation(evaluate_worker, parallel=True, ...)
│   │       ├── evaluate_generation(evaluate_fn)
│   │       │   └── ThreadPoolExecutor(max_workers=num_parallel_workers)
│   │       │       └── orchestrator.evaluate_worker(worker) × N
│   │       │           ├── apply_configuration(worker.knob_config)
│   │       │           ├── _ensure_benchmark_ready()
│   │       │           ├── executor.execute(ctx)
│   │       │           ├── collect_system_metrics()
│   │       │           ├── scoring_engine.compute_breakdown(metrics)
│   │       │           └── worker.update_metrics(metrics, score)
│   │       ├── rescue_dead_workers()
│   │       ├── update_metric_ranges_if_needed()      # saturation/drift expansion
│   │       ├── _finalize_scores()                    # rescore with final ranges
│   │       ├── _log_generation_worker_metrics_table()
│   │       ├── record_generation()
│   │       ├── execute_exploit_explore(workers, ...) # module function, not a method
│   │       │   ├── poor.clone_from(elite, current_generation)
│   │       │   └── poor.perturb(perturbation_factors=(0.8, 1.2))
│   │       └── env.clone_instances(source_id, target_ids)   # physical PGDATA clone
│   │   └── append the generation record to generation_history
│   └── should_stop(outcome) → population.should_stop()
└── finally: teardown()
    └── _assemble_results() writes the session JSON
```

Note that `record_generation()` runs **before** the evolution step, so a generation's
recorded scores are the pre-exploit ones.

## `Population.train_generation()` order

The ordering matters and is not the intuitive one — rescue happens *before* the
normalization update, and evolution happens *after* the generation is recorded:

```python
def train_generation(self, evaluate_fn, parallel=True, require_ready=True, ...):
    self.generation_timing = TimingRecorder()      # fresh recorder each generation

    # Snapshot restore is due when current_generation % restore_interval == 0.
    # The restore itself happens inside evaluate_worker, after apply_only has
    # written the new knobs to postgresql.auto.conf, so it doubles as the restart.

    self.evaluate_generation(evaluate_fn, ...)      # 1. parallel evaluation
    rescued = self.rescue_dead_workers()           # 2. rescue before rescoring
    self.update_metric_ranges_if_needed()          # 3. expand on saturation/drift
    self._finalize_scores()                        # 4. rescore with final ranges
    self._log_generation_worker_metrics_table()
    result = self.record_generation()              # 5. record pre-exploit scores

    pairs_exploited = execute_exploit_explore(...)  # 6. evolution (module function)
    if self.env is not None and pairs_exploited:
        self.env.clone_instances(source_id, target_ids)   # 7. physical clone

    self.current_generation += 1
    return result
```

There is no `Population.exploit_and_explore()` and no `_check_and_handle_saturation()`.
Exploit/explore is the module-level `execute_exploit_explore()` from
`src/tuners/pbt/evolution.py`, called inline; saturation handling lives inside
`update_metric_ranges_if_needed()` and `_finalize_scores()`.

## Liveness and the hung-worker gap

There is **no health-check thread and no `DatabaseEnvironment.is_alive()`** — neither
exists in the codebase. `barriers.abort()` is called from exactly one place in the PBT
path: the `except` clause around `future.result()` in `evaluate_generation`
(`population.py:558`). So a worker that *raises* unblocks its peers immediately, but a
worker that genuinely **hangs** — PostgreSQL unresponsive, no exception ever raised —
blocks the generation indefinitely, because nothing outside that thread can trip the
abort. Liveness probing exists only as `environment.verify_instances()`, invoked
synchronously during setup and recovery, never on a background poller.

## Dead Worker Rescue

`rescue_dead_workers()` runs inside `train_generation`, immediately after evaluation.
A worker counts as dead only when **both** conditions hold:

```python
dead = [
    w for w in self.workers
    if w.metrics is not None
    and w.metrics.failure_type is not None
    and w.performance_score < self.config.dead_config_threshold   # default 6.0
]
```

A failure-tagged worker whose score still clears the threshold is *not* rescued, and a
low-scoring worker with no `failure_type` is *not* rescued either.

Two branches follow:

- **Alive donors exist** — rescue is *deferred* to `execute_exploit_explore`, which pairs
  each dead worker with a genuine elite and perturbs from there. `rescue_dead_workers`
  itself does nothing beyond recovering the instance.
- **No alive donors** — every dead worker is resampled from an LHS candidate pool
  (`sample_diverse_configs`), choosing the candidate that maximises
  `_config_change_ratio()` against the worker's previous config subject to
  `resample_min_change_ratio`. The worker's `performance_score` is reset to `0.0`,
  `metrics`/`score_breakdown` are cleared, and `force_restart_next_eval` is set.

`step_count` is **not** reset in either branch.

## Convergence Check

`check_convergence()` (`src/tuners/pbt/evolution.py:457`) compares the **raw standard
deviation** of the valid workers' scores against the threshold — not a coefficient of
variation:

```python
stats = get_population_statistics(valid_workers)
return stats["std"] < convergence_threshold
```

Because the score is on a fixed 0–100 scale, a raw `std` is already comparable across
runs; there is no `std / mean` normalisation.

## Worker Evolution Signatures

```python
# EXPLOIT — copies knob values only. step_count is preserved.
def clone_from(
    self,
    other: "PBTWorker",
    current_generation: int,
    exclude_knobs: Optional[List[str]] = None,
) -> None: ...

# EXPLORE — factors default to ±20%; no knob_space argument.
def perturb(
    self,
    perturbation_factors: Tuple[float, float] = (0.8, 1.2),
    current_generation: Optional[int] = None,
    exclude_knobs: Optional[List[str]] = None,
    resample_probability: float = 0.0,
) -> None: ...
```

`clone_from` does **not** take an `environment` argument and performs no physical copy —
the PGDATA clone is a separate `env.clone_instances(source_id, target_ids)` call made by
`train_generation` after `execute_exploit_explore` returns its exploit pairs.

## Warm-Start Flow

Warm-start is resolved in `PBTTuner._build_warm_start_configs()`
(`src/tuners/pbt/tuner.py:833`), not in `__init__`:

```python
# Accepts a flat best_config_*.json (knob -> fraction) or a session JSON
# nested at best_configuration.knobs.
num_warm_start = math.ceil(population_size / 2)      # half the population
warm_configs = [base_config]                          # the unperturbed best config
factors = self._compute_warm_start_perturbation_factors(num_warm_start - 1)
for f_min, f_max in factors:
    warm_configs.append(knob_space.perturb_config(base_config, (f_min, f_max), rng))
# The remaining population_size - num_warm_start workers get LHS-sampled configs.
```

The perturbation spreads are **graduated**, not a single fixed range
(`_compute_warm_start_perturbation_factors`, `tuner.py:818`):

- one variant → `(0.65, 1.35)`
- N variants → spread sweeps linearly from `0.20` to `0.50`, giving
  `(0.80, 1.20)` … `(0.50, 1.50)`

so the earliest variants stay close to the warm-start point and the last ones explore
widest. The resolved factor list is recorded in the session JSON under
`warm_start.perturbation_factors`.
