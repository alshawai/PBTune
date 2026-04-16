---
name: hyperparameter-optimization
description: HPO taxonomy (grid, random, Bayesian, evolutionary, PBT), search space design, evaluation protocols. Use when implementing or reviewing optimization algorithms.
---

# Hyperparameter Optimization (HPO)

When designing, implementing, or tuning an HPO algorithm (like Bayesian Optimization, Grid Search, or Population-Based Training), follow these guidelines to ensure rigour.

## Search Space Design

The search space is often more important than the algorithm choice.
- **Categorical**: Discrete choices without an inherent ordering (e.g., `["sgd", "adam", "rmsprop"]`).
- **Ordinal / Integer**: Ordered discrete values (e.g., `num_layers` in `[2, 4, 8, 16]`).
- **Continuous (Linear)**: Values sampled uniformly (e.g., `learning_rate` between `0.01` and `0.05`).
- **Continuous (Log-Scale)**: Highly sensitive parameters where orders of magnitude matter (e.g., `learning_rate` between `1e-5` and `1e-1`, regularization strength). Use `exp(uniform(log(min), log(max)))`.

> **Note**: Avoid excessively large bounds. If the model continually selects the maximum bound, it indicates the bounds should be expanded or the algorithm is failing to converge.

## Comparing HPO Algorithms

When benchmarking different optimization algorithms (e.g., comparing BO to Random Search or evolutionary strategies):
- **Equivalence of Budget**: Never compare algorithms using 'number of iterations' if the cost per iteration varies widely. Compare them on *Total Wall-clock Time* or *Total Objective Function Evaluations*.
- **Same Evaluator Pipeline**: All algorithms must use the exact same underlying measurement pipeline (e.g., same training epochs and validation procedure) to ensure fairness.

## Evaluation Protocols

- Optimization traces should record the *incumbent* (best-found-so-far) curve over time.
- Randomness heavily affects HPO algorithms. Always run the optimizer itself multiple times (with different explicit seeds) to establish the algorithm's variance and sample efficiency.
- Clearly differentiate between the *optimization metric* (what the algorithm sees, e.g., validation loss) and the *test metric* (unseen data or independent evaluation run).

## Method-Specific Caveats

- **Grid/Random**: Will fail in high dimensions (>10).
- **Bayesian Optimization (BO)**: Gaussian Processes scale $O(N^3)$. Not suitable for extremely large numbers of sequential evaluations unless using specialized approximations (e.g., Random Forests/SMAC or Tree Parzen Estimators/Optuna).
- **Population-Based Training (PBT)**: Excels at dynamic scheduling and joint optimization of weights and hyperparams. Requires maintaining diverse populations (avoid premature convergence) and handling failed evaluations gracefully.
