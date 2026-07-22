# Changelog

All notable changes to PBTune will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.9.2] - 2026-07-22

### Bug Fixes

- Re-apply launch readiness changes lost during merge

### Features

- **distributed**: Add multi-device tuning mode (one worker per device)

## [0.9.1] - 2026-07-20

### Bug Fixes

- **bo**: Narrow Optional worker/orchestrator for mypy
- **scoring**: Preserve weight-log cursor across scorer rebuild

### Features

- **tuners**: Unify session-trace schema with tolerant readers
- **pbt**: Record exploit graph and strategy overhead in session trace

### Miscellaneous

- **deps**: Bump python-dotenv from 1.2.1 to 1.2.2
- **deps**: Bump wcwidth from 0.8.1 to 0.8.2

### Refactoring

- **bo**: Migrate bo_baseline package to src/tuners/bo
- **bo**: Route BO through unified src.tuners CLI
- **tuners**: Add cross-strategy Best Worker column
- **logging**: Rebrand startup banner to Database Tuner
- **analysis,viz**: Read unified session schema
- **logging**: Drop Memory Utilization from final summary

### Testing

- **bo**: Relocate BO tests under tests/unit/tuners/bo
- **tuners**: Update fixtures for unified session schema

## [0.9.0] - 2026-07-17

### Bug Fixes

- **lhs**: Cast numpy bools to Python bool so KnobApplicator accepts them
- **bo**: Prevent disk exhaustion from spurious snapshot restores and Docker volume leaks
- **docker**: Pre-restart CHECKPOINT and smart postmaster restart to prevent WAL disk exhaustion
- Resolve merge conflicts with origin/main
- **docker**: Prevent disk exhaustion from verbose logging knobs in BO co-tenancy
- **bo**: Periodically restore background co-tenant snapshots to prevent disk growth
- **pbt**: Deterministic RNG streams seeded from --random-seed
- **linting**: Fix linting and typechecking errors
- **tests**: Resolve Python 3.11 CI failures in cpu_perf and viz glob discovery

### Documentation

- Repoint stale src/tuner references to the unified src/tuners layout
- Remove 'Last reviewed' lines and delete orphaned VISUALIZATION.md
- **readme**: Update for public launch — badges, license, citation, team
- Update CITATION.cff with confirmed authors and GPL-3.0
- **contributing**: Rebrand to PBTune, adopt conventional commits
- Add MkDocs Material site with GitHub Pages deployment
- Repoint index.md quick-start to routed src.tuners CLI door

### Features

- Enhance visualization pipeline with dynamic metrics and flexible data sources
- **viz**: Enhance convergence plots and fix wall-clock measurement
- **visualization**: Apply boxed academic styling, comparison bar charts, and dynamic metrics

### Miscellaneous

- Ignore PBTune-experiments and figures directories
- Remove karim directory from version control
- **repo**: Fix pre-existing lint, test-collection, and fixture debt
- Remove legacy src/tuner/ entry point
- Add pre-commit config (ruff, detect-secrets, whitespace)
- Add community infrastructure files
- Enhance workflow with Python matrix, pip caching, coverage
- Add release workflow for automated GitHub Releases
- Add [project] metadata to pyproject.toml
- Add requirements-lock.txt for reproducible installs
- Add GPL v3 license headers to key source files
- Add git-cliff config and generate initial CHANGELOG
- **deps**: Bump nest-asyncio from 1.5.9 to 1.6.0
- **deps**: Bump uvicorn from 0.46.0 to 0.51.0
- **deps**: Bump botocore from 1.42.91 to 1.43.49

### Refactoring

- **knobs**: Relocate knob_space + knob_loader to src/knobs/
- **benchmarks**: Relocate workload.py to src/benchmarks/
- **engine**: Relocate barriers + restart_policy to src/tuners/engine/
- **engine**: Relocate worker.py to src/tuners/engine/
- **engine**: Relocate orchestrator to src/tuners/engine/
- **utils**: Relocate calibration.py to src/utils/
- **tuners**: Unify session schema + strip post-hoc recalibration from base
- **tuners**: Split Worker + relocate PBT core into src/tuners/pbt/
- **tuners**: PBTTuner(BaseTuner) + level the base up to PBT's completeness
- **tuners**: PBT CLI on unified subpackage + relocate PBT test suite
- **tuners**: Relocate LHS-design into src/tuners/lhs_design/ subpackage
- **results**: Workload-first output taxonomy
- **tuners**: Polish lifecycle logging and section headers
- **tuners**: Strategy-agnostic output stems, discovery module, and utils consolidation
- **tuners**: Decompose base tuner into session_assembly and tuner_logging utils
- **benchmarks**: Unify executor hierarchy under BenchmarkExecutor ABC via ExecutionContext
- **tuners**: Relocate connect/disconnect to connection layer, de-PBT-ify orchestrator
- **tuners**: Extract evaluate_worker computation islands into helpers
- **tuners**: Extract reliability gate into its own module
- **tuners**: Extract worker-metric collection into worker_metrics module
- **tuners**: Extract pre/post-workload maintenance into maintenance module
- **tuners**: Extract config activation into activation module
- **tuners**: Extract workload-feature refinement into feature_refinement module
- **tuners**: Collapse orchestrator shim docstrings to one-liners
- Update entry point from src.tuner.main to src.tuners.pbt.main

### WIP

- Pausing work to update logs

### License

- Replace Academic Research license with GPL v3

## [0.8.1] - 2026-07-07

### Bug Fixes

- **disk-probe**: Redesign fio probe and split trust floor to stop universal SSD rejection
- **analysis**: Harden fANOVA ConfigSpace bounds to prevent crash in combined model
- **analysis**: Pass DataFrame to fANOVA to fix column-ordering mismatch
- **knob-policy**: Exclude ``default_transaction_isolation`` from the search space

### Features

- **scoring**: Reduce overall variance contribution to the final score.
- **bo**: Add co-tenant load harness and CPU clock pinning for fair PBT-vs-BO comparison

### Styling

- Auto-fix ruff formatting on existing source and test files

## [0.8.0] - 2026-06-24

### Bug Fixes

- **evolution**: Allow dead-worker rescue before ready_interval is met
- **normalization**: Make anchor expansion direction-aware
- **experiments**: Correct git push remote to origin
- **isolation**: Walk partition device to parent disk for io.max
- **scoring**: Retain throughput_variance for multi-threaded sysbench
- **experiments**: Remove broken --cleanup-instances arugment from BO
- **bo**: Match BO budget to PBT actual generations, flat 50-iter patience
- **core**: Rescore workers on first normalizer calibration
- **eval**: Use static prior + add default config to PBT's LHS for fair PBT-vs-BO comparison
- **scripts**: Stage workload subtree instead of experiment id in results commits
- **utils**: Reject implausible fio disk probes and lengthen probe runtime
- **analysis**: Calibrate SCALPEL stability budget and parallelize subsamples

### Documentation

- **analysis**: Document SCALPEL architecture, ADR, rollout, diagnostics
- **tuners**: Document shared LHS CLI, profiles, snapshot/HTML parity

### Features

- **cli**: Expose snapshot_restore_interval and enable/disable_snapshots
- **tuner**: Implement resample probability exploration strategy
- **isolation**: Per-worker disk I/O limits via cgroup blkio
- **tuner**: Expose --exploit-quantile CLI flag for PBT evolution
- **scripts**: Per-experiment manifest files for parallel-machine safety
- **scripts**: Add one-shot legacy manifest migration helper
- **analysis**: Add SCALPEL tier-generation algorithm
- **analysis**: Wire SCALPEL into tier_generator, CLI, and tuner paths
- **knobs**: Skip empty tier CSVs and walk down to broader tier on load
- **analysis**: Add SCALPEL q-sensitivity sweep diagnostics
- **analysis**: Fuse fANOVA pairwise interactions into SCALPEL Lorenz signal
- **session**: Add tuning_strategy field to tuning_session JSON
- **tuners**: Add unified tuners package with BaseTuner ABC
- **tuners**: Add LHSDesignTuner + CLI for SCALPEL importance designs
- **tuners**: Expand lifecycle config, add exception taxonomy, trim outcome
- **tuners**: Make BaseTuner the shared lifecycle with ONLINE knob view
- **tuners**: Add PBT-grade per-strategy logging to BaseTuner lifecycle
- **tuners**: Add profile registry + complete shared CLI flag surface
- **tuners**: Add LHS snapshot restore, HTML logging, probe-disk diagnostics
- **tuners**: Make LHS traces SCALPEL-loadable and add experiment integration
- **scripts**: Add pre-flight smoke suite and resilient results push

### Miscellaneous

- **experiments**: Change BO default logging behavior to DEBUG

### Performance

- **benchmark**: Skip post-eval VACUUM when next iter restores snapshot

### Refactoring

- **core**: Align truncation_selection with PBT paper

### Testing

- **analysis**: Add SCALPEL test suite and tier_diagnostics figure

## [0.7.0] - 2026-06-15

### Bug Fixes

- **bootstrap**: Probe available Python versions instead of hardcoding 3.12
- **bootstrap**: Probe PostgreSQL client versions before install
- **env**: Preserve postgresql.auto.conf across docker snapshot restore
- **metrics**: Pass latency_variance instead of non-existent latency_stddev kwarg
- **typing**: Resolve mypy errors across baselined src/ modules
- **bo**: Six BO baseline correctness fixes for snapshot, scoring, and serialization

### Documentation

- Refresh project docs, agent skills, and tooling for post-instrumentation state

### Features

- **timing**: Add timing instrumentation and breakdown analysis
- **bo**: Measure sequential-mode ask/tell overhead via inter-call gap
- **config**: Default tuning_mode to OFFLINE
- **experiments**: Expand cloud matrix and pin BO/PBT flags for fair comparison

### Miscellaneous

- **config**: Restore RAPID profile snapshot_restore_interval to 10
- **build**: Expand lint and typecheck scope to all of src/ and scripts/
- **build**: Drop the baselined-modules override now that src/ is mypy-clean

### Styling

- **scripts**: Apply ruff UP and I001 fixes to experiments runner

### Merge

- Resolve conflicts with origin/main and unify BO timing instrumentation

## [0.7.1] - 2026-06-12

### Bug Fixes

- Prevent serializable isolation crash in extensive tier
- **scripts**: Forward BO seed via orchestrator and fix executor args
- **bo-baseline**: Refine read-back conversion and support paths
- **benchmark**: Stabilize metric collection
- **logging**: Hide debug worker metrics at info level
- Resolve ConfigSpace mock leakage and harden BO baseline
- **warm-start**: Serialize full knob space and resolve absolute values correctly
- **scoring**: Bound error_rate metric and relax reliability gate
- **barriers**: Remove barrier timeout — wait indefinitely
- **bo**: Stabilize surrogate model and retroactively synchronize logs
- **cleanup**: Resolve Docker volume path and networking errors

### Documentation

- Refresh stale architecture docs and prune duplicate
- Add architecture docs for previously undocumented subsystems
- Fill targeted gaps in scoring, hardware, and analysis docs
- Refresh index and record ADRs for barriers and Docker isolation
- Reorganise into Diataxis structure (getting-started/guides/architecture/reference/research)
- Fill remaining gaps with quickstart, overview, contributor guides, and references
- Untangle intent-mixing across quadrants (Diataxis hygiene)

### Features

- **bo**: Decouple parallel execution from resource division using --resource-division
- Integrate snapshot restoration into BO baseline
- **bo**: Decouple parallel execution from resource division using --resource-division
- Integrate snapshot restoration into BO baseline
- **scoring,logger**: Add scoring engine and logger context; clean graphify cache
- **scoring**: Update feature-driven scoring pipeline
- **bo_baseline**: Add early stopping, iteration parity, and direct knob sampling
- **evolution**: Physically clone database instances during exploitation
- **docker**: Parallel CPU subset isolation and resource limits for concurrent tuning
- **tuner**: Adapt PBT loops and BO baselines for parallel worker resources
- **analysis**: Restructure expert knobs and isolate data-driven artifacts
- **tuner**: Add manual worker resource allocation and fix precedence
- **experiments**: Add cloud experiment execution suite

### Miscellaneous

- **ci**: Fix ruff/mypy issues and adapt tests to logging/scoring refactor
- **logging**: Correct visualizer and evaluator module logger names

### Refactoring

- **scripts**: Update BO baseline parallel flags
- **scripts**: Update BO baseline parallel flags
- Logging and scoring integration updates
- **core**: Unify database configuration verification and read-back
- **bo**: Implement strict four-phase bootstrapping architecture
- **eval**: Enhance multi-arm metrics, fix types, and streamline execution

### Testing

- **bo**: Add unit tests for ConfigSpace constraints and logic

### Merge

- Resolve conflicts with origin/main

## [0.6.0] - 2026-05-24

### Bug Fixes

- Correct memory metric storage and gate convergence on adaptive normalization
- Harden instance manager against stale processes and port conflicts
- **tpch**: Stabilize dbgen compilation and force marker regeneration
- **evaluator**: Patch adaptive scoring and pg_ctl restart timeouts
- Correct three confirmed bugs (parallel workers, log-scale perturbation, sysbench warmup)
- Upgrade sysbench to 1.1.0 and use native --warmup-time flag
- **tpch**: Add failsafe statement_timeout and strict query validation
- **knobs**: Exclude io_method from autotuning policy
- **scoring**: Force score=0 for dead workers via failure_type
- **population**: Replace score-based saturation trigger with raw metric bounds check
- **warmstart**: Use unclamped absolute value for fraction validation
- **tpch**: Abort warmup on failure, add PG diagnostics, guard incomplete runs
- **tpch**: Ensure dbgen output files are readable after generation
- **data_loader**: Correctly parse nested PBT format and boolean features
- **linter**: Add requests-stubs and worker_memory_budget_bytes field
- **tuner**: Harden recovery and bound handling
- **analysis**: Import Optional to resolve F821 lint failure
- **analysis**: Reorder imports and use pre-defined "get_logger"
- **core**: Align tuning pipeline with applicator and restart policy
- **docker**: Harden restore and teardown timeout handling
- **evaluation**: Defer runner database config loading
- **importance**: Validate SHAP vector length and improve importances calculation
- **metrics**: Apply IQR filtering to normalization ranges and increase min_samples
- Ensure strict matching in knob-label assignment in generate_tiers function
- **typing**: Address ruff and mypy issues to pass make fix-and-check
- **tests**: Correct tpch schema cleanup test API usage
- Add module reloading for tests and restrict swig version during pyrfr installation
- **tests**: Correct tpch schema cleanup test API usage
- **docker,convergence**: Resolve relative mount paths and improve tracking
- Make jenkspy optional; accept injected logger in TPCH executor; add typing for BO runner
- **analysis**: Resolve mypy type annotation error for empty lists
- Resolve barrier deadlocks in hybrid mode and dead-worker scenarios

### Documentation

- Add environment setup guide
- Add PostgreSQL connection and knobs system documentation
- Add core PBT implementation documentation
- Add performance evaluation and configuration management documentation
- Update environment setup and PostgreSQL connection guides
- Add comprehensive README and project documentation
- Add algorithm comparison, MySQL roadmap, and docs index
- Update documentation metadata, cross-links, and testing guidance
- **results**: Add sample output demonstrating new directory structure
- **project**: Update contribution workflow and agent guidance
- **evaluation**: Add reproducibility runbook
- **evaluation**: Synchronize evaluation documentation and references
- **architecture**: Add ADR for sysbench workload modes
- **paper**: Initialize PVLDB paper workspace with LaTeX skeleton
- **scoring**: Add feature-driven scoring architecture documentation
- Update documentation for feature-driven scoring

### Feature

- Add hardware info logging

### Features

- Implement core abstractions for adaptive indexing system
- Implement storage abstraction layer with adaptive indexing support
- Add centralized configuration management for database connections
- Implement PostgreSQL connection and lifecycle management
- Add comprehensive PostgreSQL parameter retrieval and analysis
- Add executable utilities for database setup and knobs analysis
- Add root package initialization for modular toolkit
- Implement Population Based Training core components
- Implement workload evaluation and metrics system
- Add KnobApplicator for safe PostgreSQL config changes
- Implement PBT configuration and knob space system
- Implement knob retrieval and preprocessing system
- Add package initialization and exports
- Add notebook setup utilities for path configuration
- **pbt**: Implement complete Population Based Training system
- **workloads**: Add workload definitions and examples
- **scripts**: Add utility scripts for database and instance management
- Implement snapshot manager and enhance database instance stability
- Enhance terminal output with stylized logging and colored banner
- **evaluator**: Implement SchemaProvider protocol and multi-table workloads
- **workloads**: Add multi-table query templates and correct metrics
- Snapshot-based schema initialization and skip redundant gen-0 restore
- Add benchmark executor modules and research/extreme config tiers
- **core**: Implement parameter scaling, snapshot bypasses, and dynamic rescoring
- Enhance sysbench execution and tuner metrics
- **knobs**: Add autotuning policy exclusion engine
- **knobs**: Add curated TuningMetadata for 39 extensive-tier knobs
- **tuner**: Enforce valid postgres config dependencies during search
- **tuner**: Harden evaluator execution and add failure tracking
- **infra**: Integrate postgresql.auto.conf clearing into instance reuse
- **core**: Implement dead-config rescue and failure isolation
- **main**: Add per-worker PerformanceMetrics to generation history
- **knobs**: Add hardware-relative and disk-conditional metadata fields
- **config**: Implement hardware-aware fractional normalization layer
- **core**: Add warm-start CLI flag and cross-hardware transfer support
- **tuner**: Thread random_seed consistently through evaluation pipeline
- Adding Docker environment support for workers
- Add BO baseline runner and BO vs PBT plotting
- **analysis**: Implement PBT results data loader with global re-scoring (#15)
- **config**: Change DatabaseConfig.port type from str to int
- **environments**: Add Docker and bare-metal environment abstractions
- **tuner**: Integrate environment abstraction into PBT workflow
- **env**: Harden runtime lifecycle and snapshot handling
- **tuner**: Integrate runtime-aware environment setup
- **evaluation**: Add baseline comparison package
- **analysis**: Implement fANOVA variance decomposition for knob importance
- **sysbench**: Add explicit workload mode integration
- **analysis**: Add TreeSHAP cross-validation for knob importance
- **agents**: Add comprehensive project context skills and start-session workflow
- **scoring**: Add feature-driven scoring pipeline with v2 policy
- **scoring**: Implement QuantileUtilityNormalizer with IQR-based calibration
- **metrics**: Add metric instrumentation and collection utilities
- **tuner**: Integrate feature-driven scoring into PBT main loop
- **evaluator**: Add workload feature extraction and scoring integration
- **evaluation**: Integrate feature-driven scoring into post-hoc evaluation
- **analysis**: Add scoring policy support to rescoring and analysis
- **benchmarks**: Add scoring metadata to benchmark executors
- **evaluation**: Add scoring metadata to statistics and exceptions
- **analysis**: Add Jenks tier generator with validation
- **analysis**: Add importance runner and RF tuning defaults
- **bo-baseline**: Implement Pilot+Freeze normalization and surrogate options
- **logger**: Expand ANSI and HTML color support
- **viz**: Implement visualization framework infrastructure
- **visualization**: Resolve data pipeline gaps for plotting framework
- **config**: Refactor shared configuration system with benchmark presets and tuning modes
- **bo-baseline**: Standardize logger naming with global constants
- **cli**: Enhance BO baseline CLI with preset configurations and test updates
- **analysis**: Add cross-hardware importance validation and tests
- **setup**: Add automated setup script and conda environment
- **setup**: Add automated setup script and conda environment
- **infrastructure**: Support external hard drive deployment via transparent loopback storage
- Add ColorContext helper and unified ScoringEngine
- **scripts**: Align BO baseline with PBT session metadata
- **analysis**: Implement knob importance CLI orchestration and stabilize fANOVA
- Add lockstep barrier synchronization for parallel worker evaluation
- **knobs**: Integrate data-driven tiers as an optional source
- **analysis**: Add knob importance docs and deps
- **viz**: Add knob importance visualizations
- **viz**: Make importance visuals configurable and robust

### Miscellaneous

- Add .git for Python project and workspace cleanup
- Add project configuration and dependencies
- Extend gitignore with snippet and temp file patterns
- **config**: Update configuration files and dependencies
- **results**: Add results directory with example outputs
- Update dependencies and gitignore rules
- Remove dead code and deprecated tests
- Integrate GitHub Copilot configuration from awesome-copilot
- **gitignore**: Ignore chat workspace artifacts
- Update gitignore and minor script fixes
- **tooling**: Add pull request quality gates and local checks
- **evaluation**: Add WIP evaluation package initialization
- **gitignore**: Track Claude configuration files
- **agent-config**: Add CLAUDE and core project skills
- **agent-config**: Add remaining skills and update gitignore
- **gitignore**: Ignore papers and oltp comparisons artifacts
- **makefile**: Remove redundant lint-strict target
- **scripts**: Add evaluation passthrough launcher
- **archive**: Preserve legacy restart cost model in prototypes
- **gitignore**: Ignore generated benchmark artifacts
- **tpch**: Remove tracked generated scale marker
- **data**: Track knob tier metadata
- **gitignore**: Update instance and snapshot directory paths
- **.gitignore**: Support for database instances and snapshot old paths
- **tooling**: Add graphify knowledge graph configuration
- **tooling**: Add graphify agent instructions across AI platforms
- **tooling**: Add graphify knowledge graph configuration
- **tooling**: Add graphify agent instructions across AI platforms
- Remove graphify-out from git tracking
- **graphify**: Regenerate project structural report
- Test & lint fixes; ignore multi_arm_comparison result files

### Refactor

- Simplify path resolution and ensure consistent return types

### Refactoring

- **structure**: Reorganize project structure and archive prototypes
- **logger**: Unify color palette and eliminate code duplication
- Unify query execution with dynamic json workload templates
- **results**: Restructure output into results/{workload}/pbt_runs/{tier}/
- **agents**: Migrate from Copilot agents to unified workflow system
- **knobs**: Extract configuration metadata and policy to JSON files
- Replace standard logger with custom logger configuration in data_loader.py
- **population**: Replace `instance_manager` with `DatabaseEnvironment`
- **utils**: Extract shared utility modules to src/utils
- **utils**: Complete shared utils migration and gate hardening
- **analysis**: Extract shared global rescoring utility
- **evaluator**: Decouple workload and restart policies from core evaluator
- **environments**: Integrate restart and monitoring into environment backends
- **benchmarks**: Extract benchmark abstractions to dedicated module
- **analysis**: Improve data loader normalization handling
- **utils**: Update utilities for feature-driven scoring
- **logging**: Standardize logger naming and aligned formatting
- **logger**: Remove redundant enable_colors from formatters
- **tuner**: Rename evaluator package to benchmark and Evaluator to WorkloadOrchestrator
- Improve code formatting and logging consistency across multiple files
- Simplify MetricConfig to delegate to normalizer and ScoringEngine
- Remove references to removed MetricConfig fields in rescoring
- Move executor logging to module-level LOGGER
- Update logger module to export ColorContext and new helpers
- Update scoring module for unified scoring engine integration
- Update tuner core for logging revamp and scoring integration
- Update benchmark orchestrator for logging revamp
- Update tuner main and config for logging revamp
- Update environments for logging revamp
- Update utilities for logging revamp
- Update evaluation and visualization for logging revamp
- **bo_baseline**: Standardize output format and integrate global scoring policies
- Revamp logging and cleanup in TPCHExecutor and WorkloadOrchestrator

### Styling

- Apply global codebase formatting and linting
- Apply ruff formatting to bo_baseline configuration and result writer
- Fix unused imports and assignments raised by ruff

### Testing

- Add comprehensive test suite structure
- **utils**: Add regression coverage for environment and restart modules
- **warm-start**: Decouple PBTTuner tests from tier CSV files
- **warm-start**: Stub DB config in PBTTuner init tests
- **scoring**: Add comprehensive tests for feature-driven scoring pipeline
- **integration**: Update tests for feature-driven scoring integration
- Update tests for MetricConfig refactoring and executor logging changes
- Update tests for logging revamp and scoring integration

### Build

- **evaluation**: Add Docker image assets for reproducible runs
- **scripts**: Ignore mypy warning for dateutil.parser

### Tune

- **knobs**: Raise SLRU buffer tuning_min to exclude zero; mirror in tier CSV; temporary search_space guard


