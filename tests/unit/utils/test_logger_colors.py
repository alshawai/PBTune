"""Unit tests for logger color generation."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


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


_logger_colors = _load_logger_colors_module()
ColorPalette = _logger_colors.ColorPalette
ColorCode = _logger_colors.ColorCode
_logger_helpers = _load_logger_helpers_module()


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
