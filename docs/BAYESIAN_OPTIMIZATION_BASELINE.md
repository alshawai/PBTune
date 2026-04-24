# Bayesian Optimization Baseline Integration

> Last reviewed: 2026-04-25

See also: [Algorithm Comparison](./ALGORITHM_COMPARISON.md), [Documentation Index](./README.md)

This document explains the Bayesian Optimization (BO) implementation in our framework. It serves as a direct, apples-to-apples state-of-the-art baseline against which our Population-Based Training (PBT) approach is evaluated. 

## 1. Overview and Rationale

To accurately measure the effectiveness and convergence speed of PBT, we need a rigorous baseline. Bayesian Optimization (specifically using Sequential Model-Based Algorithm Configuration - SMAC) is the industry standard for database tuning (used by systems like OtterTune and iTuned).

Our BO implementation uses the exact same databases, configuration spaces, and scoring pipelines as PBT. This eliminates confounding variables (e.g., different hardware, varying latency/throughput weights, different crash handling) and ensures that we are strictly comparing the optimization algorithms themselves.

## 2. Architecture and Design

The Bayesian Optimization pipeline is compartmentalized in the `src/scripts/bo/` module, exposing a clean interface that integrates seamlessly with the core database Evaluator.

### 2.1 BOEngine (`engine.py`)

The `BOEngine` acts as a pure mathematical black-box optimizer. It wraps the `smac` library, abstracting away the intricacies of the surrogate model (Random Forest) and acquisition function. 
- It accepts a configuration space and a generic objective function.
- It is entirely unaware of PostgreSQL, executing optimization asynchronously and outputting mathematical results (the best incumbent parameters and a history of costs).

### 2.2 PBTObjectiveAdapter (`interface.py`)

The `PBTObjectiveAdapter` is the crucial bridge that ensures a fair comparison. It converts standardized ConfigSpace hyperparameter dictionaries into PostgreSQL definitions, and pipes them directly through the exact identical `evaluator.evaluate_worker()` function used by PBT.

By using the exact same `Evaluator` class:
- **Metrics Calculation:** BO receives the same throughput, latency, and resource metrics.
- **Instance Management:** BO waits for the exact same database restart cycles and caching warmups.
- **Penalty identicality:** If a configuration crashes the database (e.g., out-of-memory), BO receives the identical standardized `crash_score` or `dead_config_score` penalty that PBT forces on its population.

## 3. Cost Function Unification

A major architectural consideration is how "performance" is viewed mathematically by the respective algorithms:

- **PBT (Maximization):** Seeks to maximize a multi-variate performance *Score* (where a higher score means higher throughput and better latency).
- **SMAC / BO (Minimization):** Seeks to minimize a mathematical *Cost*. 

The `PBTObjectiveAdapter` handles this invisibly inside `bo_objective_function`:

```python
def bo_objective_function(self, configuration: Any, seed: int = 0) -> float:
    # ... applies proposed config ...
    metrics, score = self.evaluate(config_dict)
    
    # Negate the score: Maximizing score is mathematically identical to Minimizing -score
    return -float(score)
```
When `BOEngine` logs the final evaluation history, it translates the internal cost back to the positive PBT score, ensuring that the resulting JSON files match the PBT output structures 1-to-1 for seamless analytic graphing.

## 4. How to Run the BO Baseline

The BO runner shares an almost identical command-line interface as the PBT runner (`main.py`).

```bash
# Run BO on standard OLTP workload for 50 evaluations
python -m src.scripts.run_bo_comparison --workload oltp --config standard --max-evals 50

# Run BO restricting the tuning space to the core knobs
python -m src.scripts.run_bo_comparison --workload oltp --tier core --max-evals 100

# Advanced BO run with a specific random seed for repeatability
python -m src.scripts.run_bo_comparison --benchmark sysbench --seed 42 --max-evals 100 --initial-design-size 15
```

All results are dynamically tracked and cleanly outputted to `results/<workload>/bo_runs/<tier>/bo_results_<timestamp>.json`, directly matching the structure expected by the `plot_bo_vs_pbt.py` analysis scripts.
