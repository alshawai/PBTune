"""Unit tests for logger color generation and formatter behavior."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import logging
import sys
import types


def _load_logger_colors_module():
    """Load the logger colors module without importing the full src package."""
    module_path = (
        Path(__file__).resolve().parents[3] / "src" / "utils" / "logger" / "colors.py"
    )
    spec = spec_from_file_location("logger_colors", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load logger colors module from {module_path}")

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_logger_helpers_module():
    """Load the logger helpers module without importing the full src package."""
    module_path = (
        Path(__file__).resolve().parents[3] / "src" / "utils" / "logger" / "helpers.py"
    )
    spec = spec_from_file_location("logger_helpers", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load logger helpers module from {module_path}")

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_logger_formatters_module():
    """Load formatter module with in-memory aliases for logger dependencies."""
    src_module = types.ModuleType("src")
    src_module.__path__ = []  # type: ignore[attr-defined]
    utils_module = types.ModuleType("src.utils")
    utils_module.__path__ = []  # type: ignore[attr-defined]
    logger_module = types.ModuleType("src.utils.logger")
    logger_module.__path__ = []  # type: ignore[attr-defined]

    previous_modules = {
        name: sys.modules.get(name)
        for name in (
            "src",
            "src.utils",
            "src.utils.logger",
            "src.utils.logger.colors",
            "src.utils.logger.helpers",
        )
    }

    try:
        sys.modules["src"] = src_module
        sys.modules["src.utils"] = utils_module
        sys.modules["src.utils.logger"] = logger_module
        sys.modules["src.utils.logger.colors"] = _logger_colors
        sys.modules["src.utils.logger.helpers"] = _logger_helpers

        module_path = (
            Path(__file__).resolve().parents[3]
            / "src"
            / "utils"
            / "logger"
            / "formatters.py"
        )
        spec = spec_from_file_location("logger_formatters", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(
                f"Unable to load logger formatters module from {module_path}"
            )

        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


_logger_colors = _load_logger_colors_module()
ColorPalette = _logger_colors.ColorPalette
ColorCode = _logger_colors.ColorCode
set_colors_enabled = _logger_colors.set_colors_enabled
_logger_helpers = _load_logger_helpers_module()
_logger_formatters = None


def test_worker_colors_are_deterministic_and_unique():
    """Worker colors should be stable and distinct across a small range."""
    ansi_colors = [
        ColorPalette.get_worker_color(worker_id, "ansi") for worker_id in range(12)
    ]
    html_colors = [
        ColorPalette.get_worker_color(worker_id, "html") for worker_id in range(12)
    ]

    assert ansi_colors == [
        ColorPalette.get_worker_color(worker_id, "ansi") for worker_id in range(12)
    ]
    assert html_colors == [
        ColorPalette.get_worker_color(worker_id, "html") for worker_id in range(12)
    ]
    assert len(set(ansi_colors)) == len(ansi_colors)
    assert len(set(html_colors)) == len(html_colors)


def test_module_colors_are_deterministic():
    """Main orchestrator modules should use the dedicated palette; others should be dynamic."""
    primary_modules = ["PBTune", "Evaluator", "Analyzer", "Visualizer"]

    primary_ansi_colors = [
        ColorPalette.get_module_color(module_name, "ansi")
        for module_name in primary_modules
    ]
    primary_html_colors = [
        ColorPalette.get_module_color(module_name, "html")
        for module_name in primary_modules
    ]

    assert primary_ansi_colors == [
        ColorPalette.get_module_color(module_name, "ansi")
        for module_name in primary_modules
    ]
    assert primary_html_colors == [
        ColorPalette.get_module_color(module_name, "html")
        for module_name in primary_modules
    ]
    assert len(set(primary_ansi_colors)) == len(primary_ansi_colors)
    assert len(set(primary_html_colors)) == len(primary_html_colors)
    assert all(color.startswith("\033[1m\033[38;2;") for color in primary_ansi_colors)
    assert all(color.startswith("#") for color in primary_html_colors)

    alias_ansi = ColorPalette.get_module_color("src.tuner.main", "ansi")
    alias_html = ColorPalette.get_module_color("src.tuner.main", "html")
    unknown_module_ansi = ColorPalette.get_module_color("custom.module.name", "ansi")
    unknown_module_html = ColorPalette.get_module_color("custom.module.name", "html")

    assert alias_ansi == ColorPalette.get_module_color("src.tuner.main", "ansi")
    assert alias_html == ColorPalette.get_module_color("src.tuner.main", "html")
    assert unknown_module_ansi == ColorPalette.get_module_color(
        "custom.module.name", "ansi"
    )
    assert unknown_module_html == ColorPalette.get_module_color(
        "custom.module.name", "html"
    )

    assert unknown_module_ansi.startswith("\033[38;2;")
    assert unknown_module_html.startswith("#")


def test_logger_name_helpers_strip_src_prefix_and_align():
    """Logger labels should drop the leading src prefix and pad consistently."""
    normalize = _logger_helpers.normalize_logger_name
    format_name = _logger_helpers.format_logger_name

    assert normalize("src.tuner.config.knob_space") == "tuner.config.knob_space"
    assert normalize("PBTuner") == "PBTuner"
    assert format_name("src.tuner.config.knob_space", width=32) == (
        "tuner.config.knob_space".ljust(32)
    )
    assert format_name("PBTuner", width=32) == "PBTuner".ljust(32)
    assert len(format_name("src.tuner.config.knob_space", width=32)) == 32


def test_logger_level_helper_centers_text():
    """Log levels should be centered inside the fixed-width display field."""
    format_level = _logger_helpers.format_logger_level

    assert format_level("INFO") == "  INFO "
    assert format_level("DEBUG") == " DEBUG "
    assert format_level("WARNING") == "WARNING"
    assert len(format_level("INFO")) == _logger_helpers.LOGGER_LEVEL_WIDTH


def test_module_colors_ignore_src_prefix():
    """Module colors should stay stable whether or not the src prefix is present."""
    assert ColorPalette.get_module_color(
        "src.tuner.config.knob_space", "ansi"
    ) == ColorPalette.get_module_color("tuner.config.knob_space", "ansi")


def test_global_color_switch_disables_all_color_sources():
    """The runtime color policy should blank all ANSI/HTML color sources."""
    try:
        set_colors_enabled(False)

        assert ColorCode.BOLD == ""
        assert ColorCode.RESET == ""
        assert ColorPalette.get_level_color("INFO", "ansi") == ""
        assert ColorPalette.get_level_color("INFO", "html") == ""
        assert ColorPalette.get_module_color("PBTune", "ansi") == ""
        assert ColorPalette.get_worker_color(3, "ansi") == ""
        assert _logger_helpers.strip_ansi("\033[1mplain\033[0m") == "plain"
    finally:
        set_colors_enabled(True)


def test_formatter_no_color_output_is_plain_text():
    """Formatter output should lose ANSI/HTML decoration when colors are disabled."""
    global _logger_formatters
    if _logger_formatters is None:
        _logger_formatters = _load_logger_formatters_module()

    formatter = _logger_formatters.ColoredFormatter(show_module=True)
    html_formatter = _logger_formatters.HTMLFormatter(show_module=True)
    record = logging.LogRecord(
        name="src.tuner.main",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="%s%sStarting...%s",
        args=("\033[1m", "\033[38;2;1;2;3m", "\033[0m"),
        exc_info=None,
    )

    try:
        set_colors_enabled(False)

        console_output = formatter.format(record)
        html_output = html_formatter.format(record)

        assert "\033[" not in console_output
        assert "\033[" not in html_output
        assert "<span style=" not in html_output
        assert "Starting..." in console_output
        assert "Starting..." in html_output
    finally:
        set_colors_enabled(True)


def test_formatter_preserves_column_alignment_in_no_color_mode():
    """Formatter should maintain padding for module names and levels in no-color mode."""
    global _logger_formatters
    if _logger_formatters is None:
        _logger_formatters = _load_logger_formatters_module()

    formatter = _logger_formatters.ColoredFormatter(
        show_module=True, module_width=17, level_width=7
    )
    html_formatter = _logger_formatters.HTMLFormatter(
        show_module=True, module_width=17, level_width=7
    )

    # Create two records with different module name lengths
    record_short = logging.LogRecord(
        name="Population",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Test message",
        args=(),
        exc_info=None,
    )

    record_long = logging.LogRecord(
        name="BenchmarkExecutor",
        level=logging.DEBUG,
        pathname=__file__,
        lineno=1,
        msg="Test message",
        args=(),
        exc_info=None,
    )

    try:
        # Test with colors disabled
        set_colors_enabled(False)

        console_short = formatter.format(record_short)
        console_long = formatter.format(record_long)

        html_short = html_formatter.format(record_short)
        html_long = html_formatter.format(record_long)

        # Extract the module column from each output
        # Format is: "timestamp - level - module - message"
        console_short_parts = console_short.split(" - ")
        console_long_parts = console_long.split(" - ")

        html_short_parts = html_short.split(" - ")
        html_long_parts = html_long.split(" - ")

        # Module is the 3rd element (index 2)
        assert len(console_short_parts) >= 3
        assert len(console_long_parts) >= 3
        assert len(html_short_parts) >= 3
        assert len(html_long_parts) >= 3

        module_short = console_short_parts[2]
        module_long = console_long_parts[2]

        # Both should be padded to the same length (17 chars)
        assert len(module_short) == 17, (
            f"Short module '{module_short}' should be padded to 17 chars, got {len(module_short)}"
        )
        assert len(module_long) == 17, (
            f"Long module '{module_long}' should be padded to 17 chars, got {len(module_long)}"
        )

        # Verify the actual content is correct
        assert module_short.startswith("Population")
        assert module_long.startswith("BenchmarkExecutor")

        # Verify the level is centered to 7 chars
        level_short = console_short_parts[1]
        level_long = console_long_parts[1]

        assert len(level_short) == 7, (
            f"Level '{level_short}' should be 7 chars, got {len(level_short)}"
        )
        assert len(level_long) == 7, (
            f"Level '{level_long}' should be 7 chars, got {len(level_long)}"
        )
        assert "INFO" in level_short
        assert "DEBUG" in level_long
    finally:
        set_colors_enabled(True)


def test_worker_metrics_table_renders_one_section_for_four_workers():
    """Worker metric tables should stay single-block when the worker count is small."""
    table = _logger_helpers.format_worker_metrics_table(
        [
            {
                "score": 91.25,
                "latency_p95": "12.34 ms",
                "throughput": "1200 TPS",
            },
            {
                "score": 89.5,
                "latency_p95": "13.00 ms",
                "throughput": "1188 TPS",
            },
            {
                "score": 88.2,
                "latency_p95": "13.75 ms",
                "throughput": "1174 TPS",
            },
            {
                "score": 87.0,
                "latency_p95": "14.10 ms",
                "throughput": "1160 TPS",
            },
        ],
        worker_labels=["Worker-0", "Worker-1", "Worker-2", "Worker-3"],
        metric_order=["score", "latency_p95", "throughput"],
        title="Generation 3 Worker Metrics",
    )

    assert table.count("Generation 3 Worker Metrics") == 1
    assert "part 1/2" not in table
    assert "| Metric" in table
    assert "Score" in table
    assert "Latency P95" in table
    assert "Throughput" in table
    assert "Worker-3" in table


def test_worker_metrics_table_colours_headers_and_formats_score_to_three_decimals():
    """Worker tables should colorize columns and render score with three decimals."""
    colors = _logger_helpers.get_color_context()
    table = _logger_helpers.format_worker_metrics_table(
        [
            {"score": 80.9105, "latency_p95": "118.92ms"},
            {"score": 78.3736, "latency_p95": "112.67ms"},
        ],
        worker_labels=["Worker-0", "Worker-1"],
        metric_order=["score", "latency_p95"],
        title="Generation 2 Worker Metrics",
        split_threshold=4,
    )

    assert "80.9105" not in table
    assert "80.910" in table
    assert f"{colors.bold}{colors.green}80.910" not in table
    assert f"{colors.green}80.910" in table
    assert colors.cyan in table or colors.teal in table or colors.sky_blue in table
    assert _logger_helpers.ColorPalette.get_worker_color(0, "ansi") in table
    assert _logger_helpers.ColorPalette.get_worker_color(1, "ansi") in table


def test_worker_metrics_table_centers_block_and_appends_best_worker_to_last_table():
    """The best worker should be appended to the last section without splitting."""
    colors = _logger_helpers.get_color_context()
    table = _logger_helpers.format_worker_metrics_table(
        [
            {"score": 91.25, "latency_p95": "12.34 ms"},
            {"score": 89.5, "latency_p95": "13.00 ms"},
            {"score": 88.2, "latency_p95": "13.75 ms"},
            {"score": 87.0, "latency_p95": "14.10 ms"},
        ],
        worker_labels=["Worker-0", "Worker-1", "Worker-2", "Worker-3"],
        metric_order=["score", "latency_p95"],
        best_worker_metric={"score": 95.3334, "latency_p95": "11.11 ms"},
        best_worker_label="Best Worker",
        title="\nGeneration 6 Worker Metrics",
        split_threshold=4,
        center_width=120,
    )

    assert table.count("Generation 6 Worker Metrics") == 1
    assert table.startswith("\n")
    assert "Best Worker" in table
    assert "part 1/2" not in table
    assert "95.3334" not in table
    assert "95.333" in table
    assert f"{colors.bold}{colors.green}95.333" in table
    assert f"{colors.green}95.333" in table


def test_worker_metrics_table_splits_into_two_sections_for_more_than_four_workers():
    """Worker metric tables should split vertically once the worker count grows too wide."""
    table = _logger_helpers.format_worker_metrics_table(
        [
            {"score": 91.25, "latency_p95": "12.34 ms"},
            {"score": 89.5, "latency_p95": "13.00 ms"},
            {"score": 88.2, "latency_p95": "13.75 ms"},
            {"score": 87.0, "latency_p95": "14.10 ms"},
            {"score": 86.1, "latency_p95": "14.85 ms"},
        ],
        worker_labels=[
            "Worker-0",
            "Worker-1",
            "Worker-2",
            "Worker-3",
            "Worker-4",
        ],
        metric_order=["score", "latency_p95"],
        title="Generation 4 Worker Metrics",
    )

    sections = table.split("\n\n")

    assert len(sections) == 2
    assert sections[0].lstrip().startswith("Generation 4 Worker Metrics (part 1/2)")
    assert sections[1].lstrip().startswith("Generation 4 Worker Metrics (part 2/2)")
    assert "Worker-4" not in sections[0]
    assert "Worker-4" in sections[1]
    assert table.count("Score") == 2


def test_worker_metrics_table_creates_additional_sections_for_large_worker_counts():
    """Worker metric tables should keep chunking as the worker count keeps growing."""
    table = _logger_helpers.format_worker_metrics_table(
        [
            {"score": 91.25, "latency_p95": "12.34 ms"},
            {"score": 89.5, "latency_p95": "13.00 ms"},
            {"score": 88.2, "latency_p95": "13.75 ms"},
            {"score": 87.0, "latency_p95": "14.10 ms"},
            {"score": 86.1, "latency_p95": "14.85 ms"},
            {"score": 85.4, "latency_p95": "15.22 ms"},
            {"score": 84.8, "latency_p95": "15.81 ms"},
            {"score": 84.1, "latency_p95": "16.03 ms"},
            {"score": 83.6, "latency_p95": "16.44 ms"},
        ],
        worker_labels=[
            "Worker-0",
            "Worker-1",
            "Worker-2",
            "Worker-3",
            "Worker-4",
            "Worker-5",
            "Worker-6",
            "Worker-7",
            "Worker-8",
        ],
        metric_order=["score", "latency_p95"],
        title="Generation 5 Worker Metrics",
        split_threshold=4,
    )

    sections = table.split("\n\n")

    assert len(sections) == 3
    assert sections[0].lstrip().startswith("Generation 5 Worker Metrics (part 1/3)")
    assert sections[1].lstrip().startswith("Generation 5 Worker Metrics (part 2/3)")
    assert sections[2].lstrip().startswith("Generation 5 Worker Metrics (part 3/3)")
    assert "Worker-0" in sections[0]
    assert "Worker-4" in sections[1]
    assert "Worker-8" in sections[2]
    assert "Worker-8" not in sections[0]
