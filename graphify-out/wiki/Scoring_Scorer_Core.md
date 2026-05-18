# Scoring Scorer Core

> 21 nodes · cohesion 0.11

## Key Concepts

- **tier_generator.py** (18 connections) — `src/analysis/tier_generator.py`
- **_assign_labels()** (5 connections) — `src/analysis/tier_generator.py`
- **_resolve_optimal_k()** (5 connections) — `src/analysis/tier_generator.py`
- **compare_tier_results()** (4 connections) — `src/analysis/tier_generator.py`
- **TierResult** (4 connections) — `src/analysis/tier_generator.py`
- **write_tier_result()** (4 connections) — `src/analysis/tier_generator.py`
- **_assign_interval_index()** (3 connections) — `src/analysis/tier_generator.py`
- **_clean_score()** (3 connections) — `src/analysis/tier_generator.py`
- **_parse_k_values()** (3 connections) — `src/analysis/tier_generator.py`
- **_safe_silhouette()** (3 connections) — `src/analysis/tier_generator.py`
- **test_compare_tier_results_detects_shift()** (2 connections) — `tests/unit/analysis/test_tier_generator.py`
- **Tier generation for knob importance using Jenks Natural Breaks.** (1 connections) — `src/analysis/tier_generator.py`
- **Assign a value to a Jenks interval index (ascending order).      Args:         v** (1 connections) — `src/analysis/tier_generator.py`
- **Assign Jenks cluster labels for each score.      Args:         scores: Array of** (1 connections) — `src/analysis/tier_generator.py`
- **Compute silhouette score or return NaN when undefined.      Args:         scores** (1 connections) — `src/analysis/tier_generator.py`
- **Evaluate silhouette scores and select the best k.      Args:         scores: Arr** (1 connections) — `src/analysis/tier_generator.py`
- **Write a TierResult to disk.      Args:         output_path: Output JSON path.** (1 connections) — `src/analysis/tier_generator.py`
- **Compare two tier result JSON files.      Args:         result_a: Path to the fir** (1 connections) — `src/analysis/tier_generator.py`
- **Normalize and validate k-values.      Args:         values: Iterable of k values** (1 connections) — `src/analysis/tier_generator.py`
- **Result container for Jenks-based tiering.      Attributes:         optimal_k: Se** (1 connections) — `src/analysis/tier_generator.py`
- **Convert NaN scores to None for JSON output.      Args:         score: Silhouette** (1 connections) — `src/analysis/tier_generator.py`

## Relationships

- [[Instance Management]] (46 shared connections)
- [[Metric Validation Docs]] (6 shared connections)
- [[Docker Manifest Tests]] (4 shared connections)
- [[Snapshot & Persistence]] (3 shared connections)
- [[Query Pattern Analysis]] (3 shared connections)
- [[Feature Scoring Docs]] (1 shared connections)
- [[Connection Reuse]] (1 shared connections)

## Source Files

- `src/analysis/tier_generator.py`
- `tests/unit/analysis/test_tier_generator.py`

## Audit Trail

- EXTRACTED: 62 (97%)
- INFERRED: 2 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*