"""Unit tests for ``src.analysis.timing_breakdown``."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from src.analysis import timing_breakdown as tb


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _make_session(
    *,
    mode: str = "OFFLINE",
    schema: str | None = "1.0",
    worker_records: list[list[dict]] | None = None,
    evolve_seconds: float | None = 0.5,
    bootstrap_records: list[dict] | None = None,
) -> dict:
    """Build a minimal v1.0 session dict suitable for the analyzer."""
    if worker_records is None:
        worker_records = [
            [
                {"component": "apply_only", "seconds": 0.10},
                {"component": "activate_restart", "seconds": 2.00},
                {"component": "knob_verify", "seconds": 0.20},
                {"component": "workload", "seconds": 30.00},
                {"component": "score", "seconds": 0.05},
            ],
        ]
    if bootstrap_records is None:
        bootstrap_records = [
            {"component": "bootstrap_setup_instances", "seconds": 5.0},
            {"component": "bootstrap_verify_instances", "seconds": 1.0},
        ]

    session: dict = {
        "tuning_session": {
            "tuning_mode": mode,
            "total_time_seconds": 100.0,
            "tuning_time_seconds": 90.0,
            "bootstrap_seconds": 10.0,
        },
        "bootstrap_breakdown": {"records": bootstrap_records, "summary": {}},
        "generation_history": [
            {
                "worker_scores": [
                    {"worker_id": i, "timing": {"records": recs}}
                    for i, recs in enumerate(worker_records)
                ],
                "timing": (
                    {"records": [{"component": "evolve",
                                  "seconds": evolve_seconds}]}
                    if evolve_seconds is not None else {}
                ),
            }
        ],
    }
    if schema is not None:
        session["tuning_session"]["timing_schema_version"] = schema
    return session


# --------------------------------------------------------------------------- #
# load_sessions
# --------------------------------------------------------------------------- #


def test_load_sessions_reads_multiple_files(tmp_path: Path) -> None:
    s1 = _make_session()
    s2 = _make_session(mode="ONLINE")
    (tmp_path / "a.json").write_text(json.dumps(s1))
    (tmp_path / "b.json").write_text(json.dumps(s2))
    (tmp_path / "ignore.txt").write_text("not json")

    out = tb.load_sessions(str(tmp_path / "*.json"))
    assert len(out) == 2
    modes = sorted(s["tuning_session"]["tuning_mode"] for s in out)
    assert modes == ["OFFLINE", "ONLINE"]
    # source path is attached
    assert all("_source_path" in s for s in out)


def test_load_sessions_skips_unparseable(tmp_path: Path, caplog) -> None:
    (tmp_path / "good.json").write_text(json.dumps(_make_session()))
    (tmp_path / "bad.json").write_text("{not valid json")

    with caplog.at_level(logging.WARNING):
        out = tb.load_sessions(str(tmp_path / "*.json"))
    assert len(out) == 1
    assert any("Failed to load" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# extract_per_session_timings
# --------------------------------------------------------------------------- #


def test_extract_per_session_timings_flattens_records() -> None:
    session = _make_session(
        worker_records=[
            [
                {"component": "apply_only", "seconds": 0.1},
                {"component": "workload", "seconds": 30.0},
            ],
            [
                {"component": "apply_only", "seconds": 0.2},
                {"component": "workload", "seconds": 28.0},
            ],
        ],
        evolve_seconds=0.7,
    )
    flat = tb.extract_per_session_timings(session)

    assert flat["apply_only"] == [0.1, 0.2]
    assert flat["workload"] == [30.0, 28.0]
    assert flat["evolve"] == [0.7]
    # bootstrap collapses to a per-session sum.
    assert flat["bootstrap"] == [pytest.approx(6.0)]


def test_extract_falls_back_to_bootstrap_seconds_when_no_records() -> None:
    session = _make_session(bootstrap_records=[])
    # No records in bootstrap_breakdown — should pick up bootstrap_seconds.
    flat = tb.extract_per_session_timings(session)
    assert flat["bootstrap"] == [pytest.approx(10.0)]


# --------------------------------------------------------------------------- #
# aggregate_across_sessions
# --------------------------------------------------------------------------- #


def test_aggregate_across_sessions_computes_known_stats() -> None:
    # 4 apply_only durations across 2 sessions; mean=0.25, sample std=√(1/30)≈0.183
    s1 = _make_session(worker_records=[
        [{"component": "apply_only", "seconds": 0.1}],
        [{"component": "apply_only", "seconds": 0.2}],
    ], bootstrap_records=[], evolve_seconds=None)
    s2 = _make_session(worker_records=[
        [{"component": "apply_only", "seconds": 0.3}],
        [{"component": "apply_only", "seconds": 0.4}],
    ], bootstrap_records=[], evolve_seconds=None)

    agg = tb.aggregate_across_sessions([s1, s2])
    a = agg["apply_only"]
    assert a["n"] == 4
    assert a["mean"] == pytest.approx(0.25)
    assert a["total"] == pytest.approx(1.0)
    assert a["min"] == pytest.approx(0.1)
    assert a["max"] == pytest.approx(0.4)
    # sample std with n-1=3, variance = 0.05/3
    expected_std = (0.05 / 3) ** 0.5
    assert a["std"] == pytest.approx(expected_std, rel=1e-6)


def test_aggregate_handles_single_value() -> None:
    s = _make_session(worker_records=[
        [{"component": "score", "seconds": 1.5}],
    ], bootstrap_records=[], evolve_seconds=None)
    agg = tb.aggregate_across_sessions([s])
    assert agg["score"]["n"] == 1
    assert agg["score"]["std"] == 0.0


# --------------------------------------------------------------------------- #
# Pre-v1.0 skip + warning
# --------------------------------------------------------------------------- #


def test_pre_v1_sessions_are_skipped(caplog) -> None:
    new = _make_session(schema="1.0")
    legacy = _make_session(schema=None)
    kept, skipped = tb.partition_sessions_by_schema([new, legacy])
    assert len(kept) == 1 and len(skipped) == 1

    # aggregate should treat legacy as absent.
    agg = tb.aggregate_across_sessions([new, legacy])
    # Only the v1.0 session's records contribute.
    assert agg["apply_only"]["n"] == 1


def test_main_warns_about_skipped_sessions(tmp_path: Path, caplog, monkeypatch) -> None:
    (tmp_path / "ok.json").write_text(json.dumps(_make_session()))
    (tmp_path / "legacy.json").write_text(
        json.dumps(_make_session(schema=None))
    )
    out = tmp_path / "out.json"
    # ``setup_logging()`` (called inside ``tb.main``) clears root handlers,
    # wiping caplog's LogCaptureHandler. Stub it out so caplog can capture.
    monkeypatch.setattr(tb, "setup_logging", lambda *a, **kw: None)
    with caplog.at_level(logging.WARNING, logger="TimingBreakdown"):
        rc = tb.main([
            "--sessions", str(tmp_path / "*.json"),
            "--output", str(out),
            "--format", "json",
        ])
    assert rc == 0
    assert any("Skipped 1 session" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# Formatters
# --------------------------------------------------------------------------- #


def test_format_latex_table_contains_booktabs_rules() -> None:
    agg = tb.aggregate_across_sessions([_make_session()])
    tex = tb.format_latex_table(agg)
    assert "\\toprule" in tex
    assert "\\midrule" in tex
    assert "\\bottomrule" in tex
    assert "\\begin{tabular}" in tex
    assert "\\end{tabular}" in tex
    # underscores should be escaped for LaTeX
    assert "apply\\_only" in tex


def test_format_markdown_table_is_well_formed() -> None:
    agg = tb.aggregate_across_sessions([_make_session()])
    md = tb.format_markdown_table(agg)
    lines = [ln for ln in md.splitlines() if ln.strip()]
    assert lines[0].startswith("| Component")
    # second row is the separator
    assert set(lines[1].replace("|", "").replace(":", "").strip()) <= {"-", " "}
    # at least one component row
    assert any(ln.startswith("| apply_only") for ln in lines)


def test_format_csv_has_header_and_rows() -> None:
    agg = tb.aggregate_across_sessions([_make_session()])
    csv_out = tb.format_csv(agg)
    first_line = csv_out.splitlines()[0]
    assert first_line == "component,n,mean,std,min,max,total"
    assert "apply_only" in csv_out


def test_format_json_round_trips() -> None:
    agg = tb.aggregate_across_sessions([_make_session()])
    payload = json.loads(tb.format_json(agg))
    assert "apply_only" in payload
    assert payload["apply_only"]["n"] >= 1


# --------------------------------------------------------------------------- #
# CLI end-to-end
# --------------------------------------------------------------------------- #


def test_main_json_format_writes_two_fixture_sessions(tmp_path: Path) -> None:
    s1 = _make_session(worker_records=[
        [{"component": "apply_only", "seconds": 0.1},
         {"component": "workload", "seconds": 30.0}],
    ], evolve_seconds=0.5)
    s2 = _make_session(worker_records=[
        [{"component": "apply_only", "seconds": 0.3},
         {"component": "workload", "seconds": 32.0}],
    ], evolve_seconds=0.6)
    (tmp_path / "a.json").write_text(json.dumps(s1))
    (tmp_path / "b.json").write_text(json.dumps(s2))
    out = tmp_path / "breakdown.json"

    rc = tb.main([
        "--sessions", str(tmp_path / "*.json"),
        "--output", str(out),
        "--format", "json",
    ])
    assert rc == 0
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["apply_only"]["n"] == 2
    assert payload["apply_only"]["mean"] == pytest.approx(0.2)
    assert payload["workload"]["n"] == 2
    assert payload["workload"]["mean"] == pytest.approx(31.0)
    assert payload["evolve"]["n"] == 2
    assert "bootstrap" in payload


def test_main_no_sessions_returns_error(tmp_path: Path) -> None:
    rc = tb.main([
        "--sessions", str(tmp_path / "nothing_*.json"),
        "--output", "-",
    ])
    assert rc == 2


def test_main_only_legacy_sessions_returns_error(tmp_path: Path) -> None:
    (tmp_path / "old.json").write_text(json.dumps(_make_session(schema=None)))
    rc = tb.main([
        "--sessions", str(tmp_path / "*.json"),
        "--output", "-",
    ])
    assert rc == 3


def test_main_by_mode_groups_output(tmp_path: Path) -> None:
    (tmp_path / "off.json").write_text(json.dumps(_make_session(mode="OFFLINE")))
    (tmp_path / "on.json").write_text(json.dumps(_make_session(mode="ONLINE")))
    out = tmp_path / "by_mode.json"
    rc = tb.main([
        "--sessions", str(tmp_path / "*.json"),
        "--output", str(out),
        "--format", "json",
        "--by-mode",
    ])
    assert rc == 0
    payload = json.loads(out.read_text())
    assert set(payload.keys()) == {"OFFLINE", "ONLINE"}


def test_main_compare_bo_emits_both_columns(tmp_path: Path) -> None:
    pbt_dir = tmp_path / "pbt"
    bo_dir = tmp_path / "bo"
    pbt_dir.mkdir()
    bo_dir.mkdir()
    (pbt_dir / "p.json").write_text(json.dumps(_make_session()))
    (bo_dir / "b.json").write_text(json.dumps(_make_session(mode="BO")))
    out = tmp_path / "compare.md"

    rc = tb.main([
        "--sessions", str(pbt_dir / "*.json"),
        "--compare-bo", str(bo_dir / "*.json"),
        "--output", str(out),
        "--format", "markdown",
    ])
    assert rc == 0
    text = out.read_text()
    assert "PBT mean" in text and "BO mean" in text
    assert "apply_only" in text
