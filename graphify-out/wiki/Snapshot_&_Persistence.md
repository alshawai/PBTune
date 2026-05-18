# Snapshot & Persistence

> 22 nodes · cohesion 0.10

## Key Concepts

- **.to_dict()** (25 connections) — `src/tuner/core/worker.py`
- **WorkloadFeatures** (6 connections) — `src/utils/scoring/contracts.py`
- **contracts.py** (5 connections) — `src/utils/scoring/contracts.py`
- **MetricSnapshot** (3 connections) — `src/utils/scoring/contracts.py`
- **NormalizationState** (3 connections) — `src/utils/scoring/contracts.py`
- **ScoreBreakdown** (3 connections) — `src/utils/scoring/contracts.py`
- **Serialize report to a JSON-friendly dict.** (1 connections) — `src/analysis/tier_generator.py`
- **Get configuration as a dictionary (useful for psycopg2).          Returns** (1 connections) — `src/config/database.py`
- **Convert configuration to dictionary** (1 connections) — `src/tuner/config/tuner_config.py`
- **Convert worker to dictionary for serialization.          Useful for logging, che** (1 connections) — `src/tuner/core/worker.py`
- **Typed contracts for feature-driven scoring metadata and breakdowns.** (1 connections) — `src/utils/scoring/contracts.py`
- **Feature vector and extraction metadata for a workload.** (1 connections) — `src/utils/scoring/contracts.py`
- **Convert workload feature state into a serializable dictionary.** (1 connections) — `src/utils/scoring/contracts.py`
- **Per-metric contribution snapshot used to explain a composite score.** (1 connections) — `src/utils/scoring/contracts.py`
- **Convert metric snapshot into a serializable dictionary.** (1 connections) — `src/utils/scoring/contracts.py`
- **Detailed representation of a score and its component contributions.** (1 connections) — `src/utils/scoring/contracts.py`
- **Convert score breakdown into a serializable dictionary.** (1 connections) — `src/utils/scoring/contracts.py`
- **Normalization state exported for reproducible rescoring.** (1 connections) — `src/utils/scoring/contracts.py`
- **Convert normalization state into a serializable dictionary.** (1 connections) — `src/utils/scoring/contracts.py`
- **Workload feature extraction for policy-aware scoring.** (1 connections) — `src/utils/scoring/workload_features.py`
- **Convert metrics to dictionary** (1 connections) — `src/utils/metrics.py`
- **Serialize benchmark configuration for JSON output.** (1 connections) — `src/utils/types.py`

## Relationships

- [[Instance Management]] (3 shared connections)
- [[Population Initialization]] (2 shared connections)
- [[Evolution Algorithms]] (1 shared connections)
- [[Query Pattern Analysis]] (1 shared connections)
- [[Database Config & Connection]] (1 shared connections)
- [[Population Tests]] (1 shared connections)
- [[Evaluator Fault Injection]] (1 shared connections)
- [[BO Config & Worker]] (1 shared connections)
- [[Evaluator Core]] (1 shared connections)
- [[Scoring Scorer Core]] (1 shared connections)

## Source Files

- `src/analysis/tier_generator.py`
- `src/config/database.py`
- `src/tuner/config/tuner_config.py`
- `src/tuner/core/worker.py`
- `src/utils/metrics.py`
- `src/utils/scoring/contracts.py`
- `src/utils/scoring/workload_features.py`
- `src/utils/types.py`

## Audit Trail

- EXTRACTED: 60 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*