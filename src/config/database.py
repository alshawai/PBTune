"""
Database Configuration Utility
================================

Centralized utility for loading database configuration from environment variables.
This is the SINGLE SOURCE OF TRUTH for database credentials across the project.

All modules should import from this module rather than loading environment
variables directly to ensure consistency and security.
"""

import os
from typing import Dict, Optional
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class DatabaseConfig:
    """
    Database configuration loaded from environment variables.
    
    This class provides a centralized way to access database configuration
    and ensures that required variables are validated.
    
    Attributes
    ----------
    user : str
        Database username
    password : str
        Database password
    host : str
        Database host address
    port : str
        Database port
    dbname : str
        Database name
    """

    user: str
    password: str
    host: str
    port: str
    dbname: str

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        """
        Create DatabaseConfig from environment variables.
        
        Returns
        -------
        DatabaseConfig
            Database configuration instance
            
        Raises
        ------
        ValueError
            If DB_PASSWORD is not set in environment variables
        """
        password = os.getenv("DB_PASSWORD")
        if not password:
            raise ValueError(
                "DB_PASSWORD environment variable is required. "
                "Please set it in .env file. See docs/ENVIRONMENT_SETUP.md for help."
            )

        return cls(
            user=os.getenv("DB_USER", "postgres"),
            password=password,
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
            dbname=os.getenv("DB_NAME", "test_dataset"),
        )

    def to_dict(self) -> Dict[str, str]:
        """
        Get configuration as a dictionary (useful for psycopg2).
        
        Returns
        -------
        Dict[str, str]
            Dictionary with keys: user, password, host, port, dbname
        """
        return {
            "user": self.user,
            "password": self.password,
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
        }

    def get_connection_string(self, hide_password: bool = True) -> str:
        """
        Get PostgreSQL connection string.
        
        Parameters
        ----------
        hide_password : bool, default=True
            If True, replaces password with asterisks in the returned string
        
        Returns
        -------
        str
            PostgreSQL connection string (for display purposes)
        """
        password = "****" if hide_password else self.password
        return f"postgresql://{self.user}:{password}@{self.host}:{self.port}/{self.dbname}"

    def get_sqlalchemy_url(self) -> str:
        """
        Get SQLAlchemy database URL.
        
        Returns
        -------
        str
            SQLAlchemy database URL with credentials
        """
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.dbname}"

    def __repr__(self) -> str:
        """String representation with hidden password."""
        return (
            f"DatabaseConfig(user='{self.user}', host='{self.host}', "
            f"port='{self.port}', dbname='{self.dbname}')"
        )


class _ConfigHolder:
    """Holds the singleton database configuration instance."""
    _instance: Optional[DatabaseConfig] = None

    @classmethod
    def get_instance(cls) -> DatabaseConfig:
        """Get or create the database configuration instance."""
        if cls._instance is None:
            cls._instance = DatabaseConfig.from_env()
        return cls._instance


def get_db_config() -> DatabaseConfig:
    """
    Get the database configuration singleton.
    
    This ensures that configuration is loaded only once and reused across
    the application, improving performance and consistency.
    
    Returns
    -------
    DatabaseConfig
        Database configuration instance
    
    Raises
    ------
    ValueError
        If DB_PASSWORD is not set in environment variables
        
    Examples
    --------
    >>> from config.database import get_db_config
    >>> config = get_db_config()
    >>> print(config.user)
    'postgres'
    """
    return _ConfigHolder.get_instance()
