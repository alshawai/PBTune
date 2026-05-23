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


def _normalize_metric_label(metric_name: str, custom_labels: Mapping[str, str] | None = None) -> str:
    """Return a human-friendly label for a metric key."""
    if custom_labels and metric_name in custom_labels:
        return custom_labels[metric_name]
    if metric_name in DEFAULT_WORKER_METRIC_LABELS:
        return DEFAULT_WORKER_METRIC_LABELS[metric_name]
    return metric_name.replace("_", " ").strip().title()


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
            for cell, width, alignment in zip(cells, column_widths, alignments)
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

    for row_label, row_values in zip(metric_rows, cell_rows):
        lines.append(
            _row(
                [row_label, *row_values],
                ["left", *(["right"] * len(worker_headers))],
            )
        )

    lines.append(_border())
    return "\n".join(lines)


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

    if not ordered_metric_names:
        return f"{title}\n(no metrics)"

    colors = get_color_context()
    metric_label_palette = (
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

    worker_chunks: list[tuple[int, int]] = [
        (start, min(start + split_threshold, len(worker_labels)))
        for start in range(0, len(worker_labels), split_threshold)
    ]
    if not worker_chunks:
        worker_chunks = [(0, len(worker_labels))]

    sections: list[str] = []
    for chunk_index, (start, end) in enumerate(worker_chunks):
        chunk_worker_labels = list(worker_labels[start:end])
        chunk_metric_rows: list[str] = []
        chunk_cell_rows: list[list[str]] = []

        for metric_index, metric_name in enumerate(ordered_metric_names):
            metric_color = metric_label_palette[metric_index % len(metric_label_palette)]
            chunk_metric_rows.append(
                f"{colors.bold}{metric_color}{_normalize_metric_label(metric_name, metric_labels)}{colors.reset}"
            )
            row_values: list[str] = []
            for metric_mapping in coerced_metrics[start:end]:
                value = metric_mapping.get(metric_name)
                formatted_value = _format_metric_value(metric_name, value)
                row_values.append(f"{colors.green}{formatted_value}{colors.reset}")
            chunk_cell_rows.append(row_values)

        section_title = title
        if len(worker_chunks) > 1:
            section_title = f"{title} (part {chunk_index + 1}/{len(worker_chunks)})"

        if best_worker_mapping is not None and chunk_index == len(worker_chunks) - 1:
            chunk_worker_labels.append(best_worker_label)

        styled_worker_labels = [
            f"{colors.bold}{ColorPalette.get_worker_color(start + index)}{label}{colors.reset}"
            for index, label in enumerate(chunk_worker_labels)
        ]

        if best_worker_mapping is not None and chunk_index == len(worker_chunks) - 1:
            styled_worker_labels[-1] = f"{colors.bold}{colors.green}{best_worker_label}{colors.reset}"

            for metric_index, metric_name in enumerate(ordered_metric_names):
                value = best_worker_mapping.get(metric_name)
                formatted_value = _format_metric_value(metric_name, value)
                chunk_cell_rows[metric_index].append(
                    f"{colors.bold}{colors.green}{formatted_value}{colors.reset}"
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
    table = format_worker_metrics_table(
        worker_metrics,
        worker_labels=worker_labels,
        metric_order=metric_order,
        metric_labels=metric_labels,
        best_worker_metric=best_worker_metric,
        best_worker_label=best_worker_label,
        title=title,
        split_threshold=split_threshold,
        center_width=center_width,
    )
    logger.info("%s", table)


def log_section_header(
    logger: logging.Logger,
    title: str = "",
    *fmt_args,
    width: Optional[int] = None,
    level: str = "info",
    top_separator: bool = True,
    **fmt_kwargs,
) -> None:
    """
    Log a formatted section header.

    Parameters
    ----------
    logger : logging.Logger
        Logger instance
    level : str
        Logging level
    title : str
        Section title
    width : int
        Width of header line

    Example
    -------
    >>> log_section_header(logger, "GENERATION 5")
    # Output:
    # ============
    # GENERATION 5
    # ============
    """
    try:
        if fmt_args:
            formatted_title = title % tuple(fmt_args)
        elif fmt_kwargs:
            formatted_title = title % fmt_kwargs
        else:
            formatted_title = title
    except Exception:
        # If formatting fails, fall back to the raw title to avoid crashing the logger
        formatted_title = title

    # Compute visible width from ANSI-stripped text when not provided
    visible_len = len(strip_ansi(formatted_title)) if width is None else width
    # If caller provided no title and no width, fall back to a sensible default
    if visible_len == 0:
        visible_len = 80

    sep = "=" * visible_len
    if level.lower() == "debug":
        logger.debug("%s%s%s", COLORS.bold, sep, COLORS.reset)
        if formatted_title:
            logger.debug(formatted_title)
        logger.debug("%s%s%s", COLORS.bold, sep, COLORS.reset)
    else:
        if top_separator:
            logger.info("%s%s%s", COLORS.bold, sep, COLORS.reset)
        if formatted_title:
            logger.info(formatted_title)
        logger.info("%s%s%s", COLORS.bold, sep, COLORS.reset)


def log_generation_summary(
    logger: logging.Logger,
    elapsed: float,
    restart_count: int,
    generation: int,
    best_score: float,
    mean_score: float,
    std_score: float,
    exploited: int,
    converged: bool
) -> None:
    """
    Log a formatted generation summary.

    Parameters
    ----------
    logger : logging.Logger
        Logger instance
    generation_result : GenerationResult
        Result of the generation to summarize
    """
    log_section_header(
        logger, "%sGeneration %s Summary:%s",
        COLORS.bold, generation, COLORS.reset, top_separator=False
    )
    logger.info("  Best Score:  %s%.3f%%%s", COLORS.cyan, best_score, COLORS.reset)
    logger.info("  Mean Score:  %s%.3f%%%s", COLORS.cyan, mean_score, COLORS.reset)
    logger.info("  Std Dev:     %s%.4f%s", COLORS.cyan, std_score, COLORS.reset)
    logger.info("  Exploited:   %s%s%s workers", COLORS.cyan, exploited, COLORS.reset)
    logger.info("  Restarts:    %s%s%s total", COLORS.cyan, restart_count, COLORS.reset)
    logger.info("  Elapsed:     %s%.1f%s", COLORS.cyan, elapsed, COLORS.reset)
    logger.info(
        "  Converged:   %s%s%s", COLORS.orange, 'YES' if converged else 'NO', COLORS.reset
    )
    logger.info("%s==========================%s", COLORS.bold, COLORS.reset)


def log_final_summary(logger: logging.Logger, results: dict[str, Any]):
    """Print final summary of tuning session"""
    log_section_header(logger, "%sPBT TUNING COMPLETE%s", COLORS.bold, COLORS.reset)
    session = results["tuning_session"]
    best = results["best_configuration"]

    logger.info("%sSession Summary:%s", COLORS.green, COLORS.reset)
    logger.info("  Total Generations:  %s%d%s", COLORS.cyan, session["total_generations"], COLORS.reset)
    logger.info(
        "  Total Time:         %s%.1fs (%.1f min)%s",
        COLORS.cyan,
        session["total_time_seconds"],
        session["total_time_seconds"] / 60,
        COLORS.reset
    )
    logger.info("  Knobs Tuned:        %s%d%s", COLORS.cyan, session["num_knobs"], COLORS.reset)
    logger.info(
        "  Workload Type:      %s%s%s", COLORS.cyan, session["workload_type"], COLORS.reset
    )

    logger.info("%sBest Performance Metrics:%s", COLORS.green, COLORS.reset)
    logger.info("  Score:              %s%.3f%%%s", COLORS.cyan, best["score"], COLORS.reset)
    logger.info(
        "  Latency95:          %s%.3f ms%s",
        COLORS.cyan, best["metrics"]["latency_p95"], COLORS.reset
    )
    logger.info(
        "  Latency99:          %s%.3f ms%s",
        COLORS.cyan, best["metrics"]["latency_p99"], COLORS.reset
    )
    logger.info("  Throughput:         %s%.3f %s%s",
                COLORS.cyan, best["metrics"]["throughput"],
                best["metrics"]["throughput_unit"], COLORS.reset
            )
    logger.info(
        "  Memory Utilization: %s%.1f%%%s", COLORS.cyan,
        best["metrics"]["memory_utilization"] * 100.0, COLORS.reset
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
