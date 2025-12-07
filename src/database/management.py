"""
Database Management Utilities
==============================

Provides utilities for creating, dropping, and resetting PostgreSQL databases.
Uses centralized configuration from config.database module.
"""

from typing import Optional
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from src.config.database import get_db_config, DatabaseConfig
from src.database.connection import get_connection


def create_database(config: Optional[DatabaseConfig] = None) -> None:
    """
    Create the database if it does not exist.
    
    Parameters
    ----------
    config : Optional[DatabaseConfig], default=None
        Database configuration. If None, uses get_db_config()
        
    Examples
    --------
    >>> from database import create_database
    >>> create_database()
    Database 'test_dataset' created successfully!
    """
    if config is None:
        config = get_db_config()

    try:
        conn = get_connection(config, dbname="postgres")
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (config.dbname,)
        )
        exists = cursor.fetchone()

        if not exists:
            cursor.execute(f'CREATE DATABASE "{config.dbname}"')
            print(f"🟢 Database '{config.dbname}' created successfully!")
        else:
            print(f"Database '{config.dbname}' already exists.")

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"🔴 Error creating database: {e}")
        raise


def drop_database(config: Optional[DatabaseConfig] = None) -> None:
    """
    Drop the database if it exists.
    
    This will terminate all connections to the database before dropping it.
    
    Parameters
    ----------
    config : Optional[DatabaseConfig], default=None
        Database configuration. If None, uses get_db_config()
        
    Warning
    -------
    This operation is destructive and cannot be undone.
    
    Examples
    --------
    >>> from database import drop_database
    >>> drop_database()
    Database 'test_dataset' dropped successfully!
    """
    if config is None:
        config = get_db_config()

    try:
        conn = get_connection(config, dbname="postgres")
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()

        # Terminate all connections to the target database
        cursor.execute(
            """
            SELECT pg_terminate_backend(pg_stat_activity.pid)
            FROM pg_stat_activity
            WHERE pg_stat_activity.datname = %s
              AND pid <> pg_backend_pid()
            """,
            (config.dbname,),
        )

        cursor.execute(f'DROP DATABASE IF EXISTS "{config.dbname}"')
        print(f"🟢 Database '{config.dbname}' dropped successfully!")

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"🔴 Error dropping database: {e}")
        raise


def reset_database(config: Optional[DatabaseConfig] = None) -> None:
    """
    Drop and recreate the database.
    
    This provides a clean slate by removing all data and recreating the database.
    
    Parameters
    ----------
    config : Optional[DatabaseConfig], default=None
        Database configuration. If None, uses get_db_config()
        
    Warning
    -------
    This operation is destructive and cannot be undone.
    All data in the database will be lost.
    
    Examples
    --------
    >>> from database import reset_database
    >>> reset_database()
    Database 'test_dataset' dropped successfully!
    Database 'test_dataset' created successfully!
    """
    if config is None:
        config = get_db_config()

    print(f"Resetting database '{config.dbname}'...")
    drop_database(config)
    create_database(config)
    print("🟢 Database reset complete!")
