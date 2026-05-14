# Scoring & Weight Policies

> 52 nodes · cohesion 0.06

## Key Concepts

- **_make_environment()** (29 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_docker_environment.py** (28 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_default_snapshot_id_changes_when_schema_profile_changes()** (4 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_setup_instances_force_recreate_baseline_removes_snapshot_once()** (4 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_setup_instances_raises_when_baseline_snapshot_creation_fails()** (4 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_setup_instances_recreates_worker0_when_baseline_snapshot_missing()** (4 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_setup_instances_reuses_existing_snapshot_without_recommit()** (4 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_container_name_uses_configured_prefix()** (3 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_create_snapshot_returns_empty_string_on_api_error()** (3 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_create_snapshot_writes_metadata_manifest()** (3 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_default_snapshot_id_is_profile_scoped()** (3 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_rebuild_worker_instance_recreates_clean_slate_and_prepares_schema()** (3 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_restore_snapshot_removes_container_before_volume_reseed()** (3 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_restore_snapshot_returns_false_when_container_run_fails()** (3 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_restore_snapshot_returns_false_when_image_missing()** (3 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_restore_snapshot_uses_extended_timeout_for_container_removal()** (3 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_restore_snapshot_uses_restore_specific_ready_timeout()** (3 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_setup_instances_tracks_worker_before_ready_wait()** (3 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_snapshot_exists_requires_matching_manifest_signature()** (3 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_snapshot_exists_returns_true_with_matching_manifest_signature()** (3 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_start_instance_returns_false_on_read_timeout()** (3 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_stop_all_recovers_from_stop_timeout_if_container_exited()** (3 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_stop_all_stops_untracked_running_prefixed_containers()** (3 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_stop_instance_fails_when_stop_timeout_and_container_still_running()** (3 connections) — `tests/unit/utils/test_docker_environment.py`
- **test_stop_instance_recovers_when_stop_timeout_but_container_exited()** (3 connections) — `tests/unit/utils/test_docker_environment.py`
- *... and 27 more nodes in this community*

## Relationships

- [[TPC-H Benchmark Executor]] (150 shared connections)
- [[TPC-H Query Executor]] (8 shared connections)
- [[Database Config & Connection]] (1 shared connections)
- [[Bare Metal Environment]] (1 shared connections)

## Source Files

- `tests/unit/utils/test_docker_environment.py`

## Audit Trail

- EXTRACTED: 158 (99%)
- INFERRED: 2 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*