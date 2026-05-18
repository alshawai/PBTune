# Evaluator Core

> 24 nodes · cohesion 0.11

## Key Concepts

- **evolution.py** (8 connections) — `src/tuner/core/evolution.py`
- **GenerationResult** (7 connections) — `src/tuner/core/population.py`
- **.record_generation()** (7 connections) — `src/tuner/core/population.py`
- **check_convergence()** (5 connections) — `src/tuner/core/evolution.py`
- **get_best_worker()** (5 connections) — `src/tuner/core/evolution.py`
- **get_population_statistics()** (5 connections) — `src/tuner/core/evolution.py`
- **.get_population_summary()** (5 connections) — `src/tuner/core/population.py`
- **execute_exploit_explore()** (4 connections) — `src/tuner/core/evolution.py`
- **truncation_selection()** (3 connections) — `src/tuner/core/evolution.py`
- **.get_best_configuration()** (3 connections) — `src/tuner/core/population.py`
- **get_elite_workers()** (2 connections) — `src/tuner/core/evolution.py`
- **get_poor_workers()** (2 connections) — `src/tuner/core/evolution.py`
- **PBT Evolution Strategies =========================  This module implements the e** (1 connections) — `src/tuner/core/evolution.py`
- **Execute complete exploit-explore cycle for the population.      Parameters     -** (1 connections) — `src/tuner/core/evolution.py`
- **Get the elite (top-performing) workers from the population.      Useful for anal** (1 connections) — `src/tuner/core/evolution.py`
- **Get the poor (bottom-performing) workers from the population.      Useful for an** (1 connections) — `src/tuner/core/evolution.py`
- **Get the single best worker from the population.      Parameters     ----------** (1 connections) — `src/tuner/core/evolution.py`
- **Compute statistical summary of population performance.      Useful for monitorin** (1 connections) — `src/tuner/core/evolution.py`
- **Check if population has converged (all workers similar performance).      Conver** (1 connections) — `src/tuner/core/evolution.py`
- **Identify which workers should exploit (copy from) which elite workers.      This** (1 connections) — `src/tuner/core/evolution.py`
- **Get the best configuration found so far.          Returns         -------** (1 connections) — `src/tuner/core/population.py`
- **Get summary statistics for the current population.          Returns         ----** (1 connections) — `src/tuner/core/population.py`
- **Record statistics and results for the current generation.          Computes popu** (1 connections) — `src/tuner/core/population.py`
- **Results from evaluating one generation.      Tracks performance, exploit-explore** (1 connections) — `src/tuner/core/population.py`

## Relationships

- [[CLI Argument Parsing]] (49 shared connections)
- [[PBT Worker Core]] (15 shared connections)
- [[Population Initialization]] (1 shared connections)
- [[Database Config & Connection]] (1 shared connections)
- [[Knob Space Configuration]] (1 shared connections)
- [[Evaluator Fault Injection]] (1 shared connections)

## Source Files

- `src/tuner/core/evolution.py`
- `src/tuner/core/population.py`

## Audit Trail

- EXTRACTED: 49 (72%)
- INFERRED: 19 (28%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*