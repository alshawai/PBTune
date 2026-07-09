"""
Knob Applicator for PostgreSQL Configuration
=============================================

The KnobApplicator safely applies database configuration changes with:
- Context-aware application (SET vs ALTER SYSTEM vs restart required)
- Parameter validation against pg_settings constraints
- Partial success support (follows research convention)
- Restart requirement detection
- Configuration persistence options

Context Classification:
----------------------
- internal: Cannot be changed (read-only)
- postmaster: Requires server restart (e.g., shared_buffers, max_connections)
- sighup: Requires configuration reload via pg_reload_conf()
- superuser: Can change within session (SET command)
- user: Can change within session (SET command)
- backend: Set at connection startup only
- superuser-backend: Set at connection startup, superuser only

Application Strategies:
----------------------
1. Session-level (SET): Fast, temporary, no persistence
2. Server-level (ALTER SYSTEM): Persists to postgresql.auto.conf, requires reload
3. Restart: For postmaster context parameters (manual or automatic)

Example Usage:
-------------
>>> from src.utils.applicator import KnobApplicator, ApplicatorConfig
>>>
>>> config = ApplicatorConfig(
...     persist=True,
...     auto_reload=True,
...     validate=True,
...     rollback_on_error=False,  # Allow partial success
...     allow_restart_params=True  # Include high-impact parameters
... )
>>>
>>> applicator = KnobApplicator(db_config, config)
>>>
>>> knob_config = {
...     'shared_buffers': 131072,  # Requires restart
...     'work_mem': 8192,          # Runtime modifiable
...     'random_page_cost': 1.1    # Runtime modifiable
... }
>>>
>>> result = applicator.apply(knob_config)
>>> print(f"Applied: {result.applied_count}/{len(knob_config)}")
>>> print(f"Restart required for: {result.restart_required}")
>>> # Typical output: Applied 3/3, restart required for: {'shared_buffers'}
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Set
from enum import Enum
import threading
import psycopg2
from psycopg2.extensions import connection as PostgresConnection

from src.database.connection import get_connection
from src.config.database import DatabaseConfig
from src.utils.logger import get_logger, get_color_context

LOGGER = get_logger("KnobApplicator")
COLORS = get_color_context()


class KnobContext(Enum):
    """PostgreSQL parameter context types."""

    INTERNAL = "internal"
    POSTMASTER = "postmaster"
    SIGHUP = "sighup"
    SUPERUSER = "superuser"
    USER = "user"
    BACKEND = "backend"
    SUPERUSER_BACKEND = "superuser-backend"


@dataclass
class ApplicatorConfig:
    """
    Configuration for KnobApplicator behavior.

    Attributes
    ----------
    persist : bool
        Use ALTER SYSTEM for persistence (default: True)
    auto_reload : bool
        Automatically reload config after ALTER SYSTEM (default: True)
    validate : bool
        Validate parameters before applying (default: True)
    dry_run : bool
        Simulate application without making changes (default: False)
    rollback_on_error : bool
        Rollback all changes if any parameter fails (default: False for tuning)
        Note: Modern tuning systems (OtterTune, CDBTune) use partial success -
        they apply as many parameters as possible and continue with failures.
    allow_restart_params : bool
        Allow parameters that require restart (default: True)
    """

    persist: bool = True
    auto_reload: bool = True
    validate: bool = True
    dry_run: bool = False
    rollback_on_error: bool = False
    allow_restart_params: bool = True


@dataclass
class ParameterInfo:
    """Information about a PostgreSQL parameter from pg_settings."""

    name: str
    vartype: str
    context: str  # internal, postmaster, sighup, etc.
    unit: Optional[str]
    min_val: Optional[str]
    max_val: Optional[str]
    enumvals: Optional[List[str]]
    boot_val: Optional[str]
    reset_val: Optional[str]
    setting: Optional[str] = None


@dataclass
class VerificationResult:
    """
    Result of configuration verification.

    Attributes
    ----------
    matches : Dict[str, bool]
        Per-parameter verification results (True = value matches expected).
    db_config : Dict[str, Any]
        The actual values currently active in PostgreSQL, cast to
        native Python types.  Callers can use this to update their
        knob_config with the true applied values.
    """

    matches: Dict[str, bool] = field(default_factory=dict)
    db_config: Dict[str, Any] = field(default_factory=dict)

    # Convenience helpers
    @property
    def all_matched(self) -> bool:
        """Return True when every parameter matched."""
        return bool(self.matches) and all(self.matches.values())

    @property
    def failed_params(self) -> list[str]:
        """Return the list of parameter names that did NOT match."""
        return [k for k, v in self.matches.items() if not v]


@dataclass
class ApplicationResult:
    """
    Result of applying configuration changes.

    Attributes
    ----------
    success : bool
        Overall success status
    applied : Dict[str, Any]
        Successfully applied parameters
    failed : Dict[str, str]
        Failed parameters with error messages
    restart_required : Set[str]
        Parameters requiring restart
    applied_count : int
        Number of successfully applied parameters
    failed_count : int
        Number of failed parameters
    message : str
        Overall result message
    """

    success: bool
    applied: Dict[str, Any] = field(default_factory=dict)
    failed: Dict[str, str] = field(default_factory=dict)
    restart_required: Set[str] = field(default_factory=set)
    applied_count: int = 0
    failed_count: int = 0
    message: str = ""


@dataclass
class ActivationResult:
    """
    Result of activating (reload/restart) previously-written configuration.

    Attributes
    ----------
    strategy : str
        Activation strategy used: "reload", "restart", or "none".
    success : bool
        Whether the activation succeeded.
    message : str
        Human-readable summary.
    """

    strategy: str  # "reload" | "restart" | "none"
    success: bool
    message: str = ""


class KnobApplicator:
    """
    Applies PostgreSQL configuration changes safely with validation.

    The applicator handles context-aware parameter application:
    - Runtime modifiable: Applied immediately with SET or ALTER SYSTEM
    - Restart required: Marked for manual restart
    - Validation: Checks against min/max/enum constraints

    Attributes
    ----------
    config : ApplicatorConfig
        Application configuration
    connection_params : Dict[str, Any]
        PostgreSQL connection parameters
    connection : Optional[PostgresConnection]
        Active database connection
    param_cache : Dict[str, ParameterInfo]
        Cached parameter information from pg_settings

    Example
    -------
    >>> applicator = KnobApplicator(
    ...     db_config=DatabaseConfig(host='localhost', dbname='postgres'),
    ...     config=ApplicatorConfig(persist=True, validate=True)
    ... )
    >>>
    >>> result = applicator.apply({
    ...     'work_mem': 8192,
    ...     'random_page_cost': 1.1
    ... })
    >>>
    >>> if result.success:
    ...     print(f"Applied {result.applied_count} parameters")
    ...     if result.restart_required:
    ...         print(f"Restart needed for: {result.restart_required}")
    """

    def __init__(
        self,
        db_config: DatabaseConfig,
        config: Optional[ApplicatorConfig] = None,
        worker_id: Optional[int] = None,
    ):
        """
        Initialize KnobApplicator.

        Parameters
        ----------
        db_config : DatabaseConfig
            PostgreSQL database configuration
        config : Optional[ApplicatorConfig]
            Application configuration (uses defaults if None)
        worker_id : Optional[int]
            Worker ID for logging
        """
        self.db_config = db_config
        self.config = config or ApplicatorConfig()
        self.worker_id = worker_id
        self.connection: Optional[PostgresConnection] = None
        self.param_cache: Dict[str, ParameterInfo] = {}
        self._lock = threading.Lock()  # Thread safety for connection management
        self.logger = get_logger("KnobApplicator", worker_id=worker_id)

        # self.logger.debug(
        #     " Initialized KnobApplicator: persist=%s, validate=%s, dry_run=%s",
        #     self.config.persist,
        #     self.config.validate,
        #     self.config.dry_run,
        # )

    def connect(self) -> None:
        """Establish connection to PostgreSQL (thread-safe)."""
        with self._lock:
            self._connect_internal()

    def _connect_internal(self) -> None:
        """Internal connect (assumes lock is held)."""
        try:
            self.connection = get_connection(config=self.db_config)

            self.connection.autocommit = True
            self.logger.debug(" Connected to PostgreSQL")
        except psycopg2.Error as e:
            self.logger.error("Failed to connect to PostgreSQL: %s", e)
            raise

    def disconnect(self) -> None:
        """Close PostgreSQL connection (thread-safe)."""
        with self._lock:
            self._disconnect_internal()

    def _disconnect_internal(self) -> None:
        """Internal disconnect (assumes lock is held)."""
        if self.connection:
            self.connection.close()
            self.connection = None
            self.logger.debug(" Disconnected from PostgreSQL")

    def _load_parameter_info(self, param_names: List[str]) -> None:
        """
        Load parameter information from pg_settings.

        Parameters
        ----------
        param_names : List[str]
            List of parameter names to load info for
        """
        if not self.connection:
            raise RuntimeError("Not connected to PostgreSQL")

        placeholders = ",".join(["%s"] * len(param_names))
        query = f"""
            SELECT
                name,
                vartype,
                context,
                unit,
                min_val,
                max_val,
                enumvals,
                boot_val,
                reset_val,
                setting
            FROM pg_settings
            WHERE name IN ({placeholders})
        """

        cursor = self.connection.cursor()
        try:
            cursor.execute(query, param_names)
            rows = cursor.fetchall()

            for row in rows:
                param_info = ParameterInfo(
                    name=row[0],
                    vartype=row[1],
                    context=row[2],
                    unit=row[3],
                    min_val=row[4],
                    max_val=row[5],
                    enumvals=row[6]
                    if isinstance(row[6], list)
                    else (row[6].split(",") if isinstance(row[6], str) else None),
                    boot_val=row[7],
                    reset_val=row[8],
                    setting=row[9],
                )
                self.param_cache[param_info.name] = param_info

            self.logger.debug(" Loaded info for %d parameters", len(rows))

        except psycopg2.Error as e:
            self.logger.error("Failed to load parameter info: %s", e)
            raise
        finally:
            cursor.close()

    def _validate_parameter(
        self, name: str, value: Any, param_info: ParameterInfo
    ) -> tuple[bool, Optional[str]]:
        """
        Validate parameter value against constraints.

        Parameters
        ----------
        name : str
            Parameter name
        value : Any
            Parameter value
        param_info : ParameterInfo
            Parameter metadata from pg_settings

        Returns
        -------
        tuple[bool, Optional[str]]
            (is_valid, error_message)
        """
        if param_info.context == "internal":
            return False, f"{name} is read-only (internal context)"

        if param_info.context == "postmaster" and not self.config.allow_restart_params:
            return False, f"{name} requires restart (postmaster context)"

        if param_info.vartype == "bool":
            try:
                import numpy as np
                _bool_types = (bool, int, str, np.bool_)
            except (ImportError, AttributeError):
                _bool_types = (bool, int, str)
            if not isinstance(value, _bool_types):
                return False, f"{name} must be boolean"
            if isinstance(value, str):
                value_str = value.lower()
                if value_str not in (
                    "on",
                    "off",
                    "true",
                    "false",
                    "1",
                    "0",
                    "yes",
                    "no",
                ):
                    return False, f"{name} invalid boolean value: {value}"

        elif param_info.vartype == "integer":
            try:
                int_val = int(value)
                if param_info.min_val:
                    min_int = int(param_info.min_val)
                    if int_val < min_int:
                        return False, f"{name} below min ({min_int}): {int_val}"
                if param_info.max_val:
                    max_int = int(param_info.max_val)
                    if int_val > max_int:
                        return False, f"{name} above max ({max_int}): {int_val}"
            except (ValueError, TypeError) as e:
                return False, f"{name} must be integer: {e}"

        elif param_info.vartype == "real":
            try:
                float_val = float(value)
                if param_info.min_val:
                    min_float = float(param_info.min_val)
                    if float_val < min_float:
                        return False, f"{name} below min ({min_float}): {float_val}"
                if param_info.max_val:
                    max_float = float(param_info.max_val)
                    if float_val > max_float:
                        return False, f"{name} above max ({max_float}): {float_val}"
            except (ValueError, TypeError) as e:
                return False, f"{name} must be real number: {e}"

        elif param_info.vartype == "enum":
            if param_info.enumvals and str(value) not in param_info.enumvals:
                return False, f"{name} must be one of {param_info.enumvals}: {value}"

        return True, None

    @staticmethod
    def _postmaster_value_matches(
        desired: Any, current_setting: Optional[str], vartype: str
    ) -> bool:
        """Check if a postmaster parameter's running value already matches."""
        if current_setting is None:
            return False
        try:
            if isinstance(desired, bool):
                return (current_setting.lower() in ("on", "true", "1")) == desired
            if isinstance(desired, int):
                return int(round(float(current_setting))) == desired
            if isinstance(desired, float):
                import math

                return math.isclose(
                    float(current_setting), desired, rel_tol=1e-6, abs_tol=0.01
                )
            return str(current_setting) == str(desired)
        except (ValueError, TypeError):
            return False

    def _apply_parameter(
        self, name: str, value: Any, param_info: ParameterInfo
    ) -> tuple[bool, Optional[str]]:
        """
        Apply a single parameter.

        Parameters
        ----------
        name : str
            Parameter name
        value : Any
            Parameter value
        param_info : ParameterInfo
            Parameter metadata

        Returns
        -------
        tuple[bool, Optional[str]]
            (success, error_message)
        """
        if not self.connection:
            return False, "Not connected to PostgreSQL"

        if self.config.dry_run:
            self.logger.debug(
                "  %s[DRY RUN]%s Would apply: %s = %s",
                COLORS.bold,
                COLORS.reset,
                name,
                value,
            )
            return True, None

        cursor = self.connection.cursor()
        try:
            if param_info.context == "postmaster":
                if not self.config.persist:
                    return False, f"{name} requires restart but persist=False"
                cursor.execute(f"ALTER SYSTEM SET {name} = %s", (value,))

            elif param_info.context in ["sighup", "backend", "superuser-backend"]:
                # SIGHUP and BACKEND params require pg_reload_conf() (global change)
                if not self.config.persist:
                    return False, f"{name} requires pg_reload_conf() but persist=False"
                cursor.execute(f"ALTER SYSTEM SET {name} = %s", (value,))

            elif (
                self.config.persist
            ):  # runtime modifiable with persistence (USER/SUPERUSER)
                cursor.execute(f"ALTER SYSTEM SET {name} = %s", (value,))

            else:  # runtime modifiable without persistence (USER/SUPERUSER)
                cursor.execute(f"SET {name} = %s", (value,))

            return True, None

        except psycopg2.Error as e:
            error_msg = f"Failed to apply {name}: {e}"
            self.logger.error("%s", error_msg)
            return False, error_msg
        finally:
            cursor.close()

    def _reload_configuration(self) -> bool:
        """
        Reload PostgreSQL configuration (pg_reload_conf()).

        Returns
        -------
        bool
            Success status
        """
        if not self.connection:
            return False

        if self.config.dry_run:
            self.logger.debug(
                "  %s[DRY RUN]%s Would reload configuration", COLORS.bold, COLORS.reset
            )
            return True

        cursor = self.connection.cursor()
        try:
            cursor.execute("SELECT pg_reload_conf()")
            self.logger.debug("  ➤ Reloaded PostgreSQL configuration")
            return True

        except psycopg2.Error as e:
            self.logger.error("  ➤ Failed to reload configuration: %s", e)
            return False

        finally:
            cursor.close()

    def apply_only(self, knob_config: Dict[str, Any]) -> ApplicationResult:
        """
        Write knobs to postgresql.auto.conf via ALTER SYSTEM. Does NOT reload or restart.

        This performs only the ALTER SYSTEM writes and returns an ApplicationResult
        with ``restart_required`` populated. The caller decides activation strategy
        (reload, restart, or none) separately via ``activate()``.

        Parameters
        ----------
        knob_config : Dict[str, Any]
            Dictionary of parameter_name -> value

        Returns
        -------
        ApplicationResult
            Result with applied/failed parameters and restart requirements
        """
        with self._lock:
            return self._apply_locked(knob_config, skip_reload=True)

    def activate(
        self,
        *,
        restart_required: bool,
        env: Any,
        worker_id: int,
        force_restart: bool = False,
        mode: Optional[str] = None,
    ) -> "ActivationResult":
        """
        Activate previously-written knobs by either pg_reload_conf() or full restart.

        Parameters
        ----------
        restart_required : bool
            Whether the last apply_only() flagged restart-requiring parameters.
        env : DatabaseEnvironment
            Environment backend capable of restarting instances.
        worker_id : int
            Worker whose instance should be activated.
        force_restart : bool
            Force restart regardless of other conditions.
        mode : Optional[str]
            Reserved for future use (tuning mode hint).

        Returns
        -------
        ActivationResult
            Result with strategy ("reload", "restart", or "none") and success flag.
        """
        if force_restart or restart_required:
            # Full restart via environment
            try:
                success = env.restart_instance(worker_id, quiet=True)
                if success:
                    return ActivationResult(
                        strategy="restart",
                        success=True,
                        message="Restarted instance to apply postmaster-context parameters",
                    )
                else:
                    return ActivationResult(
                        strategy="restart",
                        success=False,
                        message="Restart failed",
                    )
            except Exception as e:
                return ActivationResult(
                    strategy="restart",
                    success=False,
                    message=f"Restart failed with exception: {e}",
                )
        else:
            # Reload (pg_reload_conf) for sighup/user/superuser params
            with self._lock:
                was_connected = self.connection is not None
                if not was_connected:
                    try:
                        self._connect_internal()
                    except Exception as e:
                        return ActivationResult(
                            strategy="reload",
                            success=False,
                            message=f"Failed to connect for reload: {e}",
                        )
                try:
                    success = self._reload_configuration()
                    return ActivationResult(
                        strategy="reload",
                        success=success,
                        message="Reloaded configuration" if success else "Reload failed",
                    )
                finally:
                    if not was_connected:
                        self._disconnect_internal()

    def apply(self, knob_config: Dict[str, Any]) -> ApplicationResult:
        """
        Apply knob configuration to PostgreSQL (back-compat wrapper).

        Writes knobs via ALTER SYSTEM and reloads configuration in one call.
        For new code, prefer ``apply_only()`` + ``activate()`` for explicit
        control over the activation step.

        Parameters
        ----------
        knob_config : Dict[str, Any]
            Dictionary of parameter_name -> value

        Returns
        -------
        ApplicationResult
            Result with applied/failed parameters and restart requirements

        Example
        -------
        >>> result = applicator.apply({
        ...     'shared_buffers': 131072,
        ...     'work_mem': 8192,
        ...     'random_page_cost': 1.1
        ... })
        >>>
        >>> if result.success:
        ...     print(f"Applied {result.applied_count} parameters")
        >>> else:
        ...     print(f"Failed: {result.message}")
        """
        with self._lock:
            return self._apply_locked(knob_config, skip_reload=False)

    def _apply_locked(
        self, knob_config: Dict[str, Any], skip_reload: bool = False
    ) -> ApplicationResult:
        """Internal apply method (called while holding lock)."""
        result = ApplicationResult(success=False)

        if not knob_config:
            result.success = True
            result.message = "No parameters to apply"
            return result

        was_connected = self.connection is not None
        if not was_connected:
            try:
                self._connect_internal()
            except psycopg2.Error as e:
                result.message = f"Failed to connect: {e}"
                return result

        try:
            param_names = list(knob_config.keys())
            self._load_parameter_info(param_names)

            missing = set(param_names) - set(self.param_cache.keys())
            if missing:
                self.logger.warning(
                    "  %sUnknown parameters (will skip): %s%s",
                    COLORS.italic,
                    missing,
                    COLORS.reset,
                )
                for name in missing:
                    result.failed[name] = "Parameter not found in pg_settings"
                    result.failed_count += 1

            if self.config.validate:
                for name, value in knob_config.items():
                    if name in self.param_cache:
                        param_info = self.param_cache[name]
                        is_valid, error_msg = self._validate_parameter(
                            name, value, param_info
                        )
                        if not is_valid:
                            result.failed[name] = error_msg  # type: ignore
                            result.failed_count += 1
                            self.logger.warning(
                                "  %sValidation failed:%s %s",
                                COLORS.bold,
                                error_msg,
                                COLORS.reset,
                            )

            self.logger.debug("  Applying knob values...")
            for name, value in knob_config.items():
                if name in result.failed:
                    continue  # Skip already-failed parameters

                if name not in self.param_cache:
                    continue  # Skip missing parameters

                param_info = self.param_cache[name]
                success, error_msg = self._apply_parameter(name, value, param_info)

                if success:
                    result.applied[name] = value
                    result.applied_count += 1

                    if param_info.context == "postmaster":
                        if not self._postmaster_value_matches(
                            value, param_info.setting, param_info.vartype
                        ):
                            result.restart_required.add(name)
                else:
                    result.failed[name] = error_msg if error_msg else "Unknown error"
                    result.failed_count += 1

            if result.failed_count > 0 and self.config.rollback_on_error:
                self.logger.warning(
                    "  %sFailed to apply %d parameters%s",
                    COLORS.italic,
                    result.failed_count,
                    COLORS.reset,
                )
                result.success = False
                result.message = f"{result.failed_count} failures"
            else:
                if (
                    not skip_reload
                    and self.config.persist
                    and self.config.auto_reload
                    and result.applied_count > 0
                ):
                    self._reload_configuration()

                result.success = result.applied_count > 0

                if result.applied_count > 0:
                    result.message = f"Applied {result.applied_count} parameters"
                else:
                    result.message = "No parameters applied"

                if result.failed_count > 0:
                    result.message += f", {result.failed_count} failed"
                    self.logger.info(
                        " ➤ Partial success: %d applied, %d failed",
                        result.applied_count,
                        result.failed_count,
                    )

                if result.restart_required:
                    result.message += (
                        f", {len(result.restart_required)} require restart"
                    )

        except psycopg2.Error as e:
            result.success = False
            result.message = f"Database error: {e}"
            self.logger.error("Application failed: %s", e)

        finally:  # Disconnect if we connected
            if not was_connected:
                self._disconnect_internal()

        return result

    def get_current_values(self, param_names: List[str]) -> Dict[str, tuple[str, str]]:
        """
        Get current values of specified parameters.

        Parameters
        ----------
        param_names : List[str]
            List of parameter names

        Returns
        -------
        Dict[str, tuple[str, str]]
            Dictionary of parameter_name -> (current_value, unit)
        """
        if not param_names:
            return {}

        cursor = None
        try:
            if not self.connection:
                self.connect()

            cursor = self.connection.cursor()  # type: ignore
            placeholders = ",".join(["%s"] * len(param_names))
            query = f"""
                SELECT name, setting, unit
                FROM pg_settings
                WHERE name IN ({placeholders})
            """
            cursor.execute(query, param_names)
            rows = cursor.fetchall()

            return {row[0]: (row[1], row[2]) for row in rows}

        except psycopg2.Error as e:
            self.logger.error("Failed to get current values: %s", e)
            return {}
        finally:
            if cursor is not None:
                cursor.close()

    def reset_parameter(self, name: str) -> bool:
        """
        Reset parameter to default value.

        Parameters
        ----------
        name : str
            Parameter name

        Returns
        -------
        bool
            Success status
        """
        if not self.connection:
            self.connect()

        cursor = self.connection.cursor()  # type: ignore
        try:
            if self.config.persist:
                cursor.execute(f"ALTER SYSTEM RESET {name}")
                self.logger.info("Reset %s (ALTER SYSTEM)", name)
            else:
                cursor.execute(f"RESET {name}")
                self.logger.info("Reset %s (session)", name)

            self.connection.commit()  # type: ignore
            return True

        except psycopg2.Error as e:
            self.logger.error("Failed to reset %s: %s", name, e)
            self.connection.rollback()  # type: ignore
            return False
        finally:
            cursor.close()

    def __enter__(self):
        """Context manager entry - establish connection."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close connection."""
        self.disconnect()

    def verify(
        self,
        expected_config: Dict[str, Any],
    ) -> VerificationResult:
        """
        Verify that configuration parameters match expected values.

        Queries ``pg_settings`` and compares actual values against the supplied
        expected configuration.  Returns a :class:`VerificationResult` that
        contains both per-parameter match booleans **and** the actual DB config
        (typed values) so callers can update their knob_config with the true
        applied values.

        Parameters
        ----------
        expected_config : Dict[str, Any]
            Mapping of parameter names to their expected values.

        Returns
        -------
        VerificationResult
            Per-parameter verification results and actual DB values.
        """
        verification: Dict[str, bool] = {}
        db_config: Dict[str, Any] = {}
        mismatches: list[str] = []

        conn = None
        try:
            conn = get_connection(self.db_config, connect_timeout=5)
            cursor = conn.cursor()

            for param_name, expected_value in expected_config.items():
                try:
                    cursor.execute(
                        "SELECT setting, unit, vartype FROM pg_settings WHERE name = %s",
                        (param_name,),
                    )
                    result = cursor.fetchone()

                    if not result:
                        self.logger.warning(
                            "  Parameter '%s' not found in pg_settings", param_name
                        )
                        verification[param_name] = False
                        continue

                    current_value_str, _, vartype = result
                    current_value_repr: str

                    # Cast the raw pg_settings value to a typed Python value
                    # so db_config contains the actual applied value.
                    typed_value: Any

                    if isinstance(expected_value, bool):
                        current_value = current_value_str.lower() in ("on", "true", "1")
                        match = current_value == expected_value
                        current_value_repr = str(current_value)
                        typed_value = current_value
                    elif isinstance(expected_value, (int, float)):
                        current_value_num = float(current_value_str)
                        expected_float = float(expected_value)

                        if vartype == "integer":
                            match = int(round(current_value_num)) == int(
                                round(expected_float)
                            )
                            current_value_repr = str(int(round(current_value_num)))
                            typed_value = int(round(current_value_num))
                        else:
                            import math

                            abs_tolerance = max(0.01, abs(expected_float) * 1e-6)
                            match = math.isclose(
                                current_value_num,
                                expected_float,
                                rel_tol=1e-6,
                                abs_tol=abs_tolerance,
                            )
                            current_value_repr = str(current_value_num)
                            typed_value = current_value_num
                    else:
                        current_value_text = current_value_str
                        # wal_compression enum migration: on -> pglz
                        if (
                            param_name == "wal_compression"
                            and str(expected_value).lower() in ("on", "true")
                            and str(current_value_text).lower() in ("on", "pglz")
                        ):
                            match = True
                        else:
                            match = str(current_value_text) == str(expected_value)
                        current_value_repr = str(current_value_text)
                        typed_value = str(current_value_text)

                    verification[param_name] = match
                    db_config[param_name] = typed_value

                    if not match:
                        mismatches.append(
                            f"    {param_name}: expected={expected_value}, actual={current_value_repr}"
                        )

                except Exception as e:
                    self.logger.warning(
                        "  %sFailed to verify parameter '%s': %s%s",
                        COLORS.italic,
                        param_name,
                        e,
                        COLORS.reset,
                    )
                    verification[param_name] = False

            cursor.close()

            verified_count = sum(verification.values())
            total_count = len(verification)

            if verified_count == total_count:
                self.logger.debug(
                    " ➤ Configuration verified: %d/%d parameters correct",
                    verified_count,
                    total_count,
                )
            else:
                self.logger.warning(
                    " %s➤ Configuration mismatch: %d/%d parameters verified%s",
                    COLORS.italic,
                    verified_count,
                    total_count,
                    COLORS.reset,
                )
                for mismatch in mismatches:
                    self.logger.warning("  %s", mismatch)

        except Exception as e:
            self.logger.error("Configuration verification failed: %s", e)
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

        return VerificationResult(matches=verification, db_config=db_config)
