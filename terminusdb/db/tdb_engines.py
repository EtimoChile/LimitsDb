from abc import ABC, abstractmethod
from terminusdb.core.tdb_params_config import Config
from typing import Any, List, Dict, Tuple
from datetime import datetime

class DatabaseEngine(ABC):
    """Static interface for database engine operations."""

    @staticmethod
    @abstractmethod
    def get_connection(config: Config) -> Any:
        """Establishes a connection to the database.
        Args:
            config: Configuration object.
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
    def all_status_tend(conn: Any, tables_cnf: Dict[Tuple[str, str], Any], process_date: str) -> bool:
        """Checks if all referenced tables have status 'TEND' in the control table.
        Args:
            conn: Active database connection.
            tables_cnf: Dict of (owner, table_name) keys representing configured tables.
            process_date: Processing date in 'YYYYMMDD' format.
        Returns:
            True if all tables have status 'TEND' for the given process date, False otherwise."""
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
    def get_date_cond(date_expr: str, mkp: int) -> str:
        """Returns a date condition for the given date expression and months to keep.
        Args:
            date_expr: Date expression to evaluate.
            mkp: Months to keep.
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

