"""
Logging Helper Functions
========================

This module contains helpers used by formatters and other logging utilities,
kept separate to  provide a clear, testable location for utility functions
like ANSI -> HTML conversion and standardized log section formatting.
"""

import re
import html as _html
import logging
import shutil
from typing import Optional, Any, Mapping, Sequence
from numbers import Integral, Real

from src.utils.logger.banners import COLORS
from src.utils.logger.colors import ColorPalette
from src.utils.logger.context import get_color_context


LOGGER_LEVEL_WIDTH = 7
LOGGER_MODULE_WIDTH = 20

# Basic xterm 16-color palette (normal + bright)
BASIC_COLORS = {
    0: "#000000",
    1: "#800000",
    2: "#008000",
    3: "#808000",
    4: "#000080",
    5: "#800080",
    6: "#008080",
    7: "#c0c0c0",
}
BRIGHT_COLORS = {
    0: "#808080",
    1: "#ff0000",
    2: "#00ff00",
    3: "#ffff00",
    4: "#0000ff",
    5: "#ff00ff",
    6: "#00ffff",
    7: "#ffffff",
}


def ansi_to_html(text: str) -> str:
    """Convert basic ANSI sequences in `text` to HTML spans.

    - Supports 24-bit foreground colors of the form ``\x1b[38;2;R;G;Bm``.
    - Supports bold ``\x1b[1m``, italic ``\x1b[3m``, underline ``\x1b[4m``, and reset ``\x1b[0m``.
    - Supports clearing bold (22), italic (23), underline (24).

    The function escapes non-ANSI text to prevent HTML injection and
    wraps colored segments in ``<span style="...">``, emitting CSS for
    color, font-weight, font-style, and text-decoration.
    """
    if not text:
        return ""

    def _rgb_to_hex(r: int, g: int, b: int) -> str:
        return f"#{r:02x}{g:02x}{b:02x}"

    def _index256_to_hex(idx: int) -> str:
        """Convert 0-255 xterm color index to hex string.

        Follows the standard xterm 256-color palette mapping:
        - 0-15: system colors (mapped to BASIC/BRIGHT tables)
        - 16-231: 6x6x6 color cube
        - 232-255: grayscale ramp
        """
        if 0 <= idx <= 15:
            if idx <= 7:
                return BASIC_COLORS[idx]
            return BRIGHT_COLORS[idx - 8]
        if 16 <= idx <= 231:
            c = idx - 16
            r = c // 36
            g = (c % 36) // 6
            b = c % 6

            # each component is 0..5 mapped to 0..255 via 0,95,135,175,215,255
            def comp(v: int) -> int:
                return 55 + v * 40 if v > 0 else 0

            return _rgb_to_hex(comp(r), comp(g), comp(b))
        if 232 <= idx <= 255:
            # grayscale ramp 232..255 maps to 8..238
            level = 8 + (idx - 232) * 10
            return _rgb_to_hex(level, level, level)
        # fallback
        return "#000000"

    # Split the text into ANSI SGR sequences and plain text
    parts = re.split(r"(\x1b\[[0-9;]*m)", text)
    out: list[str] = []

    # Current active style (single span representing the combination)
    current_style: dict = {}
    span_open = False

    def _open_span(style: dict) -> None:
        nonlocal out, span_open
        if not style:
            return
        attrs = []
        if style.get("color"):
            attrs.append(f"color: {style['color']}")
        if style.get("background"):
            attrs.append(f"background-color: {style['background']}")
        if style.get("bold"):
            attrs.append("font-weight: bold")
        if style.get("italic"):
            attrs.append("font-style: italic")
        if style.get("underline"):
            attrs.append("text-decoration: underline")
        style_str = "; ".join(attrs)
        out.append(f'<span style="{style_str}">')
        span_open = True

    def _close_span() -> None:
        nonlocal out, span_open
        if span_open:
            out.append("</span>")
            span_open = False

    for part in parts:
        if not part:
            continue

        m = re.match(r"\x1b\[([0-9;]*)m", part)
        if m:
            params_str = m.group(1)
            params = (
                [int(p) for p in params_str.split(";") if p != ""]
                if params_str
                else [0]
            )

            # Work on a copy of current style and mutate
            new_style = dict(current_style)

            i = 0
            while i < len(params):
                p = params[i]
                # Reset
                if p == 0:
                    new_style.clear()
                elif p == 1:
                    new_style["bold"] = True
                elif p == 3:
                    new_style["italic"] = True
                elif p == 4:
                    new_style["underline"] = True
                elif p == 22:
                    new_style.pop("bold", None)
                elif p == 23:
                    new_style.pop("italic", None)
                elif p == 24:
                    new_style.pop("underline", None)
                elif 30 <= p <= 37:
                    new_style["color"] = BASIC_COLORS[p - 30]
                elif 40 <= p <= 47:
                    new_style["background"] = BASIC_COLORS[p - 40]
                elif 90 <= p <= 97:
                    new_style["color"] = BRIGHT_COLORS[p - 90]
                elif 100 <= p <= 107:
                    new_style["background"] = BRIGHT_COLORS[p - 100]
                elif p == 39:
                    new_style.pop("color", None)
                elif p == 49:
                    new_style.pop("background", None)
                elif p == 38:
                    # Extended foreground: either 5;n (256) or 2;r;g;b (truecolor)
                    if (
                        i + 1 < len(params)
                        and params[i + 1] == 2
                        and i + 4 < len(params)
                    ):
                        r, g, b = params[i + 2], params[i + 3], params[i + 4]
                        new_style["color"] = _rgb_to_hex(r, g, b)
                        i += 4
                    elif (
                        i + 1 < len(params)
                        and params[i + 1] == 5
                        and i + 2 < len(params)
                    ):
                        # 256-color index -> convert to exact hex using xterm palette
                        idx = params[i + 2]
                        new_style["color"] = _index256_to_hex(idx)
                        i += 2
                elif p == 48:
                    # Extended background
                    if (
                        i + 1 < len(params)
                        and params[i + 1] == 2
                        and i + 4 < len(params)
                    ):
                        r, g, b = params[i + 2], params[i + 3], params[i + 4]
                        new_style["background"] = _rgb_to_hex(r, g, b)
                        i += 4
                    elif (
                        i + 1 < len(params)
                        and params[i + 1] == 5
                        and i + 2 < len(params)
                    ):
                        idx = params[i + 2]
                        new_style["background"] = _index256_to_hex(idx)
                        i += 2
                # ignore unknown codes
                i += 1

            # If style changed, close previous span and open a new one if needed
            if new_style != current_style:
                _close_span()
                current_style = new_style
                _open_span(current_style)

            continue

        # Plain text
        out.append(_html.escape(part))

    # Close any open span at end
    _close_span()
    return "".join(out)


def normalize_logger_name(name: str, strip_src_prefix: bool = True) -> str:
    """Normalize a logger name for display.

    The logger is primarily used for code under `src/`, so the leading
    `src.` package prefix is removed by default.
    """
    normalized = name.strip()
    if strip_src_prefix and normalized.startswith("src."):
        return normalized.removeprefix("src.")
    return normalized


def format_logger_name(name: str, width: int = LOGGER_MODULE_WIDTH) -> str:
    """Return a left-padded logger label for aligned log output."""
    normalized = normalize_logger_name(name)
    return normalized.ljust(width)


def format_logger_level(level: str, width: int = LOGGER_LEVEL_WIDTH) -> str:
    """Return a centered logger level label for aligned log output."""
    return level.strip().upper().center(width)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _format_value(value: Any) -> str:
    """Format knob values consistently for logging output."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, Integral):
        return str(int(value))
    if isinstance(value, Real):
        return f"{float(value):.6f}".rstrip("0").rstrip(".")
    return str(value)


def _format_metric_value(metric_name: str, value: Any) -> str:
    """Format worker metric values for the ASCII table renderer."""
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return str(value)
    if metric_name == "score" and isinstance(value, Real):
        return f"{float(value):.3f}"
    if isinstance(value, Integral):
        return str(int(value))
    if isinstance(value, Real):
        return f"{float(value):.6f}".rstrip("0").rstrip(".")
    return str(value)


DEFAULT_WORKER_METRIC_LABELS: dict[str, str] = {
    "score": "Score",
    "latency_p50": "Latency P50",
    "latency_p95": "Latency P95",
    "latency_p99": "Latency P99",
    "latency_variance": "Latency Variance",
    "tail_amplification": "Tail Amplification",
    "throughput": "Throughput",
    "throughput_variance": "Throughput Variance",
    "total_queries": "Total Queries",
    "total_time": "Total Time",
    "memory_utilization": "Memory Utilization",
    "memory_pressure": "Memory Pressure",
    "io_read_mb": "IO Read MB",
    "io_write_mb": "IO Write MB",
    "cache_hit_ratio": "Cache Hit Ratio",
    "buffer_miss_rate": "Buffer Miss Rate",
    "scan_efficiency": "Scan Efficiency",
    "rows_examined": "Rows Examined",
    "rows_returned": "Rows Returned",
    "error_rate": "Error Rate",
}


def _visible_length(text: str) -> int:
    """Measure the visible width of a string after stripping ANSI codes."""
    return len(strip_ansi(text))


def _pad_cell(text: str, width: int, alignment: str) -> str:
    """Pad a table cell while accounting for ANSI escape sequences."""
    visible_width = _visible_length(text)
    padding = max(width - visible_width, 0)

    if alignment == "right":
        return " " * padding + text
    if alignment == "center":
        left_padding = padding // 2
        right_padding = padding - left_padding
        return " " * left_padding + text + " " * right_padding
    return text + " " * padding


def _coerce_metric_mapping(metric_source: Any) -> dict[str, Any]:
    """Coerce a worker metric payload into a dictionary."""
    if isinstance(metric_source, dict):
        return dict(metric_source)

    if hasattr(metric_source, "to_dict") and callable(metric_source.to_dict):
        mapping = metric_source.to_dict()
        if isinstance(mapping, dict):
            return dict(mapping)

    raise TypeError(
        "Worker metric payloads must be dictionaries or expose a to_dict() method"
    )


def _collect_metric_order(metric_payloads: Sequence[Any]) -> list[str]:
    """Preserve metric ordering based on first appearance across payloads."""
    ordered: list[str] = []
    seen: set[str] = set()
    for payload in metric_payloads:
        mapping = _coerce_metric_mapping(payload)
        for key in mapping:
            if key not in seen:
                seen.add(key)
                ordered.append(key)
    return ordered


def _partition_metric_orders(
    metric_order: Sequence[str],
    primary_keys: Sequence[str],
    *,
    secondary_start_key: str,
) -> tuple[list[str], list[str]]:
    """Partition metrics into primary (scoring) and secondary (operational) rows.

    Primary rows are explicitly keyed by ``primary_keys`` so display does not
    depend on payload insertion order. Secondary rows begin at
    ``secondary_start_key`` and include later non-primary metrics.
    """
    primary_set = set(primary_keys)
    present = list(metric_order)
    primary_order = [key for key in primary_keys if key in present]

    if secondary_start_key in present:
        start = present.index(secondary_start_key)
        tail = present[start:]
    else:
        tail = []

    secondary_order = [key for key in tail if key not in primary_set]
    return primary_order, secondary_order


def _normalize_metric_label(
    metric_name: str, custom_labels: Mapping[str, str] | None = None
) -> str:
    """Return a human-friendly label for a metric key."""
    if custom_labels and metric_name in custom_labels:
        return custom_labels[metric_name]
    if metric_name in DEFAULT_WORKER_METRIC_LABELS:
        return DEFAULT_WORKER_METRIC_LABELS[metric_name]
    return metric_name.replace("_", " ").strip().title()


def _normalize_feature_label(feature_name: str) -> str:
    """Return a human-friendly label for a workload feature key."""
    return feature_name.replace("_", " ").strip().title()


FEATURE_ORDER = [
    "read_ratio",
    "write_ratio",
    "concurrency_pressure",
    "tail_latency_sensitivity",
    "aggregation_intensity",
    "join_intensity",
    "sort_intensity",
    "olap_complexity",
    "working_set_millions",
    "query_mix_entropy",
]

METRIC_WEIGHT_ORDER = [
    "latency_p95",
    "latency_p99",
    "latency_variance",
    "tail_amplification",
    "throughput",
    "throughput_variance",
    "scan_efficiency",
    "memory_pressure",
    "buffer_miss_rate",
    "error_rate",
]


def _label_palette(colors: Any) -> tuple[str, ...]:
    return (
        colors.cyan,
        colors.teal,
        colors.sky_blue,
        colors.yellow,
        colors.orange,
        colors.violet,
        colors.magenta,
        colors.lime,
        colors.blue,
        colors.purple,
        colors.red,
        colors.gray,
    )


def _ordered_keys(order: Sequence[str], mapping: Mapping[str, Any]) -> list[str]:
    ordered = [key for key in order if key in mapping]
    ordered.extend(sorted(set(mapping) - set(ordered)))
    return ordered


def _insert_table_break(table: str, *, break_after: int) -> str:
    """Insert a border line after a fixed number of metric rows."""
    if break_after <= 0:
        return table

    sections = table.split("\n\n")
    updated_sections: list[str] = []
    for section in sections:
        lines = section.splitlines()
        if len(lines) < 6:
            updated_sections.append(section)
            continue

        border_line = lines[1]
        insert_at = 4 + break_after
        if insert_at < len(lines) - 1:
            lines.insert(insert_at, border_line)

        updated_sections.append("\n".join(lines))

    return "\n\n".join(updated_sections)


def _styled_label(text: str, color: str, colors: Any, *, bold: bool = True) -> str:
    if not text:
        return ""
    if bold:
        return f"{colors.bold}{color}{text}{colors.reset}"
    return f"{color}{text}{colors.reset}"


def _styled_number(
    value: float,
    *,
    color: str,
    colors: Any,
    bold: bool = False,
    signed: bool = False,
    precision: int = 4,
) -> str:
    sign = "+" if signed else ""
    formatted = f"{value:{sign}.{precision}f}"
    if bold:
        return f"{colors.bold}{color}{formatted}{colors.reset}"
    return f"{color}{formatted}{colors.reset}"


def _render_ascii_table(
    title: str,
    metric_rows: Sequence[str],
    worker_headers: Sequence[str],
    cell_rows: Sequence[Sequence[str]],
) -> str:
    """Render a single ASCII table section for worker metrics."""
    row_label_width = max(
        _visible_length("Metric"),
        max((_visible_length(label) for label in metric_rows), default=0),
    )

    worker_widths: list[int] = []
    for worker_index, worker_header in enumerate(worker_headers):
        cell_width = _visible_length(worker_header)
        for row in cell_rows:
            if worker_index < len(row):
                cell_width = max(cell_width, _visible_length(row[worker_index]))
        worker_widths.append(cell_width)

    column_widths = [row_label_width, *worker_widths]

    def _border() -> str:
        return "+" + "+".join("-" * (width + 2) for width in column_widths) + "+"

    def _row(cells: Sequence[str], alignments: Sequence[str]) -> str:
        padded_cells = [
            f" {_pad_cell(str(cell), width, alignment)} "
            for cell, width, alignment in zip(
                cells, column_widths, alignments, strict=True
            )
        ]
        return "|" + "|".join(padded_cells) + "|"

    lines = [title, _border()]
    lines.append(
        _row(
            ["Metric", *worker_headers],
            ["left", *(["center"] * len(worker_headers))],
        )
    )
    lines.append(_border())

    for row_label, row_values in zip(metric_rows, cell_rows, strict=True):
        lines.append(
            _row(
                [row_label, *row_values],
                ["left", *(["right"] * len(worker_headers))],
            )
        )

    lines.append(_border())
    return "\n".join(lines)


def _render_ascii_table_with_sections(
    title: str,
    worker_headers: Sequence[str],
    metric_rows_primary: Sequence[str],
    cell_rows_primary: Sequence[Sequence[str]],
    metric_rows_secondary: Sequence[str],
    cell_rows_secondary: Sequence[Sequence[str]],
) -> str:
    """Render a single ASCII table with an optional section break."""
    all_metric_rows = list(metric_rows_primary) + list(metric_rows_secondary)
    all_cell_rows = list(cell_rows_primary) + list(cell_rows_secondary)

    row_label_width = max(
        _visible_length("Metric"),
        max((_visible_length(label) for label in all_metric_rows), default=0),
    )

    worker_widths: list[int] = []
    for worker_index, worker_header in enumerate(worker_headers):
        cell_width = _visible_length(worker_header)
        for row in all_cell_rows:
            if worker_index < len(row):
                cell_width = max(cell_width, _visible_length(row[worker_index]))
        worker_widths.append(cell_width)

    column_widths = [row_label_width, *worker_widths]

    def _border() -> str:
        return "+" + "+".join("-" * (width + 2) for width in column_widths) + "+"

    def _row(cells: Sequence[str], alignments: Sequence[str]) -> str:
        padded_cells = [
            f" {_pad_cell(str(cell), width, alignment)} "
            for cell, width, alignment in zip(
                cells, column_widths, alignments, strict=True
            )
        ]
        return "|" + "|".join(padded_cells) + "|"

    lines = [title, _border()]
    lines.append(
        _row(
            ["Metric", *worker_headers],
            ["left", *("center" for _ in worker_headers)],
        )
    )
    lines.append(_border())

    for row_label, row_values in zip(
        metric_rows_primary, cell_rows_primary, strict=True
    ):
        lines.append(
            _row(
                [row_label, *row_values],
                ["left", *("right" for _ in worker_headers)],
            )
        )

    if metric_rows_secondary:
        lines.append(_border())
        for row_label, row_values in zip(
            metric_rows_secondary, cell_rows_secondary, strict=True
        ):
            lines.append(
                _row(
                    [row_label, *row_values],
                    ["left", *("right" for _ in worker_headers)],
                )
            )

    lines.append(_border())
    return "\n".join(lines)


def _build_worker_headers(
    worker_labels: Sequence[str],
    *,
    start: int,
    end: int,
    colors: Any,
    include_best_worker: bool,
    best_worker_label: str,
) -> list[str]:
    chunk_labels = list(worker_labels[start:end])
    if include_best_worker:
        chunk_labels.append(best_worker_label)

    styled_worker_labels = [
        f"{colors.bold}{ColorPalette.get_worker_color(start + index)}{label}{colors.reset}"
        for index, label in enumerate(chunk_labels)
    ]

    if include_best_worker:
        styled_worker_labels[-1] = (
            f"{colors.bold}{colors.green}{best_worker_label}{colors.reset}"
        )

    return styled_worker_labels


def _build_worker_rows(
    ordered_metric_names: Sequence[str],
    metric_mappings: Sequence[dict[str, Any]],
    *,
    metric_labels: Optional[Mapping[str, str]],
    colors: Any,
    metric_label_palette: Sequence[str],
) -> tuple[list[str], list[list[str]]]:
    metric_rows: list[str] = []
    cell_rows: list[list[str]] = []

    for metric_index, metric_name in enumerate(ordered_metric_names):
        metric_color = metric_label_palette[metric_index % len(metric_label_palette)]
        metric_rows.append(
            f"{colors.bold}{metric_color}"
            f"{_normalize_metric_label(metric_name, metric_labels)}{colors.reset}"
        )
        row_values: list[str] = []
        for metric_mapping in metric_mappings:
            value = metric_mapping.get(metric_name)
            formatted_value = _format_metric_value(metric_name, value)
            row_values.append(f"{colors.green}{formatted_value}{colors.reset}")
        cell_rows.append(row_values)

    return metric_rows, cell_rows


def _append_best_worker_values(
    ordered_metric_names: Sequence[str],
    cell_rows: list[list[str]],
    *,
    best_worker_mapping: dict[str, Any],
    colors: Any,
) -> None:
    for metric_index, metric_name in enumerate(ordered_metric_names):
        value = best_worker_mapping.get(metric_name)
        formatted_value = _format_metric_value(metric_name, value)
        cell_rows[metric_index].append(
            f"{colors.bold}{colors.green}{formatted_value}{colors.reset}"
        )


def _render_simple_table(
    title: str,
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    alignments: Sequence[str],
    *,
    center_width: Optional[int] = None,
) -> str:
    """Render a simple ASCII table with explicit column headers."""
    column_count = len(headers)
    if column_count == 0:
        return title

    column_widths = [_visible_length(header) for header in headers]
    for row in rows:
        for index in range(column_count):
            if index < len(row):
                column_widths[index] = max(
                    column_widths[index], _visible_length(str(row[index]))
                )

    def _border() -> str:
        return "+" + "+".join("-" * (width + 2) for width in column_widths) + "+"

    def _row(cells: Sequence[str]) -> str:
        padded_cells = [
            f" {_pad_cell(str(cell), width, alignment)} "
            for cell, width, alignment in zip(
                cells, column_widths, alignments, strict=True
            )
        ]
        return "|" + "|".join(padded_cells) + "|"

    lines = [title, _border(), _row(headers), _border()]
    for row in rows:
        lines.append(_row(row))
    lines.append(_border())

    block = "\n".join(lines)
    return _center_text_block(block, width=center_width)


def format_feature_weight_table(
    features: Mapping[str, float],
    weights: Mapping[str, float],
    *,
    generation: int,
    center_width: Optional[int] = None,
) -> str:
    """Format a combined workload feature + weight table for generation 0."""
    colors = get_color_context()
    title = (
        f"\n{colors.bold}🔹 Workload Features & Metric Weights vectors 🔹{colors.reset}"
    )
    headers = [
        f"{colors.bold}Feature{colors.reset}",
        f"{colors.bold}Value{colors.reset}",
        f"{colors.bold}Metric{colors.reset}",
        f"{colors.bold}Weight{colors.reset}",
    ]

    label_palette = _label_palette(colors)
    feature_keys = _ordered_keys(FEATURE_ORDER, features)
    metric_keys = _ordered_keys(METRIC_WEIGHT_ORDER, weights)
    row_count = max(len(feature_keys), len(metric_keys))
    rows: list[list[str]] = []

    for index in range(row_count):
        feature = feature_keys[index] if index < len(feature_keys) else ""
        metric = metric_keys[index] if index < len(metric_keys) else ""

        feature_label = _styled_label(
            _normalize_feature_label(feature),
            label_palette[index % len(label_palette)],
            colors,
        )
        if feature:
            feature_value_raw = features[feature]
            if feature_value_raw < 0:
                feature_color = colors.red
            else:
                feature_color = colors.green
            feature_value = _styled_number(
                feature_value_raw,
                color=feature_color,
                colors=colors,
                bold=True,
            )
        else:
            feature_value = ""
        metric_label = _styled_label(
            _normalize_metric_label(metric),
            label_palette[index % len(label_palette)],
            colors,
        )
        weight_value = (
            _styled_number(
                weights[metric],
                color=colors.green,
                colors=colors,
                bold=True,
            )
            if metric
            else ""
        )

        rows.append([feature_label, feature_value, metric_label, weight_value])

    return _render_simple_table(
        title,
        headers,
        rows,
        ["left", "right", "left", "right"],
        center_width=center_width,
    )


def format_weight_snapshot_table(
    weights: Mapping[str, float],
    deltas: Mapping[str, float],
    *,
    generation: int,
    center_width: Optional[int] = None,
) -> str:
    """Format a colored weight snapshot table with deltas."""
    colors = get_color_context()
    title = f"\n{colors.bold}🔹 Metric Weights updated 🔹{colors.reset}"
    headers = [
        f"{colors.bold}Metric{colors.reset}",
        f"{colors.bold}Weight{colors.reset}",
        f"{colors.bold}Delta{colors.reset}",
    ]

    label_palette = _label_palette(colors)
    metric_keys = _ordered_keys(METRIC_WEIGHT_ORDER, weights)

    rows: list[list[str]] = []
    for index, metric in enumerate(metric_keys):
        weight_value = _styled_number(
            weights[metric],
            color="",
            colors=colors,
            bold=True,
        )
        delta_value = deltas.get(metric, 0.0)
        if abs(delta_value) <= 1e-8:
            delta_color = colors.gray
        elif delta_value > 0:
            delta_color = colors.lime
        else:
            delta_color = colors.red
        delta_text = _styled_number(
            delta_value,
            color=delta_color,
            colors=colors,
            signed=True,
        )

        rows.append(
            [
                _styled_label(
                    _normalize_metric_label(metric),
                    label_palette[index % len(label_palette)],
                    colors,
                ),
                weight_value,
                delta_text,
            ]
        )

    return _render_simple_table(
        title,
        headers,
        rows,
        ["left", "right", "right"],
        center_width=center_width,
    )


def log_feature_weight_table(
    logger: logging.Logger,
    features: Mapping[str, float],
    weights: Mapping[str, float],
    *,
    generation: int,
    center_width: Optional[int] = None,
) -> None:
    """Log a combined feature + weight table."""
    table = format_feature_weight_table(
        features,
        weights,
        generation=generation,
        center_width=center_width,
    )
    logger.info("%s", table)


def log_weight_snapshot_table(
    logger: logging.Logger,
    weights: Mapping[str, float],
    deltas: Mapping[str, float],
    *,
    generation: int,
    center_width: Optional[int] = None,
) -> None:
    """Log a weight snapshot table with deltas."""
    table = format_weight_snapshot_table(
        weights,
        deltas,
        generation=generation,
        center_width=center_width,
    )
    logger.info("%s", table)


def _center_text_block(text: str, width: Optional[int] = None) -> str:
    """Center a multiline ASCII block within the requested width."""
    lines = text.splitlines()
    if not lines:
        return text

    block_width = max((_visible_length(line) for line in lines), default=0)
    target_width = width or shutil.get_terminal_size(fallback=(120, 20)).columns
    target_width = max(target_width, block_width)

    centered_lines = []
    for line in lines:
        if not line:
            centered_lines.append("")
            continue
        padding = max(target_width - _visible_length(line), 0)
        centered_lines.append(" " * (padding // 2) + line)
    return "\n".join(centered_lines)


def log_section_header(
    logger: logging.Logger,
    fmt: str,
    *fmt_args: Any,
    top_separator: bool = True,
    bottom_separator: bool = True,
    level: str = "info",
    width: Optional[int] = None,
) -> None:
    """Log a formatted section header with optional separators.

    Parameters
    ----------
    logger: logging.Logger
        Logger to emit to
    fmt: str
        A format string for the title (ANSI escapes allowed via COLORS)
    *fmt_args: Any
        Arguments for the format string
    top_separator, bottom_separator: bool
        Whether to print separator lines above/below the title
    level: str
        Logging level name to use (e.g., 'info' or 'debug')
    width: Optional[int]
        Visible width for the separator; if None will be derived
    """
    formatted_title = fmt % fmt_args if fmt_args else fmt
    # Compute visible length from ANSI-stripped title when width not provided
    visible_len = width if width is not None else _visible_length(formatted_title)
    # Ensure at least a minimal separator when title is empty
    if visible_len <= 0:
        visible_len = 1
    sep = "=" * visible_len

    log_fn = getattr(logger, level, logger.info)
    if top_separator:
        log_fn("%s%s%s", COLORS.bold, sep, COLORS.reset)
    if formatted_title:
        # Emit the title directly (may include ANSI escapes)
        log_fn(formatted_title)
    if bottom_separator:
        log_fn("%s%s%s", COLORS.bold, sep, COLORS.reset)


def format_worker_metrics_table(
    worker_metrics: Sequence[Any],
    *,
    worker_labels: Optional[Sequence[str]] = None,
    metric_order: Optional[Sequence[str]] = None,
    metric_labels: Optional[Mapping[str, str]] = None,
    best_worker_metric: Optional[Any] = None,
    best_worker_label: str = "Best Worker",
    title: str = "\nWorker Metrics",
    split_threshold: int = 4,
    center_width: Optional[int] = None,
) -> str:
    """Format a metric-by-worker matrix as one or two ASCII tables.

    When the worker count exceeds ``split_threshold``, the display is split
    into as many vertically stacked tables as needed to keep the output
    readable.
    """
    if not worker_metrics:
        return f"{title}\n(no workers)"

    if worker_labels is None:
        worker_labels = [f"Worker-{index}" for index in range(len(worker_metrics))]

    if len(worker_labels) != len(worker_metrics):
        raise ValueError("worker_labels must match the number of worker metrics")

    if split_threshold < 1:
        raise ValueError("split_threshold must be at least 1")

    coerced_metrics = [_coerce_metric_mapping(metric) for metric in worker_metrics]
    best_worker_mapping = (
        _coerce_metric_mapping(best_worker_metric)
        if best_worker_metric is not None
        else None
    )

    if metric_order is None:
        ordered_metric_names: list[str] = []
        seen_metric_names: set[str] = set()
        for metric_mapping in coerced_metrics:
            for metric_name in metric_mapping:
                if metric_name not in seen_metric_names:
                    seen_metric_names.add(metric_name)
                    ordered_metric_names.append(metric_name)
    else:
        ordered_metric_names = list(metric_order)

    # Ensure Score appears first if present
    if "score" in ordered_metric_names:
        ordered_metric_names = ["score"] + [
            k for k in ordered_metric_names if k != "score"
        ]

    if not ordered_metric_names:
        return f"{title}\n(no metrics)"

    colors = get_color_context()
    metric_label_palette = _label_palette(colors)

    worker_chunks: list[tuple[int, int]] = [
        (start, min(start + split_threshold, len(worker_labels)))
        for start in range(0, len(worker_labels), split_threshold)
    ]
    if not worker_chunks:
        worker_chunks = [(0, len(worker_labels))]

    sections: list[str] = []
    for chunk_index, (start, end) in enumerate(worker_chunks):
        chunk_metric_rows, chunk_cell_rows = _build_worker_rows(
            ordered_metric_names,
            coerced_metrics[start:end],
            metric_labels=metric_labels,
            colors=colors,
            metric_label_palette=metric_label_palette,
        )

        section_title = title
        if len(worker_chunks) > 1:
            section_title = f"{title} (part {chunk_index + 1}/{len(worker_chunks)})"

        include_best_worker = (
            best_worker_mapping is not None and chunk_index == len(worker_chunks) - 1
        )
        styled_worker_labels = _build_worker_headers(
            worker_labels,
            start=start,
            end=end,
            colors=colors,
            include_best_worker=include_best_worker,
            best_worker_label=best_worker_label,
        )
        if include_best_worker and best_worker_mapping is not None:
            _append_best_worker_values(
                ordered_metric_names,
                chunk_cell_rows,
                best_worker_mapping=best_worker_mapping,
                colors=colors,
            )

        sections.append(
            _center_text_block(
                _render_ascii_table(
                    section_title,
                    chunk_metric_rows,
                    styled_worker_labels,
                    chunk_cell_rows,
                ),
                width=center_width,
            )
        )

    return "\n\n".join(sections)


def log_worker_metrics_table(
    logger: logging.Logger,
    worker_metrics: Sequence[Any],
    *,
    worker_labels: Optional[Sequence[str]] = None,
    metric_order: Optional[Sequence[str]] = None,
    metric_labels: Optional[Mapping[str, str]] = None,
    best_worker_metric: Optional[Any] = None,
    best_worker_label: str = "Best Worker",
    title: str = "\nWorker Metrics",
    split_threshold: int = 4,
    center_width: Optional[int] = None,
) -> None:
    """Log a formatted worker metrics table at INFO level."""
    root_level = logging.getLogger().getEffectiveLevel()
    show_debug_metrics = root_level <= logging.DEBUG

    # Ensure Score is the first metric in the scoring order
    primary_scoring_order = ["score"] + [k for k in METRIC_WEIGHT_ORDER if k != "score"]
    scoring_order = list(primary_scoring_order)
    # Always show these operational metrics at INFO level for visibility
    always_show = [
        "total_queries",
        "total_time",
        "rows_examined",
        "rows_returned",
        "io_read_mb",
        "io_write_mb",
        "cache_hit_ratio",
    ]
    for key in always_show:
        if key not in scoring_order:
            scoring_order.append(key)
    if not worker_metrics:
        logger.info("%s", f"{title}\n(no workers)")
        return

    if worker_labels is None:
        worker_labels = [f"Worker-{index}" for index in range(len(worker_metrics))]

    if len(worker_labels) != len(worker_metrics):
        raise ValueError("worker_labels must match the number of worker metrics")

    if split_threshold < 1:
        raise ValueError("split_threshold must be at least 1")

    coerced_metrics = [_coerce_metric_mapping(metric) for metric in worker_metrics]
    best_worker_mapping = (
        _coerce_metric_mapping(best_worker_metric)
        if best_worker_metric is not None
        else None
    )

    combined_order = _collect_metric_order(worker_metrics)
    primary_order, secondary_order = _partition_metric_orders(
        combined_order,
        primary_scoring_order,
        secondary_start_key="total_queries",
    )

    extra_order = [
        key for key in _collect_metric_order(worker_metrics) if key not in scoring_order
    ]
    if not show_debug_metrics or not extra_order:
        table = format_worker_metrics_table(
            worker_metrics,
            worker_labels=worker_labels,
            metric_order=primary_order,
            metric_labels=metric_labels,
            best_worker_metric=best_worker_metric,
            best_worker_label=best_worker_label,
            title=title,
            split_threshold=split_threshold,
            center_width=center_width,
        )
        logger.info("%s", table)
        return

    colors = get_color_context()
    metric_label_palette = _label_palette(colors)
    if not secondary_order:
        table = format_worker_metrics_table(
            worker_metrics,
            worker_labels=worker_labels,
            metric_order=primary_order,
            metric_labels=metric_labels,
            best_worker_metric=best_worker_metric,
            best_worker_label=best_worker_label,
            title=title,
            split_threshold=split_threshold,
            center_width=center_width,
        )
        logger.info("%s", table)
        return

    worker_chunks: list[tuple[int, int]] = [
        (start, min(start + split_threshold, len(worker_labels)))
        for start in range(0, len(worker_labels), split_threshold)
    ]
    if not worker_chunks:
        worker_chunks = [(0, len(worker_labels))]

    sections: list[str] = []
    for chunk_index, (start, end) in enumerate(worker_chunks):
        include_best_worker = (
            best_worker_mapping is not None and chunk_index == len(worker_chunks) - 1
        )
        styled_worker_labels = _build_worker_headers(
            worker_labels,
            start=start,
            end=end,
            colors=colors,
            include_best_worker=include_best_worker,
            best_worker_label=best_worker_label,
        )

        scoring_metric_rows, scoring_cell_rows = _build_worker_rows(
            primary_order,
            coerced_metrics[start:end],
            metric_labels=metric_labels,
            colors=colors,
            metric_label_palette=metric_label_palette,
        )
        debug_metric_rows, debug_cell_rows = _build_worker_rows(
            secondary_order,
            coerced_metrics[start:end],
            metric_labels=metric_labels,
            colors=colors,
            metric_label_palette=metric_label_palette,
        )

        if include_best_worker and best_worker_mapping is not None:
            _append_best_worker_values(
                primary_order,
                scoring_cell_rows,
                best_worker_mapping=best_worker_mapping,
                colors=colors,
            )
            _append_best_worker_values(
                secondary_order,
                debug_cell_rows,
                best_worker_mapping=best_worker_mapping,
                colors=colors,
            )

        section_title = title
        if len(worker_chunks) > 1:
            section_title = f"{title} (part {chunk_index + 1}/{len(worker_chunks)})"

        table = _render_ascii_table_with_sections(
            section_title,
            styled_worker_labels,
            scoring_metric_rows,
            scoring_cell_rows,
            debug_metric_rows,
            debug_cell_rows,
        )
        sections.append(_center_text_block(table, width=center_width))

    logger.info("%s", "\n\n".join(sections))


def log_generation_summary(
    logger: logging.Logger,
    elapsed: float,
    restart_count: Optional[int] = None,
    *,
    generation: int,
    best_score: float,
    mean_score: Optional[float] = None,
    std_score: Optional[float] = None,
    exploited: Optional[int] = None,
    design_points: Optional[str] = None,
    status: Optional[str] = None,
    converged: Optional[bool] = None,
    round_label: str = "Generation",
) -> None:
    """
    Log a formatted generation/round summary (strategy-neutral).

    Every row past ``Best Score`` is optional and renders only when the caller
    supplies it, so the summary stays meaningful for every strategy:

    - ``mean_score`` / ``std_score`` / ``exploited`` — population statistics; a
      population strategy (PBT) passes all three, a stateless sweep (LHS) or a
      single-config iterator (BO) passes ``None`` and those rows are skipped.
    - ``restart_count`` — cumulative instance-restart count; ``None`` omits the
      row for strategies with no restart concept (a pure design sweep).
    - ``design_points`` — the design range this round covered (LHS), e.g.
      ``"6-7"``; omitted when ``None``.
    - ``status`` — the run's stopping status for optimization tuners (PBT/BO),
      e.g. ``"running"`` or ``"stopped - max generations reached"``. A pure
      random tuner (LHS) has no stopping criterion and passes ``None`` so the
      row is skipped. ``converged`` is the legacy boolean fallback (renders the
      old ``Converged: YES/NO`` line) kept for the incumbent PBT ``main.py``.

    Parameters
    ----------
    logger
        Logger instance.
    elapsed
        Seconds elapsed in the tuning loop so far.
    restart_count
        Cumulative instance-restart count, or ``None`` to omit the row.
    generation
        Zero-based round index.
    best_score
        Best score observed this round.
    round_label
        Strategy-appropriate noun for one loop pass (PBT "Generation", LHS
        "Batch", BO "Iteration"). Defaults to "Generation" for backward
        compatibility with the legacy PBT caller.
    """
    log_section_header(
        logger,
        "%s%s %s Summary:%s",
        COLORS.bold,
        round_label,
        generation,
        COLORS.reset,
        top_separator=False,
    )
    logger.info("  Best Score:  %s%.3f%%%s", COLORS.cyan, best_score, COLORS.reset)
    if mean_score is not None:
        logger.info("  Mean Score:  %s%.3f%%%s", COLORS.cyan, mean_score, COLORS.reset)
    if std_score is not None:
        logger.info("  Std Dev:     %s%.4f%s", COLORS.cyan, std_score, COLORS.reset)
    if exploited is not None:
        logger.info(
            "  Exploited:   %s%s%s workers", COLORS.cyan, exploited, COLORS.reset
        )
    if restart_count is not None:
        logger.info(
            "  Restarts:    %s%s%s total", COLORS.cyan, restart_count, COLORS.reset
        )
    logger.info("  Elapsed:     %s%.1fs%s", COLORS.cyan, elapsed, COLORS.reset)
    if design_points is not None:
        logger.info(
            "  Design Pts:  %s%s%s", COLORS.cyan, design_points, COLORS.reset
        )
    if status is not None:
        status_color = COLORS.orange if status.startswith("stopped") else COLORS.teal
        logger.info("  Status:      %s%s%s", status_color, status, COLORS.reset)
    elif converged is not None:
        logger.info(
            "  Converged:   %s%s%s",
            COLORS.orange,
            "YES" if converged else "NO",
            COLORS.reset,
        )
    logger.info("%s================================%s", COLORS.bold, COLORS.reset)


def log_final_summary(logger: logging.Logger, results: dict[str, Any]):
    """Print final summary of a tuning session (strategy-neutral).

    Reads the round count from the shared ``num_rounds`` header field, falling
    back to the legacy ``total_generations`` so both new (BaseTuner) and legacy
    (PBT ``main.py``) session dicts render. The title names the strategy from
    ``tuning_session.tuning_strategy`` rather than hard-coding "PBT".
    """
    session = results.get("tuning_session")
    best = results.get("best_configuration")

    strategy_label = "TUNING"
    if isinstance(session, dict):
        strategy_label = str(
            session.get("tuning_strategy", "tuning")
        ).upper()
    log_section_header(
        logger, "%s%s COMPLETE%s", COLORS.bold, strategy_label, COLORS.reset
    )

    if not isinstance(session, dict) or not isinstance(best, dict):
        logger.info("Final results: %s", results)
        return

    logger.info("%sSession Summary:%s", COLORS.green, COLORS.reset)
    rounds_value = session.get("num_rounds", session.get("total_generations", 0))
    logger.info(
        "  Total Rounds:       %s%d%s",
        COLORS.cyan,
        int(rounds_value if rounds_value is not None else 0),
        COLORS.reset,
    )
    logger.info(
        "  Total Time:         %s%.1fs (%.1f min)%s",
        COLORS.cyan,
        session["total_time_seconds"],
        session["total_time_seconds"] / 60,
        COLORS.reset,
    )
    logger.info(
        "  Knobs Tuned:        %s%d%s", COLORS.cyan, session["num_knobs"], COLORS.reset
    )
    logger.info(
        "  Workload Type:      %s%s%s",
        COLORS.cyan,
        session["workload_type"],
        COLORS.reset,
    )

    logger.info("%sBest Performance Metrics:%s", COLORS.green, COLORS.reset)
    logger.info(
        "  Score:              %s%.3f%%%s", COLORS.cyan, best["score"], COLORS.reset
    )
    raw_metrics = best.get("metrics")
    metrics = raw_metrics if isinstance(raw_metrics, dict) else {}

    latency_p95 = metrics.get("latency_p95")
    if latency_p95 is None:
        logger.info("  Latency95:          %s%s%s", COLORS.orange, "n/a", COLORS.reset)
    else:
        logger.info(
            "  Latency95:          %s%.3f ms%s",
            COLORS.cyan,
            latency_p95,
            COLORS.reset,
        )

    latency_p99 = metrics.get("latency_p99")
    if latency_p99 is None:
        logger.info("  Latency99:          %s%s%s", COLORS.orange, "n/a", COLORS.reset)
    else:
        logger.info(
            "  Latency99:          %s%.3f ms%s",
            COLORS.cyan,
            latency_p99,
            COLORS.reset,
        )

    throughput = metrics.get("throughput")
    throughput_unit = metrics.get("throughput_unit") or "TPS"
    if throughput is None:
        logger.info("  Throughput:         %s%s%s", COLORS.orange, "n/a", COLORS.reset)
    else:
        logger.info(
            "  Throughput:         %s%.3f %s%s",
            COLORS.cyan,
            throughput,
            throughput_unit,
            COLORS.reset,
        )

    memory_utilization = metrics.get("memory_utilization")
    if memory_utilization is None:
        logger.info("  Memory Utilization: %s%s%s", COLORS.orange, "n/a", COLORS.reset)
    else:
        logger.info(
            "  Memory Utilization: %s%.1f%%%s",
            COLORS.cyan,
            memory_utilization * 100.0,
            COLORS.reset,
        )

    logger.info("%sBest Knob Configurations:%s", COLORS.green, COLORS.reset)
    for knob_name, value in sorted(best["knobs"].items()):
        logger.info(
            "  %s%-35s = %s%s",
            COLORS.cyan,
            knob_name,
            _format_value(value),
            COLORS.reset,
        )
