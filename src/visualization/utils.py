"""
Reusable plot utilities for formatting, labeling, and annotations.
"""

from typing import Literal, Sequence
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator


def despine(
    ax: Axes,
    top: bool = False,
    right: bool = False,
    left: bool = False,
    bottom: bool = False,
) -> None:
    """
    Selectively remove spines from a plot.

    Defaults changed to False (no-op) to preserve the fully-boxed axes
    style used in the CDBTune+ reference paper.  Callers that explicitly
    need despining can still pass ``top=True, right=True``.
    """
    if top:
        ax.spines["top"].set_visible(False)
    if right:
        ax.spines["right"].set_visible(False)
    if left:
        ax.spines["left"].set_visible(False)
    if bottom:
        ax.spines["bottom"].set_visible(False)


def add_panel_labels(
    axes: Sequence[Axes],
    start_char: str = "a",
    x: float = 0.5,
    y: float = -0.18,
    location: str = "below",
    include_title: bool = True,
) -> None:
    """
    Add **(a)**, **(b)**, **(c)** style labels to a sequence of axes.

    Default placement is *below* each subplot (centered), matching the
    CDBTune+ bar-chart reference.  Pass ``location="above"`` with custom
    *x*/*y* to revert to the old top-left placement.
    """
    start_ord = ord(start_char)
    for i, ax in enumerate(axes):
        label = f"({chr(start_ord + i)})"
        
        if include_title:
            title = ax.get_title()
            if title:
                label = f"{label} {title}"
                ax.set_title("")
        if location == "above":
            ax.text(
                -0.1 if x == 0.5 else x,
                1.05 if y == -0.18 else y,
                label,
                transform=ax.transAxes,
                fontsize=11,
                fontweight="bold",
                va="bottom",
                ha="right",
            )
        else:
            ax.text(
                x,
                y,
                label,
                transform=ax.transAxes,
                fontsize=11,
                fontweight="bold",
                va="top",
                ha="center",
            )


def add_top_shared_legend(
    fig: Figure,
    axes,
    ncol: int = 0,
    **legend_kwargs,
) -> None:
    """
    Extract unique handles/labels from all *axes* and place a single
    shared legend above the figure area.

    Matches the CDBTune+ grouped-bar-chart style where a multi-column
    legend spans the full figure width above the subplot grid.

    Parameters
    ----------
    fig : Figure
        The parent figure.
    axes : array-like of Axes
        All subplot axes to collect legend entries from.
    ncol : int
        Number of legend columns.  ``0`` (default) auto-sets to the
        number of unique entries.
    **legend_kwargs
        Forwarded to ``fig.legend()``.
    """
    handles, labels = [], []
    seen: set[str] = set()
    flat_axes = axes.flat if hasattr(axes, "flat") else axes
    for ax in flat_axes:
        for h, lbl in zip(*ax.get_legend_handles_labels(), strict=False):
            if lbl not in seen:
                handles.append(h)
                labels.append(lbl)
                seen.add(lbl)
        # Remove per-axis legends so only the shared one remains
        per_legend = ax.get_legend()
        if per_legend is not None:
            per_legend.remove()

    if not handles:
        return

    if ncol <= 0:
        ncol = len(handles)

    defaults = dict(
        loc="upper center",
        bbox_to_anchor=(0.5, 1.0),
        ncol=ncol,
        frameon=True,
        edgecolor="0.8",
        fancybox=False,
        fontsize=plt.rcParams.get("legend.fontsize", 9),
    )
    defaults.update(legend_kwargs)
    fig.legend(handles, labels, **defaults)


def add_baseline_line(
    ax: Axes,
    y_value: float,
    label: str = "Default",
    color: str = "#6B7280",
    linestyle: str = "--",
) -> None:
    """
    Add a horizontal baseline to compare against.
    """
    ax.axhline(y=y_value, color=color, linestyle=linestyle, label=label, zorder=1)


def add_exploit_markers(
    ax: Axes, events: list[dict], color: str = "#EF4444", alpha: float = 0.5
) -> None:
    """
    Add vertical lines to indicate PBT exploit events.
    Assumes events is a list of dicts with a 'generation' key.
    """
    for event in events:
        gen = event.get("generation")
        if gen is not None:
            ax.axvline(x=gen, color=color, linestyle=":", alpha=alpha, zorder=0)


def format_improvement_label(
    pct_improvement: float, ci_lower: float, ci_upper: float
) -> str:
    """
    Format an improvement string with confidence intervals.
    Example: "↑ 23.4% [18.1, 28.7]"
    """
    arrow = "↑" if pct_improvement >= 0 else "↓"
    return f"{arrow} {abs(pct_improvement):.1f}% [{ci_lower:.1f}, {ci_upper:.1f}]"


def truncate_legend(ax: Axes, max_items: int = 5) -> None:
    """
    Truncate a legend if it has too many items, replacing the rest with '... and N more'.
    """
    handles, labels = ax.get_legend_handles_labels()
    if len(labels) <= max_items:
        ax.legend(handles, labels)
        return

    # Keep first max_items - 1, then add a dummy entry for the rest
    keep_handles = handles[: max_items - 1]
    keep_labels = labels[: max_items - 1]

    remaining_count = len(labels) - (max_items - 1)

    import matplotlib.lines as mlines

    dummy_handle = mlines.Line2D(
        [], [], color="none", label=f"... and {remaining_count} more"
    )

    keep_handles.append(dummy_handle)
    keep_labels.append(f"... and {remaining_count} more")

    ax.legend(keep_handles, keep_labels)


def auto_grid(
    ax: Axes,
    axis: Literal["both", "x", "y"] = "both",
    which: Literal["major", "minor", "both"] = "major",
) -> None:
    """
    Enable standard light grid.
    """
    ax.grid(True, axis=axis, which=which)
    ax.set_axisbelow(True)


def set_integer_ticks(ax: Axes, axis: str = "x") -> None:
    """
    Force an axis to only use integer tick labels (useful for generation/iteration counts).
    """
    if axis in ("x", "both"):
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    if axis in ("y", "both"):
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
