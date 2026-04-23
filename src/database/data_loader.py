"""
Data Loading Utilities
=======================

Utilities for loading data from CSV files into PostgreSQL tables.
Uses centralized configuration from config.database module.
"""

import os
from typing import Optional
import pandas as pd
from sqlalchemy.engine import Engine

from src.config.database import get_db_config, DatabaseConfig
from src.database.connection import get_engine


def load_csv_to_table(
    csv_path: str,
    table_name: str,
    if_exists: str = "fail",
    config: Optional[DatabaseConfig] = None,
    engine: Optional[Engine] = None,
) -> None:
    """
    Load data from a CSV file into a PostgreSQL table.

    Parameters
    ----------
    csv_path : str
        Path to the CSV file to load
    table_name : str
        Name of the target table in PostgreSQL
    if_exists : str, default="fail"
        How to behave if the table already exists:
        - 'fail': Raise an error
        - 'replace': Drop the table and create a new one
        - 'append': Insert data to the existing table
    config : Optional[DatabaseConfig], default=None
        Database configuration. If None, uses get_db_config()
    engine : Optional[Engine], default=None
        SQLAlchemy engine. If None, creates a new engine

    Raises
    ------
    FileNotFoundError
        If the CSV file does not exist
    ValueError
        If the table already exists and if_exists='fail'

    Examples
    --------
    >>> from database import load_csv_to_table
    >>> load_csv_to_table("data/products.csv", "products")
    Loading 1000000 rows into 'products' table...
    🟢 Data loaded successfully!

    >>> # Replace existing table
    >>> load_csv_to_table("data/products.csv", "products", if_exists="replace")
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    if engine is None:
        if config is None:
            config = get_db_config()
        engine = get_engine(config)

    print(f"Reading CSV file: {csv_path}")
    df = pd.read_csv(csv_path)
    print(f"Found {len(df)} rows")

    print(f"Loading data into '{table_name}' table...")
    try:
        df.to_sql(table_name, engine, if_exists=if_exists, index=False)  # type: ignore

        if if_exists == "fail":
            print(f"🟢 Table '{table_name}' created with {len(df)} rows!")
        elif if_exists == "replace":
            print(f"🟢 Table '{table_name}' replaced with {len(df)} rows!")
        elif if_exists == "append":
            print(f"🟢 {len(df)} rows appended to table '{table_name}'!")

    except ValueError as e:
        if "already exists" in str(e):
            print(f"Table '{table_name}' already exists. Skipping data load.")
            print("Use if_exists='replace' or 'append' to modify existing data.")
        else:
            raise


def load_products_dataset(config: Optional[DatabaseConfig] = None) -> None:
    """
    Load the products dataset (convenience function).

    Parameters
    ----------
    config : Optional[DatabaseConfig], default=None
        Database configuration. If None, uses get_db_config()

    Examples
    --------
    >>> from database import load_products_dataset
    >>> load_products_dataset()
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(current_dir, "..", "..", "data", "products-1000000.csv")
    csv_path = os.path.normpath(csv_path)

    load_csv_to_table(csv_path, "products", if_exists="fail", config=config)


def load_leads_dataset(config: Optional[DatabaseConfig] = None) -> None:
    """
    Load the leads dataset (convenience function).

    Parameters
    ----------
    config : Optional[DatabaseConfig], default=None
        Database configuration. If None, uses get_db_config()

    Examples
    --------
    >>> from database import load_leads_dataset
    >>> load_leads_dataset()
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(current_dir, "..", "..", "data", "leads-100000.csv")
    csv_path = os.path.normpath(csv_path)

    load_csv_to_table(csv_path, "leads", if_exists="fail", config=config)
