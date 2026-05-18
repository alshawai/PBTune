# Instance Manager Cleanup

> 8 nodes · cohesion 0.25

## Key Concepts

- **_wilcoxon_p()** (6 connections) — `src/evaluation/statistics.py`
- **.test_wilcoxon_all_same_direction()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_wilcoxon_all_zero()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **.test_wilcoxon_mixed_directions()** (3 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **Wilcoxon signed-rank test p-value (two-sided) on paired differences.      Falls** (1 connections) — `src/evaluation/statistics.py`
- **All differences in same direction → significant at α=0.05.** (1 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **All-zero differences → p=1.0 (no effect).** (1 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`
- **Mixed direction differences → high p-value (not significant).** (1 connections) — `tests/unit/evaluation/test_evaluate_tuning.py`

## Relationships

- [[Evaluation Statistics]] (19 shared connections)

## Source Files

- `src/evaluation/statistics.py`
- `tests/unit/evaluation/test_evaluate_tuning.py`

## Audit Trail

- EXTRACTED: 13 (68%)
- INFERRED: 6 (32%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*