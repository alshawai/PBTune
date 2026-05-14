# Knob Validation

> 23 nodes · cohesion 0.09

## Key Concepts

- **Worker** (35 connections) — `src/tuner/core/worker.py`
- **.__repr__()** (12 connections) — `src/tuner/core/worker.py`
- **.is_ready()** (4 connections) — `src/tuner/core/worker.py`
- **.initialize()** (3 connections) — `src/tuner/core/population.py`
- **.clone_from()** (2 connections) — `src/tuner/core/worker.py`
- **.get_config_copy()** (2 connections) — `src/tuner/core/worker.py`
- **.perturb()** (2 connections) — `src/tuner/core/worker.py`
- **.reset_to_random()** (2 connections) — `src/tuner/core/worker.py`
- **.update_metrics()** (2 connections) — `src/tuner/core/worker.py`
- **worker.py** (2 connections) — `src/tuner/core/worker.py`
- **String representation with hidden password.** (1 connections) — `src/config/database.py`
- **String representation of the Population.** (1 connections) — `src/tuner/core/population.py`
- **Initialize the worker population.          Uses Latin Hypercube Sampling (LHS) f** (1 connections) — `src/tuner/core/population.py`
- **PBT Worker - Individual Population Member ======================================** (1 connections) — `src/tuner/core/worker.py`
- **Check if worker is ready for exploit/explore operations.          Workers must c** (1 connections) — `src/tuner/core/worker.py`
- **Copy configuration from another worker (EXPLOIT phase).          Parameters** (1 connections) — `src/tuner/core/worker.py`
- **Perturb configuration (EXPLORE phase).          Parameters         ----------** (1 connections) — `src/tuner/core/worker.py`
- **Update worker's performance after evaluation.          This is called after a wo** (1 connections) — `src/tuner/core/worker.py`
- **Get a deep copy of the current configuration.          Returns a copy to prevent** (1 connections) — `src/tuner/core/worker.py`
- **Reset worker to a new random configuration.          Useful for restarting a wor** (1 connections) — `src/tuner/core/worker.py`
- **Human-readable representation.** (1 connections) — `src/tuner/core/worker.py`
- **Single member of the PBT population.      Each worker represents one database co** (1 connections) — `src/tuner/core/worker.py`
- **String representation.** (1 connections) — `src/utils/applicator.py`

## Relationships

- [[Population Initialization]] (50 shared connections)
- [[Database Config & Connection]] (7 shared connections)
- [[PBT Worker Core]] (4 shared connections)
- [[BO Baseline & Workload]] (3 shared connections)
- [[Metric Config Recalibration]] (3 shared connections)
- [[Benchmark Orchestrator]] (2 shared connections)
- [[Snapshot & Persistence]] (2 shared connections)
- [[Evaluator Fault Injection]] (2 shared connections)
- [[PBT Literature & Papers]] (1 shared connections)
- [[BO Config & Worker]] (1 shared connections)
- [[Knob Space Configuration]] (1 shared connections)
- [[Hardware Normalization Tests]] (1 shared connections)

## Source Files

- `src/config/database.py`
- `src/tuner/core/population.py`
- `src/tuner/core/worker.py`
- `src/utils/applicator.py`

## Audit Trail

- EXTRACTED: 55 (70%)
- INFERRED: 24 (30%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*