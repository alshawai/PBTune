"""SCALPEL tier-diagnostics figure.

Surfaces the BORUTA hits, BH-FDR-adjusted p-values, stability
probabilities, Lorenz cumulative-mass curve, and DBA-prior violations
that the legacy ``knob_importance`` figure cannot show.

The figure renders best on a SCALPEL run with a populated
``scalpel_diagnostics.json`` sibling. On legacy / Lorenz-fallback
inputs the loader returns minimal payload and the plot degrades to
a single tier-summary panel + an explicit annotation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, cast

import numpy as np
from matplotlib.axes import Axes

from src.utils.logger import get_logger
from src.visualization import export_figure, register_figure
from src.visualization.exceptions import DataLoadError
from src.visualization.loaders import TierDiagnostics, load_tier_diagnostics
from src.visualization.theme import PBTuneTheme
from src.visualization.types import ExportFormat, FigureSpec
from src.visualization.utils import despine

LOGGER = get_logger("TierDiagnosticsPlot")

# Visual palette aligned with the PBTune theme (kept local to avoid
# pulling colour tokens into the loader module).
TIER_COLORS = {
    "minimal": "#1b9e77",
    "core": "#d95f02",
    "standard": "#7570b3",
    "not_confirmed": "#bdbdbd",
}
TOP_K_FOR_BARS = 25


def _shorten(name: str, max_len: int = 26) -> str:
    """Compact knob name for tight-axis bar charts."""
    if len(name) <= max_len:
        return name
    return name[: max_len - 3] + "..."


def _resolve_results_path(target: Path) -> Path:
    """Locate ``importance_results.json`` for a workload-or-file argument."""
    if target.is_file():
        return target
    if target.is_dir():
        candidate = target / "importance_results.json"
        if candidate.is_file():
            return candidate
    raise DataLoadError(
        f"Could not locate importance_results.json at or under {target}. "
        "Run `python -m src.scripts.analyze_knob_importance --algorithm scalpel` "
        "first."
    )


def _draw_tier_summary(ax: Axes, diag: TierDiagnostics) -> None:
    """Top-level tier counts + nuisance-dropped count + DBA prior."""
    counts = {"minimal": 0, "core": 0, "standard": 0}
    for _knob, tier in diag.tier_assignments.items():
        if tier in counts:
            counts[tier] += 1

    bars = list(counts.items())
    ax.bar(
        [b[0] for b in bars],
        [b[1] for b in bars],
        color=[TIER_COLORS[name] for name, _ in bars],
        edgecolor="black",
        linewidth=0.4,
    )
    for i, (_name, count) in enumerate(bars):
        ax.text(
            i,
            count + max(0.5, 0.02 * max(c for _, c in bars) if bars else 1.0),
            f"{count}",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    ax.set_title(
        f"Tier counts ({diag.algorithm})  •  nuisance-dropped: {len(diag.nuisance_dropped)}"
        f"  •  DBA-prior violations: {len(diag.dba_prior_violations)}",
        fontsize=10,
    )
    ax.set_ylabel("# knobs")
    despine(ax)


def _draw_boruta_hits(ax: Axes, diag: TierDiagnostics) -> None:
    """Horizontal bar of BORUTA hit counts for the top confirmed knobs."""
    if not diag.boruta_hits:
        ax.text(
            0.5,
            0.5,
            "BORUTA hits unavailable\n(legacy / Lorenz fallback)",
            ha="center",
            va="center",
            fontsize=9,
        )
        ax.set_axis_off()
        return

    sorted_items = sorted(
        diag.boruta_hits.items(), key=lambda kv: (-kv[1], kv[0])
    )[:TOP_K_FOR_BARS]
    knobs = [_shorten(k) for k, _ in sorted_items]
    hits = [v for _, v in sorted_items]
    tiers = [diag.tier_assignments.get(k, "not_confirmed") for k, _ in sorted_items]
    colors = [TIER_COLORS.get(t, "#bdbdbd") for t in tiers]
    ypos = np.arange(len(knobs))
    ax.barh(ypos, hits, color=colors, edgecolor="black", linewidth=0.3)
    ax.set_yticks(ypos)
    ax.set_yticklabels(knobs, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("BORUTA hit count")
    ax.set_title(f"Top-{len(knobs)} BORUTA hits (tier-coloured)", fontsize=10)
    despine(ax)


def _draw_lorenz_curve(ax: Axes, diag: TierDiagnostics) -> None:
    """Cumulative-mass curve of the confirmed subset with cut points."""
    if not diag.cumulative_coverage:
        ax.text(
            0.5,
            0.5,
            "Lorenz curve unavailable\n(no SCALPEL diagnostics)",
            ha="center",
            va="center",
            fontsize=9,
        )
        ax.set_axis_off()
        return

    items = sorted(
        diag.cumulative_coverage.items(), key=lambda kv: kv[1]
    )
    coverage = np.array([v for _, v in items])
    rank = np.arange(1, len(coverage) + 1)
    ax.plot(rank, coverage, color="black", linewidth=1.2)
    ax.fill_between(rank, 0, coverage, color="#cccccc", alpha=0.4)

    minimal_cut = diag.lorenz_breakpoints.get("minimal")
    core_cut = diag.lorenz_breakpoints.get("core")
    if minimal_cut is not None:
        ax.axhline(minimal_cut, color=TIER_COLORS["minimal"], linestyle="--", linewidth=0.8)
        ax.text(
            len(coverage),
            minimal_cut,
            f"  minimal ({minimal_cut:.2f})",
            color=TIER_COLORS["minimal"],
            va="center",
            fontsize=8,
        )
    if core_cut is not None:
        ax.axhline(core_cut, color=TIER_COLORS["core"], linestyle="--", linewidth=0.8)
        ax.text(
            len(coverage),
            core_cut,
            f"  core ({core_cut:.2f})",
            color=TIER_COLORS["core"],
            va="center",
            fontsize=8,
        )

    ax.set_xlim(1, max(len(coverage), 2))
    ax.set_ylim(0, 1.0)
    ax.set_xlabel("Confirmed knob rank")
    ax.set_ylabel("Cumulative mass")
    ax.set_title("Lorenz cumulative-mass curve", fontsize=10)
    despine(ax)


def _draw_stability_strip(ax: Axes, diag: TierDiagnostics) -> None:
    """Stability probability strip plot grouped by assigned tier."""
    if not diag.stability_probabilities:
        ax.text(
            0.5,
            0.5,
            "Stability scores unavailable",
            ha="center",
            va="center",
            fontsize=9,
        )
        ax.set_axis_off()
        return

    by_tier: dict[str, list[float]] = {"minimal": [], "core": [], "standard": []}
    for knob, prob in diag.stability_probabilities.items():
        tier = diag.tier_assignments.get(knob)
        if tier in by_tier:
            by_tier[tier].append(float(prob))

    rng = np.random.default_rng(42)
    for idx, (tier, values) in enumerate(by_tier.items()):
        if not values:
            continue
        x_jitter = rng.uniform(-0.12, 0.12, size=len(values)) + idx
        ax.scatter(
            x_jitter,
            values,
            color=TIER_COLORS[tier],
            alpha=0.85,
            s=18,
            edgecolor="black",
            linewidth=0.3,
        )
    ax.axhline(0.80, color="black", linestyle=":", linewidth=0.8)
    ax.set_xticks(range(len(by_tier)))
    ax.set_xticklabels(list(by_tier.keys()))
    ax.set_ylim(0.0, 1.05)
    ax.set_ylabel("Selection probability")
    ax.set_title("Stability per tier (B subsamples)", fontsize=10)
    despine(ax)


def _draw_dba_violations(ax: Axes, diag: TierDiagnostics) -> None:
    """Callout panel listing expert-minimal knobs landed outside data minimal."""
    ax.set_axis_off()
    if not diag.dba_prior_violations:
        ax.text(
            0.0,
            1.0,
            "No DBA-prior violations.",
            ha="left",
            va="top",
            fontsize=9,
            color="#1b9e77",
        )
        return

    lines = ["DBA-prior violations (report-only):"]
    for v in diag.dba_prior_violations[:12]:
        lines.append(
            f"  • {v.get('knob', '?')}: expert=minimal → data={v.get('data_tier', 'not_confirmed')}"
        )
    if len(diag.dba_prior_violations) > 12:
        lines.append(f"  • ... +{len(diag.dba_prior_violations) - 12} more")
    ax.text(
        0.0,
        1.0,
        "\n".join(lines),
        ha="left",
        va="top",
        fontsize=9,
        family="monospace",
        color="#d95f02",
    )


def generate_tier_diagnostics(
    data_dir: Path,
    output_dir: Path,
    formats: Iterable[ExportFormat] | None = None,
    theme: PBTuneTheme | None = None,
) -> None:
    """Render the SCALPEL tier-diagnostics figure for a workload.

    ``data_dir`` may point at either an analysis directory containing
    ``importance_results.json`` or the JSON file itself.
    """
    results_path = _resolve_results_path(Path(data_dir))
    diag = load_tier_diagnostics(results_path)

    theme = theme or PBTuneTheme()
    with theme.apply():
        fig, axes_arr = theme.subplots(
            nrows=2,
            ncols=2,
            size_hint="double",
        )
        axes = cast("list[Axes]", list(axes_arr.ravel()))
        fig.suptitle(
            f"SCALPEL diagnostics — {diag.workload_label} ({diag.algorithm})",
            fontsize=11,
        )

        _draw_tier_summary(axes[0], diag)
        _draw_boruta_hits(axes[1], diag)
        _draw_lorenz_curve(axes[2], diag)
        _draw_stability_strip(axes[3], diag)

        fig.tight_layout(rect=(0, 0, 1, 0.96))

    export_figure(
        fig,
        output_dir=output_dir,
        fig_id="tier_diagnostics",
        formats=list(formats) if formats is not None else None,
        metadata={
            "algorithm": diag.algorithm,
            "scalpel_version": diag.scalpel_version,
            "workload": diag.workload_label,
            "n_confirmed": len(diag.confirmed),
            "n_dba_violations": len(diag.dba_prior_violations),
            "data_path": str(results_path),
        },
    )


register_figure(
    FigureSpec(
        fig_id="tier_diagnostics",
        paper_label="fig:scalpel_diagnostics",
        title="SCALPEL tier diagnostics",
        section="analysis",
        category="importance",
        size_hint="double",
        generator=generate_tier_diagnostics,
        data_requirements=["importance_results_json"],
        description=(
            "BORUTA hits, BH-adjusted p-values, stability probabilities, "
            "and the Lorenz cumulative-mass curve produced by SCALPEL."
        ),
    )
)
