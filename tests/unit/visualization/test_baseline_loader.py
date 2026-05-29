import json

import numpy as np

from src.visualization.loaders.baseline import load_bo_trace


class MetricConfigStub:
    def compute_score_value(self, metrics):
        return metrics.throughput


def test_load_bo_trace_reads_generation_history_worker_metrics(tmp_path):
    result_path = tmp_path / "bo_results.json"
    result_path.write_text(
        json.dumps(
            {
                "optimizer_backend": "smac3",
                "generation_history": [
                    {
                        "timestamp": "2026-05-17T19:27:33",
                        "wall_clock_seconds": 10.0,
                        "worker_scores": [
                            {
                                "worker_id": 0,
                                "score": 1.0,
                                "metrics": {"throughput": 5.0},
                            }
                        ],
                    },
                    {
                        "timestamp": "2026-05-17T19:27:43",
                        "wall_clock_seconds": 10.0,
                        "worker_scores": [
                            {
                                "worker_id": 0,
                                "score": 2.0,
                                "metrics": {"throughput": 10.0},
                            }
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    trace = load_bo_trace(result_path, metric_config=MetricConfigStub())

    assert trace.method_name == "bo_smac"
    np.testing.assert_allclose(trace.best_scores, [5.0, 10.0])


def test_load_bo_trace_falls_back_to_worker_scores(tmp_path):
    result_path = tmp_path / "bo_results.json"
    result_path.write_text(
        json.dumps(
            {
                "generation_history": [
                    {"wall_clock_seconds": 1.0, "worker_scores": [{"score": 4.0}]},
                    {"wall_clock_seconds": 2.0, "worker_scores": [{"score": 3.0}]},
                    {"wall_clock_seconds": 3.0, "worker_scores": [{"score": 6.0}]},
                ]
            }
        ),
        encoding="utf-8",
    )

    trace = load_bo_trace(result_path)

    np.testing.assert_allclose(trace.best_scores, [4.0, 4.0, 6.0])
