# Configuration File IO

> 12 nodes · cohesion 0.17

## Key Concepts

- **main.py** (11 connections) — `src/tuner/main.py`
- **parse_args()** (5 connections) — `src/tuner/main.py`
- **_build_parser()** (3 connections) — `src/evaluation/__main__.py`
- **Parse command-line arguments.      Returns:         Parsed CLI arguments.** (1 connections) — `src/analysis/tier_generator.py`
- **CLI entry point for Bayesian Optimization baseline runner.** (1 connections) — `src/scripts/bo_baseline/__main__.py`
- **evaluate_tuning — CLI entry point ===================================  Run as a** (1 connections) — `src/evaluation/__main__.py`
- **Build and return the argument parser for the evaluation CLI.** (1 connections) — `src/evaluation/__main__.py`
- **best_config()** (1 connections) — `src/tuner/main.py`
- **best_score()** (1 connections) — `src/tuner/main.py`
- **PBT PostgreSQL Tuner - End-to-End Application ==================================** (1 connections) — `src/tuner/main.py`
- **Parse command-line arguments** (1 connections) — `src/tuner/main.py`
- **Command-line entry point for the visualization framework.** (1 connections) — `src/visualization/__main__.py`

## Relationships

- [[Connection Reuse]] (22 shared connections)
- [[Docker Manifest Tests]] (3 shared connections)
- [[BO Baseline & Workload]] (2 shared connections)
- [[Instance Management]] (1 shared connections)

## Source Files

- `src/analysis/tier_generator.py`
- `src/evaluation/__main__.py`
- `src/scripts/bo_baseline/__main__.py`
- `src/tuner/main.py`
- `src/visualization/__main__.py`

## Audit Trail

- EXTRACTED: 28 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*