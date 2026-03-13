# Population Class Implementation Summary

> Last reviewed: 2026-03-13

See also: [Documentation Index](./README.md)

## Overview

The **Population class** is the orchestrator of the PBT algorithm. It manages a pool of Worker instances, coordinates parallel evaluations, and triggers exploit-explore steps.

## Key Components

### 1. `PopulationConfig` (Dataclass)
Configuration parameters for Population behavior:

```python
@dataclass
class PopulationConfig:
    population_size: int = 8                          # Number of workers
    ready_interval: int = 3                           # Steps before exploit-explore
    exploit_quantile: float = 0.25                    # Bottom/top 25% for selection
    perturbation_factors: tuple[float, float] = (0.8, 1.2)  # ±20% perturbation
    convergence_threshold: float = 0.05               # Std dev threshold
    max_generations: int = 100                        # Maximum generations
    early_stopping_patience: int = 10                 # Generations without improvement
```

### 2. `GenerationResult` (Dataclass)
Results from one generation:

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
```

### 3. `Population` Class

**Core Responsibilities:**
- Worker pool initialization and lifecycle management
- Parallel/sequential evaluation orchestration
- Exploit-explore triggering at appropriate intervals
- Population-level statistics and history tracking
- Convergence detection and early stopping

**Key Methods:**

#### `initialize(initial_configs=None)`
Creates workers with random or provided configurations.

#### `evaluate_generation(evaluate_fn, parallel=True, max_workers=None)`
Evaluates all workers in current generation. Supports:
- **Sequential execution**: For debugging or when resources are limited
- **Parallel execution**: Using ThreadPoolExecutor for efficiency

**The `evaluate_fn` signature:**
```python
def evaluate_fn(worker: Worker) -> tuple[PerformanceMetrics, float]:
    # 1. Apply worker.knob_config to PostgreSQL
    # 2. Run workload
    # 3. Measure performance
    # 4. Compute score
    return metrics, score
```

#### `exploit_and_explore(require_ready=True, verbose=False)`
Performs PBT's exploit-explore step:
1. Identifies poor and elite workers (via `truncation_selection`)
2. Clones elite configs to poor workers (exploit)
3. Perturbs configurations (explore)

Returns the number of workers modified.

#### `train_generation(evaluate_fn, parallel=True, require_ready=True, verbose=False)`
**Main training loop method** - executes one complete PBT generation:
1. Evaluates all workers
2. Records generation statistics
3. Performs exploit-explore
4. Increments generation counter

Returns `GenerationResult` with performance summary.

#### `should_stop()`
Checks if training should stop early based on:
- Maximum generations reached
- Early stopping patience exceeded
- Population converged

#### `get_best_configuration()`
Returns `(best_config, best_score)` tuple for the best worker.

#### `get_population_summary()`
Returns comprehensive statistics dictionary with:
- Current generation
- Population size
- Best/mean/std scores
- Best worker ID and config
- Convergence status
- Best overall score
- Generations without improvement

## Design Decisions

### 1. **Functional Composition Pattern**
Population **delegates** to `evolution.py` functions rather than reimplementing logic:
- `execute_exploit_explore()` for exploit-explore step
- `get_population_statistics()` for stats
- `check_convergence()` for convergence detection
- `get_best_worker()` for best selection

**Why?** Separation of concerns - Population manages lifecycle, Evolution provides algorithms.

### 2. **Flexible Evaluation**
The `evaluate_fn` parameter allows users to provide custom evaluation logic:
- Real database workloads (SYSBENCH, TPC-H)
- Mock evaluations for testing
- Simulation-based evaluation

**Why?** Population class is agnostic to evaluation details - works with any scoring function.

### 3. **Parallel Execution Support**
ThreadPoolExecutor for concurrent worker evaluation:
```python
with ThreadPoolExecutor(max_workers=max_workers) as executor:
    future_to_worker = {executor.submit(evaluate_fn, worker): worker 
                        for worker in self.workers}
    for future in as_completed(future_to_worker):
        # Process results...
```

**Why?** Dramatically speeds up evaluation when workers can run independently.

### 4. **History Tracking**
Maintains `List[GenerationResult]` for all generations:
- Enables analysis of evolution progress
- Supports checkpointing and recovery
- Allows adaptive strategy adjustment

### 5. **Multiple Stopping Conditions**
Flexible termination via `should_stop()`:
- Max generations (hard limit)
- Early stopping (no improvement)
- Convergence (population saturated)

**Why?** Different scenarios need different stopping criteria.

## Usage Pattern

```python
# 1. Setup
knob_space = get_knob_space('minimal')
config = PopulationConfig(population_size=8, max_generations=50)
population = Population(knob_space, config)
population.initialize()

# 2. Define evaluation
def evaluate_worker(worker):
    apply_config(worker.knob_config)
    metrics = run_workload()
    score = compute_score(metrics)
    return metrics, score

# 3. Training loop
for generation in range(config.max_generations):
    result = population.train_generation(evaluate_worker, parallel=True)
    
    print(f"Gen {generation}: best={result.best_score:.4f}")
    
    if population.should_stop():
        break

# 4. Get results
best_config, best_score = population.get_best_configuration()
print(f"Best configuration: {best_config}")
```

## Testing

All tests passing (see `src/tuner/core/__main__.py`):

✅ **TEST 3.1**: Population initialization  
✅ **TEST 3.2**: Sequential evaluation  
✅ **TEST 3.3**: Complete training generation  
✅ **TEST 3.4**: Multi-generation training loop  
✅ **TEST 3.5**: Early stopping detection  
✅ **TEST 3.6**: Population summary statistics  

## Integration with PBT Components

```
┌─────────────────────────────────────────────────────────────┐
│                    Population Class                         │
│  (Orchestrator - manages lifecycle & training loop)         │
└─────────────────────────────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
┌──────────────┐  ┌─────────────────┐  ┌──────────────┐
│    Worker    │  │    Evolution    │  │  Evaluator   │
│   (State)    │  │  (Algorithms)   │  │ (Execution)  │
└──────────────┘  └─────────────────┘  └──────────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │   KnobSpace     │
                  │  (Search Space) │
                  └─────────────────┘
```

## Example Output

```
Gen  0: best=0.9328, mean=0.7129, std=0.1326, exploited=0
Gen  1: best=0.8887, mean=0.6584, std=0.1281, exploited=2
Gen  2: best=0.9412, mean=0.6971, std=0.1240, exploited=2
Gen  3: best=0.8530, mean=0.6508, std=0.1171, exploited=2
...

Best configuration (score=0.9412):
  shared_buffers: 85596
  effective_cache_size: 114484
  work_mem: 20887
  random_page_cost: 0.968
  max_parallel_workers_per_gather: 4
```

## File Locations

- **Implementation**: [src/tuner/core/population.py](../src/tuner/core/population.py)
- **Tests**: [src/tuner/core/\_\_main\_\_.py](../src/tuner/core/__main__.py)

---

## Comprehensive Documentation

This document provides a **brief summary** of the Population class. For comprehensive, detailed explanations:

### Core Documentation
- **[PBT Core Components](./PBT_CORE_COMPONENTS.md)**: Complete guide to Worker, Evolution, and Population with detailed explanations, examples, and design decisions
- **[Performance Evaluation](./PERFORMANCE_EVALUATION.md)**: How the evaluate_fn works, metrics collection, and scoring system
- **[Configuration Management](./CONFIGURATION_MANAGEMENT.md)**: How knob configurations are defined and applied to PostgreSQL
