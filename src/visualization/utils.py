"""
Reusable plot utilities for formatting, labeling, and annotations.
"""

from typing import Sequence
from matplotlib.axes import Axes
from matplotlib.ticker import MaxNLocator


def despine(
    ax: Axes,
    top: bool = True,
    right: bool = True,
    left: bool = False,
    bottom: bool = False,
) -> None:
    """
    Remove the top and right spines from plot(s).
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
    axes: Sequence[Axes], start_char: str = "a", x: float = -0.1, y: float = 1.05
) -> None:
    """
    Add (a), (b), (c) style labels to a sequence of axes.
    Useful for multi-panel figures.
    """
    start_ord = ord(start_char)
    for i, ax in enumerate(axes):
        label = f"({chr(start_ord + i)})"
        ax.text(
            x,
            y,
            label,
            transform=ax.transAxes,
            fontsize=11,
            fontweight="bold",
            va="bottom",
            ha="right",
        )


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


def auto_grid(ax: Axes, axis: str = "both", which: str = "major") -> None:
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
