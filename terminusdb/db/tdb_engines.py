from __future__ import annotations

from dataclasses import dataclass
from abc import ABC, abstractmethod
from terminusdb.core.tdb_params_config import Config
from typing import Any, Dict, List, Optional, Sequence, Tuple
from datetime import datetime


@dataclass(frozen=True)
class ColumnDefinition:
    """Generic column definition independent from a specific engine."""

    name: str
    data_type: str
    length: Optional[int] = None
    precision: Optional[int] = None
    scale: Optional[int] = None
    nullable: bool = True
    default: Optional[str] = None


@dataclass(frozen=True)
class IndexDefinition:
    """Generic index definition."""

    name: str
    columns: Tuple[str, ...]
    unique: bool = False


@dataclass(frozen=True)
class TableDefinition:
    """Generic table definition."""

    owner: str
    name: str
    columns: Tuple[ColumnDefinition, ...]
    primary_key: Optional[Tuple[str, ...]] = None
    indexes: Tuple[IndexDefinition, ...] = ()


@dataclass(frozen=True)
class SequenceDefinition:
    """Generic sequence definition."""

    owner: str
    name: str
    start_with: int = 1
    increment_by: int = 1
    minvalue: Optional[int] = None
    maxvalue: Optional[int] = None
    cycle: bool = False
    cache: Optional[int] = 20


@dataclass(frozen=True)
class RoleDefinition:
    """Generic role definition."""

    name: str


@dataclass(frozen=True)
class UserDefinition:
    """Generic user definition."""

    name: str
    password: str
    default_tablespace: Optional[str] = None
    temporary_tablespace: Optional[str] = None
    roles: Tuple[str, ...] = ()
    system_privileges: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DatabaseLinkDefinition:
    """Generic database link definition."""

    name: str
    username: str
    password: str
    dsn: str

class DatabaseEngine(ABC):
    """Static interface for database engine operations."""

    @staticmethod
    @abstractmethod
    def get_connection(config: Config, *, admin: bool = False) -> Any:
        """Establishes a connection to the database.
        Args:
            config: Configuration object.
            admin: If True, connects with admin credentials.
        Returns:
            An open database connection."""
        pass

    @staticmethod
    @abstractmethod
    def get_system_date(conn: Any) -> datetime:
        """Retrieves the current system date from the database.
        Args:
            conn: Active database connection.
        Returns:
            Current system date."""
        pass

    @staticmethod
    @abstractmethod
    def load_config(conn: Any) -> List[Dict[str, Any]]:
        """Loads configuration rows from the database.
        Args:
            conn: Active database connection.
        Returns:
            A list of configuration rows."""
        pass

    @staticmethod
    @abstractmethod
    def get_status(conn: Any, process_date: str) -> List[Dict[str, Any]]:
        """Loads control status rows for a given process date.
        Args:
            conn: Active database connection.
            process_date: The process date in 'YYYYMMDD' format.
        Returns:
            A list of control status rows."""
        pass

    @staticmethod
    @abstractmethod
    def get_rows_processed(conn: Any, owner: str, table_name: str, process_date: str) -> int:
        """Returns rows processed for a given table and process date from tdb_ctl.
        Args:
            conn: Active Oracle connection.
            owner: Schema owner of the table.
            table_name: Table name.
            process_date: Target process date in 'YYYYMMDD'.
        Returns:
            Number of rows processed or 0 if none found or mismatched date."""
        pass

    @staticmethod
    @abstractmethod
    def save_error_status(conn: Any, config: Config, owner: str, table_name: str, process_date: str, process_start: datetime,
                          message: str, plsql_code: str) -> None:
        """Saves an error status entry in the control table.
        Args:
            conn: Active database connection.
            config: Configuration object.
            owner: Schema owner.
            table_name: Table name.
            process_date: Date in 'YYYYMMDD' format.
            process_start: Start time of the process.
            message: Error message.
            plsql_code: PL/SQL or SQL block executed."""
        pass


    @staticmethod
    @abstractmethod
    def generate_sql_block(config: Config, table_cnf: Dict[str, Any], process_date: str) -> str:
        """Generates a SQL/PL block to process a given table.
        Args:
            config: Configuration object.
            table_cnf: Processed table configuration object.
            process_date: Process date in 'YYYYMMDD' format.
        Returns:
            The PL/SQL or SQL block to execute."""
        pass

    @staticmethod
    @abstractmethod
    def sql_block_run(conn: Any, plsql_code: str) -> None:
        """Executes a PL/SQL or SQL block against the database.
        Args:
            conn: Active database connection.
            plsql_code: The PL/SQL or SQL block to execute."""
        pass

    @staticmethod
    @abstractmethod
    def all_status_tend(conn: Any, tables_config: Dict[Tuple[str, str], Any], process_date: str) -> bool:
        """Checks if all referenced tables have status "Status.TABLE_END" in the control table.
        Args:
            conn: Active database connection.
            tables_config: Dict of (owner, table_name) keys representing configured tables.
            process_date: Processing date in 'YYYYMMDD' format.
        Returns:
            True if all tables have status "Status.TABLE_END" for the given process date, False otherwise."""
        pass

    @staticmethod
    @abstractmethod
    def get_table_columns(conn: Any, owner: str, table_name: str) -> List[str]:
        """Returns a list of column names for a given table in the specified schema.
        Args:
            conn: Active Oracle connection.
            owner: Schema owner of the table.
            table_name: Table name.
        Returns:
            List of column names in lowercase."""
        pass

    @staticmethod
    @abstractmethod
    def get_date_cond(date_expr: str, months_keep_src: int) -> str:
        """Returns a date condition for the given date expression and months to keep.
        Args:
            date_expr: Date expression to evaluate.
            months_keep_src: Months to keep.
        Returns:
            Date condition as string."""
        pass
    

    @staticmethod
    @abstractmethod
    def close_connection(conn: Any) -> None:
        """Closes the Oracle connection.
        Args:
            conn: Active Oracle connection."""
        pass

    # ------------------------------------------------------------------
    # Schema / security bootstrap helpers
    # ------------------------------------------------------------------

    @staticmethod
    @abstractmethod
    def ensure_roles(conn: Any, roles: Sequence[RoleDefinition]) -> List[str]:
        """Ensure that the provided roles exist, returning newly created ones."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_users(conn: Any, users: Sequence[UserDefinition]) -> List[str]:
        """Ensure that the provided users exist, returning newly created ones."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_tables(conn: Any, tables: Sequence[TableDefinition]) -> List[str]:
        """Ensure that the provided tables exist, returning newly created ones."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_sequences(conn: Any, sequences: Sequence[SequenceDefinition]) -> List[str]:
        """Ensure that the provided sequences exist, returning newly created ones."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_database_links(conn: Any, links: Sequence[DatabaseLinkDefinition]) -> List[str]:
        """Ensure that the provided database links exist, returning newly created ones."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_supporting_plsql(conn: Any, owner: str) -> None:
        """Ensure auxiliary PL/SQL objects required by TerminusDB exist in the schema."""
        pass

