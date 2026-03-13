# Population-Based Training (PBT): Core Components

> Last reviewed: 2026-03-13

See also: [Documentation Index](./README.md)

## Overview

This document explains the three core components that implement **Population Based Training (PBT)** for PostgreSQL configuration tuning: **Worker**, **Evolution**, and **Population**. These components work together to implement the evolutionary optimization algorithm described in DeepMind's 2017 paper "Population Based Training of Neural Networks."

**What is PBT?** Population Based Training is an evolutionary optimization algorithm that maintains a population of candidate solutions (workers), periodically allowing poor performers to "exploit" good performers by copying their configurations, then "exploring" nearby variations through perturbation.

### Key Insight

Unlike traditional hyperparameter tuning (which evaluates configurations independently), PBT enables configurations to **evolve during training**. Poor performers don't waste time—they copy from successful peers and explore variations, leading to faster convergence and better final results.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Component 1: Worker](#component-1-worker)
3. [Component 2: Evolution](#component-2-evolution)
4. [Component 3: Population](#component-3-population)
5. [How Components Interact](#how-components-interact)
6. [The PBT Algorithm Flow](#the-pbt-algorithm-flow)
7. [Design Decisions](#design-decisions)
8. [Related Documentation](#related-documentation)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     PBT System Architecture                     │
└─────────────────────────────────────────────────────────────────┘

                    ┌───────────────────┐
                    │    Population     │
                    │  (Orchestrator)   │
                    └─────────┬─────────┘
                              │
                 manages      │      coordinates
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
          ▼                   ▼                   ▼
    ┌──────────┐        ┌──────────┐       ┌──────────┐
    │ Worker 0 │        │ Worker 1 │  ...  │ Worker N │
    │ (State)  │        │ (State)  │       │ (State)  │
    └─────┬────┘        └─────┬────┘       └─────┬────┘
          │                   │                   │
          └───────────────────┼───────────────────┘
                              │
                         uses │
                              ▼
                    ┌───────────────────┐
                    │    Evolution      │
                    │   (Algorithms)    │
                    └───────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │                   │
                    ▼                   ▼
            ┌──────────────┐    ┌──────────────┐
            │   Exploit    │    │   Explore    │
            │ (Copy elite) │    │  (Perturb)   │
            └──────────────┘    └──────────────┘
```

### Component Roles

- **Worker**: Individual population member with its own configuration and performance state
- **Evolution**: Stateless algorithms for exploit (truncation selection) and explore (perturbation)
- **Population**: Orchestrator managing worker lifecycle, parallel evaluation, and convergence

---

## Component 1: Worker

**Location**: [src/tuner/core/worker.py](../src/tuner/core/worker.py)

### Purpose

The Worker class represents a **single member of the PBT population**. Each worker maintains:
- Its own database configuration (PostgreSQL knobs)
- Performance metrics and score from evaluations
- Evolutionary state (step count, readiness, lineage)

### Why Workers?

In PBT, we don't evaluate configurations in isolation—we track each configuration's **evolution over time**. Workers encapsulate this state, making it easy to:
1. Track how many times a configuration has been evaluated
2. Determine when it's ready for exploit/explore
3. Maintain lineage (which elite worker did this copy from?)
4. Store performance history

### Key Attributes

```python
@dataclass
class Worker:
    worker_id: int                          # Unique ID (0 to N-1)
    knob_space: KnobSpace                   # Defines valid configurations
    knob_config: Dict[str, Any]             # Current PostgreSQL parameters
    performance_score: float = 0.0          # Composite score (higher = better)
    metrics: Optional[PerformanceMetrics]   # Detailed measurements
    step_count: int = 0                     # Number of evaluations
    ready_interval: int = 3                 # Steps before exploit-eligible
    parent_id: Optional[int] = None         # ID of copied elite worker
    generation_created: int = 0             # When created/last exploited
```

### Worker Lifecycle

A worker progresses through several stages during PBT:

```
┌─────────────┐
│ Initialize  │  Random config sampled from KnobSpace
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Evaluate   │  Config applied → workload executed → metrics collected
└──────┬──────┘  score = f(metrics), step_count += 1
       │
       ▼
┌─────────────┐
│ Ready Check │  step_count >= ready_interval?
└──────┬──────┘
       │
       ├─── No ──────┐
       │             │
       ▼             │
┌─────────────┐      │
│   Exploit?  │      │
└──────┬──────┘      │
       │             │
       ├─── Yes ─────┤  Poor performer → copy from elite
       │             │
       ▼             │
┌─────────────┐      │
│   Explore   │      │  Perturb configuration
└──────┬──────┘      │
       │             │
       └─────────────┘
       │
       └──────► Back to Evaluate
```

### The "Ready" Mechanism

One of PBT's key innovations is the **ready interval**:

```python
def is_ready(self) -> bool:
    """Check if worker has been evaluated enough times for exploit/explore."""
    return self.step_count >= self.ready_interval
```

**Why is this needed?**

- Prevents **premature convergence**: New/exploited workers need time to prove themselves
- Avoids **evaluation noise**: A single bad measurement shouldn't trigger exploitation
- Typical values: 1 (aggressive), 3-5 (conservative)

**Example**: With `ready_interval=3`:
1. Worker 0 initializes (step_count=0, not ready)
2. Evaluate → step_count=1 (not ready)
3. Evaluate → step_count=2 (not ready)
4. Evaluate → step_count=3 (ready! can now be exploited/exploit others)

### Core Methods

#### `update_metrics(metrics, score)`
Records evaluation results and increments step count:

```python
def update_metrics(self, metrics: PerformanceMetrics, score: float):
    self.metrics = metrics
    self.performance_score = score
    self.step_count += 1
```

Called after each evaluation to update worker state.

#### `exploit_from(other_worker)`
Copies configuration from an elite worker:

```python
def exploit_from(self, other_worker: Worker, generation: int):
    self.knob_config = copy.deepcopy(other_worker.knob_config)
    self.parent_id = other_worker.worker_id
    self.generation_created = generation
    self.step_count = 0  # Reset: needs to prove new config
```

**Why reset step_count?** The worker has a new configuration that needs evaluation before being eligible for exploitation again. This prevents cascading exploitations in a single generation.

#### `perturb_config(perturbation_factors)`
Explores nearby configurations through random perturbation:

```python
def perturb_config(self, factors: tuple[float, float]):
    for knob_name, value in self.knob_config.items():
        knob_def = self.knob_space.get_knob(knob_name)
        
        if knob_def.is_numeric():
            # Multiply by random factor (e.g., 0.8 to 1.2)
            factor = np.random.uniform(factors[0], factors[1])
            new_value = value * factor
            # Clamp to valid range
            new_value = knob_def.clamp_value(new_value)
            self.knob_config[knob_name] = new_value
```

Perturbation ensures diversity—even if all poor workers copy the same elite, their perturbed configs will differ.

---

## Component 2: Evolution

**Location**: [src/tuner/core/evolution.py](../src/tuner/core/evolution.py)

### Purpose

The Evolution module provides **stateless algorithms** for the exploit and explore phases of PBT. It implements:
1. **Truncation Selection** (exploit): Which workers should copy from which elites?
2. **Perturbation** (explore): How should copied configs be varied?
3. **Population Statistics**: Convergence detection, best worker selection

### Why Separate Evolution Module?

**Separation of Concerns**:
- **Worker**: Manages individual state
- **Evolution**: Provides algorithms (pure functions)
- **Population**: Orchestrates the process

This design makes algorithms reusable and testable independently of worker/population state management.

### Mathematical Foundation

From the DeepMind PBT paper:

**Exploit (Truncation Selection)**:
```
For each worker wᵢ:
    if performance(wᵢ) ∈ bottom α quantile:
        wⱼ ~ Uniform(top α quantile)
        wᵢ ← copy(wⱼ)
```

**Explore (Perturbation)**:
```
For each exploited worker wᵢ:
    For each knob k:
        wᵢ.knobs[k] ← wᵢ.knobs[k] × U(0.8, 1.2)
```

Where:
- α = `exploit_quantile` (typically 0.2-0.25, meaning bottom/top 20-25%)
- U(a, b) = uniform random distribution

### Core Functions

#### `truncation_selection(workers, exploit_quantile, require_ready)`

Identifies which workers should exploit which elites.

**Algorithm**:
1. Filter workers by readiness (if required)
2. Sort by performance score (descending)
3. Select top α% as elite, bottom α% as poor
4. Randomly pair each poor worker with an elite

**Why random pairing?** 
- Increases diversity (different poors copy different elites)
- Prevents everyone converging to single best config
- Matches original PBT paper design

**Example with 8 workers, α=0.25**:
```
Sorted by score:
[W5: 0.95, W2: 0.88, W1: 0.82, W7: 0.79, W3: 0.71, W4: 0.68, W0: 0.62, W6: 0.58]

Elite (top 25%): [W5, W2]
Poor (bottom 25%): [W0, W6]

Random pairing:
- W0 copies from W5
- W6 copies from W2
```

#### `perturb_knob_config(config, knob_space, factors)`

Perturbs numerical knobs in a configuration.

**Algorithm**:
1. For each knob in config:
   - If numeric: multiply by random factor ∈ [0.8, 1.2]
   - Clamp result to valid range (min/max)
   - If non-numeric (bool/enum): leave unchanged

**Why not perturb booleans/enums?** 
- Booleans: Flipping randomly adds noise without direction
- Enums: No natural notion of "nearby" values
- Both can be explored through initial random sampling

**Example**:
```
Original: shared_buffers = 8192 (pages)
Factor: 1.15 (random)
Perturbed: 8192 × 1.15 = 9420.8 → 9420
Result: New config explores 15% higher shared_buffers
```

#### `execute_exploit_explore(workers, config)`

High-level function orchestrating the entire exploit-explore step.

**Algorithm**:
```
1. pairs = truncation_selection(workers, config.exploit_quantile)
2. For each (poor_idx, elite_idx) in pairs:
       workers[poor_idx].exploit_from(workers[elite_idx])
       perturb_worker_config(workers[poor_idx], config.perturbation_factors)
3. Return count of exploited workers
```

This is the main entry point called by the Population class.

#### Supporting Functions

**`get_best_worker(workers)`**: Returns worker with highest performance score

**`get_population_statistics(workers)`**: Computes mean, std, best score across population

**`check_convergence(workers, threshold)`**: Checks if standard deviation of scores < threshold (indicates population has converged)

---

## Component 3: Population

**Location**: [src/tuner/core/population.py](../src/tuner/core/population.py)

### Purpose

The Population class is the **orchestrator** of PBT. It manages the worker pool and coordinates the main training loop:

1. Initialize workers with random configurations
2. Evaluate all workers (parallel or sequential)
3. Trigger exploit-explore when appropriate
4. Track statistics and detect convergence
5. Handle early stopping

### Why a Population Class?

PBT is fundamentally a **population-based algorithm**—we need to manage multiple workers collectively:
- Parallel evaluation coordination
- Cross-worker comparisons (who's elite? who's poor?)
- Population-level statistics (mean, std, convergence)
- Generation history tracking

### Key Attributes

```python
class Population:
    knob_space: KnobSpace                    # Defines valid configurations
    config: PopulationConfig                 # Population parameters
    workers: List[Worker]                    # The worker pool
    generation: int = 0                      # Current generation
    history: List[GenerationResult] = []     # Performance history
    best_overall_score: float = 0.0          # Best score ever seen
    generations_without_improvement: int = 0 # For early stopping
```

### Configuration

```python
@dataclass
class PopulationConfig:
    population_size: int = 8                  # Number of workers
    ready_interval: int = 3                   # Steps before exploit-eligible
    exploit_quantile: float = 0.25            # Bottom/top 25%
    perturbation_factors: tuple = (0.8, 1.2)  # ±20% perturbation
    convergence_threshold: float = 0.05       # Std dev threshold
    max_generations: int = 100                # Hard limit
    early_stopping_patience: int = 10         # Gens without improvement
```

### Core Methods

#### `initialize(initial_configs=None)`

Creates the worker pool:

```python
def initialize(self, initial_configs=None):
    if initial_configs:
        # Use provided configs (e.g., resume from checkpoint)
        for i, config in enumerate(initial_configs):
            worker = Worker(i, self.knob_space, config, self.config.ready_interval)
            self.workers.append(worker)
    else:
        # Random sampling from knob space
        for i in range(self.config.population_size):
            config = self.knob_space.sample()
            worker = Worker(i, self.knob_space, config, self.config.ready_interval)
            self.workers.append(worker)
```

**Design choice**: Random initialization ensures diversity at start.

#### `evaluate_generation(evaluate_fn, parallel=True)`

Evaluates all workers using the provided evaluation function.

**The Evaluation Function Contract**:
```python
def evaluate_fn(worker: Worker) -> tuple[PerformanceMetrics, float]:
    """
    User-provided function that:
    1. Applies worker's config to PostgreSQL
    2. Runs workload (e.g., SYSBENCH)
    3. Measures performance
    4. Computes score
    
    Returns (metrics, score)
    """
    pass
```

**Parallel Execution**:
```python
with ThreadPoolExecutor(max_workers=max_workers) as executor:
    future_to_worker = {executor.submit(evaluate_fn, w): w for w in self.workers}
    
    for future in as_completed(future_to_worker):
        worker = future_to_worker[future]
        metrics, score = future.result()
        worker.update_metrics(metrics, score)
```

**Why ThreadPoolExecutor?** 
- Workers can be evaluated independently (no shared state during evaluation)
- Dramatically speeds up evaluation (8 workers evaluated simultaneously vs sequentially)
- I/O-bound operations (database queries) benefit from threads

**Sequential Mode**: Available for debugging or resource-limited scenarios.

#### `exploit_and_explore(require_ready=True, verbose=False)`

Triggers the exploit-explore step:

```python
def exploit_and_explore(self, require_ready=True, verbose=False):
    num_exploited = execute_exploit_explore(
        self.workers,
        self.config,
        require_ready=require_ready,
        verbose=verbose
    )
    return num_exploited
```

**When is this called?** Typically after every generation evaluation, but only affects workers that are "ready."

#### `train_generation(evaluate_fn, parallel=True)`

**The main training loop method**—executes one complete PBT generation:

```python
def train_generation(self, evaluate_fn, parallel=True) -> GenerationResult:
    self.evaluate_generation(evaluate_fn, parallel)
    stats = get_population_statistics(self.workers)
    num_exploited = self.exploit_and_explore()
    converged = check_convergence(self.workers, self.config.convergence_threshold)
    
    if stats['best_score'] > self.best_overall_score:
        self.best_overall_score = stats['best_score']
        self.generations_without_improvement = 0
    else:
        self.generations_without_improvement += 1
    
    result = GenerationResult(
        generation=self.generation,
        best_score=stats['best_score'],
        mean_score=stats['mean_score'],
        std_score=stats['std_score'],
        num_exploited=num_exploited,
        best_worker_id=stats['best_worker_id'],
        best_config=stats['best_config'],
        converged=converged
    )
    self.history.append(result)
    
    self.generation += 1
    return result
```

This encapsulates the entire PBT workflow for one generation.

#### `should_stop()`

Determines if training should terminate:

```python
def should_stop(self) -> bool:
    if self.generation >= self.config.max_generations:
        return True  # Hit generation limit
    
    if self.generations_without_improvement >= self.config.early_stopping_patience:
        return True  # No improvement, early stop
    
    if self.history and self.history[-1].converged:
        return True  # Population converged
    
    return False
```

**Three stopping conditions**:
1. **Max generations**: Hard limit prevents infinite loops
2. **Early stopping**: No improvement for N generations → likely stuck
3. **Convergence**: Population variance too low → diversity exhausted

---

## How Components Interact

### Initialization Phase

```
User Creates Population
         │
         ▼
Population.initialize()
         │
         ├─► Create Worker 0 (random config)
         ├─► Create Worker 1 (random config)
         ├─► ...
         └─► Create Worker N (random config)
```

Each worker samples a random configuration from the KnobSpace.

### Training Loop

```
┌─────────────────────────────────────────────────────────┐
│              Population.train_generation()              │
└─────────────────────────────────────────────────────────┘
                           │
    ┌──────────────────────┼──────────────────────┐
    │                      │                      │
    ▼                      ▼                      ▼
┌─────────┐          ┌─────────┐            ┌─────────┐
│Worker 0 │          │Worker 1 │    ...     │Worker N │
└────┬────┘          └────┬────┘            └────┬────┘
     │                    │                      │
     └────────────────────┼──────────────────────┘
                          │
                 evaluate_fn(worker)
                          │
              ┌───────────┴───────────┐
              │                       │
              ▼                       ▼
     Apply config to DB      Run workload (SYSBENCH)
              │                       │
              └───────────┬───────────┘
                          │
                          ▼
            Collect metrics & compute score
                          │
                          ▼
          worker.update_metrics(metrics, score)
                          │
                          ▼
         ┌────────────────────────────────┐
         │     All workers evaluated?     │
         └────────────────────────────────┘
                          │
                          ▼ Yes
       ┌─────────────────────────────────────┐
       │ Evolution.execute_exploit_explore() │
       └─────────────────────────────────────┘
                          │
          ┌───────────────┴───────────────┐
          │                               │
          ▼                               ▼
    Truncation Selection            For each poor worker:
    (identify poor & elite)              │
                                         ▼
                                  exploit_from(elite)
                                         │
                                         ▼
                                  perturb_config()
```

### Exploit-Explore Details

```
            Generation N: Workers evaluated
                        │
                        ▼
┌──────────────────────────────────────────────────┐
│  Sorted by performance:                          │
│  Elite: [W3: 0.95, W1: 0.89]                     │
│  Middle: [W5: 0.78, W2: 0.72, W4: 0.69, W7: 0.65]│
│  Poor: [W0: 0.58, W6: 0.52]                      │
└──────────────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────┐
│  Pairing (random from elite):                    │
│  W0 → copies from W3                             │
│  W6 → copies from W1                             │
└──────────────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────┐
│  Exploitation:                                   │
│  W0.knob_config = copy(W3.knob_config)           │
│  W0.parent_id = 3                                │
│  W0.step_count = 0 (reset)                       │
│                                                  │
│  W6.knob_config = copy(W1.knob_config)           │
│  W6.parent_id = 1                                │
│  W6.step_count = 0                               │
└──────────────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────┐
│  Exploration (perturbation):                     │
│  For each knob in W0.config:                     │
│    new_value = old_value × random(0.8, 1.2)      │
│  For each knob in W6.config:                     │
│    new_value = old_value × random(0.8, 1.2)      │
└──────────────────────────────────────────────────┘
                        │
                        ▼
     Generation N+1: Evaluate with new configs
```

---

## The PBT Algorithm Flow

Here's the complete end-to-end flow:

### Setup Phase

```python
knob_space = get_knob_space('minimal')  # or 'core', 'standard'

config = PopulationConfig(
    population_size=8,
    ready_interval=3,
    max_generations=50,
    exploit_quantile=0.25
)

population = Population(knob_space, config)
population.initialize()
```

### Training Loop

```python
def evaluate_worker(worker: Worker) -> tuple[PerformanceMetrics, float]:
    # Apply configuration (see CONFIGURATION_MANAGEMENT.md)
    applicator = KnobApplicator()
    applicator.apply(worker.knob_config)
    
    # Run workload (see PERFORMANCE_EVALUATION.md)
    evaluator = Evaluator(workload_config)
    metrics = evaluator.run_workload()
    
    # Compute composite score
    score = compute_score(metrics)
    return metrics, score

for generation in range(config.max_generations):
    # Evaluate all workers, exploit & explore
    result = population.train_generation(evaluate_worker, parallel=True)
    
    print(f"Gen {result.generation}: "
          f"best={result.best_score:.4f}, "
          f"mean={result.mean_score:.4f}, "
          f"exploited={result.num_exploited}")
    
    if population.should_stop():
        print(f"Stopping: {get_stop_reason()}")
        break

best_config, best_score = population.get_best_configuration()
print(f"Best configuration (score={best_score:.4f}):")
for knob, value in best_config.items():
    print(f"  {knob}: {value}")
```

### Why This Works

**Population diversity + Evolutionary pressure**:
1. **Initial diversity**: Random sampling explores different regions
2. **Exploitation**: Poor performers jump to successful regions
3. **Exploration**: Perturbation maintains diversity around good solutions
4. **Convergence**: Over time, population converges to high-performing regions

**Efficiency gain**: Unlike grid search or random search, PBT doesn't waste time evaluating poor configurations—they're replaced by variations of good ones.

---

## Design Decisions

### 1. Functional Composition Pattern

**Decision**: Population delegates to evolution.py functions rather than implementing algorithms itself.

**Why?**
- **Separation of concerns**: Population manages state, Evolution provides algorithms
- **Testability**: Can test truncation selection independently of Population
- **Reusability**: Evolution functions can be used in other contexts
- **Clarity**: Each module has a clear, focused responsibility

### 2. Flexible Evaluation Function

**Decision**: `evaluate_fn` is a user-provided callback, not hardcoded.

**Why?**
- **Flexibility**: Works with any workload (SYSBENCH, TPC-H, custom)
- **Testing**: Can use mock evaluations for unit tests
- **Modularity**: Population doesn't need to know about databases or workloads

**Trade-off**: User must implement evaluation logic, but gains complete control.

### 3. Ready Interval Mechanism

**Decision**: Workers must complete `ready_interval` evaluations before participating in exploit/explore.

**Why?**
- **Prevents premature exploitation**: New configs need time to prove themselves
- **Reduces noise**: Single bad evaluation doesn't trigger exploitation
- **From PBT paper**: Original algorithm includes this for stability

**Typical values**: 1 (aggressive), 3 (moderate), 5 (conservative)

### 4. Parallel Evaluation

**Decision**: ThreadPoolExecutor for concurrent worker evaluation.

**Why?**
- **Speed**: 8 workers evaluated simultaneously vs sequentially → 8× faster
- **I/O-bound**: Database operations benefit from threads (vs CPU-bound → use processes)
- **Independence**: Workers don't share state during evaluation

**Trade-off**: Requires more database connections (one per worker).

### 5. Multiple Stopping Conditions

**Decision**: Three ways to stop training (max generations, early stopping, convergence).

**Why?**
- **Max generations**: Hard safety limit prevents infinite loops
- **Early stopping**: Saves time when no progress is being made
- **Convergence**: Recognizes when population has exhausted diversity

**Implementation**: `should_stop()` checks all three conditions.

### 6. History Tracking

**Decision**: Maintain `List[GenerationResult]` for all generations.

**Why?**
- **Analysis**: Visualize evolution progress, identify trends
- **Checkpointing**: Can resume from any generation
- **Debugging**: Understand why/when exploit-explore happened
- **Adaptive strategies**: Could adjust parameters based on history

### 7. Worker Lineage Tracking

**Decision**: Workers store `parent_id` and `generation_created`.

**Why?**
- **Genealogy analysis**: Which elite configs produced the best final results?
- **Debugging**: Trace back how the best config evolved
- **Research**: Understand evolutionary dynamics

**Example**:
```
W0 (gen 0, random) → score: 0.58
W0 (gen 5, copied W3) → score: 0.82  [parent_id=3]
W0 (gen 12, copied W1) → score: 0.91 [parent_id=1]
```

---

## Related Documentation

### Detailed Component Documentation

- **[Configuration Management](./CONFIGURATION_MANAGEMENT.md)**: KnobSpace (search space definition) and KnobApplicator (applying configs to PostgreSQL)
- **[Performance Evaluation](./PERFORMANCE_EVALUATION.md)**: Evaluator class, metrics collection, psutil integration for accurate resource monitoring
- **[PostgreSQL Connection and Knobs](./POSTGRESQL_CONNECTION_AND_KNOBS.md)**: Database connection management and knob retrieval system

### Prerequisites

- **[Environment Setup](./ENVIRONMENT_SETUP.md)**: Install dependencies (psutil, numpy, psycopg2) and configure database connection

### System Architecture

- **[Population Implementation Summary](./POPULATION_IMPLEMENTATION.md)**: Brief overview of Population class (this document provides more comprehensive explanation)

### Next Steps

After understanding the core PBT components, you'll want to:

1. **Understand evaluation**: Read [PERFORMANCE_EVALUATION.md](./PERFORMANCE_EVALUATION.md) to learn how workers are evaluated
2. **Understand configuration**: Read [CONFIGURATION_MANAGEMENT.md](./CONFIGURATION_MANAGEMENT.md) to learn how configs are applied to PostgreSQL
3. **Run end-to-end**: With all components understood, proceed to integration testing

---

## Example Output

Running PBT on a minimal knob space with 8 workers:

```
Initializing population of 8 workers...
✓ 8 workers initialized with random configs

Generation 0:
  Evaluating 8 workers (parallel)...
  ✓ All workers evaluated
  Best: 0.8234 (Worker 3), Mean: 0.6892, Std: 0.0912
  Exploit-explore: 2 workers exploited

Generation 1:
  Evaluating 8 workers (parallel)...
  ✓ All workers evaluated
  Best: 0.8567 (Worker 3), Mean: 0.7234, Std: 0.0856
  Exploit-explore: 2 workers exploited

Generation 2:
  Evaluating 8 workers (parallel)...
  ✓ All workers evaluated
  Best: 0.8891 (Worker 1), Mean: 0.7589, Std: 0.0798
  Exploit-explore: 2 workers exploited

...

Generation 47:
  Evaluating 8 workers (parallel)...
  ✓ All workers evaluated
  Best: 0.9512 (Worker 1), Mean: 0.9401, Std: 0.0034
  Exploit-explore: 0 workers exploited (all converged)
  ⚠ Convergence detected (std=0.0034 < threshold=0.05)

Training complete!
  Total generations: 48
  Best score: 0.9512
  Best worker: Worker 1

Best configuration found:
  shared_buffers: 131072 (pages, ~1GB)
  effective_cache_size: 524288 (pages, ~4GB)
  work_mem: 16384 (kB, ~16MB)
  maintenance_work_mem: 262144 (kB, ~256MB)
  random_page_cost: 1.1
```

---

## Summary

The three core PBT components work together to implement evolutionary optimization:

1. **Worker**: Individual population member with configuration state
2. **Evolution**: Stateless algorithms for exploit (truncation selection) and explore (perturbation)
3. **Population**: Orchestrator managing worker lifecycle, parallel evaluation, and convergence

**Key Insight**: PBT's power comes from allowing configurations to **evolve during training** rather than evaluating them independently. Poor performers don't waste time—they copy from successful peers and explore variations.

**File Locations**:
- Worker: [src/tuner/core/worker.py](../src/tuner/core/worker.py)
- Evolution: [src/tuner/core/evolution.py](../src/tuner/core/evolution.py)
- Population: [src/tuner/core/population.py](../src/tuner/core/population.py)
- Tests: [src/tuner/core/\_\_main\_\_.py](../src/tuner/core/__main__.py)