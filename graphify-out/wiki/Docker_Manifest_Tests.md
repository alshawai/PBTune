# Docker Manifest Tests

> 21 nodes · cohesion 0.10

## Key Concepts

- **runner.py** (10 connections) — `src/evaluation/runner.py`
- **._resolve_output_dir()** (7 connections) — `src/evaluation/runner.py`
- **._resolve_tier_slug_from_session()** (6 connections) — `src/evaluation/runner.py`
- **._resolve_tuned_knobs()** (5 connections) — `src/evaluation/runner.py`
- **._save_result()** (5 connections) — `src/evaluation/runner.py`
- **._validate_docker_prerequisites()** (5 connections) — `src/evaluation/runner.py`
- **_sanitize_tier_name()** (4 connections) — `src/evaluation/runner.py`
- **_serialize_result()** (4 connections) — `src/evaluation/runner.py`
- **._resolve_tier_slug()** (3 connections) — `src/evaluation/runner.py`
- **_missing_docker_image_help()** (2 connections) — `src/evaluation/runner.py`
- **Main Bayesian Optimization baseline runner orchestrator.** (1 connections) — `src/scripts/bo_baseline/runner.py`
- **Comparison Runner — Core Orchestrator ======================================  Dr** (1 connections) — `src/evaluation/runner.py`
- **Fail fast when Docker evaluation prerequisites are unavailable.** (1 connections) — `src/evaluation/runner.py`
- **Resolve tuned knob values from serialized fractions to absolute values.** (1 connections) — `src/evaluation/runner.py`
- **Serialize the ComparisonResult to JSON and write to disk.          Output path:** (1 connections) — `src/evaluation/runner.py`
- **Determine the output directory, creating it if necessary.** (1 connections) — `src/evaluation/runner.py`
- **Determine the output directory for this evaluation session.** (1 connections) — `src/evaluation/runner.py`
- **Infer knob tier slug from a computed result object.** (1 connections) — `src/evaluation/runner.py`
- **Infer knob tier slug from session metadata, then from session path.** (1 connections) — `src/evaluation/runner.py`
- **Normalize tier names into stable path-safe slugs.** (1 connections) — `src/evaluation/runner.py`
- **Convert ComparisonResult to a plain JSON-serialisable dict.** (1 connections) — `src/evaluation/runner.py`

## Relationships

- [[Workload Orchestrator]] (42 shared connections)
- [[Comparison Runner]] (7 shared connections)
- [[BO Baseline & Workload]] (5 shared connections)
- [[BO Config & Worker]] (3 shared connections)
- [[Session Management]] (1 shared connections)
- [[Bare Metal Memory Tests]] (1 shared connections)
- [[Performance Metrics]] (1 shared connections)
- [[Environment Factory]] (1 shared connections)

## Source Files

- `src/evaluation/runner.py`
- `src/scripts/bo_baseline/runner.py`

## Audit Trail

- EXTRACTED: 57 (92%)
- INFERRED: 5 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*