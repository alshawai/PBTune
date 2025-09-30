"""
Storage Abstraction Layer - Adaptive Indexing System 
=====================================================

This module provides database-agnostic storage interface that can work
with any DBMS (PostgreSQL, MySQL, SQLite) or in-memory storage for testing.

Design Philosophy: 
- Clean separation between storage logic and indexing logic
- Easy to swap implementations (in-memory <-> persistent storage)
- Migration- friendly for eventual C++ optimization 
- Transaction support when available
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, Iterator, Union
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import threading
from contextlib import contextmanager


class StorageType(Enum):
    """Types of storage backends"""
    IN_MEMORY = "memory"
    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    CUSTOM = "custom"


class TransactionIsolation(Enum):
    """Transaction isolation levels"""
    READ_UNCOMMITTED = "read_uncommitted"
    READ_COMMITTED = "read_committed"
    REPEATABLE_READ = "repeatable_read"
    SERIALIZABLE = "serializable"


@dataclass
class TableSchema: 
    """Schema definition for a table"""
    table_name: str
    columns: Dict[str, str]  # column_name: data_type
    primary_key: List[str]
    constraints: List[str]

    def __post_init__(self):
        if not self.primary_key: 
            raise ValueError("Table must have at least one primary key")
        
@dataclass
class StorageStats:
    """Storage performance and usgae statistics"""
    total_rows: int
    total_size_bytes: int
    read_operations: int
    write_operations: int
    cache_hit_ratio: float
    avg_read_time_ms: float
    avg_write_time_ms: float
    last_updated: datetime


@dataclass
class QueryExecutionPlan:
    """Query execution plan information"""
    query_hash: str
    estimated_cost: float
    estimated_rows: int
    execution_time_ms: float
    indexes_used: List[str]
    scan_type: str  # "index_scan", "table_scan", "bitmap_scan", etc.


class StorageEngine(ABC):
    """
    Base interface for all storage engines.
    
    This abstraction allows our adaptive indexing system to work with any
    storage backend - from simple in-memory dictionaries for testing to
    production-grade databases like PostgreSQL.
    
    Key Design Decision: We separate storage concerns from indexing concerns.
    This storage engine handles data persistence, while our index implementations
    handle query optimization.
    """

    @abstractmethod
    def connect(self, connection_params: Dict[str, Any]) -> None: 
        """
        Connect to the storage backend
        
        Parameters
        ----------
        connection_params : Dict[str, Any]
            Backend-specific connection parameters
        """
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from the storage backend"""
        pass

    @abstractmethod
    def create_table(self, schema: TableSchema) -> bool: 
        """
        Create a table with the given shcema.
        
        Parameters
        ----------
        schema : TableSchema
            Table schema definition
            
        Returns
        -------
        bool
            True if table was created successfully
        """
        pass

    @abstractmethod
    def drop_table(self, table_name: str) -> bool: 
        """
        Drop a table by name.
        
        Parameters
        ----------
        table_name : str
            Name of the table to drop
            
        Returns
        -------
        bool
            True if table was dropped successfully
        """
        pass

    @abstractmethod
    def insert_row(self, table_name: str, row_data: Dict[str, Any]) -> Any: 
        """
        Insert a row and return the row ID. 
        
        Parameters
        ----------
        table_name : str
            Target table name
        row_data : Dict[str, Any]
            Dictionary of column_name -> value
            
        Returns
        -------
        Any
            The row ID of the inserted row
        """
        pass

    @abstractmethod
    def update_row(
        self,
        table_name: str,
        updates: Dict[str, Any]
    ) -> bool:
        """
        Update a row by row ID
        
        Parameters
        ----------
        table_name : str
            Target table name
        updates : Dict[str, Any]
            Dictionary of column_name -> new_value (must include primary key)
            
        Returns
        -------
        bool
            True if row was updated successfully
        """
        pass

    @abstractmethod
    def delete_row(self, table_name: str, row_id: Any) -> bool:
        """"""
        pass

    @abstractmethod
    def get_row(self, table_name: str, row_id: Any) -> Optional[Dict[str, Any]]: 
        """Get a single row by ID"""
        pass

    @abstractmethod
    def scan_table(
        self,
        table_name: str,
        columns: Optional[List[str]] = None,
        condition: Optional[str] = None,
        limit: Optional[int] = None
    ) -> Iterator[Dict[str, Any]]:
        """
        Scan table rows (potentially filtered)

        This is the primitive operation that indexes will optimize.
        Without indexes, this becomes a full table scan.

        Parameters
        ----------
        table_name : str
            Table to scan
        columns : List[str] (optional)
            Columns to return (None = all columns)
        condition: str (optional)
            WHERE condition (backend-specific syntax)
        limit: int (optional)
            Maximum number of rows to return

        Yeilds
        ------
        Iterator[Dict[str, Any]]
            Dictionary represting each row
        """
        pass

    @abstractmethod
    def get_table_stats(self) -> StorageStats:
        """Get statistics about a table"""
        pass

    @abstractmethod
    def execute_raw_query(
        self,
        query: str,
        params: Optional[List[Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Execute a raw SQL query (for advanced operations).
        
        Note: Our adaptive indexing system should minimize the
        use of this method and work primarily through the 
        highler-level abstractions.
        """
        pass


class TransactionManager(ABC): 
    """
    Interface for the transaction management.
    
    This is separate from StorageEngine to allow storage engines that
    don't support transactions (like simple in-memory stores) to still
    work with out system
    """

    @abstractmethod
    @contextmanager
    def transaction(
        self,
        isolation_level: TransactionIsolation = TransactionIsolation.READ_COMMITTED
    ) -> Iterator[None]:
        """
        Context manager for transactions.
        
        Usage:
            with storage.transaction_manager.transaction():
                storage.insert_row()
                storage.update_row()
                # Automatically commits on success, rolls back on exception
        """
        pass

    @abstractmethod
    def begin_transaction(
        self,
        isolation_level: TransactionIsolation = TransactionIsolation.READ_COMMITTED
    ) -> str: 
        """Begin a transaction and return transaction ID"""
        pass

    @abstractmethod
    def commit_transaction(self, transaction_id: str) -> None:
        """Commit a transaciton"""
        pass

    @abstractmethod
    def rollback_transaction(self, transaction_id: str) -> None:
        """Rolls back a transaction"""
        pass


class QueryOptimizer(ABC):
    """
    Interface for query optimization and execution planning.
    
    This component analyzes queries and provides execution plans that our
    adaptive indexing system can use to make better decisions.
    """

    @abstractmethod
    def analyze_query(self, query: str, params: Optional[List[str]]) -> QueryExecutionPlan:
        """
        Analyze a query and return execution plan infomration.
        
        This is cruical for our adaptive system - we need to understand
        how queries execute to make good indexing decisions.

        Parameters
        ----------
        query : str
            SQL query to analyze
        params : List[str] (optional)
            Query parameters

        Returns
        -------
        QueryExecutionPlan
            Information about the query execution plan
        """
        pass

    @abstractmethod
    def estimate_query_cost(self, query: str, available_indexes: List[str]) -> float:
        """
        Estimate query execution cost given available indexes. 
        
        This helps our strategies decide whether creating a new
        index would be beneficial.

        Parameters
        ----------
        query : str
            SQL query to analyze
        available_indexes : List[str]
            List of currently available indexes

        Returns
        -------
        float
            Estimated query execution cost
        """
        pass

    @abstractmethod
    def get_optimal_indexes(self, query: str) -> List[Dict[str, Any]]:
        """
        Suggest optimal indexes for a query
        
        Parameters
        ----------
        query : str
            SQL query to analyze

        Returns
        -------
        List[Dict[str, Any]]
            List of index specifications that would optimize this query
        """
        pass


class InMemoryStorage(StorageEngine):
    """
    Simple in-memory implementation for testing and development.
    
    This implementation:
        - uses Python dictionaries and lists for storage
        - provides thread-saftey with locks
        - Simulates realistic performance characteristics
        - will be easy to profile and optimize when migrating to C++
    """

    def __init__(self):
        self.tables: Dict[str, Dict] = {}
        self.schemas: Dict[str, TableSchema] = {}
        self.row_counters: Dict[str, int] = {}
        self.stats: Dict[str, StorageStats] = {}
        self._lock = threading.RLock()
        self._connected = False

    def connect(self, connection_params: Dict[str, Any]) -> None:
        """Connect to in-memory storage (essentially a no-op)"""
        with self._lock: 
            self._connected = True

    def disconnect(self) -> None:
        """Disconnet and clear all data"""
        with self._lock:
            self.tables.clear()
            self.schemas.clear()
            self.row_counters.clear()
            self._connected = False
        
    def create_table(self, schema: TableSchema) -> bool:
        """Create a new table"""
        if not self._connected:
            raise RuntimeError("Storage engine not connected")
        
        with self._lock:
            if schema.table_name in self.tables:
                return False  # Table already exists
            
            self.tables[schema.table_name] = {}
            self.schemas[schema.table_name] = schema
            self.row_counters[schema.table_name] = 0
            self.stats[schema.table_name] = StorageStats(
                total_rows=0,
                total_size_bytes=0,
                read_operations=0,
                write_operations=0,
                cache_hit_ratio=1.0,
                avg_read_time_ms=0.1,
                avg_write_time_ms=0.1,
                last_updated=datetime.now()
            )
            return True
        
    def drop_table(self, table_name: str) -> bool:
        with self._lock:
            if table_name not in self.tables:
                return False  # Table doesn't exist
            
            del self.tables[table_name]
            del self.schemas[table_name]
            del self.row_counters[table_name]
            del self.stats[table_name]
            return True
        
    def insert_row(self, table_name: str, row_data: Dict[str, Any]) -> Any:
        if table_name not in self.tables:
            raise ValueError(f"Table {table_name} deos not exist")
        
        with self._lock: 
            self.row_counters[table_name] += 1
            row_id = self.row_counters[table_name]
            
            self.tables[table_name][row_id] = row_data.copy()

            stats = self.stats[table_name]
            stats.total_rows += 1
            stats.write_operations += 1
            stats.total_size_bytes += len(str(row_data))  # Rough estimate
            stats.last_updated = datetime.now()

            return row_id
        
    def update_row(self, table_name: str, row_id: Any, updates: Dict[str, Any]) -> bool:
        if table_name not in self.tables and row_id not in self.tables[table_name]:
            return False  # table doesn't exist or row doesn't exist
        
        with self._lock:
            self.tables[table_name][row_id].update(updates)
            self.stats[table_name].write_operations += 1
            self.stats[table_name].last_updated = datetime.now()
            return True
        
    def delete_row(self, table_name: str, row_id: Any) -> bool:
        if table_name not in self.tables or row_id not in self.tables[table_name]:
            return False
        
        with self._lock:
            del self.tables[table_name][row_id]
            stats = self.stats[table_name]
            stats.total_rows -= 1
            stats.write_operations += 1
            stats.last_updated = datetime.now()
            return True
        
    def get_row(self, table_name: str, row_id: Any) -> Optional[Dict[str, Any]]:
        if table_name not in self.tables:
            return None
        
        with self._lock:
            self.stats[table_name].read_operations += 1
            return self.tables[table_name].get(row_id)

    def scan_table(
            self,
            table_name: str,
            columns: Optional[List[str]] = None,
            condition: Optional[str] = None,
            limit: Optional[int] = None
        ) -> Iterator[Dict[str, Any]]:
        if table_name not in self.tables:
            return
        
        with self._lock:
            rows_returned = 0
            
            for row_id, row_data in self.tables[table_name].items():
                if limit is not None and rows_returned >= limit:
                    break  # limit exceeded
                
                # For now, we'll skip condition filtering (would need SQL parser)
                # In production, this would evaluate the WHERE clause
                
                if columns is not None:
                    filtered_row = {col: row_data.get(col) for col in columns}
                else:
                    filtered_row = row_data.copy()
                
                filtered_row['__row_id__'] = row_id
                
                yield filtered_row
                rows_returned += 1
            
            self.stats[table_name].read_operations += rows_returned

    def get_table_stats(self, table_name: str) -> StorageStats:
        if table_name not in self.stats:
            raise ValueError(f"Table {table_name} does not exist")
        
        return self.stats[table_name]
    
    def execute_raw_query(
            self,
            query: str,
            params: Optional[List[Any]] = None
            ) -> List[Dict[str, Any]]:
        # This is a placeholder - in a real system, we'd need a SQL parser
        # For now, just return empty results
        return []

class InMemoryTransactionManager(TransactionManager):
    """
    Simple transaction manager for in-memory storage.
    
    Note: This is a basic implementation. Real transaction management
    is much more complex and would likely be implemented in C++ for
    performance reasons.
    """
    
    def __init__(self, storage_engine: InMemoryStorage):
        self.storage_engine = storage_engine
        self._transactions: Dict[str, Dict] = {}
        self._transaction_counter = 0
        self._lock = threading.Lock()
    
    @contextmanager
    def transaction(
        self,
        isolation_level: TransactionIsolation = TransactionIsolation.READ_COMMITTED
    ):
        transaction_id = self.begin_transaction(isolation_level)
        try:
            yield transaction_id
            self.commit_transaction(transaction_id)
        except Exception:
            self.rollback_transaction(transaction_id)
            raise
    
    def begin_transaction(
            self,
            isolation_level: TransactionIsolation = TransactionIsolation.READ_COMMITTED
        ) -> str:
        with self._lock:
            self._transaction_counter += 1
            transaction_id = f"txn_{self._transaction_counter}"
            self._transactions[transaction_id] = {
                'isolation_level': isolation_level,
                'started_at': datetime.now(),
                'operations': []
            }
            return transaction_id
    
    def commit_transaction(self, transaction_id: str) -> None:
        """Commit a transaction"""
        with self._lock:
            if transaction_id in self._transactions:
                # In a real implementation, we'd apply all operations atomically
                del self._transactions[transaction_id]
    
    def rollback_transaction(self, transaction_id: str) -> None:
        """Rollback a transaction"""
        with self._lock:
            if transaction_id in self._transactions:
                # In a real implementation, we'd undo all operations
                del self._transactions[transaction_id]


if __name__ == "__main__":
    print("Storage Abstraction Layer - Test")
    print("=" * 32)
    
    storage = InMemoryStorage()
    storage.connect({})
    
    schema = TableSchema(
        table_name="users",
        columns={"id": "int", "name": "varchar(100)", "age": "int"},
        primary_key=["id"],
        constraints=[]
    )
    
    success = storage.create_table(schema)
    print(f"  Table created: {success}")
    
    user1_id = storage.insert_row("users", {"id": 1, "name": "Alice", "age": 30})
    user2_id = storage.insert_row("users", {"id": 2, "name": "Bob", "age": 25})
    print(f"  Inserted users: {user1_id}, {user2_id}")
    
    print("  Table contents:")
    for row in storage.scan_table("users"):
        print(f"    {row}")
    
    stats = storage.get_table_stats("users")
    print(f"  Table stats: {stats.total_rows} rows, {stats.read_operations} reads, {stats.write_operations} writes")
    
    print("\n  Storage abstraction ready for index integration!")
