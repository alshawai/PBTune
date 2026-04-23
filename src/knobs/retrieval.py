"""
PostgreSQL Configuration Parameters (Knobs) Retrieval
======================================================

This module provides utilities to retrieve PostgreSQL configuration parameters
(knobs) for machine learning-based database tuning. These parameters control
various aspects of PostgreSQL performance including memory, query planning,
I/O, and more.

Key Categories of Knobs:
- Memory Configuration (shared_buffers, work_mem, etc.)
- Query Planning (random_page_cost, effective_cache_size, etc.)
- Write Ahead Log (WAL) (wal_buffers, checkpoint_timeout, etc.)
- Autovacuum and Maintenance
- Connection and Resource Limits
- Parallelism
"""

from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum
import psycopg2
import pandas as pd
from sqlalchemy.engine import Engine

from src.config.database import get_db_config, DatabaseConfig
from src.database.connection import get_connection, get_engine
from src.knobs.policy import annotate_autotuning_policy


class KnobCategory(Enum):
    """Categories of PostgreSQL configuration parameters"""

    MEMORY = "memory"
    QUERY_PLANNER = "query_planner"
    WAL = "wal"
    CHECKPOINT = "checkpoint"
    AUTOVACUUM = "autovacuum"
    CONNECTIONS = "connections"
    PARALLELISM = "parallelism"
    STATISTICS = "statistics"
    LOCKS = "locks"
    OTHER = "other"


@dataclass
class ConfigParameter:
    """Represents a PostgreSQL configuration parameter"""

    name: str
    value: str
    unit: Optional[str]
    category: str
    context: str  # when parameter can be changed (internal, postmaster, sighup, etc.)
    vartype: str  # bool, integer, real, string, enum
    source: str  # where the value comes from (default, configuration file, etc.)
    min_val: Optional[str]
    max_val: Optional[str]
    enumvals: Optional[List[str]]
    boot_val: Optional[str]
    reset_val: Optional[str]
    description: Optional[str]


class PostgreSQLKnobRetriever:
    """
    Retrieves and categorizes PostgreSQL configuration parameters for ML tuning.
    """

    # Critical knobs for performance tuning
    TUNABLE_KNOBS = {
        KnobCategory.MEMORY: [
            "shared_buffers",  # Memory for shared buffer pool
            "effective_cache_size",  # Planner's assumption of kernel cache size
            "work_mem",  # Memory for sort/hash operations per operation
            "maintenance_work_mem",  # Memory for maintenance operations (VACUUM, CREATE INDEX)
            "temp_buffers",  # Memory for temp tables
            "wal_buffers",  # WAL buffer size
        ],
        KnobCategory.QUERY_PLANNER: [
            "random_page_cost",  # Cost of random page fetch (SSD vs HDD)
            "seq_page_cost",  # Cost of sequential page fetch
            "cpu_tuple_cost",  # Cost of processing each row
            "cpu_index_tuple_cost",  # Cost of processing each index entry
            "cpu_operator_cost",  # Cost of processing each operator/function
            "effective_io_concurrency",  # Expected concurrent I/O operations
            "default_statistics_target",  # Amount of statistics collected
            "enable_seqscan",  # Enable/disable sequential scans
            "enable_indexscan",  # Enable/disable index scans
            "enable_bitmapscan",  # Enable/disable bitmap scans
            "enable_hashjoin",  # Enable/disable hash joins
            "enable_mergejoin",  # Enable/disable merge joins
            "enable_nestloop",  # Enable/disable nested loop joins
        ],
        KnobCategory.WAL: [
            "wal_level",  # WAL level (minimal, replica, logical)
            "fsync",  # Force synchronous updates
            "synchronous_commit",  # Synchronous commit level
            "wal_compression",  # Compress WAL data
            "wal_writer_delay",  # WAL writer delay
        ],
        KnobCategory.CHECKPOINT: [
            "checkpoint_timeout",  # Maximum time between checkpoints
            "checkpoint_completion_target",  # Fraction of interval for checkpoint completion
            "max_wal_size",  # Max WAL size before checkpoint
            "min_wal_size",  # Minimum WAL size
        ],
        KnobCategory.AUTOVACUUM: [
            "autovacuum",  # Enable autovacuum
            "autovacuum_max_workers",  # Max autovacuum worker processes
            "autovacuum_naptime",  # Time between autovacuum runs
            "autovacuum_vacuum_threshold",
            "autovacuum_analyze_threshold",
            "autovacuum_vacuum_scale_factor",
            "autovacuum_analyze_scale_factor",
            "autovacuum_vacuum_cost_delay",
            "autovacuum_vacuum_cost_limit",
        ],
        KnobCategory.CONNECTIONS: [
            "max_connections",  # Maximum concurrent connections
            "max_worker_processes",  # Maximum background worker processes
            "max_parallel_workers",  # Maximum parallel workers
            "max_parallel_workers_per_gather",  # Max parallel workers per Gather node
        ],
        KnobCategory.PARALLELISM: [
            "max_parallel_workers_per_gather",
            "max_parallel_maintenance_workers",
            "parallel_setup_cost",
            "parallel_tuple_cost",
            "min_parallel_table_scan_size",
            "min_parallel_index_scan_size",
        ],
        KnobCategory.STATISTICS: [
            "default_statistics_target",
            "track_activities",
            "track_counts",
            "track_io_timing",
            "track_functions",
        ],
        KnobCategory.LOCKS: [
            "deadlock_timeout",
            "max_locks_per_transaction",
            "max_pred_locks_per_transaction",
        ],
    }

    def __init__(self, config: Optional[DatabaseConfig] = None):
        """
        Initialize the knob retriever.

        Parameters
        ----------
        config : Optional[DatabaseConfig], default=None
            Database configuration. If None, uses get_db_config()

        Examples
        --------
        >>> from knobs import PostgreSQLKnobRetriever
        >>> retriever = PostgreSQLKnobRetriever()
        >>> knobs = retriever.get_tunable_knobs()
        """
        if config is None:
            config = get_db_config()

        self.config = config
        self._engine: Optional[Engine] = None

    def _get_connection(self) -> psycopg2.extensions.connection:
        """Create a database connection."""
        return get_connection(self.config)

    def _get_engine(self) -> Engine:
        """Create or return SQLAlchemy engine for pandas operations."""
        if self._engine is None:
            self._engine = get_engine(self.config)
        return self._engine

    def get_all_parameters(self) -> pd.DataFrame:
        """
        Retrieve all PostgreSQL configuration parameters.

        Returns
        -------
        pd.DataFrame
            DataFrame containing all configuration parameters
        """
        query = """
        SELECT 
            name,
            setting AS value,
            unit,
            category,
            context,
            vartype,
            source,
            min_val,
            max_val,
            enumvals,
            boot_val,
            reset_val,
            short_desc AS description
        FROM pg_settings
        ORDER BY name;
        """

        engine = self._get_engine()
        df = pd.read_sql_query(query, engine)
        return df

    def get_tunable_knobs(
        self, categories: Optional[List[KnobCategory]] = None
    ) -> pd.DataFrame:
        """
        Retrieve commonly tuned parameters for ML-based optimization.

        Parameters
        ----------
        categories : Optional[List[KnobCategory]], default=None
            Specific categories to retrieve. If None, retrieves all tunable knobs.

        Returns
        -------
        pd.DataFrame
            DataFrame containing tunable configuration parameters
        """
        all_params = self.get_all_parameters()

        if categories is None:
            knob_names = []
            for category_knobs in self.TUNABLE_KNOBS.values():
                knob_names.extend(category_knobs)
        else:
            knob_names = []
            for category in categories:
                if category in self.TUNABLE_KNOBS:
                    knob_names.extend(self.TUNABLE_KNOBS[category])

        tunable = all_params[all_params["name"].isin(knob_names)].copy()

        def get_custom_category(name):
            for category, knobs in self.TUNABLE_KNOBS.items():
                if name in knobs:
                    return category.value
            return "other"

        tunable["custom_category"] = tunable["name"].apply(get_custom_category)

        return tunable

    def get_numeric_knobs(self) -> pd.DataFrame:
        """
        Get only numeric knobs (integer and real) suitable for ML optimization.

        Returns
        -------
        pd.DataFrame
            DataFrame containing numeric configuration parameters
        """
        tunable = self.get_tunable_knobs()
        numeric = tunable[tunable["vartype"].isin(["integer", "real"])].copy()
        return numeric

    def get_current_values_dict(
        self, knob_names: Optional[List[str]] = None
    ) -> Dict[str, str]:
        """
        Get current values as a dictionary (useful for ML feature vectors).

        Parameters
        ----------
        knob_names : Optional[List[str]], default=None
            Specific knob names to retrieve. If None, retrieves all tunable knobs.

        Returns
        -------
        Dict[str, str]
            Dictionary mapping knob names to their current values
        """
        if knob_names is None:
            df = self.get_tunable_knobs()
        else:
            all_params = self.get_all_parameters()
            df = all_params[all_params["name"].isin(knob_names)]

        return dict(zip(df["name"], df["value"], strict=True))

    def get_knob_details(self, knob_name: str) -> Optional[ConfigParameter]:
        """
        Get detailed information about a specific knob.

        Parameters
        ----------
        knob_name : str
            Name of the configuration parameter

        Returns
        -------
        Optional[ConfigParameter]
            ConfigParameter object with details, or None if not found
        """
        query = """
        SELECT 
            name,
            setting AS value,
            unit,
            category,
            context,
            vartype,
            source,
            min_val,
            max_val,
            enumvals,
            boot_val,
            reset_val,
            short_desc AS description
        FROM pg_settings
        WHERE name = %s;
        """

        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(query, (knob_name,))
            row = cursor.fetchone()

            if row is None:
                return None

            col_names = [desc[0] for desc in cursor.description]  # type: ignore
            param_dict = dict(zip(col_names, row, strict=True))

            return ConfigParameter(**param_dict)
        finally:
            conn.close()

    def get_memory_knobs(self) -> pd.DataFrame:
        """Get memory-related configuration parameters."""
        return self.get_tunable_knobs(categories=[KnobCategory.MEMORY])

    def get_query_planner_knobs(self) -> pd.DataFrame:
        """Get query planner configuration parameters."""
        return self.get_tunable_knobs(categories=[KnobCategory.QUERY_PLANNER])

    def normalize_value(self, value: str, unit: Optional[str]) -> float:
        """
        Normalize a knob value to a standard unit (useful for ML).

        Converts memory values to MB, time values to seconds, etc.

        Parameters
        ----------
        value : str
            Current value
        unit : Optional[str]
            Unit of the value (kB, ms, etc.)

        Returns
        -------
        float
            Normalized numeric value
        """
        try:
            numeric_value = float(value)
        except (ValueError, TypeError):
            return 0.0

        if unit == "kB":
            return numeric_value / 1024.0
        elif unit == "MB":
            return numeric_value
        elif unit == "GB":
            return numeric_value * 1024.0
        elif unit == "8kB":  # PostgreSQL uses 8kB blocks
            return (numeric_value * 8.0) / 1024.0

        elif unit == "ms":
            return numeric_value / 1000.0
        elif unit == "s":
            return numeric_value
        elif unit == "min":
            return numeric_value * 60.0

        else:
            return numeric_value

    def get_normalized_features(self) -> Dict[str, float]:
        """
        Get normalized numeric features for ML models.

        Returns
        -------
        Dict[str, float]
            Dictionary of normalized knob values suitable for ML
        """
        numeric_knobs = self.get_numeric_knobs()
        features = {}

        for _, row in numeric_knobs.iterrows():
            normalized = self.normalize_value(row["value"], row["unit"])
            features[row["name"]] = normalized

        return features

    def export_to_csv(self, filepath: str, include_all: bool = False) -> None:
        """
        Export knobs to CSV for analysis or ML training.

        Parameters
        ----------
        filepath : str
            Path to save the CSV file
        include_all : bool, default=False
            If True, export all parameters. If False, only tunable ones.
        """
        if include_all:
            df = self.get_all_parameters()
        else:
            df = self.get_tunable_knobs()

        df.to_csv(filepath, index=False)
        print(f"Exported {len(df)} parameters to {filepath}")

    def get_modifiable_knobs(self) -> pd.DataFrame:
        """
        Get knobs that can be modified without restarting PostgreSQL.

        Returns
        -------
        pd.DataFrame
            DataFrame with knobs that have context != 'internal' and context != 'postmaster'
        """
        tunable = self.get_tunable_knobs()
        # 'postmaster' requires restart, 'internal' can't be changed
        modifiable = tunable[
            ~tunable["context"].isin(["internal", "postmaster"])
        ].copy()
        return modifiable

    def get_all_knobs_with_metadata(self) -> pd.DataFrame:
        """
        Retrieve ALL PostgreSQL configuration parameters with full metadata.

        This includes both predefined tunable knobs and all other parameters
        that exist in pg_settings, regardless of whether they're commonly tuned.

        Returns
        -------
        pd.DataFrame
            DataFrame containing all configuration parameters with metadata
        """
        all_params = self.get_all_parameters()

        predefined_knobs = []
        for category_knobs in self.TUNABLE_KNOBS.values():
            predefined_knobs.extend(category_knobs)

        all_params["is_predefined_tunable"] = all_params["name"].isin(predefined_knobs)

        def get_custom_category(name):
            for category, knobs in self.TUNABLE_KNOBS.items():
                if name in knobs:
                    return category.value
            return "other"

        all_params["custom_category"] = all_params["name"].apply(get_custom_category)
        all_params["is_runtime_modifiable"] = ~all_params["context"].isin(
            ["internal", "postmaster"]
        )

        return annotate_autotuning_policy(all_params)

    def save_all_knobs(self, filepath: str, include_metadata: bool = True) -> None:
        """
        Save ALL PostgreSQL knobs to a CSV file.

        This saves every single parameter from pg_settings, not just the predefined
        tunable ones. Useful for comprehensive analysis and discovering new tunable
        parameters.

        Parameters
        ----------
        filepath : str
            Path to save the CSV file
        include_metadata : bool, default=True
            If True, includes additional metadata columns (is_predefined_tunable,
            custom_category, is_runtime_modifiable)
        """
        if include_metadata:
            df = self.get_all_knobs_with_metadata()
        else:
            df = self.get_all_parameters()

        df.to_csv(filepath, index=False)

        total_knobs = len(df)
        if include_metadata:
            predefined = df["is_predefined_tunable"].sum()
        else:
            predefined = len(self.get_tunable_knobs())

        print(f"Saved ALL {total_knobs} PostgreSQL knobs to {filepath}")
        print(f"  - Predefined tunable knobs: {predefined}")
        print(f"  - Other knobs: {total_knobs - predefined}")
        if include_metadata:
            runtime_mod = df["is_runtime_modifiable"].sum()
            print(f"  - Runtime modifiable (no restart): {runtime_mod}")
            print(f"  - Requires restart: {total_knobs - runtime_mod}")

    def get_knobs_by_context(self, context: str) -> pd.DataFrame:
        """
        Get all knobs by their context (when they can be changed).

        Parameters
        ----------
        context : str
            Context type: 'internal', 'postmaster', 'sighup', 'superuser',
            'superuser-backend', 'backend', 'user'

        Returns
        -------
        pd.DataFrame
            DataFrame with all knobs of the specified context
        """
        all_params = self.get_all_parameters()
        return all_params[all_params["context"] == context].copy()

    def get_knobs_by_category(self, category: str) -> pd.DataFrame:
        """
        Get all knobs by their PostgreSQL category.

        Parameters
        ----------
        category : str
            PostgreSQL category (e.g., 'Resource Usage / Memory',
            'Query Tuning / Planner Cost Constants', etc.)

        Returns
        -------
        pd.DataFrame
            DataFrame with all knobs of the specified category
        """
        all_params = self.get_all_parameters()
        return all_params[all_params["category"] == category].copy()

    def get_all_categories(self) -> List[str]:
        """
        Get list of all PostgreSQL configuration categories.

        Returns
        -------
        List[str]
            Sorted list of all unique category names
        """
        all_params = self.get_all_parameters()
        return sorted(all_params["category"].unique().tolist())

    def get_all_contexts(self) -> List[str]:
        """
        Get list of all PostgreSQL configuration contexts.

        Returns
        -------
        List[str]
            Sorted list of all unique context types
        """
        all_params = self.get_all_parameters()
        return sorted(all_params["context"].unique().tolist())

    def get_knobs_summary(self) -> Dict[str, int]:
        """
        Get a summary of all knobs in the database.

        Returns
        -------
        Dict[str, int]
            Dictionary with counts for different knob types
        """
        all_params = self.get_all_knobs_with_metadata()

        summary = {
            "total_knobs": len(all_params),
            "predefined_tunable": int(all_params["is_predefined_tunable"].sum()),
            "runtime_modifiable": int(all_params["is_runtime_modifiable"].sum()),
            "requires_restart": int((~all_params["is_runtime_modifiable"]).sum()),
            "integer_type": int((all_params["vartype"] == "integer").sum()),
            "real_type": int((all_params["vartype"] == "real").sum()),
            "bool_type": int((all_params["vartype"] == "bool").sum()),
            "string_type": int((all_params["vartype"] == "string").sum()),
            "enum_type": int((all_params["vartype"] == "enum").sum()),
        }

        return summary
