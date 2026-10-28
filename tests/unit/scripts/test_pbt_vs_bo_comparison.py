"""Tests for the PBT-vs-BO comparison script's trace parsing."""

from src.scripts.pbt_vs_bo_comarison import EvaluationPoint


def test_evaluation_point_tolerates_null_metrics():
    # Failed evaluations persist ``worker_scores[n].metrics`` as null.
    point = EvaluationPoint(None, wall_time=1.0, evals_so_far=1)
    assert point.metrics.throughput == EvaluationPoint({}, 1.0, 1).metrics.throughput


def test_evaluation_point_ignores_unknown_metric_keys():
    point = EvaluationPoint({"throughput": 42.0, "bogus": 1}, 1.0, 1)
    assert point.metrics.throughput == 42.0
