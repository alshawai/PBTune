"""
Database Connection Utilities
==============================

Provides connection management for PostgreSQL database operations.
Uses centralized configuration from config.database module.
"""

from typing import Optional
import psycopg2
from psycopg2.extensions import connection as PgConnection
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from src.config.database import get_db_config, DatabaseConfig


def get_connection(
    config: Optional[DatabaseConfig] = None, dbname: Optional[str] = None
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

    return psycopg2.connect(**connection_params) # type: ignore


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
