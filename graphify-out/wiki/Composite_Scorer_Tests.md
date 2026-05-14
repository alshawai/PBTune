# Composite Scorer Tests

> 26 nodes · cohesion 0.09

## Key Concepts

- **test_logger_colors.py** (12 connections) — `tests/unit/utils/test_logger_colors.py`
- **set_colors_enabled()** (6 connections) — `src/utils/logger/colors.py`
- **_load_logger_formatters_module()** (4 connections) — `tests/unit/utils/test_logger_colors.py`
- **test_formatter_no_color_output_is_plain_text()** (4 connections) — `tests/unit/utils/test_logger_colors.py`
- **test_formatter_preserves_column_alignment_in_no_color_mode()** (4 connections) — `tests/unit/utils/test_logger_colors.py`
- **test_global_color_switch_disables_all_color_sources()** (3 connections) — `tests/unit/utils/test_logger_colors.py`
- **_load_logger_colors_module()** (2 connections) — `tests/unit/utils/test_logger_colors.py`
- **_load_logger_helpers_module()** (2 connections) — `tests/unit/utils/test_logger_colors.py`
- **test_logger_level_helper_centers_text()** (2 connections) — `tests/unit/utils/test_logger_colors.py`
- **test_logger_name_helpers_strip_src_prefix_and_align()** (2 connections) — `tests/unit/utils/test_logger_colors.py`
- **test_module_colors_are_deterministic()** (2 connections) — `tests/unit/utils/test_logger_colors.py`
- **test_module_colors_ignore_src_prefix()** (2 connections) — `tests/unit/utils/test_logger_colors.py`
- **test_worker_colors_are_deterministic_and_unique()** (2 connections) — `tests/unit/utils/test_logger_colors.py`
- **Enable or disable ANSI/HTML color generation at runtime.** (1 connections) — `src/utils/logger/colors.py`
- **Unit tests for logger color generation and formatter behavior.** (1 connections) — `tests/unit/utils/test_logger_colors.py`
- **Load the logger colors module without importing the full src package.** (1 connections) — `tests/unit/utils/test_logger_colors.py`
- **Main orchestrator modules should use the dedicated palette; others should be dyn** (1 connections) — `tests/unit/utils/test_logger_colors.py`
- **Logger labels should drop the leading src prefix and pad consistently.** (1 connections) — `tests/unit/utils/test_logger_colors.py`
- **Log levels should be centered inside the fixed-width display field.** (1 connections) — `tests/unit/utils/test_logger_colors.py`
- **Module colors should stay stable whether or not the src prefix is present.** (1 connections) — `tests/unit/utils/test_logger_colors.py`
- **The runtime color policy should blank all ANSI/HTML color sources.** (1 connections) — `tests/unit/utils/test_logger_colors.py`
- **Formatter output should lose ANSI/HTML decoration when colors are disabled.** (1 connections) — `tests/unit/utils/test_logger_colors.py`
- **Formatter should maintain padding for module names and levels in no-color mode.** (1 connections) — `tests/unit/utils/test_logger_colors.py`
- **Load the logger helpers module without importing the full src package.** (1 connections) — `tests/unit/utils/test_logger_colors.py`
- **Load formatter module with in-memory aliases for logger dependencies.** (1 connections) — `tests/unit/utils/test_logger_colors.py`
- *... and 1 more nodes in this community*

## Relationships

- [[Docker Manifest Tests]] (1 shared connections)
- [[Docker Snapshot]] (1 shared connections)

## Source Files

- `src/utils/logger/colors.py`
- `tests/unit/utils/test_logger_colors.py`

## Audit Trail

- EXTRACTED: 53 (88%)
- INFERRED: 7 (12%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*