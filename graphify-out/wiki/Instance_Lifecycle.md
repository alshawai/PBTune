# Instance Lifecycle

> 12 nodes · cohesion 0.20

## Key Concepts

- **CompositeScorer** (12 connections) — `src/utils/scoring/scorer.py`
- **.compute_detailed_scores()** (6 connections) — `src/utils/metrics.py`
- **.compute_score()** (6 connections) — `src/utils/metrics.py`
- **._compute_reliability_gate()** (3 connections) — `src/utils/scoring/scorer.py`
- **scorer.py** (2 connections) — `src/utils/scoring/scorer.py`
- **Composite Scorer ================  Orchestrates the computation of the final PBT** (1 connections) — `src/utils/scoring/scorer.py`
- **Compute reliability gate G in [0, 1].          If evaluation failed entirely, G** (1 connections) — `src/utils/scoring/scorer.py`
- **Compute scalar composite score.** (1 connections) — `src/utils/scoring/scorer.py`
- **Compute score and return breakdown of components.          Returns         -----** (1 connections) — `src/utils/scoring/scorer.py`
- **Computes final bounded score by applying weights and reliability gating.      Sc** (1 connections) — `src/utils/scoring/scorer.py`
- **Compute composite performance score using active policy and normalizer.** (1 connections) — `src/utils/metrics.py`
- **Compute individual score components using the active CompositeScorer.          R** (1 connections) — `src/utils/metrics.py`

## Relationships

- [[TPC-H Star Schema Queries]] (26 shared connections)
- [[DB Connection Reuse]] (3 shared connections)
- [[Cross-Module Rationale]] (2 shared connections)
- [[Metric Instrumentation]] (1 shared connections)
- [[Evaluator Fault Injection]] (1 shared connections)
- [[PBT Tuner Main & Config]] (1 shared connections)
- [[Quantile Utility Normalizer]] (1 shared connections)
- [[Metric Config Recalibration]] (1 shared connections)

## Source Files

- `src/utils/metrics.py`
- `src/utils/scoring/scorer.py`

## Audit Trail

- EXTRACTED: 29 (81%)
- INFERRED: 7 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*