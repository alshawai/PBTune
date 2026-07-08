"""Tests for :mod:`src.utils.timing`."""

from __future__ import annotations

import dataclasses
import json
import time

import pytest

from src.utils.timing import TimingRecord, TimingRecorder


def test_single_span_produces_one_record_with_correct_duration() -> None:
    recorder = TimingRecorder()
    with recorder.span("comp"):
        time.sleep(0.01)
    records = recorder.records
    assert len(records) == 1
    assert records[0].component == "comp"
    assert 0.005 < records[0].seconds < 0.5
    assert records[0].metadata == {}


def test_span_metadata_is_captured() -> None:
    recorder = TimingRecorder()
    with recorder.span("comp", strategy="reload", worker_id=3):
        pass
    record = recorder.records[0]
    assert record.metadata == {"strategy": "reload", "worker_id": 3}


def test_nested_spans_produce_two_records_outer_ge_inner() -> None:
    recorder = TimingRecorder()
    with recorder.span("outer"):
        time.sleep(0.005)
        with recorder.span("inner"):
            time.sleep(0.005)
    records = recorder.records
    assert len(records) == 2
    inner = next(r for r in records if r.component == "inner")
    outer = next(r for r in records if r.component == "outer")
    assert outer.seconds >= inner.seconds


def test_add_records_externally_measured_duration() -> None:
    recorder = TimingRecorder()
    recorder.add("warmup_observed", 1.25, observed=False)
    record = recorder.records[0]
    assert record.component == "warmup_observed"
    assert record.seconds == pytest.approx(1.25)
    assert record.metadata == {"observed": False}


def test_aggregate_multiple_records_same_component() -> None:
    recorder = TimingRecorder()
    recorder.add("score", 0.1)
    recorder.add("score", 0.2)
    recorder.add("score", 0.3)
    agg = recorder.aggregate()
    assert agg["score"]["n"] == 3
    assert agg["score"]["mean"] == pytest.approx(0.2)
    assert agg["score"]["min"] == pytest.approx(0.1)
    assert agg["score"]["max"] == pytest.approx(0.3)
    assert agg["score"]["total"] == pytest.approx(0.6)
    assert agg["score"]["std"] > 0.0


def test_aggregate_single_record_has_zero_std() -> None:
    recorder = TimingRecorder()
    recorder.add("score", 0.5)
    assert recorder.aggregate()["score"]["std"] == 0.0


def test_aggregate_empty_recorder_returns_empty_dict() -> None:
    assert TimingRecorder().aggregate() == {}


def test_by_component_groups_durations() -> None:
    recorder = TimingRecorder()
    recorder.add("a", 1.0)
    recorder.add("b", 2.0)
    recorder.add("a", 3.0)
    grouped = recorder.by_component()
    assert grouped == {"a": [1.0, 3.0], "b": [2.0]}


def test_merge_combines_records_from_two_recorders() -> None:
    left = TimingRecorder()
    left.add("a", 1.0)
    right = TimingRecorder()
    right.add("b", 2.0)
    right.add("a", 3.0)
    left.merge(right)
    components = [r.component for r in left.records]
    assert components == ["a", "b", "a"]


def test_timing_record_is_frozen() -> None:
    record = TimingRecord("comp", 0.5)
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.seconds = 1.0  # type: ignore[misc]


def test_to_dict_serializes_via_json() -> None:
    recorder = TimingRecorder()
    with recorder.span("comp", worker=1):
        pass
    recorder.add("score", 0.1)
    payload = recorder.to_dict()
    # Round-trips through JSON without error.
    encoded = json.dumps(payload)
    decoded = json.loads(encoded)
    assert "records" in decoded
    assert "summary" in decoded
    assert len(decoded["records"]) == 2
    assert decoded["records"][0]["component"] == "comp"
    assert decoded["records"][0]["metadata"] == {"worker": 1}
    # No metadata field on the bare record.
    assert "metadata" not in decoded["records"][1]


def test_records_property_returns_defensive_copy() -> None:
    recorder = TimingRecorder()
    recorder.add("comp", 0.1)
    snapshot = recorder.records
    snapshot.clear()
    assert len(recorder.records) == 1


def test_aggregate_std_uses_population_stdev_not_sample() -> None:
    """aggregate() must use statistics.pstdev (n divisor), not stdev (n-1).

    Locked in to keep the cost-decomposition table reproducible — a switch
    to sample stdev would silently inflate every per-component std bar.
    """
    import statistics

    recorder = TimingRecorder()
    durations = [0.10, 0.20, 0.40, 0.50]
    for d in durations:
        recorder.add("score", d)
    agg = recorder.aggregate()
    assert agg["score"]["std"] == pytest.approx(statistics.pstdev(durations))
    # And explicitly NOT the sample stdev.
    assert agg["score"]["std"] != pytest.approx(statistics.stdev(durations))


def test_to_dict_summary_shape_matches_schema() -> None:
    """The summary dict must expose exactly the schema-documented keys.

    docs/reference/session-json-schema.md states the summary keys are
    {n, mean, std, min, max, total}. Drift here breaks the analysis script.
    """
    recorder = TimingRecorder()
    recorder.add("comp", 0.1)
    recorder.add("comp", 0.3)
    payload = recorder.to_dict()
    assert set(payload.keys()) == {"records", "summary"}
    assert set(payload["summary"]["comp"].keys()) == {
        "n",
        "mean",
        "std",
        "min",
        "max",
        "total",
    }


def test_merge_preserves_aggregation_across_recorders() -> None:
    """After merge, aggregate() should reflect the union of all records."""
    left = TimingRecorder()
    left.add("workload", 100.0)
    left.add("workload", 200.0)
    right = TimingRecorder()
    right.add("workload", 300.0)
    right.add("score", 0.05)
    left.merge(right)
    agg = left.aggregate()
    assert agg["workload"]["n"] == 3
    assert agg["workload"]["total"] == pytest.approx(600.0)
    assert agg["score"]["n"] == 1

