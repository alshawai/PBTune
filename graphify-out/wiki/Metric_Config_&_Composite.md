# Metric Config & Composite

> 24 nodes · cohesion 0.09

## Key Concepts

- **FigureRegistry** (11 connections) — `src/visualization/registry.py`
- **FigureRegistryError** (5 connections) — `src/visualization/exceptions.py`
- **VisualizationError** (4 connections) — `src/visualization/exceptions.py`
- **registry.py** (3 connections) — `src/visualization/registry.py`
- **._discover_plots()** (3 connections) — `src/visualization/registry.py`
- **.get()** (3 connections) — `src/visualization/registry.py`
- **.register()** (3 connections) — `src/visualization/registry.py`
- **register_figure()** (3 connections) — `src/visualization/registry.py`
- **FigureSpec** (3 connections) — `src/visualization/types.py`
- **.list_all()** (2 connections) — `src/visualization/registry.py`
- **.list_by_category()** (2 connections) — `src/visualization/registry.py`
- **.list_by_section()** (2 connections) — `src/visualization/registry.py`
- **Raised for figure registry lookup failures.** (1 connections) — `src/visualization/exceptions.py`
- **Base exception for all visualization framework errors.** (1 connections) — `src/visualization/exceptions.py`
- **Central registry for tracking, discovering, and generating registered figures.** (1 connections) — `src/visualization/registry.py`
- **Catalog of all paper figures with metadata and generators.** (1 connections) — `src/visualization/registry.py`
- **Register a new figure specification.** (1 connections) — `src/visualization/registry.py`
- **Get a figure specification by its ID.** (1 connections) — `src/visualization/registry.py`
- **Return all registered figure specifications.** (1 connections) — `src/visualization/registry.py`
- **Return figures matching a specific category.** (1 connections) — `src/visualization/registry.py`
- **Return figures belonging to a specific paper section.** (1 connections) — `src/visualization/registry.py`
- **Auto-discover and load all modules in the src.visualization.plots package.** (1 connections) — `src/visualization/registry.py`
- **Convenience function for plot modules to register figures.** (1 connections) — `src/visualization/registry.py`
- **Metadata for one registered figure.** (1 connections) — `src/visualization/types.py`

## Relationships

- [[Data Loader & Analysis]] (50 shared connections)
- [[Bare Metal Memory Tests]] (2 shared connections)
- [[Visualization & Theming]] (1 shared connections)
- [[Cross-Module Rationale]] (1 shared connections)
- [[BO Config & Worker]] (1 shared connections)
- [[Evaluation Types]] (1 shared connections)

## Source Files

- `src/visualization/exceptions.py`
- `src/visualization/registry.py`
- `src/visualization/types.py`

## Audit Trail

- EXTRACTED: 49 (88%)
- INFERRED: 7 (12%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*