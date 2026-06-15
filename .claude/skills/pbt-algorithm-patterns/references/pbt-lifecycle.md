# PBT Lifecycle — Detailed Generation Data Flow

## Full Generation Cycle (from `PBTTuner.run()`)

```
┌─────────────────────────────────────────────────────────────────┐
│                    PBTTuner.run()                                │
│  1. _create_baseline_snapshot()                                 │
│  2. population.initialize(initial_configs=warm_start_configs)   │
│  3. FOR generation = 1..max_generations:                        │
│     └── run_generation(generation)                              │
│         ├── population.evaluate_generation(evaluate_fn)         │
│         │   └── ThreadPoolExecutor(max_workers=parallel_workers)        │
│         │       └── evaluate_worker(worker) × N                 │
│         │           ├── apply_configuration(worker.knob_config) │
│         │           ├── _ensure_benchmark_ready()               │
│         │           ├── executor.run_benchmark()                │
│         │           ├── collect_system_metrics()                │
│         │           ├── metric_config.compute_score(metrics)    │
│         │           └── worker.update_metrics(metrics, score)   │
│         ├── population.update_metric_ranges_if_needed()         │
│         ├── population._check_and_handle_saturation()           │
│         ├── population.rescue_dead_workers()                    │
│         ├── population.exploit_and_explore()                    │
│         │   ├── truncation_selection(workers)                   │
│         │   └── execute_exploit_explore(pairs, knob_space)      │
│         │       ├── bad.clone_from(good)                        │
│         │       └── bad.perturb(knob_space)                     │
│         ├── population.record_generation()                      │
│         ├── save_intermediate_results(generation)               │
│         └── population.should_stop() → convergence check        │
│  4. save_final_results(total_time)                              │
│  5. print_final_summary(results)                                │
└─────────────────────────────────────────────────────────────────┘
```

## Population.train_generation() Flow

The `Population.train_generation()` method orchestrates a single generation:

```python
def train_generation(self, evaluate_fn, generation):
    # 1. Evaluate all workers in parallel
    self.evaluate_generation(evaluate_fn)
    
    # 2. Update normalization ranges (gen ≥ 2)
    self.update_metric_ranges_if_needed()
    
    # 3. Handle metric saturation
    self._check_and_handle_saturation(evaluate_fn)
    
    # 4. Rescue dead workers (those with score 0.0)
    self.rescue_dead_workers()
    
    # 5. Exploit/Explore
    self.exploit_and_explore(generation)
    
    # 6. Record generation statistics
    return self.record_generation()
```

## Dead Worker Rescue

Dead workers (score = 0.0, caused by PostgreSQL crashes or benchmark failures) are
rescued using a diversity-preserving resampling strategy:

```python
def rescue_dead_workers(self):
    for worker in workers:
        if worker.performance_score == 0.0:
            # Choose config that maximizes distance from existing workers
            new_config = _choose_diverse_resample_config(worker, alive_workers)
            worker.knob_config = new_config
            worker.step_count = 0  # Reset ready status
```

The diversity metric uses `_config_change_ratio()` to measure how different
a proposed config is from all alive workers, then picks the most distinct option.

## Warm-Start Flow

```python
# PBTTuner.__init__() handles warm-start
if warm_start_path:
    loaded_config = json.load(warm_start_path)
    warm_configs = _build_warm_start_configs(loaded_config, pop_size)
    # Seeds 1-2 workers with loaded config + perturbations
    # Remaining workers get LHS-sampled configs
    population.initialize(initial_configs=warm_configs)
```

Perturbation factors for warm-start are computed via 
`_compute_warm_start_perturbation_factors()`, which uses wider ranges 
(0.7, 1.3) for more exploration around the warm-start point.
