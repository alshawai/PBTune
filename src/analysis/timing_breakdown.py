"""
Timing Breakdown Analysis
=========================

Consume timing data emitted by timing_schema_version "1.0" and produce a
CDBTune §5.1.1-style per-component cost decomposition.

Aggregation semantics
---------------------

The aggregation unit is the per-(session, generation, worker) duration record.
This matches CDBTune §5.1.1: each "trial" in their table corresponds to one
(session, generation, worker) tuple in our PBT setup, and the mean ± std are
computed across all such tuples in the input population. Generation-scoped
records (e.g. ``evolve``) contribute one duration per (session, generation);
the bootstrap row sums all bootstrap components into a single per-session
duration.

Inputs are session JSONs matching the Phase 2 timing schema:

    tuning_session.timing_schema_version: "1.0"
    timing_summary: {component: {n, mean, std, min, max, total}}
    bootstrap_breakdown: {records: [...], summary: {...}}
    generation_history[i].timing: per-generation timing
    generation_history[i].worker_scores[j].timing: per-worker timing
      where timing has a `records` list of `{component, seconds, metadata}`

Sessions without the timing block (pre-v1.0) are skipped with a warning.

CLI
---

.. code-block:: bash

    python -m src.analysis.timing_breakdown \\
        --sessions "results/.../pbt_results_*.json" \\
        --output  results/timing_breakdown/pbt_offline_extensive.tex \\
        --format  latex
"""

from __future__ import annotations

import argparse
import csv
import glob
import io
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

from src.utils.logger import setup_logging, get_logger

LOGGER = get_logger("TimingBreakdown")

DEFAULT_COMPONENTS: list[str] = [
    "apply_only",
    "activate_reload",
    "activate_restart",
    "snapshot_restore",
    "knob_verify",
    "workload",
    "score",
    "evolve",
]

BOOTSTRAP_KEY = "bootstrap"


def load_sessions(glob_pattern: str) -> list[dict]:
    """Read every JSON file matching ``glob_pattern`` and return parsed dicts.

    Files that fail to parse are skipped with a warning; the caller sees only
    the successfully-loaded session payloads.
    """
    paths = sorted(glob.glob(glob_pattern))
    sessions: list[dict] = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Failed to load %s: %s", path, exc)
            continue
        payload.setdefault("_source_path", path)
        sessions.append(payload)
    return sessions


def _has_timing_schema(session: dict) -> bool:
    """Return True iff the session declares timing_schema_version >= 1.0."""
    ts = session.get("tuning_session") or {}
    version = ts.get("timing_schema_version")
    if version is None:
        return False
    try:
        # accept "1.0", "1.1", "2.0", etc.
        return float(version) >= 1.0
    except (TypeError, ValueError):
        return False


def _iter_records(timing_block: Any) -> Iterable[dict]:
    """Yield {component, seconds, metadata} dicts from a timing block.

    Tolerates the block being missing, being a list, or having a ``records``
    key that is missing.
    """
    if not timing_block:
        return
    if isinstance(timing_block, list):
        records = timing_block
    elif isinstance(timing_block, dict):
        records = timing_block.get("records") or []
    else:
        return
    for rec in records:
        if isinstance(rec, dict) and "component" in rec and "seconds" in rec:
            yield rec


def extract_per_session_timings(session: dict) -> dict[str, list[float]]:
    """Flatten one session's records into ``{component: [durations]}``.

    Combines per-worker, per-generation, and bootstrap records. Bootstrap
    components are all collapsed onto a single ``"bootstrap"`` key (one entry
    per session — the sum across bootstrap sub-components).
    """
    out: dict[str, list[float]] = {}

    # Per-worker timings inside generation_history[i].worker_scores[j].timing
    for gen in session.get("generation_history") or []:
        for worker in (gen.get("worker_scores") or gen.get("workers") or []):
            for rec in _iter_records(worker.get("timing")):
                out.setdefault(str(rec["component"]), []).append(float(rec["seconds"]))
        # Per-generation timings (e.g. "evolve")
        for rec in _iter_records(gen.get("timing")):
            out.setdefault(str(rec["component"]), []).append(float(rec["seconds"]))

    # Bootstrap — collapsed to a single per-session sum.
    bootstrap_total = 0.0
    bootstrap_seen = False
    boot = session.get("bootstrap_breakdown") or {}
    for rec in _iter_records(boot):
        bootstrap_total += float(rec["seconds"])
        bootstrap_seen = True
    # Some emitters might also expose bootstrap_seconds directly:
    if not bootstrap_seen:
        ts = session.get("tuning_session") or {}
        if "bootstrap_seconds" in ts:
            bootstrap_total = float(ts["bootstrap_seconds"])
            bootstrap_seen = True
    if bootstrap_seen:
        out.setdefault(BOOTSTRAP_KEY, []).append(bootstrap_total)

    return out


def _summary(values: list[float]) -> dict[str, float]:
    """Compute summary stats over a non-empty population (sample std, n-1)."""
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": 0.0, "std": 0.0,
                "min": 0.0, "max": 0.0, "total": 0.0}
    total = float(sum(values))
    mean = total / n
    if n > 1:
        var = sum((v - mean) ** 2 for v in values) / (n - 1)
        std = math.sqrt(var)
    else:
        std = 0.0
    return {
        "n": n,
        "mean": mean,
        "std": std,
        "min": float(min(values)),
        "max": float(max(values)),
        "total": total,
    }


def aggregate_across_sessions(
    sessions: list[dict],
) -> dict[str, dict[str, float]]:
    """Aggregate per-component durations across the union of input sessions.

    Pre-v1.0 sessions are silently dropped here (callers should already have
    filtered them via :func:`partition_sessions_by_schema`).
    """
    pooled: dict[str, list[float]] = {}
    for session in sessions:
        if not _has_timing_schema(session):
            continue
        per_session = extract_per_session_timings(session)
        for comp, vals in per_session.items():
            pooled.setdefault(comp, []).extend(vals)
    return {comp: _summary(vals) for comp, vals in pooled.items()}


def partition_sessions_by_schema(
    sessions: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Split into (with_timing, skipped) by ``timing_schema_version``."""
    kept: list[dict] = []
    skipped: list[dict] = []
    for s in sessions:
        (kept if _has_timing_schema(s) else skipped).append(s)
    return kept, skipped


def _ordered_components(
    agg: dict[str, dict[str, float]],
    include_components: Optional[list[str]],
) -> list[str]:
    """Return component list to emit, honoring an explicit include list."""
    if include_components is not None:
        return [c for c in include_components if c in agg]
    # Default: CDBTune order, then any extra components alphabetically,
    # then the bootstrap row last.
    ordered = [c for c in DEFAULT_COMPONENTS if c in agg]
    extras = sorted(
        c for c in agg
        if c not in DEFAULT_COMPONENTS and c != BOOTSTRAP_KEY
    )
    tail = [BOOTSTRAP_KEY] if BOOTSTRAP_KEY in agg else []
    return ordered + extras + tail


def format_markdown_table(
    agg: dict[str, dict[str, float]],
    *,
    include_components: Optional[list[str]] = None,
    title: Optional[str] = None,
) -> str:
    """Render the breakdown as a GitHub-flavored markdown table."""
    comps = _ordered_components(agg, include_components)
    buf = io.StringIO()
    if title:
        buf.write(f"### {title}\n\n")
    buf.write("| Component | n | mean (s) | std (s) | total (s) |\n")
    buf.write("|---|---:|---:|---:|---:|\n")
    for comp in comps:
        s = agg[comp]
        buf.write(
            f"| {comp} | {int(s['n'])} | {s['mean']:.4f} "
            f"| {s['std']:.4f} | {s['total']:.4f} |\n"
        )
    return buf.getvalue()


def format_latex_table(
    agg: dict[str, dict[str, float]],
    *,
    include_components: Optional[list[str]] = None,
    caption: str = "Per-component cost decomposition (mean $\\pm$ std).",
    label: str = "tab:timing-breakdown",
) -> str:
    """Render the breakdown as a booktabs-style LaTeX ``tabular``."""
    comps = _ordered_components(agg, include_components)
    buf = io.StringIO()
    buf.write("\\begin{table}[t]\n")
    buf.write("\\centering\n")
    buf.write(f"\\caption{{{caption}}}\n")
    buf.write(f"\\label{{{label}}}\n")
    buf.write("\\begin{tabular}{lrrrr}\n")
    buf.write("\\toprule\n")
    buf.write("Component & $n$ & Mean (s) & Std (s) & Total (s) \\\\\n")
    buf.write("\\midrule\n")
    for comp in comps:
        s = agg[comp]
        safe = comp.replace("_", "\\_")
        buf.write(
            f"{safe} & {int(s['n'])} & {s['mean']:.4f} "
            f"& {s['std']:.4f} & {s['total']:.4f} \\\\\n"
        )
    buf.write("\\bottomrule\n")
    buf.write("\\end{tabular}\n")
    buf.write("\\end{table}\n")
    return buf.getvalue()


def format_csv(agg: dict[str, dict[str, float]]) -> str:
    """Render the breakdown as CSV (component, n, mean, std, min, max, total)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["component", "n", "mean", "std", "min", "max", "total"])
    for comp in _ordered_components(agg, None):
        s = agg[comp]
        writer.writerow([
            comp,
            int(s["n"]),
            f"{s['mean']:.6f}",
            f"{s['std']:.6f}",
            f"{s['min']:.6f}",
            f"{s['max']:.6f}",
            f"{s['total']:.6f}",
        ])
    return buf.getvalue()


def format_json(agg: dict[str, dict[str, float]]) -> str:
    """Render the breakdown as a JSON payload."""
    payload = {
        comp: {
            "n": int(agg[comp]["n"]),
            "mean": agg[comp]["mean"],
            "std": agg[comp]["std"],
            "min": agg[comp]["min"],
            "max": agg[comp]["max"],
            "total": agg[comp]["total"],
        }
        for comp in _ordered_components(agg, None)
    }
    return json.dumps(payload, indent=2)


def aggregate_by_mode(
    sessions: list[dict],
) -> dict[str, dict[str, dict[str, float]]]:
    """Group sessions by ``tuning_session.tuning_mode`` and aggregate each."""
    buckets: dict[str, list[dict]] = {}
    for s in sessions:
        if not _has_timing_schema(s):
            continue
        mode = (s.get("tuning_session") or {}).get("tuning_mode") or "UNKNOWN"
        buckets.setdefault(str(mode).upper(), []).append(s)
    return {mode: aggregate_across_sessions(sess)
            for mode, sess in buckets.items()}


def format_by_mode_markdown(
    by_mode: dict[str, dict[str, dict[str, float]]],
    *,
    include_components: Optional[list[str]] = None,
) -> str:
    """Render one markdown table per mode."""
    buf = io.StringIO()
    for mode in sorted(by_mode):
        buf.write(format_markdown_table(
            by_mode[mode],
            include_components=include_components,
            title=f"Mode: {mode}",
        ))
        buf.write("\n")
    return buf.getvalue()


def format_compare_markdown(
    pbt_agg: dict[str, dict[str, float]],
    bo_agg: dict[str, dict[str, float]],
    *,
    include_components: Optional[list[str]] = None,
) -> str:
    """Side-by-side PBT vs BO markdown comparison table."""
    comps = sorted(
        set(_ordered_components(pbt_agg, include_components))
        | set(_ordered_components(bo_agg, include_components)),
        key=lambda c: (
            DEFAULT_COMPONENTS.index(c) if c in DEFAULT_COMPONENTS
            else (10_000 if c == BOOTSTRAP_KEY else 1_000)
        ),
    )
    buf = io.StringIO()
    buf.write(
        "| Component | PBT n | PBT mean (s) | PBT std (s) "
        "| BO n | BO mean (s) | BO std (s) |\n"
    )
    buf.write("|---|---:|---:|---:|---:|---:|---:|\n")
    for comp in comps:
        p = pbt_agg.get(comp)
        b = bo_agg.get(comp)
        row = [comp]
        for s in (p, b):
            if s is None:
                row += ["-", "-", "-"]
            else:
                row += [str(int(s["n"])), f"{s['mean']:.4f}", f"{s['std']:.4f}"]
        buf.write("| " + " | ".join(row) + " |\n")
    return buf.getvalue()


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.analysis.timing_breakdown",
        description=(
            "Compute a CDBTune §5.1.1-style per-component cost decomposition "
            "from PBT (and optionally BO) session JSONs."
        ),
    )
    p.add_argument(
        "--sessions",
        required=True,
        help="Glob pattern for PBT session JSON files.",
    )
    p.add_argument(
        "--output",
        default="-",
        help="Output path (default: stdout).",
    )
    p.add_argument(
        "--format",
        choices=["markdown", "latex", "csv", "json"],
        default="markdown",
    )
    p.add_argument(
        "--by-mode",
        action="store_true",
        help="Group output by tuning_session.tuning_mode.",
    )
    p.add_argument(
        "--compare-bo",
        metavar="GLOB",
        default=None,
        help="Glob for BO session JSONs; produces a PBT-vs-BO side-by-side.",
    )
    p.add_argument(
        "--components",
        nargs="+",
        default=None,
        help="Restrict the table to this list of components (in order).",
    )
    return p


def _render(
    agg: dict[str, dict[str, float]],
    fmt: str,
    include_components: Optional[list[str]],
) -> str:
    if fmt == "markdown":
        return format_markdown_table(agg, include_components=include_components)
    if fmt == "latex":
        return format_latex_table(agg, include_components=include_components)
    if fmt == "csv":
        return format_csv(agg)
    if fmt == "json":
        return format_json(agg)
    raise ValueError(f"Unknown format: {fmt}")


def _write_output(path: str, content: str) -> None:
    if path == "-" or path == "":
        sys.stdout.write(content)
        return
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point for CLI."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    
    setup_logging()

    pbt_sessions = load_sessions(args.sessions)
    if not pbt_sessions:
        LOGGER.error("No PBT sessions matched glob: %s", args.sessions)
        return 2

    kept, skipped = partition_sessions_by_schema(pbt_sessions)
    if skipped:
        LOGGER.warning(
            "Skipped %d session(s) without timing_schema_version >= 1.0 "
            "(pre-instrumentation).",
            len(skipped),
        )
    if not kept:
        LOGGER.error(
            "No sessions with timing_schema_version >= 1.0 — nothing to "
            "aggregate.",
        )
        return 3

    if args.compare_bo:
        bo_sessions = load_sessions(args.compare_bo)
        bo_kept, bo_skipped = partition_sessions_by_schema(bo_sessions)
        if bo_skipped:
            LOGGER.warning(
                "Skipped %d BO session(s) without timing_schema_version "
                ">= 1.0.",
                len(bo_skipped),
            )
        pbt_agg = aggregate_across_sessions(kept)
        bo_agg = aggregate_across_sessions(bo_kept)

        if args.format == "markdown":
            content = format_compare_markdown(
                pbt_agg, bo_agg, include_components=args.components,
            )
        elif args.format == "json":
            content = json.dumps({
                "pbt": json.loads(format_json(pbt_agg)),
                "bo": json.loads(format_json(bo_agg))
                },
                indent=2,
            )
        else:
            # Fall back to back-to-back render for latex/csv.
            content = (
                "% PBT\n" + _render(pbt_agg, args.format, args.components)
                + "\n% BO\n" + _render(bo_agg, args.format, args.components)
            )
        _write_output(args.output, content)
        return 0

    if args.by_mode:
        by_mode = aggregate_by_mode(kept)
        if args.format == "markdown":
            content = format_by_mode_markdown(
                by_mode, include_components=args.components,
            )
        elif args.format == "json":
            content = json.dumps(
                {mode: json.loads(format_json(a)) for mode, a in by_mode.items()},
                indent=2,
            )
        else:
            parts = []
            for mode in sorted(by_mode):
                parts.append(f"% mode={mode}")
                parts.append(_render(by_mode[mode], args.format, args.components))
            content = "\n".join(parts) + "\n"
        _write_output(args.output, content)
        return 0

    agg = aggregate_across_sessions(kept)
    content = _render(agg, args.format, args.components)
    _write_output(args.output, content)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
