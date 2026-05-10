"""
Semantic color palettes and styling for visualization.
"""

from typing import Any

# Colorblind-friendly semantic palette for methods (comparisons)
METHOD_COLORS: dict[str, str] = {
    "pbtune": "#2563EB",  # Blue-600
    "bo_smac": "#F59E0B",  # Amber-500
    "ottertune": "#10B981",  # Emerald-500
    "cdbtune": "#EF4444",  # Red-500
    "llamatune": "#8B5CF6",  # Violet-500
    "qtune": "#EC4899",  # Pink-500
    "gptuner": "#14B8A6",  # Teal-500
    "default": "#6B7280",  # Gray-500
}

# Colorblind-friendly semantic palette for metrics
METRIC_COLORS: dict[str, str] = {
    "latency": "#EF4444",  # Red
    "throughput": "#2563EB",  # Blue
    "memory": "#F59E0B",  # Amber
    "score": "#10B981",  # Green
    "error_rate": "#6B7280",  # Gray
}

# Distinct markers for accessibility (never rely on color alone)
METHOD_MARKERS: dict[str, str] = {
    "pbtune": "o",  # Circle
    "bo_smac": "s",  # Square
    "ottertune": "^",  # Triangle up
    "cdbtune": "v",  # Triangle down
    "llamatune": "D",  # Diamond
    "qtune": "P",  # Plus (filled)
    "gptuner": "X",  # X (filled)
    "default": "",  # Usually a baseline line, no markers
}

# Distinct linestyles
METHOD_LINESTYLES: dict[str, str] = {
    "pbtune": "-",  # Solid
    "bo_smac": "--",  # Dashed
    "ottertune": "-.",  # Dash-dot
    "cdbtune": ":",  # Dotted
    "llamatune": "-",
    "qtune": "--",
    "gptuner": "-.",
    "default": "--",  # Often a dashed horizontal baseline
}


def get_method_style(method_name: str) -> dict[str, Any]:
    """
    Get a complete style bundle (color, marker, linestyle) for a given method.
    Useful for unpacking directly into matplotlib plot() calls.
    """
    normalized = method_name.lower().strip()
    if normalized not in METHOD_COLORS:
        # Fallback to a default style if unknown
        return {
            "color": "#9CA3AF",  # Gray-400
            "marker": "o",
            "linestyle": "-",
        }

    return {
        "color": METHOD_COLORS[normalized],
        "marker": METHOD_MARKERS[normalized],
        "linestyle": METHOD_LINESTYLES[normalized],
    }
