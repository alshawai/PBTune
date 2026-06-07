# PBT Core Components

> Last reviewed: 2026-06-07

See also: [Documentation Index](./README.md), [Generation Barriers](./GENERATION_BARRIERS.md), [Performance Evaluation](./PERFORMANCE_EVALUATION.md), [Workload Orchestrator](./WORKLOAD_ORCHESTRATOR.md)

## Overview

This document explains the three core classes that implement Population-Based Training in this project: **Worker**, **Evolution**, and **Population**. Together they realise DeepMind's 2017 PBT algorithm adapted for PostgreSQL configuration tuning.

**What PBT does.** Maintains a population of $N$ workers, each holding its own configuration $\theta_i$ and score $f(\theta_i)$. At each generation, poor performers exploit elites by copying configurations, then explore by perturbing them. Configurations evolve *during* training rather than being evaluated independently.

**What this implementation adds beyond vanilla PBT:**

- **Lockstep generation barriers** so every worker's measurement window experiences identical contention from other workers (see [GENERATION_BARRIERS.md](./GENERATION_BARRIERS.md)).
- **Physical instance cloning during exploit** so an exploited worker takes over the elite's data directory + buffer state, not just the knob values.
- **Per-worker resource slices** (`WorkerResources`) so the population can run safely on a single host with bounded RAM/CPU budgets.
- **Dead-worker rescue** so a single crashed evaluation doesn't block the generation indefinitely.
- **Adaptive normalisation re-scoring** so scores remain comparable as observed metric ranges grow.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Worker](#worker)
3. [Evolution](#evolution)
4. [Population](#population)
5. [Lockstep generation flow](#lockstep-generation-flow)
6. [Exploit-explore details](#exploit-explore-details)
7. [Dead-worker rescue and convergence](#dead-worker-rescue-and-convergence)
8. [Design decisions](#design-decisions)
9. [Related documentation](#related-documentation)

---

## Architecture

```text
                        ┌──────────────────────┐
                        │      Population      │
                        │    (orchestrator)    │
                        └──────────┬───────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
              ▼                    ▼                    ▼
       ┌────────────┐       ┌────────────┐       ┌────────────┐
       │  Worker 0  │       │  Worker 1  │  ...  │  Worker N  │
       │   (state)  │       │   (state)  │       │   (state)  │
       └─────┬──────┘       └─────┬──────┘       └─────┬──────┘
             │                    │                    │
             │ uses               │ uses               │ uses
             ▼                    ▼                    ▼
       ┌──────────────────────────────────────────────────────┐
       │                     Evolution                        │
       │  truncation_selection / execute_exploit_explore /    │
       │  perturb / convergence / population statistics       │
       └──────────────────────────────────────────────────────┘
                                   │
                                   ▼
                        ┌──────────────────────┐
                        │ GenerationBarrier    │
                        │  (B1–B17 lockstep)   │
                        └──────────┬───────────┘
                                   │
                                   ▼
                        ┌──────────────────────┐
                        │ WorkloadOrchestrator │
                        │ (per-worker eval)    │
                        └──────────────────────┘
```

**Roles:**

- **Worker** — individual population member. Owns its config, score, history, and lineage.
- **Evolution** — pure functions for selection, perturbation, statistics, convergence detection. No state.
- **Population** — orchestrator. Manages worker lifecycle, parallel evaluation, exploit-explore triggering, dead-worker rescue, generation logging, score finalisation.

---

## Worker

**Location**: [src/tuner/core/worker.py](../src/tuner/core/worker.py)

A `Worker` represents a single member of the population. It encapsulates:

- the current PostgreSQL configuration,
- the most recent `PerformanceMetrics` and `ScoreBreakdown`,
- evolutionary state (step count, lineage, generation created),
- an environment handle to its own PostgreSQL instance.

```python
@dataclass
class Worker:
    worker_id: int
    knob_space: KnobSpace
    knob_config: Dict[str, Any]
    performance_score: float = 0.0
    metrics: Optional[PerformanceMetrics] = None
    score_breakdown: Optional[ScoreBreakdown] = None
    step_count: int = 0
    ready_interval: int = 3
    parent_id: Optional[int] = None
    generation_created: int = 0
    # ... see source for full attribute set
```

### Key methods

| Method | Purpose |
| --- | --- |
| `is_ready()` | `step_count >= ready_interval`. Workers below this count are not eligible for exploit/explore. |
| `clone_from(other, generation, environment=None)` | Exploit step. Copies the elite's `knob_config`, sets `parent_id`, resets `step_count`. When `environment` is provided, the elite's data directory is also physically cloned (see below). |
| `perturb(factors)` | Explore step. Calls `KnobSpace.perturb_config()` and applies dependency repair (memory-budget enforcement). |
| `update_metrics(metrics, score, breakdown)` | Records evaluation results, increments `step_count`. |
| `get_config_copy()` | Defensive copy for serialisation. |
| `reset_to_random(seed=None)` | Used by the dead-worker rescue path. |
| `to_dict()` | Session-serialisation entry point. |

### Why "ready interval" matters

PBT's ready interval prevents premature exploitation: a worker that just exploited an elite needs at least one full evaluation under its new configuration before it can be ranked as poor again. Without this, a single noisy measurement could trigger a cascade of exploitations within one generation. Typical values: `1` (aggressive, fast convergence), `3` (balanced default), `5` (conservative, more diversity).

### Physical instance cloning during exploit

Since commit `4165ceb`, `Worker.clone_from(other, generation, environment=...)` does more than copy knob values. When the calling code passes the population's `DatabaseEnvironment` handle, the worker's underlying PostgreSQL data directory is **physically replaced** with a snapshot of the elite's data directory.

Why this matters:

- A copied configuration without the underlying database state is "cold" — its buffer cache, page cache, and OS-level state are all empty. The next evaluation includes a long warmup tail that has nothing to do with knob quality.
- With cloning, the exploit inherits the elite's warmed-up state and its first measured generation reflects the configuration honestly.

The clone path is implemented per environment backend — see [`bare_metal.py`](../src/utils/environments/bare_metal.py) and [`docker.py`](../src/utils/environments/docker.py).

---

## Evolution

**Location**: [src/tuner/core/evolution.py](../src/tuner/core/evolution.py)

A module of stateless functions implementing the algorithmic core of PBT. Keeping these as functions (not methods on `Population`) makes them independently testable — see [tests/unit/core/](../tests/unit/core/).

### Public functions

```python
truncation_selection(workers, exploit_quantile, require_ready=True) -> list[tuple[int, int]]
execute_exploit_explore(workers, config, environment=None,
                         require_ready=True, verbose=False) -> int
get_elite_workers(workers, quantile=0.2) -> list[Worker]
get_poor_workers(workers, quantile=0.2) -> list[Worker]
get_best_worker(workers) -> Worker
get_population_statistics(workers) -> dict
check_convergence(workers, threshold) -> bool
```

### Truncation selection

Identifies which workers should exploit which elites:

1. Filter to `is_ready()` workers if `require_ready=True`.
2. Sort by `performance_score` descending.
3. Slice top `α` quantile as elites, bottom `α` as poor.
4. Pair each poor worker with a uniformly-sampled elite.

The elite pairing is uniform-random (not best-elite always) to prevent the population collapsing to a single point — different poor workers learn from different elites, preserving diversity.

### `execute_exploit_explore`

The main entry point called once per generation by `Population.train_generation()`. Returns the count of workers that exploited.

```text
1. pairs = truncation_selection(workers, config.exploit_quantile)
2. for (poor_idx, elite_idx) in pairs:
     workers[poor_idx].clone_from(workers[elite_idx], generation, environment)
     workers[poor_idx].perturb(config.perturbation_factors)
3. return len(pairs)
```

### Convergence

`check_convergence(workers, threshold)` returns `True` when the population's score standard deviation drops below `threshold`. This is one of three stopping signals (the others being max generations and early-stopping patience).

---

## Population

**Location**: [src/tuner/core/population.py](../src/tuner/core/population.py)

The orchestrator. Holds the worker pool, the evaluator, the environment factory, the barriers, and the policy/normalisation state.

### `PopulationConfig`

```python
@dataclass
class PopulationConfig:
    population_size: int = 8
    ready_interval: int = 3
    exploit_quantile: float = 0.25
    perturbation_factors: tuple[float, float] = (0.8, 1.2)
    convergence_threshold: float = 0.05
    max_generations: int = 100
    early_stopping_patience: int = 10
    enable_snapshots: bool = True
    snapshot_restore_interval: int = 10
    num_parallel_workers: int = 1
    worker_resources: Optional[WorkerResources] = None
    # ... see source for full set
```

The fields beyond the textbook PBT parameters:

- **`enable_snapshots` / `snapshot_restore_interval`** — periodic baseline-snapshot restoration to prevent data drift. Implemented per environment backend.
- **`num_parallel_workers`** — how many workers run concurrently on this host. The orchestrator uses this to slice `WorkerResources`.
- **`worker_resources`** — per-worker resource slice (RAM, CPU cores, disk type). Detected at session start by [`detect_worker_resources()`](../src/utils/hardware_info.py).

### `GenerationResult`

```python
@dataclass
class GenerationResult:
    generation: int
    best_score: float
    mean_score: float
    std_score: float
    num_exploited: int
    best_worker_id: int
    best_config: Dict[str, Any]
    converged: bool
    # ... plus per-worker breakdowns and timing
```

### Public methods

| Method | Purpose |
| --- | --- |
| `initialize(initial_configs=None)` | Create N workers. With LHS sampling by default; with explicit configs for warm-start. |
| `setup_worker_instances()` | Create one PostgreSQL environment per worker (Docker container or bare-metal data dir). |
| `setup_snapshots()` | Take the baseline snapshot used by `snapshot_restore_interval`. |
| `evaluate_generation(orchestrator, parallel=True)` | Run all workers; the orchestrator drives the lockstep barriers internally. |
| `train_generation(orchestrator, parallel=True)` | One full PBT generation: evaluate, exploit-explore, record, check convergence. |
| `update_metric_ranges_if_needed()` | Triggered when the normaliser's drift detector fires; recomputes calibration anchors and rescores history for comparability. |
| `rescue_dead_workers(...)` | Replace any worker whose environment has died with a freshly-resampled config and a fresh instance. |
| `record_generation()` | Aggregate per-worker results into a `GenerationResult` and append to history. |
| `should_stop()` | Combined check: max generations / early-stopping patience / convergence. |
| `get_best_configuration()` | Return the best `(config, score)` ever observed. |
| `get_population_summary()` | Final session summary used by the JSON writer in `main.py`. |

### Score finalisation

`_finalize_scores()` runs once at session end and rescores every persisted `PerformanceMetrics` against the *final* normalisation anchors. This is what makes pre- and post-calibration generations comparable in the saved session JSON. The same rescoring helper is used by the post-hoc evaluation suite — see [src/utils/rescoring.py](../src/utils/rescoring.py).

---

## Lockstep generation flow

Every generation goes through a strict lockstep sequence enforced by the [`GenerationBarrier`](../src/tuner/core/barriers.py). Each worker thread waits at every barrier point until **all** workers have arrived — guaranteeing measurement-window overlap.

```text
Generation N

  ┌─────────────────────────────────────────────────────────────┐
  │              Population.evaluate_generation()               │
  └────────────────────────────┬────────────────────────────────┘
                               │ ThreadPoolExecutor(max_workers=N)
                               ▼
   for each worker, in its own thread:
     orchestrator.evaluate_worker(worker)
       ↓
       B1  connect          ← all N workers wait here
       B2  apply config
       B3  restart (or skip)
       B4  reconnect
       B5  verify config
       B6  pre-stats snapshot
       B7  benchmark ready
       B8  warmup
       B9  measurement       ← every worker measures concurrently
       B10 post-stats snapshot
       B11 io delta
       B12 system metrics
       B13 memory pressure
       B14 reliability gate
       B15 vacuum analyze
       B16 score
       B17 disconnect
                               │
                               ▼
                Population.exploit_and_explore()
                Population.record_generation()
                Population.update_metric_ranges_if_needed()
                Population.rescue_dead_workers()
```

Two graceful-degradation paths handle stuck/crashed workers without deadlocking:

1. `barrier.drain_remaining(start_from)` — a worker that catches an exception releases its slots in all barriers it hasn't reached yet.
2. `barrier.abort()` — when the population layer confirms a worker is dead, it instantly breaks every barrier (`BrokenBarrierError` on all waiters).

There is **no per-barrier timeout** — legitimate workloads (e.g. 5-minute OLAP queries) need to wait indefinitely. The dead-worker case is the only thing barriers need to escape from, and `drain_remaining` / `abort` cover it.

Full barrier table and rationale: [GENERATION_BARRIERS.md](./GENERATION_BARRIERS.md).

---

## Exploit-explore details

```text
After generation N evaluations:

  Sorted by score (descending):
    Elite (top 25%):   [W3: 0.95, W1: 0.89]
    Middle:            [W5: 0.78, W2: 0.72, W4: 0.69, W7: 0.65]
    Poor  (bottom 25%):[W0: 0.58, W6: 0.52]

  Truncation selection (uniform-random pairing):
    W0 → exploit W3
    W6 → exploit W1

  Exploit step (Worker.clone_from):
    W0.knob_config = deepcopy(W3.knob_config)
    W0.parent_id   = 3
    W0.step_count  = 0
    + physical data-directory clone via the environment backend

  Explore step (Worker.perturb):
    For each numeric knob k in W0.knob_config:
      k *= U(0.8, 1.2), clamped to bounds
    Memory budget repaired (KnobSpace.repair_config_dependencies)

  Generation N+1: evaluate with the new configs
```

Booleans and enums are perturbed differently — booleans flip with a configurable probability, enums probabilistically jump to a neighbour. Numeric knobs on a log scale are perturbed in log space. See [CONFIGURATION_MANAGEMENT.md](./CONFIGURATION_MANAGEMENT.md#sampling-perturbation-and-dependency-repair).

---

## Dead-worker rescue and convergence

### Dead-worker rescue

`Population.rescue_dead_workers()` runs after every generation. It checks each worker's environment health (`environment.is_alive()`), and for any dead worker:

1. tears down the broken instance,
2. recreates a fresh PostgreSQL instance,
3. resamples a configuration via `_choose_diverse_resample_config()` — which biases toward unexplored regions if the population has already converged on similar configs,
4. resets the worker's `step_count` and lineage.

This avoids the failure mode where a single environment crash silently halves the population's effective diversity.

### Stopping conditions

`should_stop()` returns `True` if any of:

- `generation >= max_generations`,
- `generations_without_improvement >= early_stopping_patience`,
- `check_convergence(workers, convergence_threshold)`.

All three are checked at the end of every generation.

---

## Design decisions

### 1. Functional Evolution module, not methods on Population

Evolution is stateless — `truncation_selection`, `perturb`, `check_convergence` are pure functions. This means:

- they can be tested without standing up a Population,
- the same logic is reused by analysis tooling and the BO baseline (which uses `get_best_worker` for incumbent extraction),
- there's no temptation to silently mutate population state inside an "algorithm" call.

### 2. Lockstep barriers around the measurement window

Without barriers, a worker that finished restarting early would measure under lower contention than a worker still restarting. The barriers force every measurement window to overlap, eliminating that bias. See [GENERATION_BARRIERS.md](./GENERATION_BARRIERS.md) for the full rationale and the abort/drain semantics.

### 3. Physical instance cloning during exploit

A pure knob-value clone is cheap but produces a "cold" inheritor whose first generation's score reflects warmup, not knob quality. Cloning the data directory at exploit time is more expensive but gives the inheritor an honest starting point. The cost is bounded — exploit happens at most once per generation per poor worker.

### 4. Per-worker resource slicing

`WorkerResources` is computed once per session and passed into `KnobSpace.resolve_hardware_ranges()`. Every worker sees the *same* search space but bounded by *its* slice of host resources. This is what makes "8 parallel workers on one host" safe on memory.

### 5. Score finalisation at session end

Adaptive normalisation means early-generation scores are anchored against narrower ranges than late-generation scores. Without finalisation, the saved session JSON would show artefactual generation-on-generation deltas. `_finalize_scores()` resolves this by rescoring every persisted metric against the final calibration anchors.

### 6. Dead-worker rescue, not session abort

A single instance crash should not kill an N-generation tuning session. `rescue_dead_workers()` swaps in a fresh instance + resampled config, logs the event, and continues. The session JSON records the rescue events so post-hoc analysis can see when they happened.

### 7. Three stopping signals

Max generations is a hard ceiling. Early-stopping patience saves time on plateaued runs. Convergence catches diversity collapse. All three are required because each fires for a different reason.

---

## Related documentation

- **[Generation Barriers](./GENERATION_BARRIERS.md)** — the B1–B17 lockstep mechanism in detail.
- **[Performance Evaluation](./PERFORMANCE_EVALUATION.md)** — the `WorkloadOrchestrator` and the `PerformanceMetrics` contract.
- **[Workload Orchestrator](./WORKLOAD_ORCHESTRATOR.md)** — orchestrator internals, restart policy, executor selection.
- **[Configuration Management](./CONFIGURATION_MANAGEMENT.md)** — `KnobSpace`, `KnobApplicator`, perturbation, repair.
- **[Hardware-Aware Normalization](./HARDWARE_AWARE_NORMALIZATION.md)** — `WorkerResources` and warm-start.
- **[Feature-Driven Scoring](./FEATURE_DRIVEN_SCORING.md)** — the scoring math and policies.
- **[Environment Backends](./ENVIRONMENT_BACKENDS.md)** — Docker vs bare-metal, instance cloning, snapshot management.

### File locations

- `Worker`: [src/tuner/core/worker.py](../src/tuner/core/worker.py)
- `Population`, `PopulationConfig`, `GenerationResult`: [src/tuner/core/population.py](../src/tuner/core/population.py)
- Evolution algorithms: [src/tuner/core/evolution.py](../src/tuner/core/evolution.py)
- Generation barriers: [src/tuner/core/barriers.py](../src/tuner/core/barriers.py)
- Tests: [tests/unit/core/](../tests/unit/core/)
