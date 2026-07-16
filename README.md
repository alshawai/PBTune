<p align="center">
  <img src="https://github.com/user-attachments/assets/6fef48b3-6733-4400-a875-d95a9ce28611" alt="PBTune Banner" width="100%">
</p>

<p align="center">
  <a href="https://github.com/alshawai/PBTune/actions/workflows/ci.yml">
    <img src="https://github.com/alshawai/PBTune/actions/workflows/ci.yml/badge.svg" alt="CI">
  </a>
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+">
  </a>
  <a href="https://www.postgresql.org/">
    <img src="https://img.shields.io/badge/postgresql-14+-336791.svg" alt="PostgreSQL 14+">
  </a>
  <a href="https://www.gnu.org/licenses/gpl-3.0">
    <img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="License: GPLv3">
  </a>
  <a href="https://github.com/alshawai/PBTune/stargazers">
    <img src="https://img.shields.io/github/stars/alshawai/PBTune" alt="GitHub stars">
  </a>
</p>

This repository contains a research implementation applying **Population-Based Training (PBT)**, originally developed by DeepMind for neural network hyperparameter optimization, to the domain of database configuration tuning. Our work demonstrates that evolutionary optimization strategies can autonomously discover high-performance PostgreSQL configurations without domain expertise.

## Demo

<p align="center">
  <img src="https://github.com/user-attachments/assets/7054cd5b-8675-44af-ae12-2e158b4677f8" alt="PBTune Demo" width="100%">
</p>

---

## Table of Contents

- [Overview](#overview)
- [Key Innovation](#key-innovation)
- [Architecture](#architecture)
- [Repository Structure](#repository-structure)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Documentation](#documentation)
- [Research Foundation](#research-foundation)
- [Experimental Results](#experimental-results)
- [Future Work](#future-work)
- [Contributing](#contributing)
- [License](#license)
- [Citation](#citation)
- [Acknowledgments](#acknowledgments)

---

## Overview

Database configuration tuning is a critical yet challenging task in database administration. PostgreSQL exposes over 300 configuration parameters ("knobs"), with complex interdependencies and non-linear effects on performance. Traditional approaches rely on:

1. **Manual tuning** by expert DBAs (time-consuming, non-scalable)
2. **Rule-based systems** (inflexible, limited adaptability)
3. **Bayesian Optimization** (sample-inefficient for high-dimensional spaces)
4. **Reinforcement Learning** (requires extensive training data, unstable)

This work proposes a **novel alternative**: applying Population-Based Training—an evolutionary algorithm that maintains a population of configurations, periodically allowing poor performers to "exploit" successful configurations and "explore" nearby variations.

### Why PBT for Database Tuning?

**Key Advantages:**

- ✅ **Parallel Exploration**: Evaluates multiple configurations simultaneously across isolated PostgreSQL instances
- ✅ **Online Adaptation**: Configurations evolve during optimization, avoiding wasted evaluations
- ✅ **Sample Efficiency**: Poor performers copy from elites rather than exploring randomly
- ✅ **No Training Required**: Unlike RL, PBT needs no pre-training phase or external datasets
- ✅ **Automatic Convergence**: Built-in exploitation naturally drives population toward optimal regions

**Novel Contributions:**

1. First application of PBT to database configuration optimization
2. Adaptive metric normalization for heterogeneous workload types
3. Feature-driven composite scoring with compatibility mode for historical sessions
4. Intelligent restart management balancing exploration vs. overhead
5. Multi-instance PostgreSQL orchestration for true parallel evaluation

---

## Key Innovation

### Population-Based Training (PBT)

PBT maintains a population of $N$ workers, each with:

- A **configuration** $\theta_i$ (knob values)
- A **performance score** $f(\theta_i)$ (throughput, latency, etc.)

At regular intervals (generations), the algorithm performs:

1. **Truncation Selection (Exploit)**: Bottom 20% of workers copy configurations from top 20%
2. **Perturbation (Explore)**: Copied configurations are perturbed (±20% for continuous, probabilistic flip for categorical)
3. **Evaluation**: All workers evaluated in parallel on isolated PostgreSQL instances

This creates a co-evolutionary process where configurations **evolve during training**, unlike traditional hyperparameter search methods that evaluate configurations independently.

### Adaptive Components

Our implementation extends vanilla PBT with database-specific adaptations:

- **Proportional Perturbation**: Categorical knobs perturbed based on value space size (30% for booleans, adaptive for enums)
- **Metric Normalization**: OtterTune-inspired percentile-based normalization adapts to observed ranges
- **Feature-Driven Scoring**: Workload features drive composite metric weighting through scoring-v2, with `fixed_v1` retained for compatibility
- **Intelligent Restarts**: CDBTune-inspired batched restarts (every 10 generations) balance configuration changes vs. overhead

See [`docs/architecture/feature-driven-scoring.md`](./docs/architecture/feature-driven-scoring.md) for the scoring model, policy metadata, and migration notes.

See [`docs/architecture/pbt-core.md`](./docs/architecture/pbt-core.md) for detailed algorithm description.

---

## Architecture

```
┌───────────────────────────────────────────────────────────────┐
│                     PBT Tuner System                          │
└───────────────────────────────────────────────────────────────┘

                    ┌──────────────────┐
                    │   Main Tuner     │
                    │  (Orchestrator)  │
                    └────────┬─────────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
          ▼                  ▼                  ▼
    ┌────────────┐    ┌────────────┐    ┌────────────┐
    │ Population │    │ Evaluator  │    │ Instance   │
    │  Manager   │───→│   System   │───→│  Manager   │
    └────────────┘    └────────────┘    └────────────┘
            │                 │               │
            │                 │               │
      ┌─────┴─────┐      ┌────┴────┐   ┌──────┴───┐
      ▼           ▼      ▼         ▼   ▼          ▼
  ┌────────┐  ┌────────┐ │    │ ┌────────┐  ┌────────┐
  │Worker 0│  │Worker 1│ │    │ │ PG:5440│  │ PG:5441│
  │config_0│  │config_1│ │    │ │Instance│  │Instance│
  │score_0 │  │score_1 │ │    │ │worker_0│  │worker_1│
  └────────┘  └────────┘ ... ...└────────┘  └────────┘
     │            │                  │           │
     ↓            ↓                  ↓           ↓
┌─────────────────────────────────────────────────────┐
│         Evolution (Exploit-Explore Cycle)           │
├─────────────────────────────────────────────────────┤
│ • Truncation Selection: Bottom 20% copy top 20%     │
│ • Perturbation: Continuous (±20%), Categorical (30%)│
│ • Convergence Detection: 3 generations stable       │
└─────────────────────────────────────────────────────┘
```

### Core Components

| Component                 | Purpose                                                                  | Location                                                                     |
| ------------------------- | ------------------------------------------------------------------------ | ---------------------------------------------------------------------------- |
| **Population**            | Manages worker pool, orchestrates PBT algorithm                          | [`src/tuner/core/population.py`](src/tuner/core/population.py)               |
| **Worker**                | Individual configuration + performance state                             | [`src/tuner/core/worker.py`](src/tuner/core/worker.py)                       |
| **Evolution**             | Exploit-explore algorithms (selection, perturbation)                     | [`src/tuner/core/evolution.py`](src/tuner/core/evolution.py)                 |
| **Generation Barriers**   | Lockstep B1–B17 synchronisation for measurement fairness                 | [`src/tuner/core/barriers.py`](src/tuner/core/barriers.py)                   |
| **Workload Orchestrator** | Per-worker evaluation: apply config, run benchmark, collect metrics      | [`src/tuner/benchmark/orchestrator.py`](src/tuner/benchmark/orchestrator.py) |
| **Environment Backends**  | Multi-instance PostgreSQL (Docker / bare-metal), CPU subsets, snapshots  | [`src/utils/environments/`](src/utils/environments/)                         |
| **Knob Space**            | Search space definition, sampling, perturbation, hardware-aware ranges   | [`src/tuner/config/knob_space.py`](src/tuner/config/knob_space.py)           |
| **Scoring (v2)**          | Feature-driven composite score with reliability gate                     | [`src/utils/scoring/`](src/utils/scoring/)                                   |

See [`docs/architecture/pbt-core.md`](./docs/architecture/pbt-core.md) for component interaction details.

---

## Repository Structure

```text
.
├── src/                          # Source code
│   ├── tuner/                    # PBT tuning engine
│   │   ├── core/                 # population, worker, evolution, barriers
│   │   ├── config/               # KnobSpace + tier loading
│   │   ├── benchmark/            # WorkloadOrchestrator + restart policy + workload loader
│   │   └── main.py               # CLI entry point
│   ├── utils/                    # Shared utilities
│   │   ├── environments/         # Docker / bare-metal PostgreSQL backends
│   │   ├── scoring/              # Feature-driven scoring v2
│   │   ├── logger/               # Colored logging + HTML output + context
│   │   ├── applicator.py         # KnobApplicator
│   │   ├── metrics.py            # PerformanceMetrics
│   │   ├── metric_instrumentation.py
│   │   ├── hardware_info.py      # WorkerResources detection
│   │   ├── rescoring.py          # Post-hoc global score recalibration
│   │   └── types.py
│   ├── benchmarks/               # External benchmark executors (sysbench, tpch)
│   ├── database/                 # psycopg2 / SQLAlchemy connections + lifecycle
│   ├── config/                   # Env-derived database credentials + data-root resolution
│   ├── knobs/                    # pg_settings retrieval + tuning metadata + policy filter
│   ├── analysis/                 # fANOVA + TreeSHAP + tier generation
│   ├── evaluation/               # Post-hoc default-vs-tuned comparison suite
│   ├── visualization/            # Plot loaders + registry + theme + CLI
│   └── scripts/                  # Setup, cleanup, BO baseline, comparisons
├── docs/                         # Documentation (see docs/README.md for the index)
├── data/                         # Knob metadata, policy, tier CSVs
│   ├── knob_metadata.json
│   ├── knob_policy.json
│   ├── expert_defined_knobs/     # minimal | core | standard | extensive CSVs
│   └── data_driven_knobs/        # workload-specific tiers from analysis pipeline
├── results/                      # Optimization results (JSON + HTML logs)
│   ├── oltp/{workload}/{pbt_runs,bo_runs,comparisons,baselines}/
│   ├── olap/{pbt_runs,bo_runs,comparisons,baselines}/
│   └── analysis/{workload}/
├── workloads/                    # Workload definitions (OLTP, OLAP, custom)
├── notebooks/                    # Jupyter notebooks for analysis
├── tests/                        # Unit test suite
├── requirements.txt              # Runtime Python dependencies
├── requirements-dev.txt          # Dev/test/lint/typecheck dependencies
├── pyproject.toml                # Ruff + mypy configuration
└── Makefile                      # Deterministic local validation targets
```

---

## Installation

### Prerequisites

- **Python 3.11+**
- **PostgreSQL 14+** (with `pg_ctl`, `initdb` in PATH)
- **psutil** (system monitoring)
- **sysbench** (optional, for OLTP workloads)

### Step 1: Clone Repository

```bash
git clone https://github.com/Data-Vanta/ai-database-optimization.git
cd ai-database-optimization
```

### Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

For contributor workflows (tests, lint, type checks), install development
dependencies as well:

```bash
pip install -r requirements-dev.txt
```

**Key Dependencies:**

- `psycopg2-binary` - PostgreSQL adapter
- `psutil` - System resource monitoring
- `numpy` - Numerical operations for PBT
- `pandas` - Knob metadata processing
- `python-dotenv` - Environment variable management
- `pytest` - Unit test runner
- `ruff` - Linting baseline (high-signal correctness checks)
- `mypy` - Type-check baseline for evaluation module

### Step 3: Configure Database

Create a `.env` file from the template:

```bash
cp .env.example .env
```

Edit `.env` with your PostgreSQL credentials:

```env
DB_USER=postgres
DB_PASSWORD=your_secure_password
DB_HOST=localhost
DB_PORT=5432
DB_NAME=test_dataset
```

See [`docs/getting-started/setup.md`](./docs/getting-started/setup.md) for detailed setup instructions.

### Step 4: Initialize Database Schema

```bash
python -m src.scripts.setup_database
```

This creates:

- `sbtest1` table with 10,000 rows (OLTP workload testing)
- Indexes and constraints

### Step 5: Validate Your Development Environment

Run the deterministic command matrix used by CI:

```bash
make lint
make typecheck
make test
# or run all gates
make check-all
```

---

## Quick Start

### Example 1: Rapid Tuning (2-3 minutes)

> **Note**: Actual runtime depends on your hardware (CPU cores, RAM, storage speed). Times shown are estimates for modern multi-core systems with SSD storage.

Optimize 5 core knobs with minimal population for quick testing:

```bash
python -m src.tuners.pbt.main \
  --tier minimal \
  --config rapid \
  --generations 10 \
  --population 4
```

**Output:**

- JSON results: `results/oltp/oltp_read_write/pbt_runs/minimal/tuning_sessions/pbt_results_TIMESTAMP.json`
- HTML log (colored): `results/oltp/oltp_read_write/pbt_runs/minimal/pbt_tuning_TIMESTAMP.html`
- Best config: `results/oltp/oltp_read_write/pbt_runs/minimal/best_configs/best_config_TIMESTAMP.json`

### Example 2: Standard Tuning Session (15-20 minutes)

Tune 13 core knobs with standard PBT configuration:

```bash
python -m src.tuners.pbt.main \
  --tier core \
  --config standard \
  --generations 30 \
  --population 8
```

### Example 3: Comprehensive Tuning (1-2 hours)

Full knob space (36 parameters) with thorough evaluation:

```bash
python -m src.tuners.pbt.main \
  --tier standard \
  --config thorough \
  --generations 50 \
  --population 12 \
  --workload oltp
```

### Example 4: Custom Workload

```bash
python -m src.tuners.pbt.main \
  --tier core \
  --config standard \
  --workload-file workloads/custom_queries.json
```

### Example 5: Tuning a Real Production Database

Point the tuner at your real database using standard environment variables, and it will automatically use `pg_basebackup` to clone a local snapshot for safe, isolated tuning:

```bash
export DB_HOST=my-production-replica.domain.com
export DB_PORT=5432
export DB_USER=admin
export DB_PASSWORD=secret
export DB_NAME=myapp

python -m src.tuners.pbt.main --workload-file workloads/my_real_queries.json
```

### Example 6: Manual Worker Resource Allocation

Override automatic hardware detection to manually allocate RAM and CPU cores for each parallel worker (up to 95% of host capacity):

```bash
python -m src.tuners.pbt.main \
  --worker-ram 4G \
  --worker-cpus 2 \
  --parallel-workers 3
```

### View Results

Open the HTML log in your browser for color-coded output:

```bash
# Path follows the workload-partitioned layout, e.g. for OLTP read-write minimal tier:
LOG=results/oltp/oltp_read_write/pbt_runs/minimal/pbt_tuning_TIMESTAMP.html

# Windows
start "$LOG"

# macOS
open "$LOG"

# Linux
xdg-open "$LOG"
```

### CLI Reference

```bash
python -m src.tuners.pbt.main --help
```

**Key Arguments:**

| Argument        | Options                                    | Default    | Description                            |
| --------------- | ------------------------------------------ | ---------- | -------------------------------------- |
| `--tier`        | `minimal`, `core`, `standard`, `extensive` | `minimal`  | Knob space tier (5, 13, 36, 80+ knobs) |
| `--config`      | `rapid`, `standard`, `thorough`, `research`, `extreme` | `standard` | PBT configuration profile  |
| `--population`  | integer                                    | from profile | Worker count (overrides profile)     |
| `--generations` | integer                                    | from profile | Optimization iterations              |
| `--workload`    | `oltp`, `olap`, `mixed`                    | `oltp`     | Workload type                          |
| `--duration`    | seconds                                    | 30         | Evaluation duration per worker         |
| `--sysbench-workload` | `oltp_read_only`, `oltp_read_write`, `oltp_write_only` | `oltp_read_write` | Sysbench OLTP mode when `--benchmark sysbench` |
| `--scoring-policy` | `fixed_v1`, `feature_driven_v2`          | falls back to PBT config | Scoring policy (`feature_driven_v2` for new runs) |
| `--scoring-policy-version` | version string                    | `2.0`      | Scoring policy version                 |
| `--scoring-calibration-evals` | integer                         | `5`        | Number of evals for normalization calibration |
| `--tuning-mode` | `online`, `offline`, `adaptive`            | `offline`  | Restart policy (online = no restarts; offline = every gen; adaptive = every N gens) |
| `--verbose`     | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `TRACE` | `INFO`   | Console log level                      |

### Sysbench Benchmark Modes

Use explicit Sysbench workload mode selection when running OLTP benchmarks:

```bash
# Read-only OLTP benchmark
python -m src.tuners.pbt.main --benchmark sysbench --sysbench-workload oltp_read_only --tier core --config standard

# Read-write OLTP benchmark (default)
python -m src.tuners.pbt.main --benchmark sysbench --sysbench-workload oltp_read_write --tier core --config standard

# Write-only OLTP benchmark
python -m src.tuners.pbt.main --benchmark sysbench --sysbench-workload oltp_write_only --tier core --config standard
```

Sysbench outputs are partitioned by mode:

- PBT sessions: `results/oltp/{sysbench_workload}/pbt_runs/{tier}/...`
- BO sessions: `results/oltp/{sysbench_workload}/bo_runs/{tier}/...`
- Evaluation comparisons: `results/oltp/{sysbench_workload}/comparisons/{tier}/...`

### Scoring Policy Configuration

Use feature-driven scoring during tuning for workload-aware metric weighting:

```bash
# Use feature-driven scoring during tuning
python -m src.tuners.pbt.main --tier core --config standard --scoring-policy feature_driven_v2

# Re-evaluate a session with a different scoring policy
python -m src.evaluation \
    --session results/olap/pbt_runs/extensive/tuning_sessions/pbt_results_YYYYMMDD_HHMM.json \
    --scoring-policy feature_driven_v2
```

Available policies:

- `fixed_v1` — legacy static weights (compatibility only; loaded automatically for historical sessions)
- `feature_driven_v2` — dynamic workload-feature-conditioned weights (**default for new runs**)

### Dual-Evaluation Strategy

This framework intentionally supports a two-pronged benchmarking methodology:

- **Academic Baselines**: For scientifically rigorous evaluations without Python overhead, use external C-binaries (e.g. `--benchmark sysbench`).
- **Custom Prototyping**: For tuning proprietary application databases, use the internal JSON-based query templates.

For full architectural details on this design, please read the [Benchmarking Documentation](./docs/reference/benchmarking.md).

### Custom Workloads

The tuner allows you to define your own workload templates using JSON or YAML.
These files are natively executed by the `WorkloadExecutor`, which supports
dynamic parameter injection for variables such as `{id}`, `{k_val}`, `{threshold}`,
`{low}`, `{high}`, and `{offset}`.

Example JSON (`my_workload.json`):

```json
{
  "name": "Custom Workload",
  "description": "Application-specific query patterns",
  "queries": [
    {
      "sql": "SELECT * FROM users WHERE id = {id}",
      "weight": 0.5,
      "description": "Primary key lookup"
    }
  ]
}
```

Run with:

```bash
python -m src.tuners.pbt.main --workload-file path/to/my_workload.json
```

See the [workloads directory README](workloads/README.md) for full formatting details.

---

## Documentation

Comprehensive documentation available in [`docs/`](./docs/):

- **[Documentation Index](./docs/README.md)** - Navigation map for all project docs

### Core System Components

- **[PBT Core Components](./docs/architecture/pbt-core.md)** - Worker, Evolution, Population, lockstep generation barriers
- **[Feature-Driven Scoring](./docs/architecture/feature-driven-scoring.md)** - Scoring-v2 policies, workload features, normalization, and reliability gate
- **[Performance Evaluation](./docs/architecture/performance-evaluation.md)** - WorkloadOrchestrator, PerformanceMetrics, scoring integration
- **[Configuration Management](./docs/architecture/configuration-management.md)** - KnobSpace, tier CSVs, KnobApplicator, verify() read-back

### Technical Details

- **[PostgreSQL Connection and Knobs](./docs/architecture/postgresql-connection-and-knobs.md)** - Database connection, knob retrieval, tuning metadata, policy filter
- **[Environment Setup](./docs/getting-started/setup.md)** - Installation, configuration, troubleshooting
- **[Evaluation Reproducibility Runbook](./docs/guides/evaluation-runbook.md)** - Canonical comparative-evaluation commands, outputs, and reproducibility checks

---

## Research Foundation

This work builds upon several research directions:

### Population-Based Training

- **Jaderberg et al. (2017)**: "Population Based Training of Neural Networks" - Original PBT algorithm for neural network hyperparameter optimization

### Autonomous Database Tuning

- **OtterTune (2017)**: Automated configuration tuning using ML and transfer learning
  - _Contribution_: Adaptive metric normalization using percentile-based ranges
- **CDBTune (2019)**: Deep reinforcement learning for database knob tuning
  - _Contribution_: Intelligent restart management, batched restarts every N iterations

- **QTune (2019)**: Query-aware database configuration tuning
  - _Contribution_: Workload-specific metric weighting

### Evolutionary Optimization

- **Latin Hypercube Sampling**: Space-filling initial population generation
- **Truncation Selection**: Efficient exploitation mechanism
- **Adaptive Perturbation**: Context-aware exploration strategies

### Relevant Papers

See the curated analysis and references in:

- [`docs/research/algorithm-comparison.md`](./docs/research/algorithm-comparison.md)
- [`docs/research/competitive-analysis.md`](./docs/research/competitive-analysis.md)
- [`docs/reference/benchmarking.md`](./docs/reference/benchmarking.md)

- Auto DBMS Tuner (5 papers)
- Reinforcement Learning for DB tuning (4 papers)
- Query Optimization (1 paper)
- Adaptive Indexing (3 papers) - future work

---

## Experimental Results

> **⚠️ Status**: This section describes the expected research methodology. Comprehensive benchmarking across diverse hardware configurations and workloads is ongoing. Results shown below are preliminary and illustrative of the system's capabilities.

### Preliminary Observations

**System Used for Development:**

- Population: 4-8 workers
- Generations: 10-30
- Knob Tier: `core` (13 parameters)
- Workload: OLTP (Sysbench-compatible)

**Observed Behavior:**

1. **Convergence**: The population shows convergence within 10-15 generations
2. **Configuration Evolution**: Poor performers successfully adopt elite configurations
3. **Stability**: Top-performing configurations remain stable across multiple generations
4. **Restart Management**: Batched restarts (every 10 generations) successfully balance configuration changes with overhead

### Example Optimized Configuration

Below is an actual configuration discovered by PBT on development hardware (format only - not claiming optimality):

```json
{
  "shared_buffers": 75984, // ~593 MB
  "effective_cache_size": 87009, // ~679 MB
  "work_mem": 6800, // ~53 MB
  "maintenance_work_mem": 504084, // ~3.8 GB
  "random_page_cost": 1.98, // SSD-friendly
  "effective_io_concurrency": 156,
  "max_parallel_workers_per_gather": 2,
  "checkpoint_completion_target": 0.55,
  "checkpoint_timeout": 439, // ~7 minutes
  "wal_buffers": 272, // ~2 MB
  "default_statistics_target": 2405,
  "max_connections": 81,
  "max_worker_processes": 6
}
```

> **Note**: Actual optimal configurations vary significantly based on hardware (CPU cores, RAM, storage type), workload characteristics (OLTP vs OLAP), and database size. The PBT system adapts to your specific environment.

### Planned Comprehensive Evaluation

Future work includes rigorous benchmarking:

- Multiple hardware configurations (cloud and on-premise)
- Diverse workload types (TPC-H, TPC-C, real-world traces)
- Comparison with baseline tuning methods
- Statistical significance testing across multiple runs

See `results/` directory for optimization traces from your runs.

---

## Future Work

### Planned Enhancements

#### 1. Test Coverage

Currently, the system lacks comprehensive test coverage. Planned additions:

- **Unit Tests**: Individual component testing (Worker, Evolution, Population)
- **Integration Tests**: End-to-end PBT workflow validation
- **Performance Tests**: Regression testing for optimization quality
- **Fixtures**: Mock PostgreSQL instances for CI/CD integration

Target: >80% code coverage with `pytest` framework.

#### 2. Advanced Features

- **Transfer Learning**: Warm-start PBT from previous optimization runs
- **Workload Prediction**: Query clustering for adaptive metric weighting
- **Online Tuning**: Continuous optimization during production workloads

#### 3. Cloud Deployment

- Kubernetes orchestration for multi-instance PostgreSQL
- AWS RDS/Aurora integration
- Distributed PBT across cloud regions

---

## Contributing

This is an **academic research project** under active development.

### Contribution Guidelines

For academic collaborators:

1. **Research Extensions**: Contact repository maintainers for collaboration
2. **Bug Reports**: Open GitHub issues with reproduction steps
3. **Documentation**: Improvements to docs always welcome

For external contributors:

- Currently **not accepting pull requests** for core algorithm changes
- Bug fixes and documentation PRs may be considered on case-by-case basis

### Code of Conduct

Be respectful, professional, and constructive. This is academic research—critiques should be evidence-based and cite relevant literature.

---

## License

PBTune is licensed under the [GNU General Public License v3.0](LICENSE).

This means you are free to use, modify, and distribute PBTune, provided that any derivative work is also released under GPL v3. All forks must remain open source.

For **commercial licensing inquiries**, contact: [Ibrahim Al-Shawa](mailto:contact.alshaw.ai@gmail.com)

---

## Citation

If you use this work in academic research, please cite:

```bibtex
@software{pbtune2026,
  title     = {PBTune: Population-Based Training for Automatic Database Parameter Tuning},
  author    = {Al-Shawa, Ibrahim and Hedia, AbdelRahman and Darwish, Mahmoud and Saber, Walaa and El-Sayed, Emad},
  year      = {2026},
  url       = {https://github.com/alshawai/PBTune},
  license   = {GPL-3.0}
}
```

## Paper

PBTune is described in a paper currently in preparation:

> **PBTune: Population-Based Training for Automatic Database Parameter Tuning**
> Ibrahim Al-Shawa, AbdelRahman Hedia, Mahmoud Darwish, Walaa Saber, Emad El-Sayed

A preprint will be available soon.

### Related Work

| Tool | Approach | Key Paper |
|------|----------|----------|
| PBTune | Evolutionary (PBT) | In preparation |
| OtterTune | GP + Lasso + NN | [Van Aken et al., SIGMOD 2017](https://doi.org/10.1145/3035918.3064029) |
| CDBTune | Deep RL (DDPG) | [Zhang et al., SIGMOD 2019](https://doi.org/10.1145/3299869.3300085) |
| LlamaTune | Sample-Efficient Transfer | [Kanellis et al., VLDB 2022](https://doi.org/10.14778/3551793.3551844) |
| GPTuner | LLM-Guided BO | [Lao et al., VLDB 2024](https://doi.org/10.14778/3659437.3659449) |
| pgtune | Static Rules | — |

> **Note:** Direct performance comparisons are omitted — these systems were evaluated on different hardware and workloads.

---

## Acknowledgments

PBTune was built by:

- [Ibrahim Al-Shawa](https://github.com/alshawai) — Project lead, PBT core, evaluation framework
- [AbdelRahman Hedia](https://github.com/bodyhedia44) — Environment backends, Docker orchestration
- [Mahmoud Darwish](https://github.com/mahmoud-darwish) — Environment backends, infrastructure

Under the supervision of:

- **Prof. Walaa Saber** — Associate Professor, Electrical Engineering, Port Said University
- **Prof. Emad El-Sayed** — Assistant Professor, Electrical Engineering, Port Said University

With contributions from:

- [Karim AbdelAziz](https://github.com/karimali03) — Strong integration of BO baseline
- [Mohammad Ahmad](https://github.com/mohamed20o03) — The first iteration of knob importance analysis

This research was inspired by [DeepMind's Population-Based Training](https://arxiv.org/abs/1711.09846), the [CMU Database Group](https://db.cs.cmu.edu/) (OtterTune), and the [PostgreSQL Community](https://www.postgresql.org/).

---

## Contact

**Maintainer**: [Ibrahim Al-Shawa](https://github.com/alshawai)  
**Email**: contact.alshaw.ai@gmail.com  
**Repository**: https://github.com/alshawai/PBTune  
**Issues**: https://github.com/alshawai/PBTune/issues

---

<div align="center">

**Built with** 🧬 **Evolutionary Optimization** | 🐘 **PostgreSQL** | 🐍 **Python**

_Advancing the state-of-the-art in autonomous database systems_

</div>
