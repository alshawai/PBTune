# PostgreSQL Autotuning Knob Policy

This document records the inclusion/exclusion design decision for every knob in the PostgreSQL snapshot (`397` total).
It avoids implementation-centric framing and instead classifies knobs by **optimization scope**: performance optimization vs.
semantics, security, topology, observability, and infrastructure policy.

See also: [`src/knobs/policy.py`](../src/knobs/policy.py) — the authoritative source of the exclusion registry.

---

## 1. Policy intent and scope

### What this tuner optimizes

The PBT tuner's objective is to maximize workload throughput and minimize query latency by searching the space of
PostgreSQL runtime configuration parameters. The knob policy determines which parameters are admitted into that search
space and which are excluded — and records the reasoning for every decision.

### In scope

Parameters that directly influence **workload execution efficiency**:

- Memory allocation (`shared_buffers`, `work_mem`, buffer pools)
- Planner cost model (`seq_page_cost`, `random_page_cost`, cost weights)
- Parallelism (`max_parallel_workers`, worker counts)
- I/O behavior (`effective_io_concurrency`, bgwriter, checkpoint timing)
- WAL and durability trade-offs (`wal_level`, `fsync`, `synchronous_commit`)
- Autovacuum scheduling and resource limits

### Out of scope

Parameters that define **what the database is** rather than **how fast it runs**:

- Security policy (SSL/TLS, authentication, encryption)
- Network topology (`port`, `listen_addresses`, replication connection strings)
- File system paths and identity (`data_directory`, `config_file`, `shared_preload_libraries`)
- Locale and formatting (`TimeZone`, `DateStyle`, `lc_*`)
- Crash and recovery behavior (`restart_after_crash`, `exit_on_error`)
- Audit/logging controls (`logging_collector`, `log_destination`, `log_file_mode`)
- Debug/developer options (`debug_print_plan`, `debug_discard_caches`)
- Data integrity bypass flags (`zero_damaged_pages`, `ignore_checksum_failure`)

---

## 2. Exclusion categories

Each exclusion is assigned a `reason_code`. The table below defines each code, its enforcement nature (absolute vs.
conditional), and the conditions under which a knob in that category could be re-admitted.

| Reason Code | Description | Nature | Re-enable criteria |
|---|---|:---:|---|
| `internal_context` | PostgreSQL `context = internal` parameters are read-only compile-time or memory-layout constants. No runtime interface can modify them. | **Absolute** | Cannot be re-enabled; inherently immutable. |
| `applicator_dependency` | Disabling this knob would break the tuner's settings-application mechanism (ALTER SYSTEM). | **Absolute** | Cannot be re-enabled without replacing the settings applicator. |
| `data_integrity` | Flags that bypass checksum validation or system index integrity. Enabling these in an automated loop risks silent data corruption across worker configurations. | **Absolute** | Never safe under automated tuning. |
| `system_catalog_safety` | Permits direct writes to system catalog tables. Automating this risks internal catalog corruption. | **Absolute** | Never in an automated context. |
| `debug_only` | Developer-mode flags with no production performance semantics. | **Absolute** | Not applicable to production optimization. |
| `domain_scope_non_performance` | String parameters encoding identity, paths, locale, credentials, or recovery targets. They define operational configuration, not execution efficiency. (~70 parameters.) | **Absolute** | Not within this tuner's optimization objective. |
| `semantic_behavior` | Flags that change SQL transactional semantics (`default_transaction_read_only`, `default_transaction_deferrable`). These alter the meaning of queries, not their speed. | **Absolute** | Not applicable. |
| `network_binding` | `port`: modifies the postmaster listen port. Not a performance parameter; changes require restart and break benchmark connectivity. | **Absolute** | Not applicable. |
| `network_discovery` | `bonjour`: Bonjour service advertisement. Unrelated to workload performance. | **Absolute** | Not applicable. |
| `session_semantics` | Per-session transaction toggles (`transaction_deferrable`, `transaction_read_only`). Setting these globally via ALTER SYSTEM does not represent stable workload-wide optimization. | **Absolute** | Not applicable for global optimization. |
| `security_transport` | SSL/TLS transport policy parameters. Security policy is external to the tuner's optimization objective. | **Conditional** | May be admitted in isolated research environments where SSL state is fixed and stable, via an explicit policy bypass. |
| `benchmark_validity` | Parameters that introduce artificial latency (`pre_auth_delay`, `post_auth_delay`) or can cancel in-flight benchmark statements (`statement_timeout`). | **Conditional** | `pre_auth_delay` and `post_auth_delay` remain excluded. `statement_timeout` may be re-admitted if the evaluator manages its own per-step timeouts at each stage (partial admission implemented in Phase C for the VACUUM ANALYZE step). |
| `mutual_exclusion` | Log statistics flags that are mutually exclusive at the PostgreSQL level. Enabling more than one subsystem flag at a time causes a configuration error. | **Conditional** | May be re-admitted if the tuner treats this group as a categorical single-select parameter and only ever activates one value at a time. |
| `format_readback` | Octal-permission parameters (`log_file_mode`, `unix_socket_permissions`). PostgreSQL stores these as integers; the canonical form is octal, which the pipeline cannot reliably round-trip. | **Conditional** | Can be re-enabled once the pipeline handles octal encoding and validation for these specific parameters. |
| `logging_pipeline_dependency` | `logging_collector`: redirects PostgreSQL log output away from stderr to managed files. The tuner's orchestration depends on stderr-based log collection. | **Conditional** | Can be re-enabled if the tuner's log collector is updated to read PostgreSQL-managed log files. |
| `stability` | JIT compilation parameters (`jit` and family). JIT shows known instability in benchmark workloads in this environment: intermittent degradation and crashes under certain query patterns make benchmark scores unreliable. | **Conditional** | Can be re-enabled on PostgreSQL builds and environments where JIT is validated stable for the target workload (e.g., PG16+ OLAP with controlled query patterns). |
| `os_alignment` | `max_stack_depth` must not exceed the OS `ulimit -s` stack size limit. The tuner cannot safely determine the correct OS-safe upper bound at runtime without reading `/proc/1/limits` or equivalent. | **Conditional** | Re-enable by adding OS stack limit discovery to the evaluator and clamping `max_stack_depth` at each worker startup. |
| `storage_safety` | `allow_in_place_tablespaces`: enables tablespace creation in the PostgreSQL data directory path. Toggling in a tuning loop risks tablespace layout side effects. | **Conditional** | Can be re-enabled in controlled environments where tablespace layout is pre-fixed and verified. |
| `uncurated_intmax_sentinel` | Numeric parameters whose PostgreSQL-native maximum is ≥ 2,000,000,000 (INT_MAX class) without a curated [`TuningMetadata`](../src/knobs/knob_metadata.py) entry defining practical bounds. The unbounded upper limit makes random search unsafe. | **Conditional** | Add a `TuningMetadata` entry in [`src/knobs/knob_metadata.py`](../src/knobs/knob_metadata.py) with practical `min_val`/`max_val` bounds and an `impact_tier`. The parameter is automatically promoted to the appropriate tier CSV on the next preprocessing run. |

### Sub-categories within `domain_scope_non_performance`

The `domain_scope_non_performance` code covers several distinct semantic groups:

| Sub-group | Examples | ~Count |
|---|---|---:|
| Paths and file locations | `data_directory`, `config_file`, `hba_file`, `ssl_cert_file` | 15 |
| Identity and naming | `cluster_name`, `application_name`, `event_source` | 5 |
| Locale and formatting | `TimeZone`, `DateStyle`, `lc_*`, `timezone_abbreviations` | 10 |
| Replication topology | `primary_conninfo`, `recovery_target_*`, `synchronous_standby_names` | 10 |
| Logging controls | `log_destination`, `log_directory`, `log_filename`, `log_line_prefix` | 7 |
| Security credentials | `ssl_cert_file`, `ssl_key_file`, `ssl_ca_file`, `krb_server_keyfile` | 8 |
| Library loading | `shared_preload_libraries`, `session_preload_libraries`, `local_preload_libraries` | 3 |
| Misc operational | `search_path`, `temp_tablespaces`, `restrict_nonsystem_relation_kind` | 10 |

---

## 3. Source-stage classification rationale

Policy classification happens in `retrieval.py::get_all_knobs_with_metadata()`, which calls
`annotate_autotuning_policy()` from `src/knobs/policy.py` before returning. This adds three columns to every row of
the 397-knob dataframe:

| Column | Type | Meaning |
|---|---|---|
| `eligible_for_autotuning` | `bool` | `True` if the knob is admitted to the optimization search space |
| `autotuning_exclusion_reason_code` | `str` | Category code from §2 (empty string for admitted knobs) |
| `autotuning_exclusion_reason_detail` | `str` | Human-readable exclusion reason (empty string for admitted knobs) |

Downstream preprocessing reads `eligible_for_autotuning` and defines no additional policy of its own. A separate
bounds safety gate (`apply_bounds_safety_gate()`) excludes uncurated INT_MAX-sentinel knobs as a second pass over
the already-annotated dataframe.

### Benefits of source-stage classification

- **Single authority.** `src/knobs/policy.py` is the only module where exclusion decisions are made.
- **Full auditability.** The complete 397-row annotated dataframe is available for dashboards, reports, and tests.
- **Structured logging.** The pipeline logs a grouped breakdown of excluded knobs by reason code at each run.
- **Safe re-enablement.** Adding or removing a policy entry requires a change to exactly one dict in one file.

---

## 4. Operational guidance

### Adding a new exclusion

1. Look up the knob name as it appears in `pg_settings.name`.
2. Select the appropriate `reason_code` from the table in §2.
3. Add an entry to `AUTOTUNING_SOURCE_EXCLUSIONS` in [`src/knobs/policy.py`](../src/knobs/policy.py):

	```python
	"knob_name": (
		 "reason_code",
		 "One sentence explaining why this knob is excluded.",
	),
	```

4. Update the knob's row in the [full inventory](#5-full-knob-decision-inventory-all-397) below: set Decision to
	`Exclude`, and fill in the Reason Code and Reason columns.
5. Run the preprocessing pipeline and confirm the exclusion count increases by 1:

	```bash
	python -m src.knobs.preprocess_knobs data/postgresql_all_knobs_demo.csv
	```

6. Run the unit tests: `pytest tests/unit/knobs/`.

### Removing an exclusion (re-admitting a knob)

1. Find the knob entry in `AUTOTUNING_SOURCE_EXCLUSIONS`.
2. Verify all re-enable conditions for its `reason_code` (§2) are satisfied.
3. Delete the entry from `AUTOTUNING_SOURCE_EXCLUSIONS`.
4. If the knob's native max is ≥ 2,000,000,000, also add a `TuningMetadata` entry (see below).
5. Run the preprocessing pipeline and confirm the knob appears in the expected tier CSV.
6. Update the inventory row to `Include` and set the appropriate Reason Code.
7. Run the unit tests: `pytest tests/unit/knobs/`.

### Promoting an `uncurated_intmax_sentinel` knob

The most common re-admission path. Most INT_MAX-sentinel knobs are excluded purely by the bounds safety gate, not by
an `AUTOTUNING_SOURCE_EXCLUSIONS` entry. To promote one:

1. Research practical operating bounds in the PostgreSQL documentation and community tuning guides.
2. Add a `TuningMetadata` entry in [`src/knobs/knob_metadata.py`](../src/knobs/knob_metadata.py):

	```python
	"knob_name": TuningMetadata(
		 description="What this knob controls.",
		 min_val=<practical_min>,
		 max_val=<practical_max>,
		 unit="<unit or empty string>",
		 impact_tier="extensive",  # or "standard", "core", "minimal"
		 log_scale=False,          # True for memory knobs that span orders of magnitude
	),
	```

3. Run the pipeline: the knob is automatically promoted to the correct tier CSV.
4. Verify: `pytest tests/unit/knobs/test_knob_metadata.py`.

### Changing a reason code

If a knob's exclusion reason is reclassified (e.g., `stability` → `benchmark_validity`):

1. Update the `AUTOTUNING_SOURCE_EXCLUSIONS` entry in `policy.py`.
2. Update the knob's row in the inventory table below.
3. Run the pipeline and unit tests.

### Version upgrade: handling new knobs

When upgrading to a new PostgreSQL minor or major version, run the preprocessing pipeline and inspect the log output
for any new knobs appearing without a policy exclusion annotation that may represent out-of-scope parameters. Add
entries to `AUTOTUNING_SOURCE_EXCLUSIONS` as needed before merging.

---

## 5. Full knob decision inventory

Columns:

- **Min/Max**: Curated bounds for curated knobs; native pg_settings bounds otherwise.
- **Decision**: Include or Exclude.
- **Reason Code / Reason**: Clear rationale category and detail.

| Knob | Min | Max | Unit | Vartype | Context | Decision | Reason Code | Reason |
|---|---:|---:|---|---|---|---|---|---|
| DateStyle | — | — | — | string | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| IntervalStyle | — | — | — | enum | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| TimeZone | — | — | — | string | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| allow_alter_system | — | — | — | bool | sighup | Exclude | applicator_dependency | Tuner applies settings via ALTER SYSTEM; disabling this breaks configuration application. |
| allow_in_place_tablespaces | — | — | — | bool | superuser | Exclude | storage_safety | Storage/path behavior option; excluded from autotuning safety policy. |
| allow_system_table_mods | — | — | — | bool | superuser | Exclude | system_catalog_safety | System catalog mutation option; excluded from autotuning safety policy. |
| application_name | — | — | — | string | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| archive_cleanup_command | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| archive_command | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| archive_library | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| archive_mode | — | — | — | enum | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| archive_timeout | 0.0 | 1073741823.0 | s | integer | sighup | Exclude | benchmark_validity | WAL archiving timer; not relevant as benchmarks don't measure archiving latency. |
| array_nulls | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| authentication_timeout | 1.0 | 600.0 | s | integer | sighup | Exclude | benchmark_validity | Login timeout setting; not a workload performance tuning knob. |
| autovacuum | — | — | — | bool | sighup | Include | admitted_curated | Enable autovacuum. Usually keep on. |
| autovacuum_analyze_scale_factor | 0.01 | 0.5 | — | real | sighup | Include | admitted_curated | fraction of table modified for analyze. |
| autovacuum_analyze_threshold | 0.0 | 2147483647.0 | — | integer | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| autovacuum_freeze_max_age | 100000.0 | 2000000000.0 | — | integer | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| autovacuum_max_workers | 1 | 8 | — | integer | sighup | Include | admitted_curated | Max autovacuum worker processes. |
| autovacuum_multixact_freeze_max_age | 10000.0 | 2000000000.0 | — | integer | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| autovacuum_naptime | 1 | 600 | s | integer | sighup | Include | admitted_curated | Time between autovacuum runs. |
| autovacuum_vacuum_cost_delay | -1.0 | 50.0 | ms | real | sighup | Include | admitted_curated | ms delay after cost limit is hit. |
| autovacuum_vacuum_cost_limit | -1 | 2000 | — | integer | sighup | Include | admitted_curated | per-worker autovacuum cost limit. |
| autovacuum_vacuum_insert_scale_factor | 0.01 | 0.5 | — | real | sighup | Include | admitted_curated | fraction of table inserted for vacuum. |
| autovacuum_vacuum_insert_threshold | -1.0 | 2147483647.0 | — | integer | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| autovacuum_vacuum_max_threshold | -1.0 | 2147483647.0 | — | integer | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| autovacuum_vacuum_scale_factor | 0.01 | 0.5 | — | real | sighup | Include | admitted_curated | fraction of table modified for vacuum. |
| autovacuum_vacuum_threshold | 0.0 | 2147483647.0 | — | integer | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| autovacuum_work_mem | -1.0 | 2147483647.0 | kB | integer | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| autovacuum_worker_slots | 1 | 16 | — | integer | postmaster | Include | admitted_curated | Autovacuum worker slot capacity; aligned with practical worker process limits. |
| backend_flush_after | 0 | 256 | 8kB | integer | user | Include | admitted_curated | pages written by backend before OS flush. |
| backslash_quote | — | — | — | enum | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| backtrace_functions | — | — | — | string | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| bgwriter_delay | 10 | 2000 | ms | integer | sighup | Include | admitted_curated | ms between bgwriter rounds. |
| bgwriter_flush_after | 0 | 256 | 8kB | integer | sighup | Include | admitted_curated | pages written by bgwriter before OS flush. |
| bgwriter_lru_maxpages | 0 | 1000 | — | integer | sighup | Include | admitted_curated | pages per bgwriter round. |
| bgwriter_lru_multiplier | 0.0 | 10.0 | — | real | sighup | Include | admitted_curated | multiplier for pages to write based on recent usage. |
| block_size | 8192.0 | 8192.0 | — | integer | internal | Exclude | internal_context | Internal parameters cannot be modified via PostgreSQL runtime/config interfaces. |
| bonjour | — | — | — | bool | postmaster | Exclude | network_discovery | Service discovery/network behavior, not performance tuning. |
| bonjour_name | — | — | — | string | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| bytea_output | — | — | — | enum | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| check_function_bodies | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| checkpoint_completion_target | 0.1 | 0.9 | — | real | sighup | Include | admitted_curated | Spread checkpoint I/O over this fraction of checkpoint_timeout. |
| checkpoint_flush_after | 0 | 256 | 8kB | integer | sighup | Include | admitted_curated | pages written by checkpointer before OS flush. |
| checkpoint_timeout | 30 | 3600 | s | integer | sighup | Include | admitted_curated | Max time between automatic checkpoints. Affects recovery time. |
| checkpoint_warning | 0.0 | 2147483647.0 | s | integer | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| client_connection_check_interval | 0.0 | 2147483647.0 | ms | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| client_encoding | — | — | — | string | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| client_min_messages | — | — | — | enum | user | Exclude | benchmark_validity | Low message-level settings can alter runtime overhead and benchmark comparability. |
| cluster_name | — | — | — | string | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| commit_delay | 0 | 10000 | — | integer | superuser | Include | admitted_curated | microseconds to wait for group commit. |
| commit_siblings | 0 | 20 | — | integer | user | Include | admitted_curated | concurrent active transactions to trigger commit_delay. |
| commit_timestamp_buffers | 0 | 1024 | 8kB | integer | postmaster | Include | admitted_curated | SLRU buffers for commit timestamps (must be multiple of 16). |
| compute_query_id | — | — | — | enum | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| config_file | — | — | — | string | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| constraint_exclusion | — | — | — | enum | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| cpu_index_tuple_cost | 0.0001 | 0.01 | — | real | user | Include | admitted_curated | Cost of processing each index entry. |
| cpu_operator_cost | 0.0001 | 0.01 | — | real | user | Include | admitted_curated | Cost of executing operators/functions. |
| cpu_tuple_cost | 0.001 | 0.1 | — | real | user | Include | admitted_curated | Cost of processing each row. |
| createrole_self_grant | — | — | — | string | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| cursor_tuple_fraction | 0.0 | 1.0 | — | real | user | Include | admitted_curated | planner estimator for cursor retrieval fraction. |
| data_checksums | — | — | — | bool | internal | Exclude | internal_context | Internal parameters cannot be modified via PostgreSQL runtime/config interfaces. |
| data_directory | — | — | — | string | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| data_directory_mode | 0.0 | 511.0 | — | integer | internal | Exclude | internal_context | Internal parameters cannot be modified via PostgreSQL runtime/config interfaces. |
| data_sync_retry | — | — | — | bool | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| deadlock_timeout | 1.0 | 2147483647.0 | ms | integer | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| debug_assertions | — | — | — | bool | internal | Exclude | internal_context | Internal parameters cannot be modified via PostgreSQL runtime/config interfaces. |
| debug_discard_caches | 0.0 | 0.0 | — | integer | superuser | Exclude | debug_only | Debug/developer option; not valid for production workload tuning. |
| debug_io_direct | — | — | — | string | postmaster | Exclude | debug_only | Debug/developer option; can produce high-volume internal debug output and distort benchmarks. |
| debug_logical_replication_streaming | — | — | — | enum | user | Exclude | debug_only | Debug/developer option; not valid for production workload tuning. |
| debug_parallel_query | — | — | — | enum | user | Exclude | debug_only | Debug/developer option; not valid for production workload tuning. |
| debug_pretty_print | — | — | — | bool | user | Exclude | debug_only | Debug/developer option; not valid for production workload tuning. |
| debug_print_parse | — | — | — | bool | user | Exclude | debug_only | Debug/developer option; not valid for production workload tuning. |
| debug_print_plan | — | — | — | bool | user | Exclude | debug_only | Debug/developer option; not valid for production workload tuning. |
| debug_print_rewritten | — | — | — | bool | user | Exclude | debug_only | Debug/developer option; not valid for production workload tuning. |
| default_statistics_target | 10 | 10000 | — | integer | user | Include | admitted_curated | Statistics sample size for ANALYZE. Higher = better plans, slower ANALYZE. |
| default_table_access_method | — | — | — | string | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| default_tablespace | — | — | — | string | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| default_text_search_config | — | — | — | string | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| default_toast_compression | — | — | — | enum | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| default_transaction_deferrable | — | — | — | bool | user | Exclude | semantic_behavior | Changes SQL behavioral semantics rather than performance characteristics. |
| default_transaction_isolation | — | — | — | enum | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| default_transaction_read_only | — | — | — | bool | user | Exclude | semantic_behavior | Changes SQL behavioral semantics rather than performance characteristics. |
| dynamic_library_path | — | — | — | string | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| dynamic_shared_memory_type | — | — | — | enum | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| effective_cache_size | 65536 | 1048576 | 8kB | integer | user | Include | admitted_curated | Planner's OS cache estimate. Doesn't allocate memory, only affects plans. |
| effective_io_concurrency | 0 | 200 | — | integer | user | Include | admitted_curated | Expected concurrent I/O. SSD: 100-200, HDD: 1-2 |
| enable_async_append | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| enable_bitmapscan | — | — | — | bool | user | Include | admitted_curated | Enable bitmap scans. |
| enable_distinct_reordering | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| enable_gathermerge | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| enable_group_by_reordering | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| enable_hashagg | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| enable_hashjoin | — | — | — | bool | user | Include | admitted_curated | Enable hash joins. |
| enable_incremental_sort | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| enable_indexonlyscan | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| enable_indexscan | — | — | — | bool | user | Include | admitted_curated | Enable index scans. Usually leave on. |
| enable_material | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| enable_memoize | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| enable_mergejoin | — | — | — | bool | user | Include | admitted_curated | Enable merge joins. |
| enable_nestloop | — | — | — | bool | user | Include | admitted_curated | Enable nested loop joins. |
| enable_parallel_append | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| enable_parallel_hash | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| enable_partition_pruning | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| enable_partitionwise_aggregate | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| enable_partitionwise_join | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| enable_presorted_aggregate | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| enable_self_join_elimination | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| enable_seqscan | — | — | — | bool | user | Include | admitted_curated | Enable sequential scans. Usually leave on. |
| enable_sort | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| enable_tidscan | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| escape_string_warning | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| event_source | — | — | — | string | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| event_triggers | — | — | — | bool | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| exit_on_error | — | — | — | bool | user | Exclude | stability | Alters error/crash behavior and destabilizes tuning loop execution. |
| extension_control_path | — | — | — | string | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| external_pid_file | — | — | — | string | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| extra_float_digits | -15.0 | 3.0 | — | integer | user | Exclude | semantic_behavior | Client display precision; semantic client behavior, not server performance. |
| file_copy_method | — | — | — | enum | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| file_extend_method | — | — | — | enum | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| from_collapse_limit | 1.0 | 2147483647.0 | — | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| fsync | — | — | — | bool | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| full_page_writes | — | — | — | bool | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| geqo | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| geqo_effort | 1 | 10 | — | integer | user | Include | admitted_curated | GEQO effort level. |
| geqo_generations | 0.0 | 2147483647.0 | — | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| geqo_pool_size | 0.0 | 2147483647.0 | — | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| geqo_seed | 0.0 | 1.0 | — | real | user | Include | admitted_curated | GEQO random seed. |
| geqo_selection_bias | 1.5 | 2.0 | — | real | user | Include | admitted_curated | GEQO selection bias. |
| geqo_threshold | 2.0 | 2147483647.0 | — | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| gin_fuzzy_search_limit | 0.0 | 2147483647.0 | — | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| gin_pending_list_limit | 64.0 | 2147483647.0 | kB | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| gss_accept_delegation | — | — | — | bool | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| hash_mem_multiplier | 1.0 | 8.0 | — | real | user | Include | admitted_curated | Hash operation memory multiplier; bounded to avoid aggressive memory oversubscription. |
| hba_file | — | — | — | string | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| hot_standby | — | — | — | bool | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| hot_standby_feedback | — | — | — | bool | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| huge_page_size | 0.0 | 2147483647.0 | kB | integer | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| huge_pages | — | — | — | enum | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| huge_pages_status | — | — | — | enum | internal | Exclude | internal_context | Internal parameters cannot be modified via PostgreSQL runtime/config interfaces. |
| icu_validation_level | — | — | — | enum | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| ident_file | — | — | — | string | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| idle_in_transaction_session_timeout | 0.0 | 2147483647.0 | ms | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| idle_replication_slot_timeout | 0.0 | 2147483647.0 | s | integer | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| idle_session_timeout | 0.0 | 2147483647.0 | ms | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| ignore_checksum_failure | — | — | — | bool | superuser | Exclude | data_integrity | Dangerous data-integrity bypass option; excluded from autotuning. |
| ignore_invalid_pages | — | — | — | bool | postmaster | Exclude | data_integrity | Dangerous data-integrity bypass option; excluded from autotuning. |
| ignore_system_indexes | — | — | — | bool | backend | Exclude | data_integrity | Dangerous data-integrity bypass option; excluded from autotuning. |
| in_hot_standby | — | — | — | bool | internal | Exclude | internal_context | Internal parameters cannot be modified via PostgreSQL runtime/config interfaces. |
| integer_datetimes | — | — | — | bool | internal | Exclude | internal_context | Internal parameters cannot be modified via PostgreSQL runtime/config interfaces. |
| io_combine_limit | 1 | 128 | 8kB | integer | user | Include | admitted_curated | I/O combine limit. |
| io_max_combine_limit | 1 | 128 | 8kB | integer | postmaster | Include | admitted_curated | Max I/O combine limit. |
| io_max_concurrency | -1 | 256 | — | integer | postmaster | Include | admitted_curated | Max I/O concurrency. |
| io_method | — | — | — | enum | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| io_workers | 1 | 16 | — | integer | sighup | Include | admitted_curated | I/O worker count (PG17+); bounded to practical CPU-core-aligned range. |
| jit | — | — | — | bool | user | Exclude | stability | Known instability for benchmark workloads in this environment. |
| jit_above_cost | -1.0 | 1.79769e+308 | — | real | user | Exclude | stability | Known instability for benchmark workloads in this environment. |
| jit_debugging_support | — | — | — | bool | superuser-backend | Exclude | stability | Known instability for benchmark workloads in this environment. |
| jit_dump_bitcode | — | — | — | bool | superuser | Exclude | stability | Known instability for benchmark workloads in this environment. |
| jit_expressions | — | — | — | bool | user | Exclude | stability | Known instability for benchmark workloads in this environment. |
| jit_inline_above_cost | -1.0 | 1.79769e+308 | — | real | user | Exclude | stability | Known instability for benchmark workloads in this environment. |
| jit_optimize_above_cost | -1.0 | 1.79769e+308 | — | real | user | Exclude | stability | Known instability for benchmark workloads in this environment. |
| jit_profiling_support | — | — | — | bool | superuser-backend | Exclude | stability | Known instability for benchmark workloads in this environment. |
| jit_provider | — | — | — | string | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| jit_tuple_deforming | — | — | — | bool | user | Exclude | stability | Known instability for benchmark workloads in this environment. |
| join_collapse_limit | 1.0 | 2147483647.0 | — | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| krb_caseins_users | — | — | — | bool | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| krb_server_keyfile | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| lc_messages | — | — | — | string | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| lc_monetary | — | — | — | string | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| lc_numeric | — | — | — | string | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| lc_time | — | — | — | string | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| listen_addresses | — | — | — | string | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| lo_compat_privileges | — | — | — | bool | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| local_preload_libraries | — | — | — | string | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| lock_timeout | 0.0 | 2147483647.0 | ms | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_autovacuum_min_duration | -1.0 | 2147483647.0 | ms | integer | sighup | Exclude | benchmark_validity | Autovacuum logging threshold; does not affect actual maintenance pacing. |
| log_checkpoints | — | — | — | bool | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_connections | — | — | — | string | superuser-backend | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_destination | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_directory | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_disconnections | — | — | — | bool | superuser-backend | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_duration | — | — | — | bool | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_error_verbosity | — | — | — | enum | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_executor_stats | — | — | — | bool | superuser | Exclude | mutual_exclusion | Mutually exclusive with statement/parser/planner stats and can cause config errors. |
| log_file_mode | 0.0 | 511.0 | — | integer | sighup | Exclude | format_readback | Octal-mode parameter with unreliable readback/validation in this pipeline. |
| log_filename | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_hostname | — | — | — | bool | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_line_prefix | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_lock_failures | — | — | — | bool | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_lock_waits | — | — | — | bool | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_min_duration_sample | -1.0 | 2147483647.0 | ms | integer | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_min_duration_statement | -1.0 | 2147483647.0 | ms | integer | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_min_error_statement | — | — | — | enum | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_min_messages | — | — | — | enum | superuser | Exclude | benchmark_validity | Low log-level settings (debug*) can flood logs and materially skew benchmark timing. |
| log_parameter_max_length | -1.0 | 1073741823.0 | B | integer | superuser | Exclude | benchmark_validity | Log truncation limit; does not affect workload performance. |
| log_parameter_max_length_on_error | -1.0 | 1073741823.0 | B | integer | user | Exclude | benchmark_validity | Error log truncation; does not affect workload performance. |
| log_parser_stats | — | — | — | bool | superuser | Exclude | mutual_exclusion | Mutually exclusive with statement/planner/executor stats and can cause config errors. |
| log_planner_stats | — | — | — | bool | superuser | Exclude | mutual_exclusion | Mutually exclusive with statement/parser/executor stats and can cause config errors. |
| log_recovery_conflict_waits | — | — | — | bool | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_replication_commands | — | — | — | bool | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_rotation_age | 0.0 | 35791394.0 | min | integer | sighup | Exclude | benchmark_validity | Log file rotation schedule; does not affect workload performance. |
| log_rotation_size | 0.0 | 2147483647.0 | kB | integer | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_startup_progress_interval | 0.0 | 2147483647.0 | ms | integer | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_statement | — | — | — | enum | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_statement_sample_rate | 0.0 | 1.0 | — | real | superuser | Exclude | benchmark_validity | Fraction of statements logged; can skew benchmark measurements. |
| log_statement_stats | — | — | — | bool | superuser | Exclude | mutual_exclusion | Mutually exclusive with parser/planner/executor stats and can cause config errors. |
| log_temp_files | -1.0 | 2147483647.0 | kB | integer | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_timezone | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| log_transaction_sample_rate | 0.0 | 1.0 | — | real | superuser | Exclude | benchmark_validity | Fraction of transactions logged; can skew benchmark measurements. |
| log_truncate_on_rotation | — | — | — | bool | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| logging_collector | — | — | — | bool | postmaster | Exclude | logging_pipeline_dependency | Redirects logs and interferes with restart/log-driven orchestration behavior. |
| logical_decoding_work_mem | 64.0 | 2147483647.0 | kB | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| maintenance_io_concurrency | 0 | 200 | — | integer | user | Include | admitted_curated | Maintenance I/O concurrency for VACUUM/CREATE INDEX, similar semantics to effective_io_concurrency. |
| maintenance_work_mem | 65536 | 262144 | kB | integer | user | Include | admitted_curated | For VACUUM, CREATE INDEX. Can be larger than work_mem. |
| max_active_replication_origins | 0.0 | 262143.0 | — | integer | postmaster | Exclude | benchmark_validity | Replication origin slots; not relevant as benchmarks don't use replication fanout. |
| max_connections | 50 | 200 | — | integer | postmaster | Include | admitted_curated | Max concurrent connections. Requires restart. High values increase memory. |
| max_files_per_process | 64.0 | 2147483647.0 | — | integer | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| max_function_args | 100.0 | 100.0 | — | integer | internal | Exclude | internal_context | Internal parameters cannot be modified via PostgreSQL runtime/config interfaces. |
| max_identifier_length | 63.0 | 63.0 | — | integer | internal | Exclude | internal_context | Internal parameters cannot be modified via PostgreSQL runtime/config interfaces. |
| max_index_keys | 32.0 | 32.0 | — | integer | internal | Exclude | internal_context | Internal parameters cannot be modified via PostgreSQL runtime/config interfaces. |
| max_locks_per_transaction | 10.0 | 2147483647.0 | — | integer | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| max_logical_replication_workers | 0 | 10 | — | integer | postmaster | Include | admitted_curated | Logical replication worker cap; bounded for stability in non-replication-centric tuning runs. |
| max_notify_queue_pages | 64.0 | 2147483647.0 | — | integer | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| max_parallel_apply_workers_per_subscription | 0 | 8 | — | integer | sighup | Include | admitted_curated | Logical replication apply parallelism; low-impact for non-replication benchmarks but safely tunable. |
| max_parallel_maintenance_workers | 0 | 4 | — | integer | user | Include | admitted_curated | Max parallel workers for maintenance (CREATE INDEX, VACUUM). |
| max_parallel_workers | 0 | 16 | — | integer | user | Include | admitted_curated | Max parallel workers system-wide. Must be <= max_worker_processes. |
| max_parallel_workers_per_gather | 0 | 4 | — | integer | user | Include | admitted_curated | Parallelism for analytical queries. Limited by CPU cores. |
| max_pred_locks_per_page | 0.0 | 2147483647.0 | — | integer | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| max_pred_locks_per_relation | -2147483648.0 | 2147483647.0 | — | integer | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| max_pred_locks_per_transaction | 10.0 | 2147483647.0 | — | integer | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| max_prepared_transactions | 0.0 | 262143.0 | — | integer | postmaster | Exclude | benchmark_validity | 2PC transaction slots; not used by standard OLTP/OLAP benchmarks. |
| max_replication_slots | 0.0 | 262143.0 | — | integer | postmaster | Exclude | benchmark_validity | Replication slot cap; not relevant as benchmarks don't use replication fanout. |
| max_slot_wal_keep_size | -1.0 | 2147483647.0 | MB | integer | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| max_stack_depth | 100.0 | 2147483647.0 | kB | integer | superuser | Exclude | os_alignment | Depends on OS stack limits and can crash backend when misaligned. |
| max_standby_archive_delay | -1.0 | 2147483647.0 | ms | integer | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| max_standby_streaming_delay | -1.0 | 2147483647.0 | ms | integer | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| max_sync_workers_per_subscription | 0.0 | 262143.0 | — | integer | sighup | Exclude | benchmark_validity | Sync parallelism for replication; not relevant for standalone benchmarks. |
| max_wal_senders | 0 | 10 | — | integer | postmaster | Include | admitted_curated | WAL sender process cap; safely bounded for environments without heavy replication fanout. |
| max_wal_size | 80 | 10240 | MB | integer | sighup | Include | admitted_curated | Max WAL size before forced checkpoint. |
| max_worker_processes | 4 | 16 | — | integer | postmaster | Include | admitted_curated | Max background workers. Requires restart. Must be >= max_parallel_workers. |
| md5_password_warnings | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| min_dynamic_shared_memory | 0.0 | 2147483647.0 | MB | integer | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| min_parallel_index_scan_size | 0 | 16384 | 8kB | integer | user | Include | admitted_curated | pages required to trigger parallel index scan. |
| min_parallel_table_scan_size | 0 | 65536 | 8kB | integer | user | Include | admitted_curated | pages required to trigger parallel table scan. |
| min_wal_size | 80 | 2048 | MB | integer | sighup | Include | admitted_curated | Minimum WAL size to keep. |
| multixact_member_buffers | 4 | 64 | 8kB | integer | postmaster | Include | admitted_curated | MultiXact member SLRU buffers (pages); bounded for stable memory usage. |
| multixact_offset_buffers | 4 | 64 | 8kB | integer | postmaster | Include | admitted_curated | MultiXact offset SLRU buffers (pages); bounded for stable memory usage. |
| notify_buffers | 4 | 64 | 8kB | integer | postmaster | Include | admitted_curated | LISTEN/NOTIFY buffers (pages); bounded for safe memory footprint. |
| num_os_semaphores | 0.0 | 2147483647.0 | — | integer | internal | Exclude | internal_context | Internal parameters cannot be modified via PostgreSQL runtime/config interfaces. |
| oauth_validator_libraries | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| parallel_leader_participation | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| parallel_setup_cost | 1.0 | 10000.0 | — | real | user | Include | admitted_curated | Cost of starting parallel workers. |
| parallel_tuple_cost | 0.0001 | 10.0 | — | real | user | Include | admitted_curated | Cost of transferring tuples between workers. |
| password_encryption | — | — | — | enum | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| plan_cache_mode | — | — | — | enum | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| port | 1.0 | 65535.0 | — | integer | postmaster | Exclude | network_binding | Instance network binding parameter; not workload performance tuning. |
| post_auth_delay | 0.0 | 2147.0 | s | integer | backend | Exclude | benchmark_validity | Artificially delays post-auth processing and distorts benchmark latency. |
| pre_auth_delay | 0.0 | 60.0 | s | integer | sighup | Exclude | benchmark_validity | Artificially delays connection auth and distorts benchmark latency. |
| primary_conninfo | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| primary_slot_name | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| quote_all_identifiers | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| random_page_cost | 0.1 | 4.0 | — | real | user | Include | admitted_curated | Critical for index vs seqscan decisions. SSD: 1.0-1.5, HDD: 3.0-4.0 |
| recovery_end_command | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| recovery_init_sync_method | — | — | — | enum | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| recovery_min_apply_delay | 0.0 | 2147483647.0 | ms | integer | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| recovery_prefetch | — | — | — | enum | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| recovery_target | — | — | — | string | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| recovery_target_action | — | — | — | enum | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| recovery_target_inclusive | — | — | — | bool | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| recovery_target_lsn | — | — | — | string | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| recovery_target_name | — | — | — | string | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| recovery_target_time | — | — | — | string | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| recovery_target_timeline | — | — | — | string | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| recovery_target_xid | — | — | — | string | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| recursive_worktable_factor | 0.1 | 100.0 | — | real | user | Include | admitted_curated | planner multiplier for recursive queries. |
| remove_temp_files_after_crash | — | — | — | bool | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| reserved_connections | 0 | 5 | — | integer | postmaster | Include | admitted_curated | Reserved connection slots; conservative bounds preserve user connection capacity. |
| restart_after_crash | — | — | — | bool | sighup | Exclude | stability | Alters crash recovery behavior and destabilizes tuning loop execution. |
| restore_command | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| restrict_nonsystem_relation_kind | — | — | — | string | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| row_security | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| scram_iterations | 1.0 | 2147483647.0 | — | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| search_path | — | — | — | string | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| segment_size | 131072.0 | 131072.0 | 8kB | integer | internal | Exclude | internal_context | Internal parameters cannot be modified via PostgreSQL runtime/config interfaces. |
| send_abort_for_crash | — | — | — | bool | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| send_abort_for_kill | — | — | — | bool | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| seq_page_cost | 0.1 | 2.0 | — | real | user | Include | admitted_curated | Cost of sequential page fetch. Usually kept at 1.0 as baseline. |
| serializable_buffers | 16 | 1024 | 8kB | integer | postmaster | Include | admitted_curated | SLRU buffers for serializable transactions (must be multiple of 16). |
| server_encoding | — | — | — | string | internal | Exclude | internal_context | Internal parameters cannot be modified via PostgreSQL runtime/config interfaces. |
| server_version | — | — | — | string | internal | Exclude | internal_context | Internal parameters cannot be modified via PostgreSQL runtime/config interfaces. |
| server_version_num | 180003.0 | 180003.0 | — | integer | internal | Exclude | internal_context | Internal parameters cannot be modified via PostgreSQL runtime/config interfaces. |
| session_preload_libraries | — | — | — | string | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| session_replication_role | — | — | — | enum | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| shared_buffers | 16384 | 131072 | 8kB | integer | postmaster | Include | admitted_curated | Most impactful knob. Log scale because doubling matters more than addition. |
| shared_memory_size | 0.0 | 2147483647.0 | MB | integer | internal | Exclude | internal_context | Internal parameters cannot be modified via PostgreSQL runtime/config interfaces. |
| shared_memory_size_in_huge_pages | -1.0 | 2147483647.0 | — | integer | internal | Exclude | internal_context | Internal parameters cannot be modified via PostgreSQL runtime/config interfaces. |
| shared_memory_type | — | — | — | enum | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| shared_preload_libraries | — | — | — | string | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| ssl | — | — | — | bool | sighup | Exclude | security_transport | Security transport policy parameter, excluded from autotuning scope. |
| ssl_ca_file | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| ssl_cert_file | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| ssl_ciphers | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| ssl_crl_dir | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| ssl_crl_file | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| ssl_dh_params_file | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| ssl_groups | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| ssl_key_file | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| ssl_library | — | — | — | string | internal | Exclude | internal_context | Internal parameters cannot be modified via PostgreSQL runtime/config interfaces. |
| ssl_max_protocol_version | — | — | — | enum | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| ssl_min_protocol_version | — | — | — | enum | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| ssl_passphrase_command | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| ssl_passphrase_command_supports_reload | — | — | — | bool | sighup | Exclude | security_transport | Security transport policy parameter, excluded from autotuning scope. |
| ssl_prefer_server_ciphers | — | — | — | bool | sighup | Exclude | security_transport | Security transport policy parameter, excluded from autotuning scope. |
| ssl_tls13_ciphers | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| standard_conforming_strings | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| statement_timeout | 0.0 | 2147483647.0 | ms | integer | user | Exclude | benchmark_validity | Can cancel post-workload maintenance and produce false failure signals. |
| stats_fetch_consistency | — | — | — | enum | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| subtransaction_buffers | 0 | 1024 | 8kB | integer | postmaster | Include | admitted_curated | SLRU buffers for subtransactions (must be multiple of 16). |
| summarize_wal | — | — | — | bool | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| superuser_reserved_connections | 0 | 5 | — | integer | postmaster | Include | admitted_curated | Reserved connection slots for superusers; tuned conservatively to prevent starvation. |
| sync_replication_slots | — | — | — | bool | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| synchronize_seqscans | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| synchronized_standby_slots | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| synchronous_commit | — | — | — | enum | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| synchronous_standby_names | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| syslog_facility | — | — | — | enum | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| syslog_ident | — | — | — | string | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| syslog_sequence_numbers | — | — | — | bool | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| syslog_split_messages | — | — | — | bool | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| tcp_keepalives_count | 0.0 | 2147483647.0 | — | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| tcp_keepalives_idle | 0.0 | 2147483647.0 | s | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| tcp_keepalives_interval | 0.0 | 2147483647.0 | s | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| tcp_user_timeout | 0.0 | 2147483647.0 | ms | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| temp_buffers | 1024 | 4096 | 8kB | integer | user | Include | admitted_curated | Temp buffer size per session. |
| temp_file_limit | -1.0 | 2147483647.0 | kB | integer | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| temp_tablespaces | — | — | — | string | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| timezone_abbreviations | — | — | — | string | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| trace_connection_negotiation | — | — | — | bool | postmaster | Exclude | debug_only | Debug/developer trace option; can generate noisy logs and perturb timing. |
| trace_notify | — | — | — | bool | user | Exclude | debug_only | Debug/developer trace option; can generate noisy logs and perturb timing. |
| trace_sort | — | — | — | bool | user | Exclude | debug_only | Debug/developer trace option; can generate noisy logs and perturb timing. |
| track_activities | — | — | — | bool | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| track_activity_query_size | 100.0 | 1048576.0 | B | integer | postmaster | Exclude | benchmark_validity | pg_stat_activity query truncation; memory overhead is trivial and unmeasured. |
| track_commit_timestamp | — | — | — | bool | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| track_cost_delay_timing | — | — | — | bool | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| track_counts | — | — | — | bool | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| track_functions | — | — | — | enum | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| track_io_timing | — | — | — | bool | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| track_wal_io_timing | — | — | — | bool | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| transaction_buffers | 0 | 1024 | 8kB | integer | postmaster | Include | admitted_curated | SLRU buffers for transactions (must be multiple of 16). |
| transaction_deferrable | — | — | — | bool | user | Exclude | session_semantics | Transaction/session semantic toggle, not a stable global performance knob. |
| transaction_isolation | — | — | — | enum | user | Exclude | session_semantics | Transaction/session semantic toggle, not a stable global performance knob. |
| transaction_read_only | — | — | — | bool | user | Exclude | session_semantics | Transaction/session semantic toggle, not a stable global performance knob. |
| transaction_timeout | 0.0 | 2147483647.0 | ms | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| transform_null_equals | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| unix_socket_directories | — | — | — | string | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| unix_socket_group | — | — | — | string | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| unix_socket_permissions | 0.0 | 511.0 | — | integer | postmaster | Exclude | format_readback | Octal-mode parameter with unreliable readback/validation in this pipeline. |
| update_process_title | — | — | — | bool | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| vacuum_buffer_usage_limit | 128 | 2048 | kB | integer | user | Include | admitted_curated | buffer usage limit for vacuum (pages). |
| vacuum_cost_delay | 0.0 | 100.0 | ms | real | user | Exclude | maintenance_only | Manual VACUUM cost delay affects only post-workload unmeasured maintenance. |
| vacuum_cost_limit | 1 | 2000 | — | integer | user | Include | admitted_curated | aggregate cost cap for vacuum. |
| vacuum_cost_page_dirty | 0 | 1000 | — | integer | user | Include | admitted_curated | cost of dirtying a page. |
| vacuum_cost_page_hit | 0 | 100 | — | integer | user | Include | admitted_curated | cost of vacuuming a buffer-hit page. |
| vacuum_cost_page_miss | 0 | 1000 | — | integer | user | Include | admitted_curated | cost of vacuuming a disk-read page. |
| vacuum_failsafe_age | 0.0 | 2100000000.0 | — | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| vacuum_freeze_min_age | 0 | 100000000 | — | integer | user | Include | admitted_curated | minimum age before freezing tuples. |
| vacuum_freeze_table_age | 0.0 | 2000000000.0 | — | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| vacuum_max_eager_freeze_failure_rate | 0.0 | 1.0 | — | real | user | Include | admitted_native_bounded | Safe bounded native PostgreSQL domain (`real`). Included as an extensive-tier candidate. |
| vacuum_multixact_failsafe_age | 0.0 | 2100000000.0 | — | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| vacuum_multixact_freeze_min_age | 0 | 100000000 | — | integer | user | Include | admitted_curated | minimum age before freezing multixacts. |
| vacuum_multixact_freeze_table_age | 0.0 | 2000000000.0 | — | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| vacuum_truncate | — | — | — | bool | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| wal_block_size | 8192.0 | 8192.0 | — | integer | internal | Exclude | internal_context | Internal parameters cannot be modified via PostgreSQL runtime/config interfaces. |
| wal_buffers | 64 | 2048 | 8kB | integer | postmaster | Include | admitted_curated | WAL buffer size. Default -1 means auto (1/32 of shared_buffers). |
| wal_compression | — | — | — | enum | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| wal_consistency_checking | — | — | — | string | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| wal_decode_buffer_size | 65536.0 | 1073741823.0 | B | integer | postmaster | Exclude | benchmark_validity | Recovery decode buffer; benchmarks measure normal execution, not crash recovery. |
| wal_init_zero | — | — | — | bool | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| wal_keep_size | 0.0 | 2147483647.0 | MB | integer | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| wal_level | — | — | — | enum | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| wal_log_hints | — | — | — | bool | postmaster | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| wal_receiver_create_temp_slot | — | — | — | bool | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| wal_receiver_status_interval | 0.0 | 2147483.0 | s | integer | sighup | Exclude | benchmark_validity | Replication heartbeat interval; not relevant for standalone benchmarks. |
| wal_receiver_timeout | 0.0 | 2147483647.0 | ms | integer | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| wal_recycle | — | — | — | bool | superuser | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| wal_retrieve_retry_interval | 1.0 | 2147483647.0 | ms | integer | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| wal_segment_size | 1048576.0 | 1073741824.0 | B | integer | internal | Exclude | internal_context | Internal parameters cannot be modified via PostgreSQL runtime/config interfaces. |
| wal_sender_timeout | 0.0 | 2147483647.0 | ms | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| wal_skip_threshold | 0.0 | 2147483647.0 | kB | integer | user | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| wal_summary_keep_time | 0.0 | 35791394.0 | min | integer | sighup | Exclude | benchmark_validity | WAL summary retention; does not affect workload performance. |
| wal_sync_method | — | — | — | enum | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| wal_writer_delay | 1 | 5000 | ms | integer | sighup | Include | admitted_curated | ms between WAL flushes. |
| wal_writer_flush_after | 0.0 | 2147483647.0 | 8kB | integer | sighup | Exclude | uncurated_intmax_sentinel | Native max value is INT_MAX-sentinel/unbounded; requires curated practical bounds before safe autotuning admission. |
| work_mem | 4096 | 65536 | kB | integer | user | Include | admitted_curated | Per-operation memory. Total can be work_mem * connections * operations_per_query |
| xmlbinary | — | — | — | enum | user | Exclude | semantic_behavior | XML encoding toggle; semantic client behavior, not server performance. |
| xmloption | — | — | — | enum | user | Exclude | semantic_behavior | XML default handling; semantic client behavior, not server performance. |
| zero_damaged_pages | — | — | — | bool | superuser | Exclude | data_integrity | Dangerous data-integrity bypass option; excluded from autotuning. |
