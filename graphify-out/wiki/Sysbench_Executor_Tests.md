# Sysbench Executor Tests

> 45 nodes · cohesion 0.06

## Key Concepts

- **hardware_info.py** (14 connections) — `src/utils/hardware_info.py`
- **get_system_info()** (11 connections) — `src/utils/hardware_info.py`
- **test_hardware_info.py** (9 connections) — `tests/unit/utils/test_hardware_info.py`
- **detect_disk_type()** (7 connections) — `src/utils/hardware_info.py`
- **test_detect_worker_resources_bare_metal()** (5 connections) — `tests/unit/utils/test_hardware_info.py`
- **test_detect_worker_resources_container()** (5 connections) — `tests/unit/utils/test_hardware_info.py`
- **detect_core_count()** (4 connections) — `src/utils/hardware_info.py`
- **detect_cpu_model()** (4 connections) — `src/utils/hardware_info.py`
- **detect_pg_version()** (4 connections) — `src/utils/hardware_info.py`
- **detect_ram_total()** (4 connections) — `src/utils/hardware_info.py`
- **_is_containerized()** (4 connections) — `src/utils/hardware_info.py`
- **log_system_info()** (4 connections) — `src/utils/hardware_info.py`
- **_detect_disk_type_linux()** (3 connections) — `src/utils/hardware_info.py`
- **_detect_disk_type_windows()** (3 connections) — `src/utils/hardware_info.py`
- **detect_os_info()** (3 connections) — `src/utils/hardware_info.py`
- **test_detect_core_count()** (3 connections) — `tests/unit/utils/test_hardware_info.py`
- **test_detect_cpu_model()** (3 connections) — `tests/unit/utils/test_hardware_info.py`
- **test_detect_ram_total()** (3 connections) — `tests/unit/utils/test_hardware_info.py`
- **test_is_containerized_dockerenv()** (3 connections) — `tests/unit/utils/test_hardware_info.py`
- **test_is_containerized_false()** (3 connections) — `tests/unit/utils/test_hardware_info.py`
- **test_system_info_dict_keys()** (3 connections) — `tests/unit/utils/test_hardware_info.py`
- **Integration test: resolve ranges with realistically detected bare-metal resource** (1 connections) — `tests/unit/config/test_hardware_normalization.py`
- **Integration test: resolve ranges with container limits.** (1 connections) — `tests/unit/config/test_hardware_normalization.py`
- **Hardware Information Detection ==============================  Detects and repor** (1 connections) — `src/utils/hardware_info.py`
- **Detect total system RAM in bytes and human-readable format.** (1 connections) — `src/utils/hardware_info.py`
- *... and 20 more nodes in this community*

## Relationships

- [[Hardware Detection & Info]] (116 shared connections)
- [[Session Management]] (4 shared connections)
- [[Hardware Normalization Tests]] (3 shared connections)
- [[Environment Factory]] (1 shared connections)
- [[Cross-Module Rationale]] (1 shared connections)
- [[BO Baseline & Workload]] (1 shared connections)

## Source Files

- `src/utils/hardware_info.py`
- `tests/unit/config/test_hardware_normalization.py`
- `tests/unit/utils/test_hardware_info.py`

## Audit Trail

- EXTRACTED: 109 (87%)
- INFERRED: 17 (13%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*