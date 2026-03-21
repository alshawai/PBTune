"""Shared policy engine for PostgreSQL autotuning knob admission and exclusion."""

from typing import Dict

import pandas as pd

from src.knobs.knob_metadata import KNOB_TUNING_METADATA


AUTOTUNING_SOURCE_EXCLUSIONS: Dict[str, tuple[str, str]] = {
    "vacuum_cost_delay": (
        "maintenance_only",
        "Manual VACUUM cost delay affects only post-workload unmeasured maintenance.",
    ),
    "max_stack_depth": (
        "os_alignment",
        "Depends on OS stack limits and can crash backend when misaligned.",
    ),
    "allow_alter_system": (
        "applicator_dependency",
        "Tuner applies settings via ALTER SYSTEM; disabling this breaks configuration application.",
    ),
    "bonjour": (
        "network_discovery",
        "Service discovery/network behavior, not performance tuning.",
    ),
    "transaction_deferrable": (
        "session_semantics",
        "Transaction/session semantic toggle, not a stable global performance knob.",
    ),
    "transaction_read_only": (
        "session_semantics",
        "Transaction/session semantic toggle, not a stable global performance knob.",
    ),
    "transaction_isolation": (
        "session_semantics",
        "Transaction/session semantic toggle, not a stable global performance knob.",
    ),
    "port": (
        "network_binding",
        "Instance network binding parameter; not workload performance tuning.",
    ),
    "ssl": (
        "security_transport",
        "Security transport policy parameter, excluded from autotuning scope.",
    ),
    "ssl_passphrase_command_supports_reload": (
        "security_transport",
        "Security transport policy parameter, excluded from autotuning scope.",
    ),
    "ssl_prefer_server_ciphers": (
        "security_transport",
        "Security transport policy parameter, excluded from autotuning scope.",
    ),
    "pre_auth_delay": (
        "benchmark_validity",
        "Artificially delays connection auth and distorts benchmark latency.",
    ),
    "post_auth_delay": (
        "benchmark_validity",
        "Artificially delays post-auth processing and distorts benchmark latency.",
    ),
    "log_statement_stats": (
        "mutual_exclusion",
        "Mutually exclusive with parser/planner/executor stats and can cause config errors.",
    ),
    "log_parser_stats": (
        "mutual_exclusion",
        "Mutually exclusive with statement/planner/executor stats and can cause config errors.",
    ),
    "log_planner_stats": (
        "mutual_exclusion",
        "Mutually exclusive with statement/parser/executor stats and can cause config errors.",
    ),
    "log_executor_stats": (
        "mutual_exclusion",
        "Mutually exclusive with statement/parser/planner stats and can cause config errors.",
    ),
    "log_file_mode": (
        "format_readback",
        "Octal-mode parameter with unreliable readback/validation in this pipeline.",
    ),
    "unix_socket_permissions": (
        "format_readback",
        "Octal-mode parameter with unreliable readback/validation in this pipeline.",
    ),
    "logging_collector": (
        "logging_pipeline_dependency",
        "Redirects logs and interferes with restart/log-driven orchestration behavior.",
    ),
    "jit": (
        "stability",
        "Known instability for benchmark workloads in this environment.",
    ),
    "jit_above_cost": (
        "stability",
        "Known instability for benchmark workloads in this environment.",
    ),
    "jit_inline_above_cost": (
        "stability",
        "Known instability for benchmark workloads in this environment.",
    ),
    "jit_optimize_above_cost": (
        "stability",
        "Known instability for benchmark workloads in this environment.",
    ),
    "jit_debugging_support": (
        "stability",
        "Known instability for benchmark workloads in this environment.",
    ),
    "jit_dump_bitcode": (
        "stability",
        "Known instability for benchmark workloads in this environment.",
    ),
    "jit_expressions": (
        "stability",
        "Known instability for benchmark workloads in this environment.",
    ),
    "jit_profiling_support": (
        "stability",
        "Known instability for benchmark workloads in this environment.",
    ),
    "jit_tuple_deforming": (
        "stability",
        "Known instability for benchmark workloads in this environment.",
    ),
    "statement_timeout": (
        "benchmark_validity",
        "Can cancel post-workload maintenance and produce false failure signals.",
    ),
    "zero_damaged_pages": (
        "data_integrity",
        "Dangerous data-integrity bypass option; excluded from autotuning.",
    ),
    "ignore_checksum_failure": (
        "data_integrity",
        "Dangerous data-integrity bypass option; excluded from autotuning.",
    ),
    "ignore_invalid_pages": (
        "data_integrity",
        "Dangerous data-integrity bypass option; excluded from autotuning.",
    ),
    "ignore_system_indexes": (
        "data_integrity",
        "Dangerous data-integrity bypass option; excluded from autotuning.",
    ),
    "post_column_optimize": (
        "data_integrity",
        "Developer/internal behavior toggle not suitable for autotuning.",
    ),
    "default_transaction_read_only": (
        "semantic_behavior",
        "Changes SQL behavioral semantics rather than performance characteristics.",
    ),
    "default_transaction_deferrable": (
        "semantic_behavior",
        "Changes SQL behavioral semantics rather than performance characteristics.",
    ),
    "exit_on_error": (
        "stability",
        "Alters error/crash behavior and destabilizes tuning loop execution.",
    ),
    "restart_after_crash": (
        "stability",
        "Alters crash recovery behavior and destabilizes tuning loop execution.",
    ),
    "debug_discard_caches": (
        "debug_only",
        "Debug/developer option; not valid for production workload tuning.",
    ),
    "debug_io_direct": (
        "debug_only",
        "Debug/developer option; can produce high-volume internal "
        "debug output and distort benchmarks.",
    ),
    "debug_parallel_query": (
        "debug_only",
        "Debug/developer option; not valid for production workload tuning.",
    ),
    "debug_logical_replication_streaming": (
        "debug_only",
        "Debug/developer option; not valid for production workload tuning.",
    ),
    "debug_print_parse": (
        "debug_only",
        "Debug/developer option; not valid for production workload tuning.",
    ),
    "debug_print_plan": (
        "debug_only",
        "Debug/developer option; not valid for production workload tuning.",
    ),
    "debug_print_rewritten": (
        "debug_only",
        "Debug/developer option; not valid for production workload tuning.",
    ),
    "debug_pretty_print": (
        "debug_only",
        "Debug/developer option; not valid for production workload tuning.",
    ),
    "trace_connection_negotiation": (
        "debug_only",
        "Debug/developer trace option; can generate noisy logs and perturb timing.",
    ),
    "trace_notify": (
        "debug_only",
        "Debug/developer trace option; can generate noisy logs and perturb timing.",
    ),
    "trace_sort": (
        "debug_only",
        "Debug/developer trace option; can generate noisy logs and perturb timing.",
    ),
    "log_min_messages": (
        "benchmark_validity",
        "Low log-level settings (debug*) can flood logs and materially skew benchmark timing.",
    ),
    "client_min_messages": (
        "benchmark_validity",
        "Low message-level settings can alter runtime overhead and benchmark comparability.",
    ),
    "allow_system_table_mods": (
        "system_catalog_safety",
        "System catalog mutation option; excluded from autotuning safety policy.",
    ),
    "allow_in_place_tablespaces": (
        "storage_safety",
        "Storage/path behavior option; excluded from autotuning safety policy.",
    ),
    "log_rotation_age": (
        "benchmark_validity",
        "Log file rotation schedule; does not affect workload performance.",
    ),
    "log_parameter_max_length": (
        "benchmark_validity",
        "Log truncation limit; does not affect workload performance.",
    ),
    "log_parameter_max_length_on_error": (
        "benchmark_validity",
        "Error log truncation; does not affect workload performance.",
    ),
    "log_statement_sample_rate": (
        "benchmark_validity",
        "Fraction of statements logged; can skew benchmark measurements.",
    ),
    "log_transaction_sample_rate": (
        "benchmark_validity",
        "Fraction of transactions logged; can skew benchmark measurements.",
    ),
    "log_autovacuum_min_duration": (
        "benchmark_validity",
        "Autovacuum logging threshold; does not affect actual maintenance pacing.",
    ),
    "track_activity_query_size": (
        "benchmark_validity",
        "pg_stat_activity query truncation; memory overhead is trivial and unmeasured.",
    ),
    "authentication_timeout": (
        "benchmark_validity",
        "Login timeout setting; not a workload performance tuning knob.",
    ),
    "wal_receiver_status_interval": (
        "benchmark_validity",
        "Replication heartbeat interval; not relevant for standalone benchmarks.",
    ),
    "wal_summary_keep_time": (
        "benchmark_validity",
        "WAL summary retention; does not affect workload performance.",
    ),
    "max_active_replication_origins": (
        "benchmark_validity",
        "Replication origin slots; not relevant as benchmarks don't use replication fanout.",
    ),
    "max_replication_slots": (
        "benchmark_validity",
        "Replication slot cap; not relevant as benchmarks don't use replication fanout.",
    ),
    "max_sync_workers_per_subscription": (
        "benchmark_validity",
        "Sync parallelism for replication; not relevant for standalone benchmarks.",
    ),
    "max_prepared_transactions": (
        "benchmark_validity",
        "2PC transaction slots; not used by standard OLTP/OLAP benchmarks.",
    ),
    "archive_timeout": (
        "benchmark_validity",
        "WAL archiving timer; not relevant as benchmarks don't measure archiving latency.",
    ),
    "extra_float_digits": (
        "semantic_behavior",
        "Client display precision; semantic client behavior, not server performance.",
    ),
    "xmlbinary": (
        "semantic_behavior",
        "XML encoding toggle; semantic client behavior, not server performance.",
    ),
    "xmloption": (
        "semantic_behavior",
        "XML default handling; semantic client behavior, not server performance.",
    ),
    "wal_decode_buffer_size": (
        "benchmark_validity",
        "Recovery decode buffer; benchmarks measure normal execution, not crash recovery.",
    ),
}

SOURCE_POLICY_COLUMNS = (
    "eligible_for_autotuning",
    "autotuning_exclusion_reason_code",
    "autotuning_exclusion_reason_detail",
)
SUPPORTED_AUTOTUNING_VARTYPES = frozenset({"integer", "real", "bool", "enum"})
INT_MAX_SENTINEL = 2_000_000_000


def annotate_autotuning_policy(df: pd.DataFrame) -> pd.DataFrame:
    """Annotate source-stage autotuning eligibility and exclusion reasons."""
    annotated = df.copy()

    annotated["eligible_for_autotuning"] = True
    annotated["autotuning_exclusion_reason_code"] = ""
    annotated["autotuning_exclusion_reason_detail"] = ""

    internal_mask = annotated["context"] == "internal"
    annotated.loc[internal_mask, "eligible_for_autotuning"] = False
    annotated.loc[internal_mask, "autotuning_exclusion_reason_code"] = "internal_context"
    annotated.loc[
        internal_mask,
        "autotuning_exclusion_reason_detail",
    ] = "Internal parameters cannot be modified via PostgreSQL runtime/config interfaces."

    for knob_name, (reason_code, reason_detail) in AUTOTUNING_SOURCE_EXCLUSIONS.items():
        knob_mask = annotated["name"] == knob_name
        if knob_mask.any():  # type: ignore
            annotated.loc[knob_mask, "eligible_for_autotuning"] = False
            annotated.loc[knob_mask, "autotuning_exclusion_reason_code"] = reason_code
            annotated.loc[knob_mask, "autotuning_exclusion_reason_detail"] = reason_detail

    return annotated


def ensure_autotuning_policy_annotations(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure source-policy annotations are present exactly once in the workflow."""
    if set(SOURCE_POLICY_COLUMNS).issubset(df.columns):
        return df
    return annotate_autotuning_policy(df)


def apply_bounds_safety_gate(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Exclude uncurated knobs with INT_MAX-style max bounds.

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame]
        Filtered dataframe and dataframe of excluded knobs for audit/logging.
    """
    if "max_val" not in df.columns:
        return df, df.iloc[0:0].copy()

    max_vals = pd.to_numeric(df["max_val"], errors="coerce")
    safe_bounds_mask = (
        df["name"].isin(KNOB_TUNING_METADATA.keys())
        | (max_vals < INT_MAX_SENTINEL)
        | max_vals.isna()
    )

    excluded_details = df.loc[
        ~safe_bounds_mask, ["name", "max_val", "vartype", "context"]
    ].sort_values("name")

    return df[safe_bounds_mask].copy(), excluded_details
