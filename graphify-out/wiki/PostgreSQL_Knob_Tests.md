# PostgreSQL Knob Tests

> 12 nodes · cohesion 0.18

## Key Concepts

- **helpers.py** (8 connections) — `src/utils/logger/helpers.py`
- **format_logger_name()** (4 connections) — `src/utils/logger/helpers.py`
- **format_logger_level()** (3 connections) — `src/utils/logger/helpers.py`
- **log_generation_summary()** (3 connections) — `src/utils/logger/helpers.py`
- **normalize_logger_name()** (3 connections) — `src/utils/logger/helpers.py`
- **strip_ansi()** (3 connections) — `src/utils/logger/helpers.py`
- **Logging Helper Functions ========================  This module contains helpers** (1 connections) — `src/utils/logger/helpers.py`
- **Normalize a logger name for display.      The logger is primarily used for code** (1 connections) — `src/utils/logger/helpers.py`
- **Return a left-padded logger label for aligned log output.** (1 connections) — `src/utils/logger/helpers.py`
- **Return a centered logger level label for aligned log output.** (1 connections) — `src/utils/logger/helpers.py`
- **Remove ANSI escape sequences from text.** (1 connections) — `src/utils/logger/helpers.py`
- **Log a formatted generation summary.      Parameters     ----------     logger :** (1 connections) — `src/utils/logger/helpers.py`

## Relationships

- [[Metric Config Schema]] (28 shared connections)
- [[BO Baseline & Workload]] (2 shared connections)

## Source Files

- `src/utils/logger/helpers.py`

## Audit Trail

- EXTRACTED: 26 (87%)
- INFERRED: 4 (13%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*