# Dead Worker Rescue

> 13 nodes · cohesion 0.15

## Key Concepts

- **test_tuner_cli.py** (6 connections) — `tests/unit/core/test_tuner_cli.py`
- **test_parse_args_sysbench_workload_value()** (3 connections) — `tests/unit/core/test_tuner_cli.py`
- **test_parse_args_disable_early_stopping_defaults_to_false()** (2 connections) — `tests/unit/core/test_tuner_cli.py`
- **test_parse_args_disable_early_stopping_enabled()** (2 connections) — `tests/unit/core/test_tuner_cli.py`
- **test_parse_args_no_color_defaults_to_false()** (2 connections) — `tests/unit/core/test_tuner_cli.py`
- **test_parse_args_no_color_enables_plain_output()** (2 connections) — `tests/unit/core/test_tuner_cli.py`
- **Tests for tuner CLI argument parsing.** (1 connections) — `tests/unit/core/test_tuner_cli.py`
- **CLI should keep colors enabled unless --no-color is provided.** (1 connections) — `tests/unit/core/test_tuner_cli.py`
- **CLI should disable colors when --no-color is provided.** (1 connections) — `tests/unit/core/test_tuner_cli.py`
- **CLI should keep the no-improvement early stop enabled by default.** (1 connections) — `tests/unit/core/test_tuner_cli.py`
- **CLI should parse the no-improvement early stopping disable flag.** (1 connections) — `tests/unit/core/test_tuner_cli.py`
- **CLI keeps sysbench workload unset unless explicitly provided.** (1 connections) — `tests/unit/core/test_tuner_cli.py`
- **CLI should parse explicit sysbench workload mode.** (1 connections) — `tests/unit/core/test_tuner_cli.py`

## Relationships

- [[Workload File Loading]] (24 shared connections)

## Source Files

- `tests/unit/core/test_tuner_cli.py`

## Audit Trail

- EXTRACTED: 24 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*