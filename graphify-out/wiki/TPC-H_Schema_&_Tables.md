# TPC-H Schema & Tables

> 23 nodes · cohesion 0.09

## Key Concepts

- **__init__.py** (27 connections) — `src/__init__.py`
- **connection.py** (4 connections) — `src/database/connection.py`
- **Bayesian Optimization baseline runner for PostgreSQL configuration tuning.** (2 connections) — `src/scripts/bo_baseline/__init__.py`
- **Analysis Module ===============  Tools and data structures for analyzing Populat** (1 connections) — `src/analysis/__init__.py`
- **Workload Orchestration and Performance Metrics =================================** (1 connections) — `src/tuner/benchmark/__init__.py`
- **Configuration Package Initialization ====================================  Expor** (1 connections) — `src/tuner/config/__init__.py`
- **PBT Core Module ===============  This module contains the core PBT algorithm imp** (1 connections) — `src/tuner/core/__init__.py`
- **Database Connection Utilities ==============================  Provides connectio** (1 connections) — `src/database/connection.py`
- **Database utilities for connection, management, and data loading.** (1 connections) — `src/database/__init__.py`
- **Environments Subpackage =======================  Provides polymorphic database e** (1 connections) — `src/utils/environments/__init__.py`
- **WIP placeholder package for evaluation pipeline modules.** (1 connections) — `src/evaluation/__init__.py`
- **PostgreSQL knob retrieval and analysis utilities.** (1 connections) — `src/knobs/__init__.py`
- **Data loaders for the visualization framework.  This layer transforms raw JSON /** (1 connections) — `src/visualization/loaders/__init__.py`
- **Enhanced Logging for PBT PostgreSQL Tuning =====================================** (1 connections) — `src/utils/logger/__init__.py`
- **Auto-discovery trigger for plot implementations. Importing this package triggers** (1 connections) — `src/visualization/plots/__init__.py`
- **Scoring contracts and constants used by tuning, evaluation, and analysis.** (1 connections) — `src/utils/scoring/__init__.py`
- **Executable scripts for database operations and analysis.** (1 connections) — `src/scripts/__init__.py`
- **Database Optimization with AI ==============================  A modular toolkit** (1 connections) — `src/__init__.py`
- **Tests package for Database Optimization with AI.** (1 connections) — `tests/__init__.py`
- **TPC-H Benchmark Support =======================  Provides data generation (via d** (1 connections) — `src/benchmarks/tpch/__init__.py`
- **Population Based Training for PostgreSQL Configuration Tuning ==================** (1 connections) — `src/tuner/__init__.py`
- **Shared Utilities ================  Cross-module utilities for the PBT PostgreSQL** (1 connections) — `src/utils/__init__.py`
- **Public API for the visualization framework.  This framework handles rendering pr** (1 connections) — `src/visualization/__init__.py`

## Relationships

- [[BO Baseline & Workload]] (1 shared connections)
- [[Metric Config & Composite]] (1 shared connections)
- [[PostgreSQL Knob Retrieval]] (1 shared connections)
- [[Database Operations]] (1 shared connections)
- [[PostgreSQL Knob Tests]] (1 shared connections)
- [[Logger Colors]] (1 shared connections)
- [[Score Normalization Tests]] (1 shared connections)
- [[Drift Detection]] (1 shared connections)
- [[Knob Metadata]] (1 shared connections)

## Source Files

- `src/__init__.py`
- `src/analysis/__init__.py`
- `src/benchmarks/tpch/__init__.py`
- `src/database/__init__.py`
- `src/database/connection.py`
- `src/evaluation/__init__.py`
- `src/knobs/__init__.py`
- `src/scripts/__init__.py`
- `src/scripts/bo_baseline/__init__.py`
- `src/tuner/__init__.py`
- `src/tuner/benchmark/__init__.py`
- `src/tuner/config/__init__.py`
- `src/tuner/core/__init__.py`
- `src/utils/__init__.py`
- `src/utils/environments/__init__.py`
- `src/utils/logger/__init__.py`
- `src/utils/scoring/__init__.py`
- `src/visualization/__init__.py`
- `src/visualization/loaders/__init__.py`
- `src/visualization/plots/__init__.py`

## Audit Trail

- EXTRACTED: 53 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*