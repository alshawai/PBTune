"""
Semantic color palettes, hatch patterns, and styling for visualization.

Palette tuned to match the traditional academic bar-chart style from the
CDBTune+ reference paper (muted/pastel fills with dense hatch overlays)
and the red/yellow/blue line-plot palette from the iterative convergence
figures.
"""

from typing import Any

# ── Method colors (bar charts & general use) ─────────────────────────
# Muted/pastel palette matching CDBTune+ grouped bar chart reference.
METHOD_COLORS: dict[str, str] = {
    "default":    "#C4A882",   # tan/khaki   (MySQL Default)
    "cdbtune":    "#E8A0BF",   # pink/salmon (CDBTune+)
    "ottertune":  "#8B6914",   # dark gold   (OtterTune)
    "bestconfig": "#7CB77F",   # sage green  (BestConfig)
    "dba":        "#C9A0DC",   # lavender    (DBA)
    "pbtune":     "#2563EB",   # blue-600    (our method — prominent)
    "bo_smac":    "#F59E0B",   # amber-500
    "llamatune":  "#8B5CF6",   # violet-500
    "qtune":      "#EC4899",   # pink-500
    "gptuner":    "#14B8A6",   # teal-500
}

# ── Metric colors (for per-metric styling) ───────────────────────────
METRIC_COLORS: dict[str, str] = {
    "latency": "#EF4444",    # Red
    "throughput": "#2563EB",  # Blue
    "memory": "#F59E0B",     # Amber
    "score": "#10B981",      # Green
    "error_rate": "#6B7280", # Gray
}

# ── Line-plot colors (Fig 7 iterative convergence style) ─────────────
# Vivid red/yellow/blue tricolor for workload-variant line plots.
LINE_PLOT_COLORS: dict[str, str] = {
    "ro":     "#E03131",   # red
    "rw":     "#F59F00",   # yellow/amber
    "wo":     "#1C7ED6",   # blue
    "ro_per": "#E03131",   # same hue, distinguished by marker/dash
    "rw_per": "#F59F00",
    "wo_per": "#1C7ED6",
}

# ── Distinct markers for accessibility ───────────────────────────────
# Prominent filled markers matching CDBTune+ line-plot reference
# (squares, diamonds, triangles, circles).
METHOD_MARKERS: dict[str, str] = {
    "pbtune":     "s",    # filled square
    "bo_smac":    "D",    # filled diamond
    "ottertune":  "o",    # filled circle
    "cdbtune":    "^",    # filled triangle-up
    "llamatune":  "v",    # filled triangle-down
    "qtune":      "P",    # plus (filled)
    "gptuner":    "X",    # X (filled)
    "default":    "d",    # thin diamond
    "bestconfig": "p",    # pentagon
    "dba":        "h",    # hexagon
}

# ── Distinct linestyles ──────────────────────────────────────────────
METHOD_LINESTYLES: dict[str, str] = {
    "pbtune":     "-",    # Solid
    "bo_smac":    "--",   # Dashed
    "ottertune":  "-",    # Solid
    "cdbtune":    "-",    # Solid
    "llamatune":  "-.",   # Dash-dot
    "qtune":      "--",   # Dashed
    "gptuner":    "-.",   # Dash-dot
    "default":    ":",    # Dotted
    "bestconfig": "--",   # Dashed
    "dba":        "-.",   # Dash-dot
}

# ── Hatch patterns for grouped bar charts ────────────────────────────
# Dense hatch overlays matching CDBTune+ bar chart reference
# (dots, diagonals, verticals, cross-hatch, etc.)
METHOD_HATCHES: dict[str, str] = {
    "default":    "...",       # dotted          (MySQL Default)
    "cdbtune":    "///",       # diagonal lines  (CDBTune+)
    "ottertune":  "",          # solid fill      (OtterTune)
    "bestconfig": "\\\\\\",   # back-diagonal   (BestConfig)
    "dba":        "xxx",       # cross-hatch     (DBA)
    "pbtune":     "///",       # diagonal lines
    "bo_smac":    "|||",       # vertical lines
    "llamatune":  "+++",       # plus pattern
    "qtune":      "ooo",       # circle pattern
    "gptuner":    "***",       # star pattern
}


def get_method_style(method_name: str) -> dict[str, Any]:
    """
    Get a complete style bundle (color, marker, linestyle, hatch) for a
    given method.  Useful for unpacking directly into matplotlib plot()
    or bar() calls.
    """
    normalized = method_name.lower().strip()
    
    # Handle aliases from comparison json
    if normalized == "bo":
        normalized = "bo_smac"
    elif normalized == "pbt":
        normalized = "pbtune"
        
    if normalized not in METHOD_COLORS:
        # Fallback to a default style if unknown
        return {
            "color": "#9CA3AF",  # Gray-400
            "marker": "o",
            "linestyle": "-",
            "hatch": "",
        }

    return {
        "color": METHOD_COLORS[normalized],
        "marker": METHOD_MARKERS.get(normalized, "o"),
        "linestyle": METHOD_LINESTYLES.get(normalized, "-"),
        "hatch": METHOD_HATCHES.get(normalized, ""),
    }
