# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This is a research implementation of Population-Based Training (PBT) for PostgreSQL configuration tuning. The system applies evolutionary optimization to automatically discover high-performance database configurations without domain expertise.

**Key Innovation**: First application of PBT to database configuration optimization, featuring parallel evaluation across multiple PostgreSQL instances and adaptive metric normalization.

## Development Commands

### Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run basic tuning session (minimal knobs, 2-3 minutes)
python -m src.tuner.main --tier minimal --config rapid

# Standard tuning session (core knobs, 15-20 minutes)
python -m src.tuner.main --tier core --config standard

# Comprehensive tuning (standard knobs, 1-2 hours)
python -m src.tuner.main --tier standard --config thorough
```

### Advanced Usage

```bash
# Custom configuration
python -m src.tuner.main --tier core --population 8 --generations 30

# Custom workload
python -m src.tuner.main --workload-file workloads/custom_queries.json

# External benchmarks
python -m src.tuner.main --benchmark sysbench --sysbench-tables 4
python -m src.tuner.main --benchmark tpch --scale-factor 1.0

# Warm-Starting (Transfer Learning across hardware boundaries)
python -m src.tuner.main --warm-start results/best_configs/best_config_YYYYMMDD_HHMM.json
```

### Evaluation Commands

```bash
# Compare PBT-tuned config vs default PostgreSQL (Docker, 5 repetitions)
python -m src.evaluation \
    --session results/olap/pbt_runs/extensive/tuning_sessions/pbt_results_YYYYMMDD_HHMM.json

# More repetitions for tighter confidence intervals
python -m src.evaluation \
    --session results/olap/pbt_runs/extensive/tuning_sessions/pbt_results_YYYYMMDD_HHMM.json \
    --repetitions 10

# Bare-metal fallback (no Docker required — reduced isolation)
python -m src.evaluation \
    --session results/olap/pbt_runs/extensive/tuning_sessions/pbt_results_YYYYMMDD_HHMM.json \
    --no-docker
```

### Setup Commands

```bash
# Database schema setup
python -m src.scripts.setup_database

# Clean up instances
python -m src.scripts.cleanup_instances

# Analyze knob metadata
python -m src.scripts.analyze_knobs
```

## Architecture Overview

### Core Components

The system follows a layered architecture:

1. **Population Manager** (`src/tuner/core/population.py`): Orchestrates PBT algorithm
2. **Worker** (`src/tuner/core/worker.py`): Individual configuration + performance state
3. **Evolution** (`src/tuner/core/evolution.py`): Exploit-explore algorithms
4. **Evaluator** (`src/tuner/evaluator/evaluator.py`): Workload execution and metric collection
5. **Environment Factory** (`src/utils/environments/factory.py`): Docker/Bare-metal environment orchestration
6. **Knob Space** (`src/tuner/config/knob_space.py`): Search space definition and sampling

### Key Design Patterns

- **Parallel Evaluation**: Each worker runs on a dedicated PostgreSQL instance (ports 5440+)
- **Dual-Benchmarking**: Supports both internal JSON workloads and external C-binaries (sysbench/tpch)
- **Snapshot Management**: Intelligent baseline snapshots for fast restarts
- **Feature-Driven Scoring**: Workload-feature-conditioned weighting with compatibility policy and robust normalization

### Directory Structure

```
src/tuner/
├── core/           # PBT algorithm implementation
├── config/         # Configuration management
├── evaluator/      # Performance evaluation
└── main.py         # Entry point

src/utils/
├── environments/    # Docker/Bare-metal database environment backends
├── logger/          # Logging setup and formatters
├── metrics.py       # Performance metrics and scoring
├── rescoring.py     # Shared post-hoc global score recalibration utilities
└── restart_manager.py

src/evaluation/          # Post-hoc evaluation tools (independent of PBT loop)
├── types.py         # Dataclasses: ComparisonConfig, RunResult, etc.
├── loader.py        # load_tuning_session() — parse pbt_results JSON
├── statistics.py    # Wilcoxon + bootstrap CI + Holm-corrected secondary endpoints + Cohen's d
├── runner.py        # ComparisonRunner — main orchestrator
└── __main__.py      # CLI: python -m src.evaluation

src/database/            # Database connection and management
src/knobs/               # Knob metadata retrieval
src/scripts/             # Utility scripts
docker/                  # Docker evaluation images
├── eval.Dockerfile      # PostgreSQL + sysbench + TPC-H dbgen
├── build_dbgen.sh       # TPC-H dbgen compilation
└── run_power_test.sh    # TPC-H 22-query power test runner
docs/                    # Documentation
results/                 # Optimization results
├── olap/comparisons/    # evaluate_tuning output (JSON with statistics)
└── oltp/comparisons/
workloads/               # Workload definitions
tests/                   # Test suite
```

## Common Development Tasks

### Adding New Knobs

1. Update knob metadata in `src/knobs/knob_metadata.py`
2. Add to appropriate tier in `src/tuner/config/knob_loader.py`
3. Update perturbation logic in `src/tuner/core/evolution.py`

### Creating New Workloads

1. Create JSON file in `workloads/` directory
2. Define queries with SQL templates and weights
3. Use parameter injection: `{id}`, `{k_val}`, `{threshold}`, etc.

### Modifying PBT Algorithm

1. Core algorithm changes in `src/tuner/core/evolution.py`
2. Population management in `src/tuner/core/population.py`
3. Configuration in `src/tuner/config/tuner_config.py`

### Testing Changes

```bash
# Quick validation with minimal configuration
python -m src.tuner.main --tier minimal --config rapid --population 2 --generations 5

# Check logging output
python -m src.tuner.main --tier core --config standard --verbose DEBUG
```

## Performance Considerations

- **Instance Overhead**: Each worker requires a separate PostgreSQL instance (5440+)
- **Memory Usage**: Scales with population size and workload complexity
- **Evaluation Duration**: Configurable via `--duration` parameter (default: 30 seconds)
- **Hardware Requirements**: Multi-core recommended for parallel evaluation

## Key Files to Understand

- `src/tuner/main.py` - Main orchestration and CLI
- `src/tuner/core/population.py` - PBT algorithm implementation
- `src/tuner/evaluator/evaluator.py` - Performance measurement
- `src/tuner/config/knob_space.py` - Knob space management
- `src/utils/environments/factory.py` - Environment backend selection and lifecycle
- `docs/FEATURE_DRIVEN_SCORING.md` - Canonical reference for the scoring-v2 architecture and migration path

## Research Context

This work builds on:

- DeepMind's Population-Based Training (Jaderberg et al., 2017)
- OtterTune's adaptive metric normalization (Van Aken et al., 2017)
- CDBTune's intelligent restart management (Zhang et al., 2019)

## Debugging Tips

- Check `results/` directory for JSON logs and HTML reports
- Use `--verbose DEBUG` for detailed logging
- Verify PostgreSQL instances are running on ports 5440+
- Check `.env` file for database credentials
- Use `src/scripts/cleanup_instances.py` to reset state

## Common Issues

- **Connection Errors**: Ensure PostgreSQL instances are properly initialized
- **Permission Issues**: Check instance data directories permissions
- **Memory Limits**: Reduce population size for low-memory systems
- **Long Runtimes**: Adjust `--duration` parameter for faster testing

## Academic Research Context

This is academic research software with non-commercial license. For commercial licensing inquiries, contact repository maintainers.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:

- ALWAYS read graphify-out/GRAPH_REPORT.md before reading any source files, running grep/glob searches, or answering codebase questions. The graph is your primary map of the codebase.
- IF graphify-out/wiki/index.md EXISTS, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
