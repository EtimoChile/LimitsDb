from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
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


@dataclass(frozen=True)
class LogEntry:
    """One row of the execution log."""

    log_id: int  # increases with time
    owner: str
    table_name: str
    action: str
    process_date: datetime
    status: str
    started: datetime
    ended: datetime | None
    message: str | None
    rows: int


@dataclass(frozen=True)
class ControlEntry:
    """The control row of a table: where its processing stands now."""

    owner: str
    table_name: str
    status: str
    run_id: str | None  # runner that owns the row
    action: str
    process_date: datetime
    started: datetime | None
    ended: datetime | None
    rows: int
    last_activity: datetime | None  # last time the row was written (heartbeat)
    session: str | None  # engine-specific id of the session that runs the table, when registered


@dataclass(frozen=True)
class TableRunRecord:
    """What the control and log tables hold about one table within one execution."""

    run_id: str  # runner that owns the control row; empty when there is none
    owner: str
    table_name: str
    action: str
    process_date: datetime | None
    started: datetime | None
    ended: datetime | None
    last_status: str  # status of the last log row of the table in this run
    message: str | None  # message of that last row
    rows: int  # rows moved and committed in this run
    in_progress: bool  # the control row still belongs to this run in an in-progress status
    skipped: bool  # the table was refused because it was already processed: neither an error nor work
    last_activity: datetime | None  # heartbeat of an in-progress table
    session: str | None  # engine-specific id of the session of an in-progress table, when it was registered


@dataclass(frozen=True)
class MonitoringAccess:
    """Which session views the runtime user can read, and the statements that would grant the rest."""

    session: bool  # session liveness and current wait
    progress: bool  # long-operation progress
    waits: bool  # accumulated waits and CPU of the session
    grants: tuple[str, ...] = ()


@dataclass(frozen=True)
class WaitItem:
    """Time spent in one wait event (or in CPU), in seconds."""

    name: str
    seconds: float


@dataclass(frozen=True)
class OperationProgress:
    """Progress of the long operation a session is running."""

    percent: float
    remaining_seconds: float | None
    description: str | None


@dataclass(frozen=True)
class SessionActivity:
    """Live facts of one session. ``alive`` is None when it could not be verified."""

    alive: bool | None
    current_wait: WaitItem | None  # only when the session has been waiting for a significant time
    progress: OperationProgress | None
    waits: tuple[WaitItem, ...]  # every non-idle wait event plus CPU, unsorted


DdlSink = Callable[[str], None]
"""Receives the DDL an ``ensure_*`` method would otherwise execute."""

DdlFailures = list[tuple[str, str]]
"""Collects one (statement, error message) pair per statement that failed, instead of raising."""

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
    def get_fallback_expression(expression: str, fallback: str) -> str:
        """Return an expression that uses ``fallback`` when ``expression`` is null."""
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
    def rewrite_expression_identifiers(expression: str, replacements: Mapping[str, str], *, qualifier: str) -> str:
        """Rewrite prefixed identifiers using engine-specific identifier rules.

        Args:
            expression: Expression containing identifiers prefixed with ``@``.
            replacements: Mapping from normalized source identifiers to persisted identifiers.
            qualifier: SQL qualifier to apply to each replacement.
        Returns:
            The expression with every mapped identifier rendered for the database engine.
        """
        pass

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
    def generate_sql_block(config: Config, table_cnf: Mapping[str, Any], process_date: str) -> str:
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
    def sql_block_run(conn: Any, plsql_code: str, mode: str, run_id: str) -> None:
        """Executes a PL/SQL or SQL block against the database.
        Args:
            conn: Active database connection.
            plsql_code: The PL/SQL or SQL block to execute.
            mode: EXECUTE or PREVIEW, handed to the block.
            run_id: Identifier of the runner that owns the table, handed to the block."""
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
        rows_processed: int = 0,
        run_id: str | None = None,
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
            plsql_code: PL/SQL or SQL block executed.
            rows_processed: Rows already moved and committed before the failure.
            run_id: Identifier of the runner that owns the table; the error is dropped when it lost the table."""
        pass

    @staticmethod
    @abstractmethod
    def get_resumed_rows(conn: Any, owner: str, table_name: str, process_date: str) -> int:
        """Returns the rows kept when an EXECUTE continues an incomplete EXECUTE of the same process date.
        Args:
            conn: Active database connection.
            owner: Schema owner of the table.
            table_name: Table name.
            process_date: Target process date in 'YYYYMMDD'.
        Returns:
            Rows already processed by the incomplete run, or 0 when the table starts over."""
        pass

    @staticmethod
    @abstractmethod
    def error_recorded_since(conn: Any, owner: str, table_name: str, since: datetime) -> bool:
        """Tells whether the control table already holds an error written at or after ``since``.
        Args:
            conn: Active database connection.
            owner: Schema owner of the table.
            table_name: Table name.
            since: Start of the run whose failure is being recorded."""
        pass

    @staticmethod
    @abstractmethod
    def get_log_entries(conn: Any, max_rows: int) -> list[LogEntry]:
        """Returns the most recent ``max_rows`` rows of the execution log, newest first.
        Args:
            conn: Active database connection.
            max_rows: Number of rows to read at most."""
        pass

    @staticmethod
    @abstractmethod
    def get_control_entries(conn: Any) -> list[ControlEntry]:
        """Returns the control row of every table: the current state of its processing.
        Args:
            conn: Active database connection."""
        pass

    # Optional capabilities of the STATUS mode: an engine without live statistics keeps these defaults, and the
    # report still shows states and rows from the control tables.

    @staticmethod
    def is_run_alive(conn: Any, owner: str, table_name: str, run_id: str) -> bool | None:
        """Tells whether the runner that owns a table is alive by trying its mutex without waiting.
        The lock is released at once, so the probe never holds anything. Optional: the default cannot tell.
        Args:
            conn: Active database connection (its own connection: it is rolled back).
            owner: Schema owner of the table.
            table_name: Table name.
            run_id: Run recorded for the table.
        Returns:
            True when another session holds the table's runner lock, False when nobody does, and None when
            the run has no runner lock (a generated SCRIPT) or the lock cannot be probed."""
        return None

    @staticmethod
    def check_monitoring_access(conn: Any, grantee: str) -> MonitoringAccess:
        """Probes the session-monitoring facilities with the connection's own privileges. Optional: the default
        reports that the engine offers none, so STATUS skips them without grants or scripts.
        Args:
            conn: Active database connection.
            grantee: User or role named in the grant hints."""
        return MonitoringAccess(session=False, progress=False, waits=False)

    @staticmethod
    def get_session_activity(
        conn: Any, session: str, access: MonitoringAccess, min_wait_seconds: float
    ) -> SessionActivity:
        """Reads the liveness, long-operation progress, current wait and wait profile of a session.
        Only what ``access`` allows is read; the rest comes back empty (``alive`` None without session access).
        Optional: the default reports nothing.
        Args:
            conn: Active database connection.
            session: Session registered by the run.
            access: Result of ``check_monitoring_access``.
            min_wait_seconds: A current wait shorter than this is not reported."""
        return SessionActivity(alive=None, current_wait=None, progress=None, waits=())

    @staticmethod
    @abstractmethod
    def acquire_run_locks(
        lock_conn: Any,
        key_conn: Any,
        tables: Sequence[tuple[str, str]],
        run_id: str,
        mode: str,
        host: str,
        pid: int,
    ) -> None:
        """Takes the per-table runner mutex and publishes ``run_id`` for each table.
        Args:
            lock_conn: Dedicated connection that holds the locks until released; it must not commit.
            key_conn: Separate connection that writes and commits the anchors and the keys.
            tables: (owner, table_name) pairs to protect.
            run_id: Identifier of this run.
            mode: EXECUTE or PREVIEW.
            host: Host of the runner.
            pid: Process id of the runner.
        Raises:
            RunnerBusyError: Another runner holds one of the tables; nothing is left locked."""
        pass

    @staticmethod
    @abstractmethod
    def check_run_locks(lock_conn: Any) -> None:
        """Verifies that the lock connection is alive.
        Raises:
            ExecutionError: The connection was lost, so the mutex cannot be trusted."""
        pass

    @staticmethod
    @abstractmethod
    def release_run_locks(lock_conn: Any) -> None:
        """Frees the runner locks held by ``lock_conn``."""
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
    def get_primary_key_columns(
        conn: Any, owner: str, table_name: str, table_cnf: Mapping[str, Any]
    ) -> tuple[str, ...]:
        """Returns the primary key columns for the specified table."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_roles(conn: Any, roles: Sequence[RoleDefinition], *, ddl_sink: DdlSink | None = None) -> list[str]:
        """Ensure that the provided roles exist, returning newly created ones.

        ``ddl_sink`` (``conn`` must be None): nothing is read or executed; the sink receives the
        script fragments a DBA runs to create the roles, each ready to be written as is, tolerating
        only "already exists" so the script can be rerun. Returns an empty list."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_users(conn: Any, users: Sequence[UserDefinition], *, ddl_sink: DdlSink | None = None) -> list[str]:
        """Ensure that the provided users exist, returning newly created ones.

        ``ddl_sink`` (``conn`` must be None): as in ``ensure_roles``. The fragments hold no
        password: they prompt for it and warn when a user already exists, since its password is kept."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_tables(conn: Any, tables: Sequence[TableDefinition], *, ddl_sink: DdlSink | None = None) -> list[str]:
        """Ensure that the provided tables exist, returning newly created ones.

        ``ddl_sink`` (``conn`` must be None): as in ``ensure_roles``."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_table_structure(
        conn: Any,
        table: TableDefinition,
        table_cnf: Mapping[str, Any],
        *,
        reference: tuple[str, str] | None = None,
        ddl_sink: DdlSink | None = None,
        ddl_failures: DdlFailures | None = None,
    ) -> None:
        """Ensure that the provided table matches the expected structure.

        The statements needed are computed from dictionary views readable with a non-administrative
        connection; nothing is changed when the table already matches.

        - ``ddl_sink``: the statements go to the sink instead of being executed.
        - ``ddl_failures``: when executing, a statement that fails is recorded here and the next one
          is attempted; without it the first failure raises.
        - ``reference``: an (owner, table name) whose structure is taken as the current one instead
          of the table's own.
        - ``conn`` None (needs ``ddl_sink``): no table is read and everything is created."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_sequences(
        conn: Any, sequences: Sequence[SequenceDefinition], *, ddl_sink: DdlSink | None = None
    ) -> list[str]:
        """Ensure that the provided sequences exist, returning newly created ones.

        ``ddl_sink`` (``conn`` must be None): as in ``ensure_roles``."""
        pass

    @staticmethod
    @abstractmethod
    def script_password_prompts(
        users: Sequence[UserDefinition], links: Sequence[DatabaseLinkDefinition], *, ddl_sink: DdlSink
    ) -> None:
        """Hand ``ddl_sink`` the fragments that ask the DBA for every password a script needs, hidden.

        A script calls it first, so all passwords are asked up front. The users' passwords are asked in
        order, then the links'; the fragments of ``ensure_users`` and ``ensure_database_links`` only use
        the answers. Nothing is read from the database."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_database_links(
        conn: Any,
        links: Sequence[DatabaseLinkDefinition],
        *,
        ddl_sink: DdlSink | None = None,
        connect_as: tuple[str, str] | None = None,
    ) -> list[str]:
        """Ensure that the provided database links exist, returning newly created ones.

        ``ddl_sink`` (``conn`` must be None): as in ``ensure_roles``, plus ``connect_as``, the
        (username, dsn) of the runtime user that must own the links: the fragments reconnect as that
        user with the password already asked by ``script_password_prompts`` for it in the same script (it must be
        the first user), and use the password of each link asked there."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_table_privileges(
        conn: Any,
        role: str,
        tables: Sequence[tuple[str, str]],
        privileges: Sequence[str],
        *,
        ddl_sink: DdlSink | None = None,
        ddl_failures: DdlFailures | None = None,
    ) -> list[str]:
        """Ensure the role has the specified privileges on each table, returning new grants.

        Grants the role already holds are skipped. ``ddl_sink`` and ``ddl_failures`` work as in
        ``ensure_table_structure``. With ``conn`` None (needs ``ddl_sink``) nothing is read, so every
        grant is emitted; they are safe to run again."""
        pass

    @staticmethod
    @abstractmethod
    def get_missing_table_privileges(
        conn: Any, role: str, tables: Sequence[tuple[str, str]], privileges: Sequence[str]
    ) -> list[tuple[str, str, str]]:
        """Return the ``(owner, table, privilege)`` the role lacks, read with the connection's own user.

        Needs no admin credentials: it reports what the connected user receives through the role, so it
        also answers what a runtime user can do. A role that is not enabled for the user grants nothing,
        so every privilege is then reported missing. Nothing is granted or changed."""
        pass

    @staticmethod
    @abstractmethod
    def is_role_enabled(conn: Any, role: str) -> bool:
        """Return whether the role is enabled for the connected user (granted to it and active in the session)."""
        pass

    @staticmethod
    @abstractmethod
    def generate_table_privilege_script(
        role: str, tables: Sequence[tuple[str, str]], privileges: Sequence[str]
    ) -> list[str]:
        """Return the statements that grant the privileges on each table to the role, without executing them.
        The statements are unconditional and safe to run again."""
        pass

    @staticmethod
    @abstractmethod
    def ensure_supporting_objects(conn: Any, owner: str, *, ddl_sink: DdlSink | None = None) -> list[str]:
        """Ensure auxiliary PL/SQL objects required by LimitsDb exist in the schema.

        ``ddl_sink`` (``conn`` must be None): as in ``ensure_roles``."""
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
