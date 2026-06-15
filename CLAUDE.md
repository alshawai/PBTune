# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This is a research implementation of Population-Based Training (PBT) for PostgreSQL configuration tuning. The system applies evolutionary optimization to automatically discover high-performance database configurations without domain expertise.

**Key Innovation**: First application of PBT to database configuration optimization, featuring parallel evaluation across multiple PostgreSQL instances and adaptive metric normalization.

## Development Commands

### Quick Start

```bash
# Setup environment (Recommended for Linux/macOS)
# (Handles system dependencies, Python version checks, and GCC quirks)
./scripts/bootstrap.sh
source .venv/bin/activate

# Alternative Setup: Conda (Best for avoiding C++ compilation)
# conda env create -f environment.yml
# conda activate pbt-tuning

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
python -m src.tuner.main --warm-start results/olap/pbt_runs/extensive/best_configs/best_config_YYYYMMDD_HHMM.json
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
python -m src.scripts.analyze_knob_importance
```

## Architecture Overview

### Core Components

The system follows a layered architecture:

1. **Population Manager** (`src/tuner/core/population.py`): Orchestrates PBT algorithm
2. **Worker** (`src/tuner/core/worker.py`): Individual configuration + performance state
3. **Evolution** (`src/tuner/core/evolution.py`): Exploit-explore algorithms
4. **Generation Barriers** (`src/tuner/core/barriers.py`): Lockstep B1–B17 synchronisation
5. **Workload Orchestrator** (`src/tuner/benchmark/orchestrator.py`): Per-worker apply → run → measure pipeline (formerly "Evaluator")
6. **Restart Policy** (`src/tuner/benchmark/restart_policy.py`): TuningMode-driven restart decisions
7. **Environment Factory** (`src/utils/environments/factory.py`): Docker / bare-metal lifecycle
8. **Knob Space** (`src/tuner/config/knob_space.py`): Search space definition and LHS sampling
9. **Composite Scorer** (`src/utils/scoring/`): Feature-driven score = G × Σ(wᵢ × uᵢ)
10. **Timing Recorder** (`src/utils/timing.py`, `src/utils/session_clock.py`): Monotonic-clock instrumentation (schema v1.1)

### Key Design Patterns

- **Parallel Evaluation**: Each worker runs on a dedicated PostgreSQL instance (ports 5440+)
- **Dual-Benchmarking**: Supports both internal JSON workloads and external C-binaries (sysbench/tpch)
- **Snapshot Management**: Intelligent baseline snapshots for fast restarts
- **Feature-Driven Scoring**: Workload-feature-conditioned weighting with reliability gate and quantile-anchored normalization
- **Lockstep Generation Barriers**: B1–B17 synchronisation so every worker's measurement window experiences identical contention
- **Hardware-Aware Encoding**: Knobs serialise as fractions of detected resources for cross-host portability
- **Timing Instrumentation**: Three-layer (`bootstrap_timing`, `generation_timing`, per-worker) recorders aggregate into the session JSON

### Directory Structure

```text
src/tuner/
├── core/                # population, worker, evolution, barriers
├── config/              # knob_space, knob_loader, tuner_config
├── benchmark/           # orchestrator (was "evaluator"), restart_policy, workload
└── main.py              # CLI entry point

src/utils/
├── environments/        # base, docker, bare_metal, factory
├── scoring/             # scorer, normalization, weights, policies, workload_features, contracts, constants, outlier_filtering
├── logger/              # Colored logging + HTML output + adapters
├── applicator.py        # KnobApplicator — ALTER SYSTEM + verify() read-back
├── metrics.py           # PerformanceMetrics dataclass
├── metric_instrumentation.py
├── hardware_info.py     # WorkerResources detection
├── rescoring.py         # Post-hoc global score recalibration utilities
├── timing.py            # TimingRecorder + TimingRecord (schema v1.1, monotonic clock)
├── session_clock.py     # session_timestamp() — wall-clock for filenames/log lines
└── types.py

src/evaluation/          # Post-hoc evaluation tools (independent of PBT loop)
├── types.py             # Dataclasses: ComparisonConfig, RunResult, etc.
├── loader.py            # load_tuning_session() — schema-tolerant session JSON parser
├── statistics.py        # Wilcoxon + bootstrap CI + Holm-corrected secondary endpoints + Cohen's d
├── runner.py            # ComparisonRunner — main orchestrator
├── exceptions.py        # Domain-specific exception hierarchy
└── __main__.py          # CLI: python -m src.evaluation

src/analysis/            # data_loader, importance, hardware_validator, tier_generator, timing_breakdown
src/database/            # connection, data_loader, management
src/knobs/               # knob_metadata, retrieval, preprocess_knobs, policy
src/benchmarks/          # executor + sysbench/ + tpch/
src/scripts/             # setup_database, cleanup_instances, analyze_knobs, analyze_knob_importance,
                         # pbt_vs_bo_comarison (sic), bo_baseline/ subpackage
src/visualization/       # plots, loaders, registry, theme, export, __main__
docker/                  # Docker evaluation images
├── eval.Dockerfile      # PostgreSQL + sysbench + TPC-H dbgen
├── build_dbgen.sh       # TPC-H dbgen compilation
└── run_power_test.sh    # TPC-H 22-query power test runner
data/                    # Knob metadata, policy, tier CSVs
├── knob_metadata.json
├── knob_policy.json
├── postgresql_all_knobs.csv
├── expert_defined_knobs/  # minimal | core | standard | extensive CSVs
└── data_driven_knobs/     # Workload-specific tiers from analysis pipeline
docs/                    # Documentation (Diataxis: getting-started/guides/reference/architecture/research)
results/                 # Optimization results
├── olap/{pbt_runs,bo_runs,comparisons,baselines}/{tier}/
└── oltp/{oltp_read_only,oltp_read_write,oltp_write_only}/{pbt_runs,bo_runs,comparisons,baselines}/{tier}/
workloads/               # Workload definitions (oltp.json, olap.json, mixed.json, custom)
tests/                   # Test suite (unit/ — analysis, benchmarks, config, core, evaluation, knobs, scoring, scripts, utils)
```

## Common Development Tasks

### Adding New Knobs

1. Add the knob row to the appropriate tier CSV in `data/expert_defined_knobs/` (or generate via `data/data_driven_knobs/` for data-driven tiers)
2. Ensure the knob is present in `data/knob_metadata.json` (run `python -m src.scripts.analyze_knobs --refresh-raw` if it isn't)
3. If the knob has tuning constraints, update `data/knob_policy.json` and the policy logic in `src/knobs/policy.py`
4. Knob retrieval is handled by `src/knobs/retrieval.py`; loading by `src/tuner/config/knob_loader.py`
5. Update perturbation logic in `src/tuner/core/evolution.py` only if the knob needs special perturbation semantics

### Creating New Workloads

1. Create JSON file in `workloads/` directory
2. Define queries with SQL templates and weights
3. Use parameter injection: `{id}`, `{k_val}`, `{threshold}`, etc.

### Modifying PBT Algorithm

1. Core algorithm changes in `src/tuner/core/evolution.py`
2. Population management in `src/tuner/core/population.py`
3. Configuration in `src/tuner/config/tuner_config.py`
4. Lockstep barriers (B1–B17) in `src/tuner/core/barriers.py`
5. Per-worker evaluation flow in `src/tuner/benchmark/orchestrator.py`

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
- `src/tuner/benchmark/orchestrator.py` - WorkloadOrchestrator (apply → run → measure)
- `src/tuner/config/knob_space.py` - Knob space management
- `src/utils/environments/factory.py` - Environment backend selection and lifecycle
- `src/utils/scoring/scorer.py` - CompositeScorer (S = G × Σ(wᵢ × uᵢ))
- `src/utils/timing.py` - Timing instrumentation primitives (schema v1.1)
- `docs/architecture/feature-driven-scoring.md` - Canonical reference for the scoring-v2 architecture
- `docs/architecture/overview.md` - Top-level system map

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
