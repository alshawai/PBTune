"""
Database Connection Utilities
==============================

Provides connection management for PostgreSQL database operations.
Uses centralized configuration from config.database module.
"""

import time
from typing import Optional
import logging

import psycopg2
from psycopg2.extensions import connection as PgConnection
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from src.config.database import get_db_config, DatabaseConfig

_logger = logging.getLogger(__name__)


def get_connection(
    config: Optional[DatabaseConfig] = None,
    dbname: Optional[str] = None,
    connect_timeout: Optional[int] = None,
) -> PgConnection:
    """
    Create a psycopg2 database connection.

    Parameters
    ----------
    config : Optional[DatabaseConfig], default=None
        Database configuration. If None, uses get_db_config()
    dbname : Optional[str], default=None
        Override database name. Useful for connecting to 'postgres' database
        for administrative operations.

    Returns
    -------
    psycopg2.extensions.connection
        Database connection object

    Examples
    --------
    >>> from database import get_connection
    >>> conn = get_connection()
    >>> cursor = conn.cursor()
    >>> cursor.execute("SELECT version()")
    >>> print(cursor.fetchone())
    >>> conn.close()

    >>> # Connect to postgres database for admin operations
    >>> admin_conn = get_connection(dbname="postgres")
    """
    if config is None:
        config = get_db_config()

    connection_params = config.to_dict()
    if dbname is not None:
        connection_params["dbname"] = dbname

    if connect_timeout is not None:
        connection_params["connect_timeout"] = connect_timeout  # type: ignore

    return psycopg2.connect(**connection_params)  # type: ignore


def connect_with_retry(
    config: Optional[DatabaseConfig] = None,
    *,
    max_retries: int = 1,
    retry_delay: float = 2.0,
    autocommit: bool = False,
) -> PgConnection:
    """
    Establish a PostgreSQL connection with retry logic for transient failures.

    Retries on recoverable errors (instance starting up, not yet accepting
    connections, connection refused during recovery). Raises on the last
    attempt's error if all retries are exhausted.

    Parameters
    ----------
    config : Optional[DatabaseConfig]
        Database configuration. If None, uses get_db_config().
    max_retries : int
        Maximum number of connection attempts (default: 1, no retry).
    retry_delay : float
        Delay in seconds between retries (default: 2.0).
    autocommit : bool
        Set autocommit on the returned connection (default: False).

    Returns
    -------
    PgConnection
        Active PostgreSQL connection.

    Raises
    ------
    psycopg2.Error
        If connection fails after all retries.
    """
    last_error: Optional[psycopg2.Error] = None

    for attempt in range(1, max_retries + 1):
        try:
            connection = get_connection(config=config)
            connection.autocommit = autocommit
            if attempt > 1:
                _logger.debug("Connection established after %d attempts", attempt)
            return connection
        except psycopg2.Error as e:
            last_error = e
            error_msg = str(e).lower()

            recoverable = (
                "starting up" in error_msg
                or "not yet accepting connections" in error_msg
                or "consistent recovery state" in error_msg
                or (
                    "connection refused" in error_msg
                    and "is the server running" in error_msg
                )
            )

            if recoverable and attempt < max_retries:
                _logger.warning(
                    "Database recovering, retry %d/%d in %.1fs...",
                    attempt,
                    max_retries,
                    retry_delay,
                )
                time.sleep(retry_delay)

    _logger.error("Failed to connect after %d attempts: %s", max_retries, last_error)
    raise last_error  # type: ignore


def safe_disconnect(
    connection: Optional[PgConnection],
    *,
    worker_id: Optional[int] = None,
    logger: Optional[logging.Logger] = None,
) -> None:
    """
    Close a PostgreSQL connection, suppressing and logging any errors.

    Parameters
    ----------
    connection : Optional[PgConnection]
        Connection to close. No-op if None or already closed.
    worker_id : Optional[int]
        Worker ID for logging context.
    logger : Optional[logging.Logger]
        Logger to use. Falls back to module logger.
    """
    if not connection:
        return
    log = logger or _logger
    try:
        connection.close()
        log.debug("Disconnected from PostgreSQL (worker=%s)", worker_id)
    except Exception as e:
        log.warning("Error closing connection (worker=%s): %s", worker_id, e)


def get_engine(config: Optional[DatabaseConfig] = None) -> Engine:
    """
    Create a SQLAlchemy engine for pandas and ORM operations.

    Parameters
    ----------
    config : Optional[DatabaseConfig], default=None
        Database configuration. If None, uses get_db_config()

    Returns
    -------
    sqlalchemy.engine.Engine
        SQLAlchemy engine object

    Examples
    --------
    >>> from database import get_engine
    >>> import pandas as pd
    >>> engine = get_engine()
    >>> df = pd.read_sql("SELECT * FROM products LIMIT 10", engine)
    """
    if config is None:
        config = get_db_config()

    return create_engine(config.get_sqlalchemy_url())
