from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from limitsdb.core.ldb_params_config import Config


@dataclass(frozen=True)
class ColumnDefinition:
    """Generic column definition independent from a specific engine."""

    name: str
    data_type: str
    id: int | None = None
    length: int | None = None
    precision: int | None = None
    scale: int | None = None
    nullable: bool = True
    default: str | None = None


IndexMap = dict[str, dict[str, Any]]
ColumnMetadata = dict[str, ColumnDefinition]


@dataclass(frozen=True)
class IndexDefinition:
    """Generic index definition."""

    name: str
    columns: tuple[str, ...]
    unique: bool = False


@dataclass(frozen=True)
class TableDefinition:
    """Generic table definition."""

    owner: str
    name: str
    columns: tuple[ColumnDefinition, ...]
    primary_key: tuple[str, ...] | None = None
    indexes: tuple[IndexDefinition, ...] = ()


@dataclass(frozen=True)
class SequenceDefinition:
    """Generic sequence definition."""

    owner: str
    name: str
    start_with: int = 1
    increment_by: int = 1
    minvalue: int | None = None
    maxvalue: int | None = None
    cycle: bool = False
    cache: int | None = 20


@dataclass(frozen=True)
class RoleDefinition:
    """Generic role definition."""

    name: str


@dataclass(frozen=True)
class UserDefinition:
    """Generic user definition."""

    name: str
    password: str
    default_tablespace: str | None = None
    temporary_tablespace: str | None = None
    roles: tuple[str, ...] = ()
    roles_with_admin_option: tuple[str, ...] = ()
    system_privileges: tuple[str, ...] = ()


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
    def get_connection(config: Config, *, admin: bool = False, env: Literal["SOURCE", "HISTORY"] | None = None) -> Any:
        """Establishes a connection to the database.
        Args:
            config: Configuration object.
            admin: If True, connects with admin credentials.
        Returns:
            An open database connection."""
        pass

    @staticmethod
    @abstractmethod
    def close_connection(conn: Any) -> None:
        """Closes the Oracle connection.
        Args:
            conn: Active Oracle connection."""
        pass

    @staticmethod
    @abstractmethod
    def load_config(conn: Any) -> list[dict[str, Any]]:
        """Loads configuration rows from the database.
        Args:
            conn: Active database connection.
        Returns:
            A list of configuration rows."""
        pass

    @staticmethod
    @abstractmethod
    def get_table_columns(conn: Any, owner: str, table_name: str) -> tuple[list[str], dict[str, ColumnDefinition]]:
        """Retrieves column names and metadata for a given table.
        Args:
            conn: Active Oracle connection.
            owner: Schema owner of the table.
            table_name: Table name.
        Returns:
            Tuple containing a list of column names and a dict of column metadata."""
        pass

    @staticmethod
    @abstractmethod
    def get_date_condition(date_expr: str, months_keep_src: int) -> str:
        """Returns a date condition for the given date expression and months to keep.
        Args:
            date_expr: Date expression to evaluate.
            months_keep_src: Months to keep.
        Returns:
            Date condition as string."""
        pass

    @staticmethod
    @abstractmethod
    def get_identifiers_from_expression(expression: str) -> set[str]:
        """Returns a set of @prefixxed identifiers found in the given expression.
        Args:
            expression: The expression string to process.
        Returns:
            A set of identifier strings found in the expression.
        """

    @staticmethod
    @abstractmethod
    def get_column_type(column: ColumnDefinition) -> str:
        """Returns the database-specific column type definition for the given column.
        Args:
            column: The ColumnDefinition object.
        Returns:
            The database-specific column type as a string.
        """
        pass

    @staticmethod
    @abstractmethod
    def generate_sql_block(config: Config, table_cnf: dict[str, Any], process_date: str) -> str:
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
    def get_system_date(conn: Any) -> datetime:
        """Retrieves the current system date from the database.
        Args:
            conn: Active database connection.
        Returns:
            Current system date."""
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
    def get_status(conn: Any, process_date: str) -> list[dict[str, Any]]:
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
        """Returns rows processed for a given table and process date from ldb_ctl.
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
    def save_error_status(
        conn: Any,
        config: Config,
        owner: str,
        table_name: str,
        process_date: str,
        process_start: datetime,
        message: str,
        plsql_code: str,
    ) -> None:
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
    def all_status_tend(conn: Any, tables_config: dict[tuple[str, str], Any], process_date: str) -> bool:
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
    def get_primary_key_columns(conn: Any, owner: str, table_name: str, table_cnf: dict[str, Any]) -> tuple[str, ...]:
        """Returns the primary key columns for the specified table."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_roles(conn: Any, roles: Sequence[RoleDefinition]) -> list[str]:
        """Ensure that the provided roles exist, returning newly created ones."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_users(conn: Any, users: Sequence[UserDefinition]) -> list[str]:
        """Ensure that the provided users exist, returning newly created ones."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_tables(conn: Any, tables: Sequence[TableDefinition]) -> list[str]:
        """Ensure that the provided tables exist, returning newly created ones."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_table_structure(conn: Any, table: TableDefinition, table_cnf: dict[str, Any]) -> None:
        """Ensure that the provided table matches the expected structure."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_sequences(conn: Any, sequences: Sequence[SequenceDefinition]) -> list[str]:
        """Ensure that the provided sequences exist, returning newly created ones."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_database_links(conn: Any, links: Sequence[DatabaseLinkDefinition]) -> list[str]:
        """Ensure that the provided database links exist, returning newly created ones."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_table_privileges(
        conn: Any, role: str, tables: Sequence[tuple[str, str]], privileges: Sequence[str]
    ) -> list[str]:
        """Ensure the role has the specified privileges on each table, returning new grants."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_supporting_objects(conn: Any, owner: str) -> list[str]:
        """Ensure auxiliary PL/SQL objects required by LimitsDb exist in the schema."""
        pass

    @staticmethod
    @abstractmethod
    def get_ldb_columns_expressions() -> tuple[str, str]:
        """Returns the expressions for the LDB process date and insert date columns.
        Returns:
            A tuple containing the process date expression and insert date expression.
        """
        pass

    @staticmethod
    @abstractmethod
    def get_identifier_str(identifier: str) -> str:
        """Returns the identifier string required to query dictionary views.
        Args:
            indentifier: The identifier string to process.
        Returns:
            The identifier string without angle brackets.
        """
        pass
