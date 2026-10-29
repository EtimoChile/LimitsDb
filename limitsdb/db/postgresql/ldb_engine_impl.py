"""PostgreSQL engine (9.5 or later) built on the pure-Python ``pg8000`` driver.

Rows travel from the SOURCE to the HISTORY database through ``dblink`` (a ``dblink_fdw`` foreign server and user
mapping, so no credential is ever written in a block), never through the runner. See DEC-048 for the design.
"""

import json
import re
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Literal

import pg8000.dbapi

from limitsdb.core.ldb_errors import (
    DatabaseConnectionError,
    ExecutionError,
    InvalidCredentialsError,
    RunnerBusyError,
)
from limitsdb.core.ldb_logger import get_logger
from limitsdb.core.ldb_params_config import Config
from limitsdb.core.ldb_status import Status
from limitsdb.core.ldb_utils import get_effective_credentials, indent_lines, join_wrapped, nvl
from limitsdb.db.ldb_engines import (
    ColumnDefinition,
    ColumnMetadata,
    ControlEntry,
    DatabaseEngine,
    DatabaseLinkDefinition,
    DdlFailures,
    DdlSink,
    IndexMap,
    LogEntry,
    MonitoringAccess,
    RoleDefinition,
    SequenceDefinition,
    SessionActivity,
    TableDefinition,
    UserDefinition,
    WaitItem,
)

logger = get_logger("postgresql.engine")

PROGRAM_MARK = "--@@ldb-program "
SECTION_MARK = "--@@ "

# Names are matched in the lower-case SQL written in this package and rendered quoted upper case, because the
# control tables are created from their upper-case definitions (see ldb_impl) and PostgreSQL folds bare names down.
_CONTROL_NAMES = re.compile(r"\b(ldb_ctl|ldb_log_id|ldb_log|ldb_run_lock|ctl_[a-z_]+|log_[a-z_]+|rl_[a-z_]+)\b")

# Reserved words that cannot be used as bare identifiers (PostgreSQL 9.5 "reserved" column of the key words table)
_RESERVED = frozenset(
    """all analyse analyze and any array as asc asymmetric authorization binary both case cast check collate collation
    column concurrently constraint create cross current_catalog current_date current_role current_schema current_time
    current_timestamp current_user default deferrable desc distinct do else end except false fetch for foreign freeze
    from full grant group having ilike in initially inner intersect into is isnull join lateral leading left like limit
    localtime localtimestamp natural not notnull null offset on only or order outer overlaps placing primary references
    returning right select session_user similar some symmetric table then to trailing true union unique user using
    variadic verbose when where window with""".split()
)
_BARE = re.compile(r"[a-z_][a-z0-9_$]*")


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def format_identifier(name: str | None) -> str:
    """Render a catalog name for SQL: bare when PostgreSQL would fold it to itself, quoted exact otherwise."""
    if not name:
        return '""'
    if _BARE.fullmatch(name) and name not in _RESERVED:
        return name
    return quote(name)


def control(sql: str) -> str:
    """Quote and upper-case the names of the control objects found in ``sql``."""
    return _CONTROL_NAMES.sub(lambda match: quote(match.group(1).upper()), sql)


_ABORT_CODES = "20001, 20002, 20003, 20005, 20006"
_ACTIVE = "'TSTART', 'CSTART', 'CEND', 'LSTART', 'LEND', 'PTSTART', 'PCSTART', 'PCEND', 'PLSTART', 'PLEND'"

CHECK_SAVE_STATUS = """
CREATE OR REPLACE FUNCTION {owner}.check_save_status(
    p_owner varchar, p_table_name varchar, p_process_date timestamp, p_action varchar, p_status varchar,
    p_process_start timestamp, p_chunk_start timestamp, p_process_end timestamp, p_message varchar,
    p_rows_processed bigint, p_plsql text, p_run_id varchar, p_sqlcode integer,
    OUT o_sqlcode integer, OUT o_message varchar)
RETURNS record AS $fn$
DECLARE
    l_status varchar;
    l_action varchar;
    l_process_start timestamp;
    l_process_end timestamp;
    l_process_date timestamp;
    l_rows bigint;
    l_ctl_run_id varchar;
    l_found boolean := true;
    l_keep_rows boolean := false;
    l_key varchar;
    l_now timestamp := localtimestamp(0);
    l_session varchar;
BEGIN
    o_sqlcode := p_sqlcode;
    o_message := NULL;
    IF coalesce(p_sqlcode, 0) NOT IN ({abort_codes}) THEN
        BEGIN
            SELECT ctl_status, ctl_action, ctl_process_date, ctl_process_start, ctl_process_end,
                   coalesce(ctl_rows_processed, 0), ctl_run_id
              INTO STRICT l_status, l_action, l_process_date, l_process_start, l_process_end, l_rows, l_ctl_run_id
              FROM ldb_ctl
             WHERE ctl_owner = p_owner AND ctl_table_name = p_table_name
               FOR UPDATE NOWAIT;
        EXCEPTION
            WHEN lock_not_available THEN
                o_sqlcode := 20001;
                o_message := 'Table ' || p_table_name || ' is currently in process';
                RETURN;
            WHEN no_data_found THEN
                l_found := false;
        END;

        -- A SCRIPT run_id starts with S and has no key row: the run is identified by ldb_ctl.ctl_run_id.
        -- PL/pgSQL cannot ROLLBACK: every refusal returns before anything is written and the client rolls back.
        IF p_run_id IS NOT NULL AND p_run_id NOT LIKE 'S%'
           AND p_status IN ('TSTART', 'PTSTART', 'CSTART', 'PCSTART', 'LSTART', 'PLSTART', 'ERROR', 'PERROR') THEN
            SELECT rl_run_id INTO l_key FROM ldb_run_lock
             WHERE rl_owner = p_owner AND rl_table_name = p_table_name AND rl_kind = 'K';
            IF l_key IS NULL OR l_key <> p_run_id THEN
                o_sqlcode := 20005;
                o_message := 'Table ' || p_table_name || ' is now owned by another run';
                RETURN;
            END IF;
        END IF;

        IF p_run_id IS NOT NULL AND p_status IN ('CSTART', 'PCSTART', 'LSTART', 'PLSTART', 'ERROR', 'PERROR')
           AND l_ctl_run_id IS NOT NULL AND l_ctl_run_id <> p_run_id THEN
            o_sqlcode := 20005;
            o_message := 'Table ' || p_table_name || ' is now owned by another run';
            RETURN;
        END IF;

        IF l_found AND p_status IN ('TSTART', 'PTSTART') THEN
            -- The heartbeat is the last chunk end: a live run refreshes it between chunks, when the row is not locked
            IF l_status IN ({active})
               AND extract(epoch FROM (l_now - greatest(l_process_start, coalesce(l_process_end, l_process_start)))) < 5
            THEN
                o_sqlcode := 20002;
                o_message := 'Table ' || p_table_name || ' is currently in process without lock';
                RETURN;
            ELSIF p_status = 'PTSTART' AND l_process_date = p_process_date
                  AND l_status IN ('TSTART', 'CSTART', 'CEND', 'LSTART', 'LEND', 'TEND', 'ERROR') THEN
                o_sqlcode := 20006;
                o_message := 'Table ' || p_table_name || ' has an EXECUTE status for date '
                             || to_char(p_process_date, 'YYYYMMDD') || ': a PREVIEW is not allowed';
                RETURN;
            ELSIF p_status = 'TSTART' AND l_status = 'TEND' AND l_process_date = p_process_date THEN
                o_sqlcode := 20003;
                o_message := 'Table ' || p_table_name || ' is already processed for date '
                             || to_char(p_process_date, 'YYYYMMDD');
                RETURN;
            END IF;

            -- Only an incomplete EXECUTE of the same process date is continued; anything else starts over
            IF p_status = 'TSTART' AND l_process_date = p_process_date
               AND l_status IN ('TSTART', 'CSTART', 'CEND', 'LSTART', 'LEND', 'ERROR') THEN
                l_keep_rows := true;
            ELSIF l_status IN ({active}) THEN
                INSERT INTO ldb_log (log_id, log_owner, log_table_name, log_process_date, log_action, log_status,
                                     log_process_start, log_process_end, log_message, log_rows_processed, log_plsql)
                VALUES (nextval('ldb_log_id'), p_owner, p_table_name, coalesce(l_process_date, p_process_date),
                        coalesce(l_action, p_action),
                        CASE WHEN l_status LIKE 'P%' THEN 'PERROR' ELSE 'ERROR' END,
                        coalesce(l_process_start, l_now), coalesce(l_process_end, l_process_start),
                        'Incomplete process closed by a later process', l_rows, NULL);
            END IF;
        END IF;

        -- The session that runs the table, for the STATUS mode: pid and start second of the backend
        SELECT pg_backend_pid() || '.' || floor(extract(epoch FROM backend_start))::bigint INTO l_session
          FROM pg_stat_activity WHERE pid = pg_backend_pid();

        IF NOT l_found THEN
            INSERT INTO ldb_ctl (ctl_owner, ctl_table_name, ctl_process_date, ctl_action, ctl_status,
                                 ctl_process_start, ctl_process_end, ctl_rows_processed, ctl_plsql, ctl_run_id,
                                 ctl_db_session_id)
            VALUES (p_owner, p_table_name, p_process_date, p_action, p_status, coalesce(p_process_start, l_now),
                    NULL, 0, p_plsql, p_run_id, l_session);
            l_ctl_run_id := p_run_id;
        END IF;

        UPDATE ldb_ctl
           SET ctl_process_date = p_process_date,
               ctl_action = p_action,
               ctl_status = p_status,
               ctl_process_start = coalesce(p_process_start, ctl_process_start),
               ctl_process_end = CASE WHEN p_status IN ('TSTART', 'PTSTART') THEN NULL
                                      ELSE coalesce(p_process_end, ctl_process_end) END,
               ctl_rows_processed = CASE
                   WHEN p_status IN ('TSTART', 'PTSTART') THEN
                       CASE WHEN l_keep_rows THEN ctl_rows_processed ELSE p_rows_processed END
                   WHEN p_status IN ('ERROR', 'PERROR') THEN ctl_rows_processed
                   ELSE ctl_rows_processed + p_rows_processed END,
               ctl_run_id = CASE WHEN p_status IN ('TSTART', 'PTSTART') THEN p_run_id ELSE ctl_run_id END,
               ctl_db_session_id = CASE WHEN p_status IN ('TSTART', 'PTSTART') THEN l_session
                                        ELSE ctl_db_session_id END,
               ctl_plsql = coalesce(p_plsql, ctl_plsql)
         WHERE ctl_owner = p_owner AND ctl_table_name = p_table_name;
    END IF;

    INSERT INTO ldb_log (log_id, log_owner, log_table_name, log_process_date, log_action, log_status,
                         log_process_start, log_process_end, log_message, log_rows_processed, log_plsql)
    VALUES (nextval('ldb_log_id'), p_owner, p_table_name, p_process_date, p_action, p_status,
            coalesce(p_chunk_start, p_process_start), p_process_end, p_message, p_rows_processed, p_plsql);
END;
$fn$ LANGUAGE plpgsql SET search_path TO {owner}
"""

CHECK_REFERENCING_TABLES = """
CREATE OR REPLACE FUNCTION {owner}.check_referencing_tables(
    p_referencing_tables varchar[], p_process_date timestamp, p_preview integer DEFAULT 0)
RETURNS void AS $fn$
DECLARE
    l_ref varchar;
    l_pos integer;
BEGIN
    IF p_referencing_tables IS NULL THEN
        RETURN;
    END IF;
    FOREACH l_ref IN ARRAY p_referencing_tables LOOP
        l_pos := position('.' IN l_ref);
        PERFORM 1 FROM ldb_ctl
         WHERE ctl_owner = substr(l_ref, 1, l_pos - 1)
           AND ctl_table_name = substr(l_ref, l_pos + 1)
           AND ctl_process_date = p_process_date
           AND (ctl_status = 'TEND' OR (p_preview = 1 AND ctl_status = 'PTEND'));
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Referencing table % was not fully processed for date %',
                l_ref, to_char(p_process_date, 'YYYYMMDD') USING ERRCODE = 'LD004';
        END IF;
    END LOOP;
END;
$fn$ LANGUAGE plpgsql SET search_path TO {owner}
"""

SUPPORTING_OBJECT_LIST = ["FUNCTION check_save_status", "FUNCTION check_referencing_tables"]


def supporting_functions(owner: str) -> list[str]:
    """The CREATE OR REPLACE statements of the supporting functions, living in schema ``owner``."""
    target = format_identifier(owner)
    return [
        control(CHECK_SAVE_STATUS.format(owner=target, abort_codes=_ABORT_CODES, active=_ACTIVE)).strip(),
        control(CHECK_REFERENCING_TABLES.format(owner=target)).strip(),
    ]


# --- the block program -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Program:
    """A parsed block program: the header facts and the SQL of each named section."""

    header: dict[str, Any]
    sections: dict[str, str]

    @property
    def owner(self) -> str:
        return str(self.header["owner"])

    @property
    def table_name(self) -> str:
        return str(self.header["table"])


def build_program(header: dict[str, Any], sections: dict[str, str]) -> str:
    parts = [PROGRAM_MARK + json.dumps(header, sort_keys=True)]
    for name, sql in sections.items():
        parts.append(SECTION_MARK + name)
        parts.append(sql.strip())
    return "\n".join(parts) + "\n"


def parse_program(text: str) -> Program:
    lines = text.splitlines()
    if not lines or not lines[0].startswith(PROGRAM_MARK):
        raise ValueError("not a LimitsDb PostgreSQL block program")
    header = json.loads(lines[0][len(PROGRAM_MARK) :])
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in lines[1:]:
        if line.startswith(SECTION_MARK):
            current = sections.setdefault(line[len(SECTION_MARK) :].strip(), [])
        elif current is not None:
            current.append(line)
    return Program(header, {name: "\n".join(body).strip() for name, body in sections.items()})


# Name of the persistent dblink connection a session opens towards the HISTORY database
_DBLINK_CONNECTION = "ldb_hist"
# Codes of check_save_status that mean the control row belongs to someone else and stays untouched
_FOREIGN_CODES = (20001, 20002, 20003, 20005, 20006)
_NOTICE_ROWS = "ldb_rows="
_CUSTOM_SQLSTATE = re.compile(r"LD(\d{3})")


@dataclass(frozen=True)
class BlockDetail:
    """What the runner reads from a refused block: the code (20000 + nnn of SQLSTATE LDnnn) and the message."""

    code: int
    message: str


class BlockAborted(Exception):
    """A block stopped by a LimitsDb rule; ``args[0]`` carries the detail the runner extracts."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(BlockDetail(code, message))
        self.code = code
        self.message = message


class _StatusAbort(Exception):
    """check_save_status refused the call (the table belongs to another run, or is already processed)."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _pg_error(exc: BaseException) -> tuple[str | None, str]:
    """(SQLSTATE, message) of a pg8000 error, whose first argument is the server's error fields."""
    fields = exc.args[0] if exc.args else None
    if isinstance(fields, dict):
        return fields.get("C"), str(fields.get("M", exc))
    return None, str(exc)


def _ld_code(sqlstate: str | None) -> int | None:
    match = _CUSTOM_SQLSTATE.fullmatch(sqlstate or "")
    return 20000 + int(match.group(1)) if match else None


def _parse_dsn(dsn: str) -> tuple[str, int | None, str]:
    """Split ``host[:port]/dbname``."""
    endpoint, _, database = dsn.partition("/")
    if not endpoint or not database:
        raise ValueError(f"Invalid PostgreSQL DSN '{dsn}': use host[:port]/dbname")
    host, _, port = endpoint.partition(":")
    return host, int(port) if port else None, database


class PostgresEngine(DatabaseEngine):
    """PostgreSQL engine: connection, catalog access, block programs, locks and DDL."""

    _connection_envs: ClassVar[dict[int, str]] = {}
    _admin_connections: ClassVar[set[int]] = set()
    REQUIRED_SYSTEM_PRIVILEGES: tuple[str, ...] = ()
    _QUALIFIED_TABLE = re.compile(
        r'(?<![\w$."])("(?:""|[^"])*"|[A-Za-z_][A-Za-z0-9_$]*)\s*\.\s*("(?:""|[^"])*"|[A-Za-z_][A-Za-z0-9_$]*)'
        r'(?![\w$"])'
    )
    _PREFIXED_IDENTIFIER = re.compile(r'@("(?:""|[^"])*"|[A-Za-z_][A-Za-z0-9_$]*)')
    _PRIVATE_DDL_MARK = re.compile(r"##(.*?)##", re.DOTALL)

    # ------------------------------------------------------------------ connection
    @staticmethod
    def get_connection(config: Config, *, admin: bool = False, env: Literal["SOURCE", "HISTORY"] | None = None) -> Any:
        user, password, dsn = get_effective_credentials(config, admin=admin, env=env)
        target = f"as {user} at {dsn}"
        try:
            host, port, database = _parse_dsn(dsn)
            connection = pg8000.dbapi.connect(
                user=user, password=password, host=host, port=port or 5432, database=database,
                application_name="LimitsDb",
            )  # fmt: skip
        except Exception as exc:
            logger.critical("Failed to connect to PostgreSQL %s.", target, exc_info=True)
            if _pg_error(exc)[0] in ("28P01", "28000"):
                raise InvalidCredentialsError(f"Failed to connect to PostgreSQL database {target}") from exc
            raise DatabaseConnectionError(f"Failed to connect to PostgreSQL database {target}") from exc
        if env:
            PostgresEngine._connection_envs[id(connection)] = env
        if admin:
            PostgresEngine._admin_connections.add(id(connection))
        return connection

    @staticmethod
    def close_connection(conn: Any) -> None:
        try:
            if conn:
                PostgresEngine._connection_envs.pop(id(conn), None)
                PostgresEngine._admin_connections.discard(id(conn))
                conn.close()
        except Exception:
            logger.critical("Failed to close PostgreSQL connection.", exc_info=True)

    @staticmethod
    def get_system_date(conn: Any) -> datetime:
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT localtimestamp(0)")
            sysdate: datetime = cursor.fetchone()[0]
            return sysdate
        except Exception as exc:
            logger.critical("Failed to retrieve system date from PostgreSQL.", exc_info=True)
            raise ExecutionError("Failed to retrieve system date from PostgreSQL") from exc
        finally:
            if cursor:
                cursor.close()

    # ------------------------------------------------------------------ catalog
    @staticmethod
    def get_table_columns(conn: Any, owner: str, table_name: str) -> tuple[list[str], dict[str, ColumnDefinition]]:
        cursor = conn.cursor()
        columns: list[str] = []
        metadata: dict[str, ColumnDefinition] = {}
        try:
            cursor.execute(
                """SELECT ordinal_position, column_name, data_type, character_maximum_length, numeric_precision,
                          numeric_scale, is_nullable, column_default, datetime_precision
                     FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position""",
                [owner, table_name],
            )
            for (
                position,
                name,
                data_type,
                length,
                precision,
                scale,
                nullable,
                default,
                dt_precision,
            ) in cursor.fetchall():
                dtype, precision, scale = PostgresEngine._generic_type(data_type, precision, scale, dt_precision)
                metadata[name] = ColumnDefinition(
                    name=name,
                    data_type=dtype,
                    id=int(position),
                    length=int(length) if length is not None else None,
                    precision=precision,
                    scale=scale,
                    nullable=nullable == "YES",
                    default=default.strip() if isinstance(default, str) else None,
                )
                columns.append(name)
        finally:
            cursor.close()
        return columns, metadata

    @staticmethod
    def _generic_type(
        data_type: str, precision: Any, scale: Any, dt_precision: Any
    ) -> tuple[str, int | None, int | None]:
        """Map an information_schema type to the engine-neutral names used by the configuration."""
        dtype = data_type.lower()
        fixed = {"smallint": 5, "integer": 10, "bigint": 19}
        if dtype in fixed:
            return "number", fixed[dtype], 0
        if dtype == "numeric":
            return "number", None if precision is None else int(precision), None if scale is None else int(scale)
        if dtype == "character varying":
            return "varchar", None, None
        if dtype == "character":
            return "char", None, None
        if dtype == "text":
            return "clob", None, None
        if dtype == "date" or (dtype == "timestamp without time zone" and dt_precision == 0):
            return "date", None, None
        if dtype == "timestamp without time zone":
            return "timestamp", None, None
        if dtype == "timestamp with time zone":
            return "timestamptz", None, None
        return dtype, None if precision is None else int(precision), None if scale is None else int(scale)

    @staticmethod
    def get_column_type(column: ColumnDefinition) -> str:
        dtype = column.data_type.lower()
        if dtype in ("string", "varchar", "varchar2", "character varying"):
            return f"varchar({column.length or 255})"
        if dtype in ("char", "character"):
            return f"char({column.length or 1})"
        if dtype in ("number", "numeric", "decimal"):
            if column.precision is not None and column.scale is not None:
                return f"numeric({column.precision},{column.scale})"
            if column.precision is not None:
                return f"numeric({column.precision})"
            return "numeric"
        if dtype in ("integer", "int"):
            return "numeric(10)"
        if dtype == "date":
            return "timestamp(0)"
        if dtype in ("clob", "text"):
            return "text"
        if dtype in ("timestamp", "timestamptz", "bytea", "boolean", "real", "double precision"):
            return dtype
        raise ValueError(f"Unsupported column data type: {column.data_type}")

    @staticmethod
    def get_primary_key_columns(
        conn: Any, owner: str, table_name: str, table_cnf: Mapping[str, Any]
    ) -> tuple[str, ...]:
        cursor = conn.cursor()
        try:
            indexes = PostgresEngine._read_indexes(cursor, owner, table_name)
            for info in indexes.values():
                if info["primary"]:
                    return tuple(info["columns"])
            if not indexes:
                return ()
            cursor.execute(
                """SELECT attname, n_distinct FROM pg_stats WHERE schemaname = %s AND tablename = %s""",
                [owner, table_name],
            )
            distinct = {name: value for name, value in cursor.fetchall()}
            cursor.execute(
                """SELECT c.reltuples FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = %s AND c.relname = %s""",
                [owner, table_name],
            )
            row = cursor.fetchone()
            tuples = float(row[0]) if row and row[0] and row[0] > 0 else None
            for info in indexes.values():
                estimate = distinct.get(info["columns"][0])
                if estimate is None:
                    info["distinct_keys"] = None
                elif estimate >= 0:
                    info["distinct_keys"] = int(estimate)
                else:
                    info["distinct_keys"] = int(-estimate * tuples) if tuples else None
            chosen = PostgresEngine._choose_best_index_for_primary_key(indexes, table_cnf.get("columns_metadata", {}))
            return tuple(chosen) if chosen else ()
        finally:
            cursor.close()

    @staticmethod
    def _read_indexes(cursor: Any, owner: str, table_name: str) -> IndexMap:
        """Plain (non-partial, non-expression) indexes of a table: name -> columns, uniqueness, primary flag."""
        cursor.execute(
            """SELECT ic.relname, i.indisunique, i.indisprimary, a.attname
                 FROM pg_index i
                 JOIN pg_class c ON c.oid = i.indrelid
                 JOIN pg_namespace n ON n.oid = c.relnamespace
                 JOIN pg_class ic ON ic.oid = i.indexrelid
                 JOIN generate_series(0, 31) AS k(pos) ON k.pos < i.indnatts
                 JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = i.indkey[k.pos]
                WHERE n.nspname = %s AND c.relname = %s AND i.indpred IS NULL AND i.indexprs IS NULL
                ORDER BY ic.relname, k.pos""",
            [owner, table_name],
        )
        indexes: IndexMap = {}
        for name, unique, primary, column in cursor.fetchall():
            info = indexes.setdefault(name, {"columns": [], "unique": bool(unique), "primary": bool(primary)})
            info["columns"].append(column)
        return indexes

    @staticmethod
    def _choose_best_index_for_primary_key(
        indexes: IndexMap, column_metadata: ColumnMetadata
    ) -> tuple[str, ...] | None:
        candidates: list[tuple[bool, bool, int, int, str, tuple[str, ...]]] = []
        for name, info in indexes.items():
            definitions = [column_metadata.get(col) for col in info["columns"]]
            if not definitions or any(d is None for d in definitions):
                continue
            all_not_null = all(d is not None and not d.nullable for d in definitions)
            distinct = info.get("distinct_keys")
            candidates.append(
                (
                    not (info["unique"] and all_not_null),
                    not info["unique"],
                    -(distinct if isinstance(distinct, int) else -1),
                    len(info["columns"]),
                    name,
                    tuple(info["columns"]),
                )
            )
        return min(candidates)[5] if candidates else None

    # ------------------------------------------------------------------ expressions and identifiers
    @staticmethod
    def get_date_condition(date_expr: str, months_keep_src: int) -> str:
        return f"({date_expr} < (l_process_date - interval '{int(months_keep_src)} months'))"

    @staticmethod
    def get_fallback_expression(expression: str, fallback: str) -> str:
        return f"coalesce({expression}, {fallback})"

    @staticmethod
    def get_ldb_columns_expressions() -> tuple[str, str]:
        return "l_process_date", "localtimestamp(0)"

    @staticmethod
    def get_identifiers_from_expression(expression: str) -> set[str]:
        return {
            PostgresEngine.get_identifier_str(match.group(1))
            for match in PostgresEngine._PREFIXED_IDENTIFIER.finditer(expression)
        }

    @staticmethod
    def rewrite_expression_identifiers(expression: str, replacements: Mapping[str, str], *, qualifier: str) -> str:
        def replace_identifier(match: re.Match[str]) -> str:
            replacement = replacements.get(PostgresEngine.get_identifier_str(match.group(1)))
            if replacement is None:
                return match.group(0)
            return f"{qualifier}.{format_identifier(replacement)}"

        return PostgresEngine._PREFIXED_IDENTIFIER.sub(replace_identifier, expression)

    @staticmethod
    def rewrite_table_owners(expression: str, owners: Mapping[tuple[str, str], str]) -> str:
        def replace_owner(match: re.Match[str]) -> str:
            owner_token, table_token = match.group(1), match.group(2)
            source_owner = PostgresEngine.get_identifier_str(owner_token)
            new_owner = owners.get((source_owner, PostgresEngine.get_identifier_str(table_token)))
            if new_owner is None or new_owner == source_owner:
                return match.group(0)
            return f"{format_identifier(new_owner)}.{table_token}"

        return PostgresEngine._QUALIFIED_TABLE.sub(replace_owner, expression)

    @staticmethod
    def format_identifier(name: str) -> str:
        return format_identifier(name)

    @staticmethod
    def get_identifier_str(identifier: str) -> str:
        """The catalog form of a name written in SQL: quoted names exactly, bare names folded to lower case."""
        identifier = identifier.strip()
        if identifier.startswith('"') and identifier.endswith('"'):
            return identifier[1:-1].replace('""', '"')
        return identifier.lower()

    # ------------------------------------------------------------------ block generation
    @staticmethod
    def generate_sql_block(config: Config, table_cnf: Mapping[str, Any], process_date: str) -> str:
        """Build the block program of a table (see ``build_program``), or the single DO block of a SCRIPT run."""
        execution = config.execution
        cnd0 = table_cnf["conds"][0]
        source_owner, history_owner, table_name = cnd0["source_owner"], cnd0["history_owner"], cnd0["table_name"]
        source_ilm = execution.action == "SOURCE_ILM"
        runtime_owner = source_owner if source_ilm else history_owner
        other_columns = table_cnf["other_columns"]
        aliases = [col["name"] for col in other_columns]
        table_columns = list(table_cnf["table_columns"])
        query_expr = table_cnf["query_expr"]
        copy = source_ilm and nvl(table_cnf["months_keep_history_max"], 1) > 0
        check_refs = source_ilm or not execution.use_added_columns
        refs = [f"{owner}.{name}" for owner, name in table_cnf["referencing_tables"]]
        script = execution.generate_script
        if script:
            process_date = "00000000"
        date_literal = f"{process_date[:4]}-{process_date[4:6]}-{process_date[6:8]} 00:00:00"
        names = [*table_columns, *aliases]
        fmt = format_identifier
        select_items = ["A.ctid AS ldb_rowid"]
        if copy:
            select_items += [f"A.{fmt(col)}" for col in table_columns]
            select_items += [f"{col['expr']} AS {fmt(col['name'])}" for col in other_columns]
        select_list = join_wrapped(", ", select_items, 200)
        values_expr = " || ',' || ".join(f"quote_nullable(v.{fmt(col)})" for col in names)
        insert_prefix = (
            f"INSERT INTO {fmt(history_owner)}.{fmt(table_name)} ({join_wrapped(', ', [fmt(c) for c in names], 200)}) "
            "VALUES "
        )
        target = f"{fmt(runtime_owner)}.{fmt(table_name)}"
        declare = f"l_process_date timestamp := timestamp '{date_literal}';"
        aggregate = "array_agg(v.ldb_rowid)" + (f", string_agg('(' || {values_expr} || ')', ',')" if copy else "")
        into = "l_ids" + (", l_values" if copy else "")
        chunk_select = (
            f"SELECT {aggregate}, count(*)\n      INTO {into}, l_n\n"
            f"      FROM (SELECT {indent_lines(select_list, 12)}\n"
            f"            {indent_lines(query_expr, 12)}\n            LIMIT {execution.chunk_size}) v;"
        )
        move = ""
        if copy:
            move += f"        PERFORM dblink_exec({quote_literal(_DBLINK_CONNECTION)}, {quote_literal(insert_prefix)} || l_values);\n"
        move += f"        DELETE FROM {target} WHERE ctid = ANY(l_ids);"
        count_select = f"SELECT count(*) INTO l_n\n      {indent_lines(query_expr, 6)};"
        connect = (
            f"IF NOT coalesce({quote_literal(_DBLINK_CONNECTION)} = ANY(dblink_get_connections()), false) THEN\n"
            f"        PERFORM dblink_connect({quote_literal(_DBLINK_CONNECTION)}, "
            f"{quote_literal(config.administration.source_to_history_dblink_name)});\n    END IF;"
        )
        if script:
            return PostgresEngine._script_block(
                config=config, owner=source_owner, table_name=table_name, refs=refs, check_refs=check_refs,
                copy=copy, chunk_select=chunk_select, count_select=count_select, move=move,
                connect=connect,
            )  # fmt: skip
        header = {
            "v": 1,
            "owner": source_owner,
            "table": table_name,
            "action": execution.action,
            "date": process_date,
            "refs": refs,
            "check_refs": check_refs,
            "copy": copy,
            "dblink": _DBLINK_CONNECTION,
        }
        sections: dict[str, str] = {}
        if copy:
            sections["connect"] = f"DO $ldb$\nBEGIN\n    {connect}\nEND\n$ldb$"
        sections["chunk"] = (
            f"DO $ldb$\nDECLARE\n    {declare}\n    l_ids tid[];\n    l_values text;\n    l_n integer;\nBEGIN\n"
            f"    {chunk_select}\n    IF l_n > 0 THEN\n{move}\n    END IF;\n"
            f"    RAISE NOTICE '{_NOTICE_ROWS}%', l_n;\nEND\n$ldb$"
        )
        sections["count"] = (
            f"DO $ldb$\nDECLARE\n    {declare}\n    l_n bigint;\nBEGIN\n    {count_select}\n"
            f"    RAISE NOTICE '{_NOTICE_ROWS}%', l_n;\nEND\n$ldb$"
        )
        return build_program(header, sections)

    @staticmethod
    def _script_block(
        *,
        config: Config,
        owner: str,
        table_name: str,
        refs: list[str],
        check_refs: bool,
        copy: bool,
        chunk_select: str,
        count_select: str,
        move: str,
        connect: str,
    ) -> str:
        """The DO block of one table in a SCRIPT: the whole table in one transaction (no ERROR row on failure)."""

        def status(code: str, *, start: str = "NULL", chunk: str = "NULL", end: str = "NULL", rows: str = "0") -> str:
            return (
                f"SELECT * INTO l_res FROM check_save_status(l_source_owner, l_table_name, l_process_date, l_action,\n"
                f"        l_pfx || '{code}', {start}, {chunk}, {end}, l_message, {rows}, NULL, l_run_id, NULL);\n"
                "    IF l_res.o_sqlcode IS NOT NULL THEN\n"
                "        RAISE EXCEPTION '%', l_res.o_message\n"
                "            USING ERRCODE = 'LD' || lpad((l_res.o_sqlcode - 20000)::text, 3, '0');\n"
                "    END IF;"
            )

        refs_array = "ARRAY[" + ", ".join(quote_literal(ref) for ref in refs) + "]::varchar[]"
        lines = [
            "DO $ldb$",
            "DECLARE",
            f"    l_source_owner varchar := {quote_literal(owner)};",
            f"    l_table_name varchar := {quote_literal(table_name)};",
            f"    l_action varchar := {quote_literal(config.execution.action)};",
            "    l_mode varchar := current_setting('ldb.mode');",
            "    l_pfx varchar;",
            "    l_message varchar;",
            "    l_run_id varchar := 'S' || md5(random()::text || clock_timestamp()::text);",
            "    l_process_date timestamp := to_date(current_setting('ldb.process_date'), 'YYYYMMDD')::timestamp;",
            "    l_process_start timestamp;",
            "    l_chunk_start timestamp;",
            "    l_ids tid[];",
            "    l_values text;",
            "    l_n bigint := 1;",
            "    l_res record;",
            "BEGIN",
            "    IF l_mode NOT IN ('EXECUTE', 'PREVIEW') THEN",
            "        RAISE EXCEPTION 'Invalid ldb_mode %: use EXECUTE or PREVIEW', l_mode USING ERRCODE = 'LD007';",
            "    END IF;",
            "    l_pfx := CASE WHEN l_mode = 'PREVIEW' THEN 'P' ELSE '' END;",
            "    l_message := CASE WHEN l_mode = 'PREVIEW' THEN 'PREVIEW' ELSE 'SCRIPT' END;",
            "    l_process_start := localtimestamp(0);",
            "    " + status("TSTART", start="l_process_start"),
        ]
        if check_refs:
            lines.append(
                f"    PERFORM check_referencing_tables({refs_array}, l_process_date, "
                "CASE WHEN l_mode = 'PREVIEW' THEN 1 ELSE 0 END);"
            )
        if copy:
            lines.append(f"    IF l_mode = 'EXECUTE' THEN\n    {connect}\n    END IF;")
        lines += [
            "    IF l_mode = 'PREVIEW' THEN",
            "        l_chunk_start := localtimestamp(0);",
            "        " + status("CSTART", chunk="l_chunk_start"),
            "        " + count_select,
            "        IF l_n > 0 THEN",
            "            " + status("CEND", chunk="l_chunk_start", end="localtimestamp(0)", rows="l_n"),
            "        END IF;",
            "    ELSE",
            "        LOOP",
            "            l_chunk_start := localtimestamp(0);",
            "            " + status("CSTART", chunk="l_chunk_start"),
            "            " + chunk_select,
            "            EXIT WHEN l_n = 0;",
            "    " + move.replace("\n        ", "\n            "),
            "            " + status("CEND", chunk="l_chunk_start", end="localtimestamp(0)", rows="l_n"),
            "        END LOOP;",
            "    END IF;",
            "    " + status("TEND", start="l_process_start", end="localtimestamp(0)"),
        ]
        if copy:
            lines.append(
                f"    IF l_mode = 'EXECUTE' THEN PERFORM dblink_disconnect({quote_literal(_DBLINK_CONNECTION)}); END IF;"
            )
        lines += ["END", "$ldb$;"]
        return "\n".join(lines)

    # ------------------------------------------------------------------ block execution
    @staticmethod
    def sql_block_run(conn: Any, plsql_code: str, mode: str, run_id: str) -> None:
        """Run a block program phase by phase on ``conn``, keeping the status through ``check_save_status``."""
        if mode not in ("EXECUTE", "PREVIEW"):
            raise BlockAborted(20007, f"Invalid ldb_mode {mode}: use EXECUTE or PREVIEW")
        program = parse_program(plsql_code)
        header = program.header
        preview = mode == "PREVIEW"
        prefix = "P" if preview else ""
        message = "PREVIEW" if preview else None
        process_date = datetime.strptime(str(header["date"]), "%Y%m%d")
        copy = bool(header["copy"]) and not preview
        cursor = conn.cursor()
        started: datetime | None = None
        rows = 0

        def call(status: str, **kwargs: Any) -> None:
            code, text = PostgresEngine._call_status(
                cursor, program.owner, program.table_name, process_date, header["action"], prefix + status,
                run_id=run_id, **kwargs,
            )  # fmt: skip
            if code is not None:
                raise _StatusAbort(code, text or "")

        try:
            try:
                started = PostgresEngine._now(cursor)
                call("TSTART", process_start=started, message=message, plsql=plsql_code)
                if header["check_refs"]:
                    cursor.execute(
                        "SELECT check_referencing_tables(%s::varchar[], %s::timestamp, %s::integer)",
                        [list(header["refs"]), process_date, 1 if preview else 0],
                    )
                    conn.commit()
                if copy:
                    cursor.execute(program.sections["connect"])
                section = program.sections["count" if preview else "chunk"]
                while True:
                    chunk_start = PostgresEngine._now(cursor)
                    call("CSTART", chunk_start=chunk_start, message=message)
                    count = PostgresEngine._run_phase(conn, cursor, section)
                    if count <= 0:
                        break
                    rows += count
                    call(
                        "CEND",
                        chunk_start=chunk_start,
                        process_end=PostgresEngine._now(cursor),
                        message=message,
                        rows=count,
                    )
                    conn.commit()
                    if preview:
                        break
                call("TEND", process_start=started, process_end=PostgresEngine._now(cursor), message=message)
                conn.commit()
            except _StatusAbort as abort:
                PostgresEngine._record_failure(
                    conn,
                    cursor,
                    program,
                    process_date,
                    prefix,
                    started,
                    abort.message,
                    abort.code,
                    rows,
                    plsql_code,
                    run_id,
                )
                if abort.code != 20003:
                    raise BlockAborted(abort.code, abort.message) from abort
            except Exception as exc:
                sqlstate, text = _pg_error(exc)
                code = _ld_code(sqlstate)
                PostgresEngine._record_failure(
                    conn, cursor, program, process_date, prefix, started, text, code if code in _FOREIGN_CODES else None, rows, plsql_code, run_id
                )  # fmt: skip
                if code is not None:
                    raise BlockAborted(code, text) from exc
                raise
        finally:
            if copy:
                PostgresEngine._disconnect_dblink(conn, cursor)
            cursor.close()

    @staticmethod
    def _run_phase(conn: Any, cursor: Any, sql: str) -> int:
        """Execute a DO section and return the row count it announced with RAISE NOTICE."""
        notices = getattr(conn, "notices", None)
        if notices is not None:
            notices.clear()
        cursor.execute(sql)
        count = 0
        for notice in list(notices or ()):
            text = PostgresEngine._notice_message(notice)
            if text.startswith(_NOTICE_ROWS):
                count = int(text[len(_NOTICE_ROWS) :])
        return count

    @staticmethod
    def _notice_message(notice: Mapping[Any, Any]) -> str:
        """The message of a notice, whose keys and values pg8000 leaves as bytes."""
        for key, value in notice.items():
            if key in (b"M", "M"):
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
        return ""

    @staticmethod
    def _disconnect_dblink(conn: Any, cursor: Any) -> None:
        try:
            conn.rollback()
            cursor.execute(
                f"SELECT dblink_disconnect({quote_literal(_DBLINK_CONNECTION)}) "
                f"WHERE coalesce({quote_literal(_DBLINK_CONNECTION)} = ANY(dblink_get_connections()), false)"
            )
            conn.commit()
        except Exception:
            logger.warning("The dblink connection to the HISTORY database could not be closed.", exc_info=True)
            conn.rollback()

    @staticmethod
    def _record_failure(
        conn: Any,
        cursor: Any,
        program: Any,
        process_date: datetime,
        prefix: str,
        started: datetime | None,
        message: str,
        sqlcode: int | None,
        rows: int,
        plsql_code: str,
        run_id: str,
    ) -> None:
        """Roll back and write the ERROR of the run; with a foreign ``sqlcode`` only the log row is written."""
        conn.rollback()
        try:
            PostgresEngine._call_status(
                cursor, program.owner, program.table_name, process_date, program.header["action"], prefix + "ERROR",
                process_start=started or PostgresEngine._now(cursor), process_end=PostgresEngine._now(cursor),
                message=message, rows=rows, plsql=plsql_code, run_id=run_id, sqlcode=sqlcode,
            )  # fmt: skip
            conn.commit()
        except Exception:
            logger.warning(
                "The ERROR status of %s.%s could not be written.", program.owner, program.table_name, exc_info=True
            )
            conn.rollback()

    @staticmethod
    def _now(cursor: Any) -> datetime:
        cursor.execute("SELECT localtimestamp(0)")
        now: datetime = cursor.fetchone()[0]
        return now

    @staticmethod
    def _call_status(
        cursor: Any,
        owner: str,
        table_name: str,
        process_date: datetime,
        action: str,
        status: str,
        *,
        process_start: datetime | None = None,
        chunk_start: datetime | None = None,
        process_end: datetime | None = None,
        message: str | None = None,
        rows: int = 0,
        plsql: str | None = None,
        run_id: str | None = None,
        sqlcode: int | None = None,
    ) -> tuple[int | None, str | None]:
        cursor.execute(
            """SELECT o_sqlcode, o_message FROM check_save_status(
                   %s::varchar, %s::varchar, %s::timestamp, %s::varchar, %s::varchar, %s::timestamp, %s::timestamp,
                   %s::timestamp, %s::varchar, %s::bigint, %s::text, %s::varchar, %s::integer)""",
            [owner, table_name, process_date, action, status, process_start, chunk_start, process_end, message,
             rows, plsql, run_id, sqlcode],
        )  # fmt: skip
        row = cursor.fetchone()
        return row[0], row[1]

    # ------------------------------------------------------------------ control tables
    @staticmethod
    def get_status(conn: Any, process_date: str) -> list[dict[str, Any]]:
        cursor = conn.cursor()
        try:
            cursor.execute(
                control(
                    "SELECT ctl_owner, ctl_table_name, ctl_status FROM ldb_ctl "
                    "WHERE ctl_process_date = to_date(%s, 'YYYYMMDD')::timestamp"
                ),
                [process_date],
            )
            return [{"ctl_owner": row[0], "ctl_table_name": row[1], "ctl_status": row[2]} for row in cursor.fetchall()]
        finally:
            cursor.close()

    @staticmethod
    def _fetch_one(conn: Any, sql: str, params: Sequence[Any]) -> Any:
        cursor = conn.cursor()
        try:
            cursor.execute(control(sql), list(params))
            return cursor.fetchone()
        finally:
            cursor.close()

    @staticmethod
    def get_rows_processed(conn: Any, owner: str, table_name: str, process_date: str) -> int:
        try:
            row = PostgresEngine._fetch_one(
                conn,
                "SELECT ctl_rows_processed FROM ldb_ctl WHERE ctl_owner = %s AND ctl_table_name = %s "
                "AND ctl_process_date = to_date(%s, 'YYYYMMDD')::timestamp",
                [owner, table_name, process_date],
            )
        except Exception as exc:
            logger.critical("Unexpected error in get_rows_processed for %s.%s:", owner, table_name, exc_info=True)
            raise ExecutionError(f"Failed to read rows processed for {owner}.{table_name}") from exc
        return int(row[0]) if row else 0

    @staticmethod
    def get_resumed_rows(conn: Any, owner: str, table_name: str, process_date: str) -> int:
        row = PostgresEngine._fetch_one(
            conn,
            "SELECT coalesce(ctl_rows_processed, 0) FROM ldb_ctl WHERE ctl_owner = %s AND ctl_table_name = %s "
            "AND ctl_process_date = to_date(%s, 'YYYYMMDD')::timestamp "
            "AND ctl_status IN ('TSTART', 'CSTART', 'CEND', 'LSTART', 'LEND', 'ERROR')",
            [owner, table_name, process_date],
        )
        return int(row[0]) if row else 0

    @staticmethod
    def error_recorded_since(conn: Any, owner: str, table_name: str, since: datetime) -> bool:
        row = PostgresEngine._fetch_one(
            conn,
            f"SELECT 1 FROM ldb_ctl WHERE ctl_owner = %s AND ctl_table_name = %s "
            f"AND ctl_status IN ('{Status.ERROR}', '{Status.PREVIEW_ERROR}') AND ctl_process_end >= %s::timestamp",
            [owner, table_name, since],
        )
        return row is not None

    @staticmethod
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
        cursor = conn.cursor()
        try:
            code, text = PostgresEngine._call_status(
                cursor, owner, table_name, datetime.strptime(process_date, "%Y%m%d"), config.execution.action,
                Status.PREVIEW_ERROR if config.execution.mode == "PREVIEW" else Status.ERROR,
                process_start=process_start, process_end=PostgresEngine._now(cursor), message=message,
                rows=rows_processed, plsql=plsql_code, run_id=run_id,
            )  # fmt: skip
            if code is not None:
                conn.rollback()
                logger.warning("The error of %s.%s was not recorded: %s", owner, table_name, text)
            else:
                conn.commit()
        except Exception as exc:
            logger.critical("Unexpected error in process_table for %s.%s:", owner, table_name, exc_info=True)
            raise ExecutionError(f"Failed to save error status for {owner}.{table_name}") from exc
        finally:
            cursor.close()

    @staticmethod
    def get_log_entries(conn: Any, max_rows: int) -> list[LogEntry]:
        cursor = conn.cursor()
        try:
            cursor.execute(
                control(
                    "SELECT log_id, log_owner, log_table_name, log_action, log_process_date, log_status, "
                    "log_process_start, log_process_end, log_message, coalesce(log_rows_processed, 0) "
                    "FROM ldb_log ORDER BY log_id DESC LIMIT %s"
                ),
                [max_rows],
            )
            return [
                LogEntry(int(r[0]), r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], int(r[9]))
                for r in cursor.fetchall()
            ]
        except pg8000.dbapi.DatabaseError as exc:
            raise ExecutionError(f"Could not read the execution log: {exc}") from exc
        finally:
            cursor.close()

    @staticmethod
    def get_control_entries(conn: Any) -> list[ControlEntry]:
        cursor = conn.cursor()
        try:
            cursor.execute(
                control(
                    "SELECT ctl_owner, ctl_table_name, ctl_status, ctl_run_id, ctl_action, ctl_process_date, "
                    "ctl_process_start, ctl_process_end, coalesce(ctl_rows_processed, 0), "
                    "greatest(ctl_process_start, coalesce(ctl_process_end, ctl_process_start)), ctl_db_session_id "
                    "FROM ldb_ctl"
                )
            )
            return [
                ControlEntry(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], int(r[8]), r[9], r[10])
                for r in cursor.fetchall()
            ]
        except pg8000.dbapi.DatabaseError as exc:
            hint = (
                " (the control tables predate the STATUS mode: rerun ldb-impl)" if _pg_error(exc)[0] == "42703" else ""
            )
            raise ExecutionError(f"Could not read the control table: {exc}{hint}") from exc
        finally:
            cursor.close()

    @staticmethod
    def all_status_tend(conn: Any, tables_config: dict[tuple[str, str], Any], process_date: str) -> bool:
        for (owner, table_name), table_cnf in tables_config.items():
            if table_cnf["skip"]:
                continue
            row = PostgresEngine._fetch_one(
                conn,
                f"SELECT ctl_status FROM ldb_ctl WHERE ctl_owner = %s AND ctl_table_name = %s "
                f"AND ctl_process_date = to_date(%s, 'YYYYMMDD')::timestamp AND ctl_status = '{Status.TABLE_END}'",
                [owner, table_name, process_date],
            )
            if not row:
                return False
        return True

    # ------------------------------------------------------------------ monitoring
    @staticmethod
    def check_monitoring_access(conn: Any, grantee: str) -> MonitoringAccess:
        # pg_stat_activity lists the sessions of the connected user (and all of them to a superuser)
        return MonitoringAccess(session=True, progress=False, waits=False)

    @staticmethod
    def get_session_activity(
        conn: Any, session: str, access: MonitoringAccess, min_wait_seconds: float
    ) -> SessionActivity:
        empty = SessionActivity(alive=None, current_wait=None, progress=None, waits=())
        try:
            pid_text, started_text = session.split(".")
            pid, started = int(pid_text), int(started_text)
        except ValueError:  # not an id this engine wrote
            return empty
        if not access.session:
            return empty
        cursor = conn.cursor()
        try:
            cursor.execute(
                """SELECT waiting, extract(epoch FROM (now() - state_change)) FROM pg_stat_activity
                    WHERE pid = %s AND floor(extract(epoch FROM backend_start))::bigint = %s""",
                [pid, started],
            )
            row = cursor.fetchone()
        except pg8000.dbapi.DatabaseError as exc:
            raise ExecutionError(f"Could not read the session activity: {exc}") from exc
        finally:
            cursor.close()
            conn.rollback()
        if row is None:
            return SessionActivity(alive=False, current_wait=None, progress=None, waits=())
        waiting, seconds = row
        wait = (
            WaitItem("lock", float(seconds))
            if waiting and seconds is not None and seconds >= min_wait_seconds
            else None
        )
        return SessionActivity(alive=True, current_wait=wait, progress=None, waits=())

    # ------------------------------------------------------------------ runner locks
    @staticmethod
    def _lock_key(owner: str, table_name: str) -> tuple[int, int]:
        """Two signed 32-bit keys for the advisory lock of a table."""

        def signed(value: str) -> int:
            number = zlib.crc32(value.encode("utf-8"))
            return number - (1 << 32) if number >= 1 << 31 else number

        return signed(owner), signed(table_name)

    @staticmethod
    def acquire_run_locks(
        lock_conn: Any,
        key_conn: Any,
        tables: Sequence[tuple[str, str]],
        run_id: str,
        mode: str,
        host: str,
        pid: int,
    ) -> None:
        ordered = sorted(tables)
        key_cursor = key_conn.cursor()
        try:
            for owner, table_name in ordered:
                key_cursor.execute(
                    control(
                        "INSERT INTO ldb_run_lock (rl_owner, rl_table_name, rl_kind) VALUES (%s, %s, 'K') "
                        "ON CONFLICT DO NOTHING"
                    ),
                    [owner, table_name],
                )
                key_conn.commit()  # one anchor per transaction
        finally:
            key_cursor.close()
        lock_cursor = lock_conn.cursor()
        try:
            for owner, table_name in ordered:
                first, second = PostgresEngine._lock_key(owner, table_name)
                try:
                    lock_cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [first, second])
                    acquired = bool(lock_cursor.fetchone()[0])
                except Exception as exc:
                    lock_conn.rollback()
                    PostgresEngine.release_run_locks(lock_conn)
                    raise ExecutionError(f"Failed to lock {owner}.{table_name} for this run") from exc
                if not acquired:
                    PostgresEngine.release_run_locks(lock_conn)
                    holder = PostgresEngine._lock_holder(key_conn, owner, table_name)
                    raise RunnerBusyError(
                        f"Another runner is processing {owner}.{table_name}{holder}; nothing was started"
                    )
        finally:
            lock_cursor.close()
        key_cursor = key_conn.cursor()
        try:
            for owner, table_name in ordered:
                key_cursor.execute(
                    control(
                        "UPDATE ldb_run_lock SET rl_run_id = %s, rl_mode = %s, rl_host = %s, rl_pid = %s, "
                        "rl_start = localtimestamp(0) WHERE rl_owner = %s AND rl_table_name = %s AND rl_kind = 'K'"
                    ),
                    [run_id, mode, host, pid, owner, table_name],
                )
            key_conn.commit()
        finally:
            key_cursor.close()

    @staticmethod
    def _lock_holder(key_conn: Any, owner: str, table_name: str) -> str:
        try:
            row = PostgresEngine._fetch_one(
                key_conn,
                "SELECT rl_run_id, rl_mode, rl_host, rl_pid, rl_start FROM ldb_run_lock "
                "WHERE rl_owner = %s AND rl_table_name = %s AND rl_kind = 'K'",
                [owner, table_name],
            )
        except Exception:
            key_conn.rollback()
            return ""
        key_conn.rollback()
        if row is None or row[0] is None:
            return ""
        run_id, mode, host, pid, start = row
        since = f", since {start:%Y-%m-%d %H:%M:%S}" if start is not None else ""
        return f" (runner {run_id}, {mode}, host {host}, pid {pid}{since})"

    @staticmethod
    def check_run_locks(lock_conn: Any) -> None:
        try:
            cursor = lock_conn.cursor()
            try:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            finally:
                cursor.close()
        except Exception as exc:
            raise ExecutionError("The runner lock connection was lost") from exc

    @staticmethod
    def release_run_locks(lock_conn: Any) -> None:
        cursor = lock_conn.cursor()
        try:
            cursor.execute("SELECT pg_advisory_unlock_all()")
        finally:
            cursor.close()
        lock_conn.rollback()

    @staticmethod
    def is_run_alive(conn: Any, owner: str, table_name: str, run_id: str) -> bool | None:
        if run_id.startswith("S"):
            return None
        first, second = PostgresEngine._lock_key(owner, table_name)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [first, second])
            if cursor.fetchone()[0]:
                cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [first, second])
                return False
            cursor.execute(
                control(
                    "SELECT rl_run_id FROM ldb_run_lock WHERE rl_owner = %s AND rl_table_name = %s AND rl_kind = 'K'"
                ),
                [owner, table_name],
            )
            key_row = cursor.fetchone()
            return None if key_row is None else key_row[0] == run_id
        except pg8000.dbapi.DatabaseError:
            return None
        finally:
            cursor.close()
            conn.rollback()

    # ------------------------------------------------------------------ DDL helpers
    @staticmethod
    def _execute_ddl(conn: Any, cursor: Any, statement: str) -> None:
        log_statement = PostgresEngine._PRIVATE_DDL_MARK.sub("****", statement)
        if len(log_statement) > 100:
            log_statement = log_statement[:100] + " ... [truncated]"
        statement = PostgresEngine._PRIVATE_DDL_MARK.sub(lambda m: m.group(1), statement)
        env = PostgresEngine._connection_envs.get(id(conn))
        logger.info("Executing %s DDL: %s", env, " ".join(log_statement.split()))
        cursor.execute(statement)

    @staticmethod
    def _emit_ddl(
        conn: Any, cursor: Any, statement: str, ddl_sink: DdlSink | None, ddl_failures: DdlFailures | None
    ) -> None:
        if ddl_sink is not None:
            ddl_sink(statement)
            return
        try:
            PostgresEngine._execute_ddl(conn, cursor, statement)
        except pg8000.dbapi.Error as exc:
            conn.rollback()  # a failed statement aborts the transaction in PostgreSQL
            if ddl_failures is None:
                raise
            message = " ".join(_pg_error(exc)[1].split())
            logger.warning("DDL statement failed and was left pending: %s", message)
            ddl_failures.append((statement, message))

    @staticmethod
    def _exists(cursor: Any, query: str, params: Sequence[Any]) -> bool:
        cursor.execute(query, list(params))
        return cursor.fetchone() is not None

    @staticmethod
    def _require_script_mode(conn: Any) -> None:
        if conn is not None:
            raise ValueError("a ddl_sink on this method needs conn=None: no database is read when scripting")

    @staticmethod
    def _tolerant_block(statement: str, conditions: Sequence[str]) -> str:
        """DO block that runs the statement and ignores only the given 'already exists' conditions."""
        handlers = " ".join(f"WHEN {condition} THEN NULL;" for condition in conditions)
        return f"DO $ldb$\nBEGIN\n    EXECUTE {quote_literal(statement)};\nEXCEPTION {handlers}\nEND\n$ldb$;"

    @staticmethod
    def _column_sql(column: ColumnDefinition) -> str:
        default = f" DEFAULT {column.default}" if column.default is not None else ""
        null = "" if column.nullable else " NOT NULL"
        return f"{format_identifier(column.name)} {PostgresEngine.get_column_type(column)}{default}{null}"

    # ------------------------------------------------------------------ roles and users
    @staticmethod
    def ensure_roles(conn: Any, roles: Sequence[RoleDefinition], *, ddl_sink: DdlSink | None = None) -> list[str]:
        created: list[str] = []
        if not roles:
            return created
        if ddl_sink is not None:
            PostgresEngine._require_script_mode(conn)
            for role in roles:
                statement = f"CREATE ROLE {format_identifier(role.name)}"
                ddl_sink(PostgresEngine._tolerant_block(statement, ("duplicate_object",)))
                ddl_sink("")
            return created
        cursor = conn.cursor()
        try:
            for role in roles:
                if PostgresEngine._exists(cursor, "SELECT 1 FROM pg_roles WHERE rolname = %s", [role.name]):
                    continue
                PostgresEngine._execute_ddl(conn, cursor, f"CREATE ROLE {format_identifier(role.name)}")
                created.append(format_identifier(role.name))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.critical("Failed to ensure roles.", exc_info=True)
            raise ExecutionError("Failed to ensure PostgreSQL roles") from exc
        finally:
            cursor.close()
        return created

    @staticmethod
    def _create_user_sql(user: UserDefinition, password_expression: str) -> str:
        return f"CREATE ROLE {format_identifier(user.name)} LOGIN PASSWORD {password_expression}"

    @staticmethod
    def ensure_users(conn: Any, users: Sequence[UserDefinition], *, ddl_sink: DdlSink | None = None) -> list[str]:
        created: list[str] = []
        if not users:
            return created
        if ddl_sink is not None:
            PostgresEngine._require_script_mode(conn)
            PostgresEngine._script_users(users, ddl_sink)
            return created
        cursor = conn.cursor()
        try:
            for user in users:
                name = format_identifier(user.name)
                if not PostgresEngine._exists(cursor, "SELECT 1 FROM pg_roles WHERE rolname = %s", [user.name]):
                    PostgresEngine._execute_ddl(
                        conn, cursor, PostgresEngine._create_user_sql(user, f"##{quote_literal(user.password)}##")
                    )
                    created.append(name)
                PostgresEngine._execute_ddl(conn, cursor, f"CREATE SCHEMA IF NOT EXISTS {name} AUTHORIZATION {name}")
                admin_option = {format_identifier(r) for r in user.roles_with_admin_option}
                for role in user.roles:
                    role_name = format_identifier(role)
                    with_admin = role_name in admin_option
                    if with_admin and not PostgresEngine._member_of(cursor, user.name, role, admin=True):
                        PostgresEngine._execute_ddl(conn, cursor, f"GRANT {role_name} TO {name} WITH ADMIN OPTION")
                    elif not with_admin and not PostgresEngine._member_of(cursor, user.name, role, admin=False):
                        PostgresEngine._execute_ddl(conn, cursor, f"GRANT {role_name} TO {name}")
                for attribute in user.system_privileges:
                    PostgresEngine._execute_ddl(conn, cursor, f"ALTER ROLE {name} {attribute}")
                if user.default_tablespace:
                    PostgresEngine._execute_ddl(
                        conn,
                        cursor,
                        f"ALTER ROLE {name} SET default_tablespace = {quote_literal(user.default_tablespace)}",
                    )
                if user.temporary_tablespace:
                    PostgresEngine._execute_ddl(
                        conn,
                        cursor,
                        f"ALTER ROLE {name} SET temp_tablespaces = {quote_literal(user.temporary_tablespace)}",
                    )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.critical("Failed to ensure users.", exc_info=True)
            raise ExecutionError("Failed to ensure PostgreSQL users") from exc
        finally:
            cursor.close()
        return created

    @staticmethod
    def _member_of(cursor: Any, member: str, role: str, *, admin: bool) -> bool:
        return PostgresEngine._exists(
            cursor,
            """SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles u ON u.oid = m.member
                WHERE u.rolname = %s AND r.rolname = %s AND (%s = false OR m.admin_option)""",
            [member, role, admin],
        )

    @staticmethod
    def _script_users(users: Sequence[UserDefinition], sink: DdlSink) -> None:
        fmt = format_identifier
        for position, user in enumerate(users, start=1):
            name = fmt(user.name)
            sink(
                "DO $ldb$\nBEGIN\n"
                f"    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {quote_literal(user.name)}) THEN\n"
                f"        EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', {quote_literal(user.name)}, "
                f"current_setting('ldb.user_pwd_{position}'));\n"
                "    ELSE\n"
                f"        RAISE NOTICE {quote_literal(f'User {user.name} already exists: its password was not changed. The runtime password must match it.')};\n"
                "    END IF;\nEND\n$ldb$;"
            )
            sink(f"CREATE SCHEMA IF NOT EXISTS {name} AUTHORIZATION {name};")
            admin_option = {fmt(role) for role in user.roles_with_admin_option}
            for role in user.roles:
                option = " WITH ADMIN OPTION" if fmt(role) in admin_option else ""
                sink(f"GRANT {fmt(role)} TO {name}{option};")
            for attribute in user.system_privileges:
                sink(f"ALTER ROLE {name} {attribute};")
            if user.default_tablespace:
                sink(f"ALTER ROLE {name} SET default_tablespace = {quote_literal(user.default_tablespace)};")
            if user.temporary_tablespace:
                sink(f"ALTER ROLE {name} SET temp_tablespaces = {quote_literal(user.temporary_tablespace)};")
            sink("")

    # ------------------------------------------------------------------ tables
    @staticmethod
    def _qualified(owner: str, name: str) -> str:
        return f"{format_identifier(owner)}.{format_identifier(name)}"

    @staticmethod
    def _create_table_statements(table: TableDefinition) -> list[str]:
        target = PostgresEngine._qualified(table.owner, table.name)
        columns = ",\n        ".join(PostgresEngine._column_sql(col) for col in table.columns)
        # The administrator creates the table, so ownership is handed to the schema owner (as in Oracle)
        statements = [
            f"CREATE TABLE {target} (\n        {columns}\n    )",
            f"ALTER TABLE {target} OWNER TO {format_identifier(table.owner)}",
        ]
        pk = tuple(table.primary_key or ())
        if pk:
            statements.append(
                f"ALTER TABLE {target} ADD CONSTRAINT {format_identifier(f'{table.name}_PK')}"
                f" PRIMARY KEY ({', '.join(format_identifier(c) for c in pk)})"
            )
        for name, (columns_, unique) in PostgresEngine._prepare_desired_indexes(table).items():
            statements.append(PostgresEngine._create_index_sql(table, name, columns_, unique))
        return statements

    @staticmethod
    def _create_index_sql(table: TableDefinition, name: str, columns: Sequence[str], unique: bool) -> str:
        return (
            f"CREATE {'UNIQUE ' if unique else ''}INDEX {format_identifier(name)}"
            f" ON {PostgresEngine._qualified(table.owner, table.name)} ({', '.join(format_identifier(c) for c in columns)})"
        )

    @staticmethod
    def ensure_tables(conn: Any, tables: Sequence[TableDefinition], *, ddl_sink: DdlSink | None = None) -> list[str]:
        created: list[str] = []
        if not tables:
            return created
        if ddl_sink is not None:
            PostgresEngine._require_script_mode(conn)
            PostgresEngine._script_tables(tables, ddl_sink)
            return created
        cursor = conn.cursor()
        try:
            for table in tables:
                for statement in PostgresEngine._compute_table_structure_delta(conn, cursor, table, None):
                    PostgresEngine._execute_ddl(conn, cursor, statement)
                    if statement.startswith("CREATE TABLE"):
                        created.append(PostgresEngine._qualified(table.owner, table.name))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.critical("Failed to ensure tables.", exc_info=True)
            raise ExecutionError("Failed to ensure PostgreSQL tables") from exc
        finally:
            cursor.close()
        return created

    @staticmethod
    def ensure_table_structure(
        conn: Any,
        table: TableDefinition,
        table_cnf: Mapping[str, Any],
        *,
        reference: tuple[str, str] | None = None,
        ddl_sink: DdlSink | None = None,
        ddl_failures: DdlFailures | None = None,
    ) -> None:
        if conn is None:
            if ddl_sink is None:
                raise ValueError("ensure_table_structure needs a ddl_sink when conn is None")
            for statement in PostgresEngine._create_table_statements(table):
                ddl_sink(statement)
            return
        cursor = conn.cursor()
        try:
            for statement in PostgresEngine._compute_table_structure_delta(conn, cursor, table, reference):
                PostgresEngine._emit_ddl(conn, cursor, statement, ddl_sink, ddl_failures)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.critical("Failed to ensure table structure.", exc_info=True)
            raise ExecutionError("Failed to ensure PostgreSQL table structure") from exc
        finally:
            cursor.close()

    @staticmethod
    def _compute_table_structure_delta(
        conn: Any, cursor: Any, table: TableDefinition, reference: tuple[str, str] | None
    ) -> list[str]:
        statements: list[str] = []
        target = PostgresEngine._qualified(table.owner, table.name)
        current_owner, current_name = reference or (table.owner, table.name)
        if not PostgresEngine._exists(
            cursor,
            "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relname = %s AND c.relkind = 'r'",
            [current_owner, current_name],
        ):
            return PostgresEngine._create_table_statements(table)
        _, existing_columns = PostgresEngine.get_table_columns(conn, current_owner, current_name)
        for column in table.columns:
            if column.name not in existing_columns:
                statements.append(f"ALTER TABLE {target} ADD COLUMN {PostgresEngine._column_sql(column)}")
            elif PostgresEngine._column_needs_update(existing_columns[column.name], column):
                statements.append(
                    f"ALTER TABLE {target} ALTER COLUMN {format_identifier(column.name)} "
                    f"TYPE {PostgresEngine.get_column_type(column)}"
                )
        existing = PostgresEngine._read_indexes(cursor, current_owner, current_name)
        pk_name = next((name for name, info in existing.items() if info["primary"]), None)
        pk_columns = tuple(existing[pk_name]["columns"]) if pk_name else ()
        effective = {name: (tuple(info["columns"]), bool(info["unique"])) for name, info in existing.items()}
        desired_pk = tuple(table.primary_key or ())
        constraint = f"{table.name}_PK"
        if pk_name and pk_columns != desired_pk:
            statements.append(f"ALTER TABLE {target} DROP CONSTRAINT {format_identifier(pk_name)}")
            effective.pop(pk_name, None)
            pk_name = None
        if desired_pk and pk_name is None:
            clash = effective.get(constraint)
            if clash is not None:
                statements.append(f"DROP INDEX {PostgresEngine._qualified(table.owner, constraint)}")
                effective.pop(constraint, None)
            statements.append(
                f"ALTER TABLE {target} ADD CONSTRAINT {format_identifier(constraint)}"
                f" PRIMARY KEY ({', '.join(format_identifier(c) for c in desired_pk)})"
            )
            pk_name = constraint
        desired = PostgresEngine._prepare_desired_indexes(table)
        for name, (columns, unique) in desired.items():
            if effective.get(name) == (columns, unique):
                continue
            if name in effective:
                statements.append(f"DROP INDEX {PostgresEngine._qualified(table.owner, name)}")
            statements.append(PostgresEngine._create_index_sql(table, name, columns, unique))
            effective[name] = (columns, unique)
        keep = set(desired) | ({pk_name} if pk_name else set())
        if table.drop_unmanaged_indexes:
            for name in effective:
                if name not in keep:
                    logger.info("Unmanaged index %s on %s will be dropped", format_identifier(name), target)
                    statements.append(f"DROP INDEX {PostgresEngine._qualified(table.owner, name)}")
        return statements

    @staticmethod
    def _column_needs_update(existing: ColumnDefinition, desired: ColumnDefinition) -> bool:
        dtype, etype = desired.data_type.lower(), existing.data_type.lower()
        family = {
            "string": "varchar",
            "varchar2": "varchar",
            "numeric": "number",
            "decimal": "number",
            "int": "integer",
        }
        dtype, etype = family.get(dtype, dtype), family.get(etype, etype)
        if dtype == "integer":
            dtype = "number"
            desired = ColumnDefinition(desired.name, "number", precision=desired.precision or 10, scale=0)
        if dtype != etype:
            return True
        if dtype in ("varchar", "char"):
            return bool((desired.length or 0) > (existing.length or 0))
        if dtype == "number":
            if (
                desired.precision is not None
                and existing.precision is not None
                and existing.precision < desired.precision
            ):
                return True
            return (desired.scale or 0) > (existing.scale or 0)
        return False

    @staticmethod
    def _determine_process_date_column(table: TableDefinition) -> str | None:
        return "LDB_PROCESS_DATE" if any(column.name == "LDB_PROCESS_DATE" for column in table.columns) else None

    @staticmethod
    def _prepare_desired_indexes(table: TableDefinition) -> dict[str, tuple[tuple[str, ...], bool]]:
        desired_pk = tuple(table.primary_key or ())
        date_column = PostgresEngine._determine_process_date_column(table)
        desired: dict[str, tuple[tuple[str, ...], bool]] = {}
        for index in table.indexes:
            columns = tuple(index.columns)
            if (
                index.unique
                and date_column is not None
                and date_column not in columns
                and (not desired_pk or columns != desired_pk)
            ):
                columns = (*columns, date_column)
            desired[index.name] = (columns, index.unique)
        return desired

    @staticmethod
    def _script_tables(tables: Sequence[TableDefinition], sink: DdlSink) -> None:
        for table in tables:
            target = PostgresEngine._qualified(table.owner, table.name)
            columns = ",\n        ".join(PostgresEngine._column_sql(col) for col in table.columns)
            sink(f"CREATE TABLE IF NOT EXISTS {target} (\n        {columns}\n    );")
            for col in table.columns:
                add = f"ALTER TABLE {target} ADD COLUMN {PostgresEngine._column_sql(col)}"
                sink(PostgresEngine._tolerant_block(add, ("duplicate_column",)))
            if table.primary_key:
                cols = ", ".join(format_identifier(c) for c in table.primary_key)
                name = format_identifier(f"{table.name}_PK")
                pk = f"ALTER TABLE {target} ADD CONSTRAINT {name} PRIMARY KEY ({cols})"
                sink(PostgresEngine._tolerant_block(pk, ("duplicate_object", "invalid_table_definition")))
                sink(PostgresEngine._primary_key_check(table))
            for index in table.indexes:
                statement = PostgresEngine._create_index_sql(table, index.name, index.columns, index.unique)
                sink(statement.replace("INDEX ", "INDEX IF NOT EXISTS ", 1) + ";")
            sink("")

    @staticmethod
    def _primary_key_check(table: TableDefinition) -> str:
        """DO block that fails unless the primary key has the required name and columns."""
        name = f"{table.name}_PK"
        columns = ",".join(table.primary_key or ())
        message = (
            f"Table {table.owner}.{table.name} must have primary key {name} on ({columns}), but its primary "
            "key or a constraint with that name is different. LimitsDb does not change it: fix it and rerun."
        )
        return (
            "DO $ldb$\nBEGIN\n"
            "    PERFORM 1 FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid\n"
            "        JOIN pg_namespace n ON n.oid = t.relnamespace\n"
            f"     WHERE n.nspname = {quote_literal(table.owner)} AND t.relname = {quote_literal(table.name)}\n"
            f"       AND c.contype = 'p' AND c.conname = {quote_literal(name)}\n"
            "       AND (SELECT string_agg(a.attname::text, ',' ORDER BY k.pos)\n"
            "              FROM generate_series(1, array_length(c.conkey, 1)) AS k(pos)\n"
            "              JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[k.pos])\n"
            f"           = {quote_literal(columns)};\n"
            f"    IF NOT FOUND THEN RAISE EXCEPTION {quote_literal(message)}; END IF;\n"
            "END\n$ldb$;"
        )

    # ------------------------------------------------------------------ sequences
    @staticmethod
    def _sequence_owner_sql(sequence: SequenceDefinition) -> str:
        return (
            f"ALTER SEQUENCE {PostgresEngine._qualified(sequence.owner, sequence.name)}"
            f" OWNER TO {format_identifier(sequence.owner)}"
        )

    @staticmethod
    def _create_sequence_sql(sequence: SequenceDefinition) -> str:
        sql = (
            f"CREATE SEQUENCE IF NOT EXISTS {PostgresEngine._qualified(sequence.owner, sequence.name)} "
            f"START WITH {sequence.start_with} INCREMENT BY {sequence.increment_by}"
        )
        sql += f" MINVALUE {sequence.minvalue}" if sequence.minvalue is not None else " NO MINVALUE"
        sql += f" MAXVALUE {sequence.maxvalue}" if sequence.maxvalue is not None else " NO MAXVALUE"
        sql += " CYCLE" if sequence.cycle else " NO CYCLE"
        sql += f" CACHE {sequence.cache}" if sequence.cache is not None else ""
        return sql

    @staticmethod
    def ensure_sequences(
        conn: Any, sequences: Sequence[SequenceDefinition], *, ddl_sink: DdlSink | None = None
    ) -> list[str]:
        created: list[str] = []
        if not sequences:
            return created
        if ddl_sink is not None:
            PostgresEngine._require_script_mode(conn)
            for sequence in sequences:
                ddl_sink(PostgresEngine._create_sequence_sql(sequence) + ";")
                ddl_sink(PostgresEngine._sequence_owner_sql(sequence) + ";")
                ddl_sink("")
            return created
        cursor = conn.cursor()
        try:
            for sequence in sequences:
                if PostgresEngine._exists(
                    cursor,
                    "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = %s AND c.relname = %s AND c.relkind = 'S'",
                    [sequence.owner, sequence.name],
                ):
                    continue
                PostgresEngine._execute_ddl(conn, cursor, PostgresEngine._create_sequence_sql(sequence))
                PostgresEngine._execute_ddl(conn, cursor, PostgresEngine._sequence_owner_sql(sequence))
                created.append(PostgresEngine._qualified(sequence.owner, sequence.name))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.critical("Failed to ensure sequences.", exc_info=True)
            raise ExecutionError("Failed to ensure PostgreSQL sequences") from exc
        finally:
            cursor.close()
        return created

    # ------------------------------------------------------------------ scripts
    @staticmethod
    def script_password_prompts(
        users: Sequence[UserDefinition], links: Sequence[DatabaseLinkDefinition], *, ddl_sink: DdlSink
    ) -> None:
        # psql cannot hide a \prompt answer: it is echoed. The answers go to session settings the DO blocks read.
        for position, user in enumerate(users, start=1):
            prompt = f"Password for user {user.name} (set if it does not exist yet; must match it if it does): "
            ddl_sink(f"\\prompt {quote_literal(prompt)} ldb_user_pwd_{position}")
            ddl_sink(f"SELECT set_config('ldb.user_pwd_{position}', :'ldb_user_pwd_{position}', false);")
        for position, link in enumerate(links, start=1):
            prompt = f"Password of {link.username} for database link {link.name}: "
            ddl_sink(f"\\prompt {quote_literal(prompt)} ldb_link_pwd_{position}")
            ddl_sink(f"SELECT set_config('ldb.link_pwd_{position}', :'ldb_link_pwd_{position}', false);")

    @staticmethod
    def script_preamble(*, interactive: bool) -> list[str]:
        return ["\\set ON_ERROR_STOP on", "\\set ECHO all"]

    @staticmethod
    def script_prompt(message: str) -> str:
        return f"\\echo {message}"

    @staticmethod
    def script_epilogue() -> list[str]:
        return ["\\q"]

    @staticmethod
    def ilm_script_header(schema: str) -> list[str]:
        return [
            "\\set ON_ERROR_STOP on",
            "\\set ECHO all",
            f"\\o ldb_{schema}.log",
            "-- usage: psql -v ldb_mode=EXECUTE|PREVIEW -f script.sql <connection>",
            "SELECT set_config('ldb.mode', upper(:'ldb_mode'), false);",
            "SELECT set_config('ldb.process_date', to_char(localtimestamp, 'YYYYMMDD'), false);",
        ]

    @staticmethod
    def ilm_script_table(owner: str, table_name: str, sql_block: str) -> list[str]:
        return [f"-- table: {owner}.{table_name}", sql_block]

    @staticmethod
    def ilm_script_footer() -> list[str]:
        return ["\\o", "\\q"]

    # ------------------------------------------------------------------ database links (dblink)
    @staticmethod
    def _server_options(link: DatabaseLinkDefinition) -> str:
        host, port, database = _parse_dsn(link.dsn)
        options = [f"host {quote_literal(host)}", f"dbname {quote_literal(database)}"]
        if port is not None:
            options.append(f"port {quote_literal(str(port))}")
        return ", ".join(options)

    @staticmethod
    def ensure_database_links(
        conn: Any,
        links: Sequence[DatabaseLinkDefinition],
        *,
        ddl_sink: DdlSink | None = None,
        connect_as: tuple[str, str] | None = None,
    ) -> list[str]:
        created: list[str] = []
        if not links:
            return created
        if ddl_sink is not None:
            PostgresEngine._require_script_mode(conn)
            if connect_as is None:
                raise ValueError("connect_as (runtime username, dsn) is required to script database links")
            PostgresEngine._script_database_links(links, connect_as, ddl_sink)
            return created
        cursor = conn.cursor()
        try:
            for link in links:
                if PostgresEngine._exists(cursor, "SELECT 1 FROM pg_foreign_server WHERE srvname = %s", [link.name]):
                    continue
                name = format_identifier(link.name)
                PostgresEngine._execute_ddl(
                    conn,
                    cursor,
                    f"CREATE SERVER {name} FOREIGN DATA WRAPPER dblink_fdw OPTIONS ({PostgresEngine._server_options(link)})",
                )
                PostgresEngine._execute_ddl(
                    conn,
                    cursor,
                    f"CREATE USER MAPPING FOR CURRENT_USER SERVER {name} OPTIONS "
                    f"(user {quote_literal(link.username)}, password ##{quote_literal(link.password)}##)",
                )
                created.append(name)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.critical("Failed to ensure database links.", exc_info=True)
            raise ExecutionError("Failed to ensure PostgreSQL database links") from exc
        finally:
            cursor.close()
        return created

    @staticmethod
    def _script_database_links(
        links: Sequence[DatabaseLinkDefinition], connect_as: tuple[str, str], sink: DdlSink
    ) -> None:
        username, dsn = connect_as
        host, port, database = _parse_dsn(dsn)
        sink(f"\\echo Connecting as {username}; psql asks for its password.")
        sink(f"\\connect {quote(database)} {quote(username)} {host} {port or 5432}")
        for position, link in enumerate(links, start=1):
            sink(
                "DO $ldb$\nBEGIN\n"
                f"    IF NOT EXISTS (SELECT 1 FROM pg_foreign_server WHERE srvname = {quote_literal(link.name)}) THEN\n"
                f"        CREATE SERVER {format_identifier(link.name)} FOREIGN DATA WRAPPER dblink_fdw "
                f"OPTIONS ({PostgresEngine._server_options(link)});\n"
                f"        EXECUTE format('CREATE USER MAPPING FOR CURRENT_USER SERVER %I OPTIONS (user %L, password %L)',\n"
                f"            {quote_literal(link.name)}, {quote_literal(link.username)}, "
                f"current_setting('ldb.link_pwd_{position}'));\n"
                "    END IF;\nEND\n$ldb$;"
            )

    # ------------------------------------------------------------------ privileges
    @staticmethod
    def _grantable(privileges: Sequence[str]) -> list[str]:
        """PostgreSQL has no ALTER table privilege: structure changes need the owner."""
        return [p.upper() for p in privileges if p.upper() != "ALTER"]

    @staticmethod
    def _grant_sql(role: str, owner: str, table_name: str, privilege: str) -> str:
        return f"GRANT {privilege} ON {PostgresEngine._qualified(owner, table_name)} TO {format_identifier(role)}"

    @staticmethod
    def _has_privilege(cursor: Any, grantee: str, owner: str, table_name: str, privilege: str) -> bool:
        cursor.execute(
            "SELECT has_table_privilege(%s::name, quote_ident(%s) || '.' || quote_ident(%s), %s)",
            [grantee, owner, table_name, privilege],
        )
        return bool(cursor.fetchone()[0])

    @staticmethod
    def ensure_table_privileges(
        conn: Any,
        role: str,
        tables: Sequence[tuple[str, str]],
        privileges: Sequence[str],
        *,
        ddl_sink: DdlSink | None = None,
        ddl_failures: DdlFailures | None = None,
    ) -> list[str]:
        granted: list[str] = []
        if not tables:
            return granted
        wanted = PostgresEngine._grantable(privileges)
        if conn is None:
            if ddl_sink is None:
                raise ValueError("ensure_table_privileges needs a ddl_sink when conn is None")
            for owner, table_name in tables:
                for privilege in wanted:
                    ddl_sink(PostgresEngine._grant_sql(role, owner, table_name, privilege))
                    granted.append(f"{owner}.{table_name}:{privilege}")
            return granted
        cursor = conn.cursor()
        try:
            for owner, table_name in tables:
                for privilege in wanted:
                    if PostgresEngine._has_privilege(cursor, role, owner, table_name, privilege):
                        continue
                    PostgresEngine._emit_ddl(
                        conn,
                        cursor,
                        PostgresEngine._grant_sql(role, owner, table_name, privilege),
                        ddl_sink,
                        ddl_failures,
                    )
                    granted.append(f"{PostgresEngine._qualified(owner, table_name)}:{privilege}")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.critical("Failed to ensure table privileges.", exc_info=True)
            raise ExecutionError("Failed to ensure PostgreSQL table privileges") from exc
        finally:
            cursor.close()
        return granted

    @staticmethod
    def get_missing_table_privileges(
        conn: Any, role: str, tables: Sequence[tuple[str, str]], privileges: Sequence[str]
    ) -> list[tuple[str, str, str]]:
        missing: list[tuple[str, str, str]] = []
        cursor = conn.cursor()
        try:
            enabled = PostgresEngine._role_enabled(cursor, role)
            for owner, table_name in tables:
                for privilege in PostgresEngine._grantable(privileges):
                    if not enabled or not PostgresEngine._has_privilege(cursor, "", owner, table_name, privilege):
                        missing.append((owner, table_name, privilege))
        except Exception as exc:
            logger.critical("Failed to read table privileges.", exc_info=True)
            raise ExecutionError("Failed to read PostgreSQL table privileges") from exc
        finally:
            cursor.close()
        return missing

    @staticmethod
    def _role_enabled(cursor: Any, role: str) -> bool:
        cursor.execute("SELECT pg_has_role(current_user, %s::name, 'USAGE')", [role])
        return bool(cursor.fetchone()[0])

    @staticmethod
    def is_role_enabled(conn: Any, role: str) -> bool:
        cursor = conn.cursor()
        try:
            return PostgresEngine._role_enabled(cursor, role)
        except Exception as exc:
            logger.critical("Failed to read the enabled roles.", exc_info=True)
            raise ExecutionError("Failed to read the PostgreSQL session roles") from exc
        finally:
            cursor.close()

    @staticmethod
    def generate_table_privilege_script(
        role: str, tables: Sequence[tuple[str, str]], privileges: Sequence[str]
    ) -> list[str]:
        return [
            PostgresEngine._grant_sql(role, owner, table_name, privilege)
            for owner, table_name in tables
            for privilege in PostgresEngine._grantable(privileges)
        ]

    # ------------------------------------------------------------------ supporting objects
    @staticmethod
    def ensure_supporting_objects(conn: Any, owner: str, *, ddl_sink: DdlSink | None = None) -> list[str]:
        prerequisites = [
            "CREATE EXTENSION IF NOT EXISTS dblink",
            f"GRANT USAGE ON FOREIGN DATA WRAPPER dblink_fdw TO {format_identifier(owner)}",
        ]
        statements = [*prerequisites, *supporting_functions(owner)]
        if ddl_sink is not None:
            PostgresEngine._require_script_mode(conn)
            ddl_sink("\n".join(f"{statement};\n" for statement in statements))
            return []
        cursor = conn.cursor()
        try:
            for statement in statements:
                PostgresEngine._execute_ddl(conn, cursor, statement)
            conn.commit()
            return SUPPORTING_OBJECT_LIST
        except Exception as exc:
            conn.rollback()
            logger.critical("Failed to ensure supporting objects.", exc_info=True)
            raise ExecutionError("Failed to ensure PostgreSQL supporting objects") from exc
        finally:
            cursor.close()
