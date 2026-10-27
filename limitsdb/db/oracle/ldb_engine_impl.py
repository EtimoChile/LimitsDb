import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, ClassVar, Literal, cast

import oracledb

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
    OperationProgress,
    RoleDefinition,
    SequenceDefinition,
    SessionActivity,
    TableDefinition,
    UserDefinition,
    WaitItem,
)

SUPPORTING_OBJECT_LIST = [
    "TYPE t_referencing_tables",
    "PROCEDURE check_save_status",
    "PROCEDURE check_referencing_tables",
]
CHECK_SAVE_STATUS_PROC = """
CREATE OR REPLACE PROCEDURE check_save_status(
    p_owner            IN VARCHAR2,
    p_table_name       IN VARCHAR2,
    p_process_date     IN DATE,
    p_action           IN VARCHAR2,
    p_status           IN VARCHAR2,
    p_process_start    IN DATE,
    p_chunk_start      IN DATE,
    p_process_end      IN DATE,
    p_message          IN VARCHAR2,
    p_rows_processed   IN VARCHAR2,
    p_plsql            IN VARCHAR2,
    p_run_id           IN VARCHAR2,
    p_sqlcode       IN OUT NUMBER,
    p_out_message   OUT VARCHAR2
)
IS
    l_status         VARCHAR2(10);
    l_action         VARCHAR2(11);
    l_process_start  DATE;
    l_process_end    DATE;
    l_process_date   DATE;
    l_rows           NUMBER;
    l_ctl_run_id     VARCHAR2(36);
    l_found          BOOLEAN := TRUE;
    l_keep_rows      NUMBER := 0;
    l_key            VARCHAR2(36);
    in_use           EXCEPTION;
    PRAGMA EXCEPTION_INIT(in_use, -54);
BEGIN
    IF NVL(p_sqlcode, 0) NOT IN (-20001, -20002, -20003, -20005, -20006) THEN
        BEGIN
            SELECT ctl_status, ctl_action, ctl_process_date, ctl_process_start, ctl_process_end,
                   NVL(ctl_rows_processed, 0), ctl_run_id
              INTO l_status, l_action, l_process_date, l_process_start, l_process_end, l_rows, l_ctl_run_id
              FROM ldb_ctl
             WHERE ctl_owner = p_owner
               AND ctl_table_name = p_table_name
             FOR UPDATE NOWAIT;
        EXCEPTION
            WHEN in_use THEN
                p_sqlcode := -20001;
                p_out_message := 'Table ' || p_table_name || ' is currently in process';
                RETURN;
            WHEN NO_DATA_FOUND THEN
                l_found := FALSE;
        END;

        IF NOT l_found THEN
            INSERT INTO ldb_ctl (
                ctl_owner,
                ctl_table_name,
                ctl_process_date,
                ctl_action,
                ctl_status,
                ctl_process_start,
                ctl_process_end,
                ctl_rows_processed,
                ctl_plsql,
                ctl_run_id,
                ctl_db_session_id
            )
            VALUES (
                p_owner,
                p_table_name,
                p_process_date,
                p_action,
                p_status,
                NVL(p_process_start, SYSDATE),
                NULL,
                0,
                p_plsql,
                p_run_id,
                sys_context('USERENV', 'INSTANCE') || '.' || sys_context('USERENV', 'SID')
                    || '.' || sys_context('USERENV', 'SESSIONID')
            );
            l_ctl_run_id := p_run_id;
        END IF;

        -- A SCRIPT run_id starts with S and has no lock row: the run is identified by ldb_ctl.ctl_run_id
        IF p_run_id IS NOT NULL
           AND p_run_id NOT LIKE 'S%'
           AND p_status IN ('TSTART', 'PTSTART', 'CSTART', 'PCSTART', 'LSTART', 'PLSTART', 'ERROR', 'PERROR') THEN
            BEGIN
                SELECT rl_run_id
                  INTO l_key
                  FROM ldb_run_lock
                 WHERE rl_owner = p_owner
                   AND rl_table_name = p_table_name
                   AND rl_kind = 'K';
            EXCEPTION
                WHEN NO_DATA_FOUND THEN
                    l_key := NULL;
            END;
            IF l_key IS NULL OR l_key <> p_run_id THEN
                ROLLBACK;
                p_sqlcode := -20005;
                p_out_message := 'Table ' || p_table_name || ' is now owned by another run';
                RETURN;
            END IF;
        END IF;

        IF p_run_id IS NOT NULL
           AND p_status IN ('CSTART', 'PCSTART', 'LSTART', 'PLSTART', 'ERROR', 'PERROR')
           AND l_ctl_run_id IS NOT NULL
           AND l_ctl_run_id <> p_run_id THEN
            ROLLBACK;
            p_sqlcode := -20005;
            p_out_message := 'Table ' || p_table_name || ' is now owned by another run';
            RETURN;
        END IF;

        IF l_found AND p_status IN ('TSTART', 'PTSTART') THEN
            -- The heartbeat is the last chunk end: a live run refreshes it between chunks, when the row is not locked
            IF l_status IN ('TSTART', 'CSTART', 'CEND', 'LSTART', 'LEND',
                            'PTSTART', 'PCSTART', 'PCEND', 'PLSTART', 'PLEND')
               AND (SYSDATE - GREATEST(l_process_start, NVL(l_process_end, l_process_start))) * 3600 * 24 < 5 THEN
                p_sqlcode := -20002;
                p_out_message := 'Table ' || p_table_name || ' is currently in process without lock';
                RETURN;
            ELSIF p_status = 'PTSTART'
                  AND l_process_date = p_process_date
                  AND l_status IN ('TSTART', 'CSTART', 'CEND', 'LSTART', 'LEND', 'TEND', 'ERROR') THEN
                ROLLBACK;
                p_sqlcode := -20006;
                p_out_message := 'Table ' || p_table_name ||
                                 ' has an EXECUTE status for date ' ||
                                 TO_CHAR(p_process_date, 'YYYYMMDD') || ': a PREVIEW is not allowed';
                RETURN;
            ELSIF p_status = 'TSTART'
                  AND l_status = 'TEND'
                  AND l_process_date = p_process_date THEN
                ROLLBACK;
                p_sqlcode := -20003;
                p_out_message := 'Table ' || p_table_name ||
                                 ' is already processed for date ' ||
                                 TO_CHAR(p_process_date, 'YYYYMMDD');
                RETURN;
            END IF;

            -- Only an incomplete EXECUTE of the same process date is continued; anything else starts over
            IF p_status = 'TSTART'
               AND l_process_date = p_process_date
               AND l_status IN ('TSTART', 'CSTART', 'CEND', 'LSTART', 'LEND', 'ERROR') THEN
                l_keep_rows := 1;
            ELSIF l_status IN ('TSTART', 'CSTART', 'CEND', 'LSTART', 'LEND',
                               'PTSTART', 'PCSTART', 'PCEND', 'PLSTART', 'PLEND') THEN
                INSERT INTO ldb_log (
                    log_id,
                    log_owner,
                    log_table_name,
                    log_process_date,
                    log_action,
                    log_status,
                    log_process_start,
                    log_process_end,
                    log_message,
                    log_rows_processed,
                    log_plsql
                )
                VALUES (
                    ldb_log_id.NEXTVAL,
                    p_owner,
                    p_table_name,
                    NVL(l_process_date, p_process_date),
                    NVL(l_action, p_action),
                    CASE WHEN l_status LIKE 'P%' THEN 'PERROR' ELSE 'ERROR' END,
                    NVL(l_process_start, SYSDATE),
                    NVL(l_process_end, l_process_start),
                    'Incomplete process closed by a later process',
                    l_rows,
                    NULL
                );
            END IF;
        END IF;

        UPDATE ldb_ctl
           SET ctl_process_date   = p_process_date,
               ctl_action         = p_action,
               ctl_status         = p_status,
               ctl_process_start  = NVL(p_process_start, ctl_process_start),
               ctl_process_end    = CASE
                                        WHEN p_status IN ('TSTART', 'PTSTART') THEN NULL
                                        ELSE NVL(p_process_end, ctl_process_end)
                                    END,
               ctl_rows_processed = CASE
                                        WHEN p_status IN ('TSTART', 'PTSTART') THEN
                                            CASE WHEN l_keep_rows = 1 THEN ctl_rows_processed
                                                 ELSE TO_NUMBER(p_rows_processed) END
                                        WHEN p_status IN ('ERROR', 'PERROR') THEN ctl_rows_processed
                                        ELSE ctl_rows_processed + TO_NUMBER(p_rows_processed)
                                    END,
               ctl_run_id         = CASE
                                        WHEN p_status IN ('TSTART', 'PTSTART') THEN p_run_id
                                        ELSE ctl_run_id
                                    END,
               -- The session that runs the table, for the STATUS mode (Oracle: instance.sid.audsid, RAC aware)
               ctl_db_session_id  = CASE
                                        WHEN p_status IN ('TSTART', 'PTSTART')
                                            THEN sys_context('USERENV', 'INSTANCE') || '.' || sys_context('USERENV', 'SID')
                                                 || '.' || sys_context('USERENV', 'SESSIONID')
                                        ELSE ctl_db_session_id
                                    END
         WHERE ctl_owner = p_owner
           AND ctl_table_name = p_table_name;

        -- Apart from the main UPDATE: mixing the CLOB column with a VARCHAR2 in NVL fails (ORA-22835) over 4000 chars
        IF p_plsql IS NOT NULL THEN
            UPDATE ldb_ctl
               SET ctl_plsql = p_plsql
             WHERE ctl_owner = p_owner
               AND ctl_table_name = p_table_name;
        END IF;
    END IF;

    INSERT INTO ldb_log (
        log_id,
        log_owner,
        log_table_name,
        log_process_date,
        log_action,
        log_status,
        log_process_start,
        log_process_end,
        log_message,
        log_rows_processed,
        log_plsql
    )
    VALUES (
        ldb_log_id.NEXTVAL,
        p_owner,
        p_table_name,
        p_process_date,
        p_action,
        p_status,
        NVL(p_chunk_start, p_process_start),
        p_process_end,
        p_message,
        p_rows_processed,
        p_plsql
    );
END check_save_status;
"""
T_REFERENCING_TABLES_TYPE = """
CREATE OR REPLACE TYPE t_referencing_tables AS TABLE OF VARCHAR2(100);
"""
CHECK_REFERENCING_TABLES_PROC = """
CREATE OR REPLACE PROCEDURE check_referencing_tables(
    p_referencing_tables IN t_referencing_tables,
    p_process_date       IN DATE,
    p_preview            IN NUMBER DEFAULT 0
)
IS
    l_ref_table      VARCHAR2(100);
    l_ref_owner      VARCHAR2(100);
    l_ref_table_name VARCHAR2(100);
    l_dummy          NUMBER;
BEGIN
    FOR i IN 1 .. p_referencing_tables.COUNT LOOP
        l_ref_table := p_referencing_tables(i);
        l_ref_owner := SUBSTR(l_ref_table, 1, INSTR(l_ref_table, '.') - 1);
        l_ref_table_name := SUBSTR(l_ref_table, INSTR(l_ref_table, '.') + 1);
        BEGIN
            SELECT 1
              INTO l_dummy
              FROM ldb_ctl
             WHERE ctl_owner = l_ref_owner
               AND ctl_table_name = l_ref_table_name
               AND ctl_process_date = p_process_date
               AND (ctl_status = 'TEND' OR (p_preview = 1 AND ctl_status = 'PTEND'));
        EXCEPTION
            WHEN NO_DATA_FOUND THEN
                RAISE_APPLICATION_ERROR(
                    -20004,
                    'Referencing table ' || l_ref_table ||
                    ' was not fully processed for date ' ||
                    TO_CHAR(p_process_date, 'YYYYMMDD')
                );
        END;
    END LOOP;
END check_referencing_tables;
"""
logger = get_logger("oracle.engine")


class OracleEngine(DatabaseEngine):
    """Oracle DB engine with methods for connection, configuration loading, and PL/SQL generation."""

    # states and class constants
    _connection_envs: ClassVar[dict[int, str]] = {}
    _admin_connections: ClassVar[set[int]] = set()
    REQUIRED_SYSTEM_PRIVILEGES: tuple[str, ...] = (
        "CREATE SESSION",
        "ALTER SESSION",
        "CREATE TABLE",
        "CREATE PROCEDURE",
        "CREATE TYPE",
        "CREATE DATABASE LINK",
        "CREATE SEQUENCE",
        "RESUMABLE",
        "ALTER USER",
        "CREATE SYNONYM",
        "CREATE VIEW",
        "CREATE ROLE",
        "CREATE TRIGGER",
        "CREATE MATERIALIZED VIEW",
        "QUERY REWRITE",
    )
    _REGULAR_IDENTIFIER = re.compile(r"[A-Z_#$][A-Z0-9_#$]*")  # only DD uppercase identifiers are unquoted
    _QUALIFIED_TABLE = re.compile(
        r'(?<![\w$#."])("(?:""|[^"])*"|[A-Za-z_#$][A-Za-z0-9_#$]*)\s*\.\s*("(?:""|[^"])*"|[A-Za-z_#$][A-Za-z0-9_#$]*)'
        r'(?![\w$#"])'
    )
    _PREFIXED_IDENTIFIER = re.compile(r'@("(?:""|[^"])*"|[A-Za-z_#$][A-Za-z0-9_#$]*)')
    _PRIVATE_DDL_MARK = re.compile(r"##(.*?)##")

    # public API
    @staticmethod
    def get_connection(
        config: Config, *, admin: bool = False, env: Literal["SOURCE", "HISTORY"] | None = None
    ) -> oracledb.Connection:
        """Returns an Oracle connection using provided config.
        Args:
            config: Database config object.
            admin: Whether to use admin credentials.
        Returns:
            An active oracledb.Connection."""
        user, password, dsn = get_effective_credentials(config, admin=admin, env=env)
        target = f"as {user} at {dsn}"
        if not admin:
            # The runtime user is created from its dictionary name, so the login must quote it the same way
            user = OracleEngine._format_identifier(user)
        try:
            connection = oracledb.connect(user=user, password=password, dsn=dsn)
            OracleEngine._register_connection_env(connection, env)
            if admin:
                OracleEngine._admin_connections.add(id(connection))
            return cast(oracledb.Connection, connection)
        except Exception as exc:
            logger.critical("Failed to connect to Oracle DB %s.", target, exc_info=True)
            if OracleEngine._is_invalid_credentials(exc):
                raise InvalidCredentialsError(f"Failed to connect to Oracle database {target}") from exc
            raise DatabaseConnectionError(f"Failed to connect to Oracle database {target}") from exc

    @staticmethod
    def _is_invalid_credentials(exc: Exception) -> bool:
        """True for ORA-01017, which Oracle raises alike for an unknown user and a wrong password."""
        error = exc.args[0] if exc.args else None
        return getattr(error, "code", None) == 1017 or "ORA-01017" in str(exc)

    @staticmethod
    def close_connection(conn: oracledb.Connection) -> None:
        """Closes the Oracle connection.
        Args:
            conn: Active Oracle connection."""
        try:
            if conn:
                OracleEngine._register_connection_env(conn, None)
                OracleEngine._admin_connections.discard(id(conn))
                conn.close()
        except Exception:
            logger.critical("Failed to close Oracle DB connection.", exc_info=True)

    @staticmethod
    def get_table_columns(
        conn: oracledb.Connection, owner: str, table_name: str
    ) -> tuple[list[str], dict[str, ColumnDefinition]]:
        """Retrieves column names and metadata for a given table.
        Args:
            conn: Active Oracle connection.
            owner: Schema owner of the table.
            table_name: Table name.
        Returns:
            Tuple containing a list of column names and a dict of column metadata."""
        cursor: oracledb.Cursor | None = None
        columns: list[str] = []
        metadata: dict[str, ColumnDefinition] = {}
        try:
            cursor = conn.cursor()
            cursor.execute(
                """select column_id, column_name, data_type, data_length, data_precision, data_scale, nullable, data_default, char_length, char_used
                from all_tab_columns
                where  owner = :1 and table_name = :2 and column_id is not null
                order by column_id""",
                [owner, table_name],
            )
            for row in cast(
                Iterable[tuple[int, str, str, int | None, int | None, int | None, str, str | None, str | None, str]],
                cursor,
            ):
                (
                    column_id,
                    column_name,
                    data_type,
                    data_length,
                    data_precision,
                    data_scale,
                    nullable,
                    data_default,
                    char_length,
                    char_used,
                ) = row
                dtype = data_type.lower()
                length: int | None = None
                if dtype in ("varchar2", "varchar", "char"):
                    if char_used == "C" and char_length is not None:
                        length = int(char_length)
                    elif data_length is not None:
                        length = int(data_length)
                precision = int(data_precision) if data_precision is not None else None
                scale = int(data_scale) if data_scale is not None else None
                metadata[column_name] = ColumnDefinition(
                    name=column_name,
                    data_type=dtype,
                    id=column_id,
                    length=length,
                    precision=precision,
                    scale=scale,
                    nullable=(nullable == "Y"),
                    default=data_default.strip() if isinstance(data_default, str) else None,
                )
                columns.append(column_name)
        finally:
            if cursor:
                cursor.close()
        return columns, metadata

    @staticmethod
    def get_date_condition(date_expr: str, months_keep_src: int) -> str:
        """Returns a date condition for the given date expression and months to keep.
        Args:
            date_expr: Date expression to evaluate.
            months_keep_src: Months to keep.
        Returns:
            Date condition as string."""
        return f"({date_expr} < add_months(l_process_date,-{months_keep_src}))"

    @staticmethod
    def get_fallback_expression(expression: str, fallback: str) -> str:
        """Return Oracle's null-safe fallback expression."""
        return f"coalesce({expression}, {fallback})"

    @staticmethod
    def get_identifiers_from_expression(expression: str) -> set[str]:
        """Returns a set of @prefixxed identifiers found in the given expression.
        Args:
            expression: The expression string to process.
        Returns:
            A set of identifier strings found in the expression."""
        identifiers: set[str] = set()
        for match in OracleEngine._PREFIXED_IDENTIFIER.finditer(expression):
            ident = match.group(1)
            if ident.startswith('"') and ident.endswith('"'):
                ident = ident[1:-1].replace('""', '"')
            else:
                ident = ident.upper()
            identifiers.add(ident)
        return identifiers

    @staticmethod
    def rewrite_expression_identifiers(expression: str, replacements: Mapping[str, str], *, qualifier: str) -> str:
        """Rewrite ``@`` identifiers to qualified persisted Oracle columns."""

        def replace_identifier(match: re.Match[str]) -> str:
            token = match.group(1)
            if token.startswith('"') and token.endswith('"'):
                identifier = token[1:-1].replace('""', '"')
            else:
                identifier = token.upper()
            replacement = replacements.get(identifier)
            if replacement is None:
                return match.group(0)
            return f"{qualifier}.{OracleEngine._format_identifier(replacement)}"

        return OracleEngine._PREFIXED_IDENTIFIER.sub(replace_identifier, expression)

    @staticmethod
    def rewrite_table_owners(expression: str, owners: Mapping[tuple[str, str], str]) -> str:
        """Point ``owner.table`` references at another owner. Each name found in the SQL text is brought to its
        dictionary form with ``get_identifier_str`` before it is looked up."""

        def replace_owner(match: re.Match[str]) -> str:
            owner_token, table_token = match.group(1), match.group(2)
            source_owner = OracleEngine.get_identifier_str(owner_token)
            new_owner = owners.get((source_owner, OracleEngine.get_identifier_str(table_token)))
            if new_owner is None or new_owner == source_owner:
                return match.group(0)
            return f"{OracleEngine._format_identifier(new_owner)}.{table_token}"

        return OracleEngine._QUALIFIED_TABLE.sub(replace_owner, expression)

    @staticmethod
    def get_column_type(column: ColumnDefinition) -> str:
        dtype = column.data_type.lower()
        if dtype in ("string", "varchar", "varchar2"):
            length = column.length or 255
            return f"VARCHAR2({length})"
        if dtype == "char":
            length = column.length or 1
            return f"CHAR({length})"
        if dtype in ("number", "numeric", "decimal"):
            if column.precision is not None and column.scale is not None:
                return f"NUMBER({column.precision},{column.scale})"
            if column.precision is not None:
                return f"NUMBER({column.precision})"
            return "NUMBER"
        if dtype in ("integer", "int"):
            return "NUMBER(10)"
        if dtype == "date":
            return "DATE"
        if dtype == "clob":
            return "CLOB"
        raise ValueError(f"Unsupported column data type: {column.data_type}")

    @staticmethod
    def generate_sql_block(config: Config, table_cnf: Mapping[str, Any], process_date: str) -> str:
        """Generates a PL/SQL block for table processing with optional chunking and LOB handling.

        The mode (EXECUTE or PREVIEW) is not part of the text: the runner binds ``:ldb_mode`` and a SCRIPT receives
        ``&ldb_mode`` from SQL*Plus, so one block serves both and the PREVIEW/EXECUTE rules live in check_save_status.
        Args:
            config: Configuration object.
            table_cnf: Processed table configuration object.
            process_date: Process date in 'YYYYMMDD' format.
        Returns:
            PL/SQL block as string."""
        execution = config.execution
        administration = config.administration
        cnd0 = table_cnf["conds"][0]
        source_owner, history_owner, table_name = cnd0["source_owner"], cnd0["history_owner"], cnd0["table_name"]
        history_hint_expr = cnd0.get("history_hint_expr")
        hint_expr = cnd0["hint_expr"]
        if execution.action == "HISTORY_ILM":
            hint_expr = nvl(history_hint_expr, hint_expr)
        has_lob_columns = cnd0["has_lob_columns"] == "Y"
        other_columns, referencing_tables = table_cnf["other_columns"], table_cnf["referencing_tables"]
        other_cols_alias = [col["name"] for col in other_columns]
        other_cols_exprs = [col["expr"] for col in other_columns]
        query_expr, table_columns, months_keep_history_max = (
            table_cnf["query_expr"],
            table_cnf["table_columns"],
            table_cnf["months_keep_history_max"],
        )
        referencing_tables = ", ".join([f"'{rt[0]}.{rt[1]}'" for rt in referencing_tables])
        source_ilm = execution.action == "SOURCE_ILM"
        runtime_owner = source_owner if source_ilm else history_owner
        ins_cols = join_wrapped(", ", table_columns + other_cols_alias, 200)
        ins_vals = join_wrapped(", ", [f"r_rec(i).{col}" for col in table_columns + other_cols_alias], 200)
        script = execution.generate_script
        if script:
            process_date = "&ldb_process_date"
        mode_init = "upper('&ldb_mode')" if script else ":ldb_mode"
        run_id_init = "'S' || rawtohex(sys_guid())" if script else ":ldb_run_id"
        message_default = "'SCRIPT'" if script else "null"
        plsql = f"""declare
    l_source_owner varchar2(50) := '{source_owner}';
    l_history_owner varchar2(50) := '{history_owner}';
    l_action varchar2(11) := '{execution.action}';
    l_mode varchar2(10) := {mode_init};
    l_pfx varchar2(1) := case when l_mode = 'PREVIEW' then 'P' else null end;
    l_run_id varchar2(36) := {run_id_init};
    l_message varchar2(200) := case when l_mode = 'PREVIEW' then 'PREVIEW' else {message_default} end;
    l_table_name varchar2(50) := '{table_name}';
    l_process_date date := to_date('{process_date}', 'YYYYMMDD');
    l_referencing_tables t_referencing_tables := t_referencing_tables({referencing_tables});
    l_process_start date;
    l_record_count pls_integer := 0;
    l_plsql clob := {"null" if script else ":plsql_code"};
    l_sqlcode number := null;
    l_out_message varchar2(200) := null;"""
        if not has_lob_columns:
            other_cols_select = [
                f"{expression} AS {OracleEngine._format_identifier(alias)}"
                for expression, alias in zip(other_cols_exprs, other_cols_alias, strict=True)
            ]
            cols_expr = ", ".join(["A.*", *other_cols_select])
            plsql += f"""
    l_chunk_size pls_integer := {execution.chunk_size}; l_chunk_start date;
    cursor c_records is
        select /*+ {hint_expr} */ A.rowid{", " + cols_expr if source_ilm else ""}
        {indent_lines(query_expr, 8)};
    type t_records is table of c_records%rowtype index by pls_integer; r_rec t_records;"""
        else:
            plsql += """
    l_chunk_start date;"""
        # Every call to check_save_status that can lose the table raises, so a worker that lost it stops at once
        raise_on_code = "if l_sqlcode is not null then raise_application_error(l_sqlcode, l_out_message); end if;"
        plsql += f"""
begin
    if l_mode is null or l_mode not in ('EXECUTE', 'PREVIEW') then
        raise_application_error(-20007, 'Invalid ldb_mode ' || nvl(l_mode, 'NULL') || ': use EXECUTE or PREVIEW');
    end if;
    begin
    l_process_start := sysdate;
    check_save_status(l_source_owner, l_table_name, l_process_date, l_action, l_pfx || 'TSTART', l_process_start, null, null, l_message, 0, l_plsql, l_run_id, l_sqlcode, l_out_message);
    {raise_on_code}"""
        if source_ilm or not execution.use_added_columns:
            plsql += """
    check_referencing_tables(l_referencing_tables, l_process_date, case when l_mode = 'PREVIEW' then 1 else 0 end); commit;"""
        if not has_lob_columns or not source_ilm:
            plsql += f"""
    open c_records;
    loop
        l_chunk_start := sysdate;
        check_save_status(l_source_owner, l_table_name, l_process_date, l_action, l_pfx || 'CSTART', null, l_chunk_start, null, l_message, 0, null, l_run_id, l_sqlcode, l_out_message);
        {raise_on_code}
        fetch c_records bulk collect into r_rec limit l_chunk_size;
        if r_rec.count <= 0 then
            exit;
        end if;
        if l_mode = 'EXECUTE' then"""
            if source_ilm and nvl(months_keep_history_max, 1) > 0:
                plsql += f"""
            for i in 1 .. r_rec.count loop
                insert into {OracleEngine._format_identifier(history_owner)}.{OracleEngine._format_identifier(table_name)}@{OracleEngine._format_identifier(administration.source_to_history_dblink_name)}
                ({indent_lines(ins_cols, 16)})
                values ({indent_lines(ins_vals, 16)});
            end loop;"""
            plsql += f"""
            forall i in 1 .. r_rec.count
                delete from {OracleEngine._format_identifier(runtime_owner)}.{OracleEngine._format_identifier(table_name)} where rowid = r_rec(i).rowid;
        end if;
        l_record_count := l_record_count + r_rec.count;
        check_save_status(l_source_owner, l_table_name, l_process_date, l_action, l_pfx || 'CEND', null, l_chunk_start, sysdate, l_message, r_rec.count, null, l_run_id, l_sqlcode, l_out_message);
        {raise_on_code}
        commit;
    end loop;
    close c_records;"""
        else:
            cols_select = ", ".join([f"a.{col}" for col in table_columns] + other_cols_exprs)
            # LSTART locks the control row without commit for the whole statement; LEND and TEND close it
            plsql += f"""
    l_chunk_start := sysdate;
    check_save_status(l_source_owner, l_table_name, l_process_date, l_action, l_pfx || 'LSTART', null, l_chunk_start, null, l_message, 0, null, l_run_id, l_sqlcode, l_out_message);
    {raise_on_code}
    if l_mode = 'EXECUTE' then"""
            if source_ilm and nvl(months_keep_history_max, 0) > 0:
                plsql += f"""
        insert into {OracleEngine._format_identifier(history_owner)}.{OracleEngine._format_identifier(table_name)}@{OracleEngine._format_identifier(administration.source_to_history_dblink_name)}({indent_lines(ins_cols, 8)})
        select /*+ {hint_expr} */ {indent_lines(cols_select, 8)}
        {indent_lines(query_expr, 8)};"""
            plsql += f"""
        delete from {OracleEngine._format_identifier(runtime_owner)}.{OracleEngine._format_identifier(table_name)} where rowid in
        (select /*+ {hint_expr} */ a.rowid
        {indent_lines(query_expr, 8)});
        l_record_count := sql%rowcount;
    else
        select /*+ {hint_expr} */ count(*) into l_record_count
        {indent_lines(query_expr, 8)};
    end if;
    check_save_status(l_source_owner, l_table_name, l_process_date, l_action, l_pfx || 'LEND', null, l_chunk_start, sysdate, l_message, l_record_count, null, l_run_id, l_sqlcode, l_out_message);
    {raise_on_code}"""
        plsql += f"""
    check_save_status(l_source_owner, l_table_name, l_process_date, l_action, l_pfx || 'TEND', l_process_start, null, sysdate, l_message, 0, null, l_run_id, l_sqlcode, l_out_message);
    {raise_on_code}
    commit;
    exception
        when others then
            rollback;
            check_save_status(l_source_owner, l_table_name, l_process_date, l_action, l_pfx || 'ERROR', l_process_start, null, sysdate, sqlerrm, l_record_count, l_plsql, l_run_id, l_sqlcode, l_out_message);
            commit;
            if nvl(l_sqlcode, 0) <> -20003 then
                raise;
            end if;
    end;
end;"""
        return plsql

    @staticmethod
    def get_system_date(conn: oracledb.Connection) -> datetime:
        """Returns the current system date from Oracle.
        Args:
            conn: Active Oracle connection.
        Returns:
            Current system date as Python date."""
        cursor: oracledb.Cursor | None = None
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT SYSDATE FROM dual")
            sysdate: datetime = cursor.fetchone()[0]
            return sysdate
        except Exception as exc:
            logger.critical("Failed to retrieve system date from Oracle.", exc_info=True)
            raise ExecutionError("Failed to retrieve system date from Oracle") from exc
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def sql_block_run(conn: oracledb.Connection, plsql_code: str, mode: str, run_id: str) -> None:
        """Executes a PL/SQL block on the database.
        Args:
            conn: Active Oracle connection.
            plsql_code: The PL/SQL block to execute.
            mode: EXECUTE or PREVIEW, bound as ``:ldb_mode``.
            run_id: Identifier of the runner that owns the table, bound as ``:ldb_run_id``."""
        cursor: oracledb.Cursor | None = None
        try:
            cursor = conn.cursor()
            cursor.setinputsizes(plsql_code=oracledb.CLOB)
            cursor.execute(plsql_code, {"plsql_code": plsql_code, "ldb_mode": mode, "ldb_run_id": run_id})
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def get_status(conn: oracledb.Connection, process_date: str) -> list[dict[str, Any]]:
        """Loads status from ldb_ctl for the given process date.
        Args:
            conn: Active Oracle connection.
            process_date: Date in 'YYYYMMDD' format.
        Returns:
            List of status rows."""
        cursor: oracledb.Cursor | None = None
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT ctl_owner, ctl_table_name, ctl_status
                FROM ldb_ctl
                WHERE ctl_process_date = TO_DATE(:1, 'YYYYMMDD')""",
                [process_date],
            )
            cols = [col[0].lower() for col in cursor.description]
            return [dict(zip(cols, row, strict=True)) for row in cursor.fetchall()]
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def get_rows_processed(conn: oracledb.Connection, owner: str, table_name: str, process_date: str) -> int:
        """Returns rows processed for a given table and process date from ldb_ctl.
        Args:
            conn: Active Oracle connection.
            owner: Schema owner of the table.
            table_name: Table name.
            process_date: Target process date in 'YYYYMMDD'.
        Returns:
            Number of rows processed or 0 if none found or mismatched date."""
        cursor: oracledb.Cursor | None = None
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT ctl_rows_processed
                FROM ldb_ctl WHERE ctl_owner = :1 AND ctl_table_name = :2 AND ctl_process_date = TO_DATE(:3, 'YYYYMMDD')""",
                [owner, table_name, process_date],
            )
            result = cursor.fetchone()
            return result[0] if result else 0
        except Exception as exc:
            logger.critical(f"Unexpected error in get_rows_processed for {owner}.{table_name}:", exc_info=True)
            raise ExecutionError(f"Failed to read rows processed for {owner}.{table_name}") from exc
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def save_error_status(
        conn: oracledb.Connection,
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
        cursor: oracledb.Cursor | None = None
        try:
            cursor = conn.cursor()
            process_end = OracleEngine.get_system_date(conn)
            sqlcode = cursor.var(oracledb.NUMBER)
            out_message = cursor.var(oracledb.STRING)
            cursor.callproc(
                "check_save_status",
                [
                    owner,
                    table_name,
                    datetime.strptime(process_date, "%Y%m%d").date(),
                    config.execution.action,
                    Status.PREVIEW_ERROR if config.execution.mode == "PREVIEW" else Status.ERROR,
                    process_start,
                    None,
                    process_end,
                    message,
                    rows_processed,
                    plsql_code,
                    run_id,
                    sqlcode,
                    out_message,
                ],
            )
            conn.commit()
        except Exception as exc:
            logger.critical(f"Unexpected error in process_table for {owner}.{table_name}:", exc_info=True)
            raise ExecutionError(f"Failed to save error status for {owner}.{table_name}") from exc
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def get_log_entries(conn: oracledb.Connection, max_rows: int) -> list[LogEntry]:
        """Returns the most recent ``max_rows`` rows of ``ldb_log``, newest first (the PL/SQL of a run is not read).
        Args:
            conn: Active Oracle connection.
            max_rows: Number of rows to read at most."""
        cursor: oracledb.Cursor | None = None
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT log_id, log_owner, log_table_name, log_action, log_process_date, log_status,
                          log_process_start, log_process_end, log_message, NVL(log_rows_processed, 0)
                     FROM ldb_log ORDER BY log_id DESC FETCH FIRST :max_rows ROWS ONLY""",
                {"max_rows": max_rows},
            )
            return [
                LogEntry(
                    log_id=int(row[0]),
                    owner=row[1],
                    table_name=row[2],
                    action=row[3],
                    process_date=row[4],
                    status=row[5],
                    started=row[6],
                    ended=row[7],
                    message=row[8],
                    rows=int(row[9]),
                )
                for row in cursor.fetchall()
            ]
        except oracledb.DatabaseError as exc:
            raise ExecutionError(f"Could not read the execution log: {exc}") from exc
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def get_control_entries(conn: oracledb.Connection) -> list[ControlEntry]:
        """Returns every row of ``ldb_ctl``: the current state of each table.
        Args:
            conn: Active Oracle connection."""
        cursor: oracledb.Cursor | None = None
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT ctl_owner, ctl_table_name, ctl_status, ctl_run_id, ctl_action, ctl_process_date,
                          ctl_process_start, ctl_process_end, NVL(ctl_rows_processed, 0),
                          GREATEST(ctl_process_start, NVL(ctl_process_end, ctl_process_start)), ctl_db_session_id
                     FROM ldb_ctl"""
            )
            return [
                ControlEntry(
                    owner=row[0],
                    table_name=row[1],
                    status=row[2],
                    run_id=row[3],
                    action=row[4],
                    process_date=row[5],
                    started=row[6],
                    ended=row[7],
                    rows=int(row[8]),
                    last_activity=row[9],
                    session=row[10],
                )
                for row in cursor.fetchall()
            ]
        except oracledb.DatabaseError as exc:
            detail = exc.args[0] if exc.args else None
            hint = (
                " (the control tables predate the STATUS mode: rerun ldb-impl)"
                if getattr(detail, "code", None) == 904
                else ""
            )
            raise ExecutionError(f"Could not read the control table: {exc}{hint}") from exc
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def is_run_alive(conn: oracledb.Connection, owner: str, table_name: str, run_id: str) -> bool | None:
        """Tells whether the runner that owns a table is alive by trying its mutex without waiting.
        The lock is released at once, so the probe never holds anything.
        Args:
            conn: Active Oracle connection (its own connection: it is rolled back).
            owner: Schema owner of the table.
            table_name: Table name.
            run_id: Run recorded for the table.
        Returns:
            True when a session holds the table's runner lock and the key row names this run, False when
            nobody does or the holder is a different run (a later one), and None when the run has no runner
            lock (a generated SCRIPT, whose id starts with S) or it cannot be probed."""
        if run_id.startswith("S"):
            return None
        cursor = conn.cursor()
        try:
            cursor.execute(
                """SELECT 1 FROM ldb_run_lock
                WHERE rl_owner = :1 AND rl_table_name = :2 AND rl_kind = 'L' FOR UPDATE NOWAIT""",
                [owner, table_name],
            )
            found = cursor.fetchone() is not None
            return False if found else None
        except oracledb.DatabaseError as exc:
            detail = exc.args[0] if exc.args else None
            if getattr(detail, "code", None) != 54:
                return None
            # The lock is held, but the table may belong to a later run than the one asked about
            try:
                cursor.execute(
                    """SELECT rl_run_id FROM ldb_run_lock
                    WHERE rl_owner = :1 AND rl_table_name = :2 AND rl_kind = 'K'""",
                    [owner, table_name],
                )
                key_row = cursor.fetchone()
            except oracledb.DatabaseError:
                return None
            return None if key_row is None else key_row[0] == run_id
        finally:
            cursor.close()
            conn.rollback()

    # Views probed by the STATUS mode; a grant is on the SYS wrapper of the GV$ view
    _MONITORING_VIEWS: ClassVar[tuple[str, ...]] = (
        "gv$session",
        "gv$session_longops",
        "gv$session_event",
        "gv$sess_time_model",
    )

    @staticmethod
    def check_monitoring_access(conn: oracledb.Connection, grantee: str) -> MonitoringAccess:
        """Probes the session-monitoring views with the connection's own privileges.
        Args:
            conn: Active Oracle connection.
            grantee: User or role named in the grant hints."""
        readable: dict[str, bool] = {}
        for view in OracleEngine._MONITORING_VIEWS:
            cursor = conn.cursor()
            try:
                cursor.execute(f"SELECT 1 FROM {view} WHERE 1 = 0")
                readable[view] = True
            except oracledb.DatabaseError:
                readable[view] = False
            finally:
                cursor.close()
        target = OracleEngine._format_identifier(grantee)
        hints = tuple(
            f"GRANT SELECT ON sys.{view.replace('$', '_$')} TO {target}" for view, ok in readable.items() if not ok
        )
        return MonitoringAccess(
            session=readable["gv$session"],
            progress=readable["gv$session"] and readable["gv$session_longops"],
            waits=readable["gv$session_event"] and readable["gv$sess_time_model"],
            grants=hints,
        )

    @staticmethod
    def get_session_activity(
        conn: oracledb.Connection, session: str, access: MonitoringAccess, min_wait_seconds: float
    ) -> SessionActivity:
        """Reads the liveness, long-operation progress, current wait and wait profile of a session.
        Only what ``access`` allows is read; the rest comes back empty (``alive`` None without session access).
        Args:
            conn: Active Oracle connection.
            session: Session id registered by the run (``instance.sid.audsid``).
            access: Result of ``check_monitoring_access``.
            min_wait_seconds: A current wait shorter than this is not reported."""
        alive: bool | None = None
        try:
            inst_id, sid, audsid = (int(part) for part in session.split("."))
        except ValueError:  # not an id this engine wrote: nothing to look up
            return SessionActivity(alive=None, current_wait=None, progress=None, waits=())
        current: WaitItem | None = None
        progress: OperationProgress | None = None
        waits: list[WaitItem] = []
        cursor = conn.cursor()
        try:
            if access.session:
                cursor.execute(
                    """SELECT serial#, state, event, seconds_in_wait, wait_class FROM gv$session
                    WHERE inst_id = :inst AND sid = :sid AND audsid = :aud""",
                    {"inst": inst_id, "sid": sid, "aud": audsid},
                )
                row = cursor.fetchone()
                alive = row is not None
                if row is not None:
                    serial, state, event, seconds, wait_class = row
                    if state == "WAITING" and wait_class != "Idle" and float(seconds or 0) >= min_wait_seconds:
                        current = WaitItem(event, float(seconds))
                    if access.progress:
                        cursor.execute(
                            """SELECT sofar, totalwork, time_remaining, opname, target FROM gv$session_longops
                            WHERE inst_id = :inst AND sid = :sid AND serial# = :ser AND totalwork > 0
                              AND sofar < totalwork ORDER BY last_update_time DESC FETCH FIRST 1 ROW ONLY""",
                            {"inst": inst_id, "sid": sid, "ser": serial},
                        )
                        longop = cursor.fetchone()
                        if longop is not None:
                            sofar, totalwork, remaining, opname, target = longop
                            progress = OperationProgress(
                                percent=100.0 * float(sofar) / float(totalwork),
                                remaining_seconds=float(remaining) if remaining is not None else None,
                                description=" ".join(part for part in (opname, target) if part) or None,
                            )
            if access.waits and alive is not False:
                cursor.execute(
                    """SELECT event, time_waited_micro / 1e6 FROM gv$session_event
                    WHERE inst_id = :inst AND sid = :sid AND wait_class <> 'Idle' AND time_waited_micro > 0""",
                    {"inst": inst_id, "sid": sid},
                )
                waits = [WaitItem(event, float(seconds)) for event, seconds in cursor.fetchall()]
                cursor.execute(
                    """SELECT value / 1e6 FROM gv$sess_time_model
                    WHERE inst_id = :inst AND sid = :sid AND stat_name = 'DB CPU'""",
                    {"inst": inst_id, "sid": sid},
                )
                cpu = cursor.fetchone()
                if cpu is not None and cpu[0]:
                    waits.append(WaitItem("CPU", float(cpu[0])))
        except oracledb.DatabaseError as exc:
            raise ExecutionError(f"Could not read the session activity: {exc}") from exc
        finally:
            cursor.close()
        return SessionActivity(alive=alive, current_wait=current, progress=progress, waits=tuple(waits))

    @staticmethod
    def get_resumed_rows(conn: oracledb.Connection, owner: str, table_name: str, process_date: str) -> int:
        """Returns the rows kept when an EXECUTE continues an incomplete EXECUTE of the same process date.
        Args:
            conn: Active Oracle connection.
            owner: Schema owner of the table.
            table_name: Table name.
            process_date: Target process date in 'YYYYMMDD'.
        Returns:
            Rows already processed by the incomplete run, or 0 when TSTART will start the table over."""
        cursor: oracledb.Cursor | None = None
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT NVL(ctl_rows_processed, 0) FROM ldb_ctl
                WHERE ctl_owner = :1 AND ctl_table_name = :2 AND ctl_process_date = TO_DATE(:3, 'YYYYMMDD')
                AND ctl_status IN ('TSTART', 'CSTART', 'CEND', 'LSTART', 'LEND', 'ERROR')""",
                [owner, table_name, process_date],
            )
            result = cursor.fetchone()
            return int(result[0]) if result else 0
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def error_recorded_since(conn: oracledb.Connection, owner: str, table_name: str, since: datetime) -> bool:
        """Tells whether the table already holds an ERROR or PERROR written at or after ``since``.
        Args:
            conn: Active Oracle connection.
            owner: Schema owner of the table.
            table_name: Table name.
            since: Start of the run whose failure is being recorded.
        Returns:
            True when the block itself already persisted the error of this run."""
        cursor: oracledb.Cursor | None = None
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"""SELECT 1 FROM ldb_ctl
                WHERE ctl_owner = :1 AND ctl_table_name = :2
                AND ctl_status IN ('{Status.ERROR}', '{Status.PREVIEW_ERROR}') AND ctl_process_end >= :3""",
                [owner, table_name, since],
            )
            return cursor.fetchone() is not None
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def acquire_run_locks(
        lock_conn: oracledb.Connection,
        key_conn: oracledb.Connection,
        tables: Sequence[tuple[str, str]],
        run_id: str,
        mode: str,
        host: str,
        pid: int,
    ) -> None:
        """Takes the per-table runner mutex and publishes the run identifier of each table.

        ``lock_conn`` must never commit nor run DDL while the runner lives: its row locks are the mutex and Oracle
        frees them if the process or the session dies. ``key_conn`` writes and commits the anchor and key rows.
        Args:
            lock_conn: Dedicated connection that holds the lock rows.
            key_conn: Separate connection used to insert the anchors and publish the keys.
            tables: (owner, table_name) pairs to protect.
            run_id: Identifier of this run, checked by the workers under the ``ldb_ctl`` lock.
            mode: EXECUTE or PREVIEW.
            host: Host of the runner.
            pid: Process id of the runner.
        Raises:
            RunnerBusyError: Another runner holds the lock of one of the tables; nothing is left locked."""
        ordered = sorted(tables)
        key_cursor = key_conn.cursor()
        try:
            for owner, table_name in ordered:
                for kind in ("L", "K"):
                    try:
                        key_cursor.execute(
                            """INSERT INTO ldb_run_lock (rl_owner, rl_table_name, rl_kind)
                            SELECT :owner, :table_name, :kind FROM dual
                            WHERE NOT EXISTS (SELECT 1 FROM ldb_run_lock
                                              WHERE rl_owner = :owner AND rl_table_name = :table_name
                                              AND rl_kind = :kind)""",
                            {"owner": owner, "table_name": table_name, "kind": kind},
                        )
                        key_conn.commit()  # one anchor per transaction: a lost race must not undo the others
                    except oracledb.IntegrityError:
                        key_conn.rollback()  # another runner inserted the same anchor first
        finally:
            key_cursor.close()
        lock_cursor = lock_conn.cursor()
        try:
            for owner, table_name in ordered:
                try:
                    lock_cursor.execute(
                        """SELECT 1 FROM ldb_run_lock
                        WHERE rl_owner = :1 AND rl_table_name = :2 AND rl_kind = 'L' FOR UPDATE NOWAIT""",
                        [owner, table_name],
                    )
                    if lock_cursor.fetchone() is None:
                        lock_conn.rollback()
                        raise ExecutionError(f"The lock anchor of {owner}.{table_name} is missing; nothing was started")
                except oracledb.DatabaseError as exc:
                    lock_conn.rollback()
                    detail = exc.args[0] if exc.args else None
                    if getattr(detail, "code", None) == 54:
                        holder = OracleEngine._lock_holder(key_conn, owner, table_name)
                        raise RunnerBusyError(
                            f"Another runner is processing {owner}.{table_name}{holder}; nothing was started"
                        ) from exc
                    raise ExecutionError(f"Failed to lock {owner}.{table_name} for this run") from exc
        finally:
            lock_cursor.close()
        key_cursor = key_conn.cursor()
        try:
            for owner, table_name in ordered:
                key_cursor.execute(
                    """UPDATE ldb_run_lock
                    SET rl_run_id = :1, rl_mode = :2, rl_host = :3, rl_pid = :4, rl_start = SYSDATE
                    WHERE rl_owner = :5 AND rl_table_name = :6 AND rl_kind = 'K'""",
                    [run_id, mode, host, pid, owner, table_name],
                )
            key_conn.commit()
        finally:
            key_cursor.close()

    @staticmethod
    def _lock_holder(key_conn: oracledb.Connection, owner: str, table_name: str) -> str:
        """Describes the runner published in the key row of a table (`` (run …, mode …, host …, pid …, since …)``);
        empty when it cannot be read, since it only enriches the busy message."""
        cursor = key_conn.cursor()
        try:
            cursor.execute(
                """SELECT rl_run_id, rl_mode, rl_host, rl_pid, rl_start FROM ldb_run_lock
                WHERE rl_owner = :1 AND rl_table_name = :2 AND rl_kind = 'K'""",
                [owner, table_name],
            )
            row = cursor.fetchone()
        except oracledb.DatabaseError:
            return ""
        finally:
            cursor.close()
        if row is None or row[0] is None:
            return ""
        run_id, mode, host, pid, start = row
        since = f", since {start:%Y-%m-%d %H:%M:%S}" if start is not None else ""
        return f" (runner {run_id}, {mode}, host {host}, pid {pid}{since})"

    @staticmethod
    def check_run_locks(lock_conn: oracledb.Connection) -> None:
        """Runs a trivial query on the lock connection so a cut session is noticed.
        Raises:
            ExecutionError: The lock connection no longer answers, so the mutex cannot be trusted."""
        try:
            cursor = lock_conn.cursor()
            try:
                cursor.execute("SELECT 1 FROM dual")
                cursor.fetchone()
            finally:
                cursor.close()
        except Exception as exc:
            raise ExecutionError("The runner lock connection was lost") from exc

    @staticmethod
    def release_run_locks(lock_conn: oracledb.Connection) -> None:
        """Frees the lock rows; they are never committed, so a rollback is enough."""
        lock_conn.rollback()

    @staticmethod
    def all_status_tend(
        conn: oracledb.Connection, tables_config: dict[tuple[str, str], Any], process_date: str
    ) -> bool:
        """Checks if all referenced tables have status "Status.TABLE_END" in ldb_ctl.
        Args:
            conn: Active Oracle connection.
            tables_config: Dict of (owner, table_name) keys representing configured tables.
            process_date: Processing date in 'YYYYMMDD' format.
        Returns:
            True if all tables have status "Status.TABLE_END" for the given process date, False otherwise."""
        cursor: oracledb.Cursor | None = None
        try:
            cursor = conn.cursor()
            for (owner, table_name), table_cnf in tables_config.items():
                if table_cnf["skip"]:
                    continue
                cursor.execute(
                    f"""SELECT ctl_status FROM ldb_ctl
                    WHERE ctl_owner = :1 AND ctl_table_name = :2
                    AND ctl_process_date = TO_DATE(:3, 'YYYYMMDD') AND ctl_status = '{Status.TABLE_END}'""",
                    [owner, table_name, process_date],
                )
                if not cursor.fetchone():
                    return False
            return True
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def ensure_users(
        conn: oracledb.Connection | None, users: Sequence[UserDefinition], *, ddl_sink: DdlSink | None = None
    ) -> list[str]:
        created: list[str] = []
        if not users:
            return created
        if ddl_sink is not None:
            OracleEngine._require_script_mode(conn)
            OracleEngine._script_users(users, ddl_sink)
            return created
        assert conn is not None
        cursor: oracledb.Cursor | None = None
        changed = False
        try:
            cursor = conn.cursor()
            database_default_tablespace = OracleEngine._get_database_default_tablespace(cursor)
            for user in users:
                fmttd_username = OracleEngine._format_identifier(user.name)
                tablespace_for_quota = user.default_tablespace
                if not OracleEngine._user_exists(cursor, user.name):
                    sql = OracleEngine._create_user_sql(user, f"##{OracleEngine._quote(user.password)}##")
                    logger.debug(f"Creating user with SQL: {sql}")
                    OracleEngine._execute_ddl(conn, cursor, sql)
                    created.append(fmttd_username)
                    changed = True
                    if not user.default_tablespace:
                        tablespace_for_quota = (
                            OracleEngine._get_user_default_tablespace(cursor, user.name) or database_default_tablespace
                        )
                else:
                    tablespace_for_quota = (
                        OracleEngine._get_user_default_tablespace(cursor, user.name) or database_default_tablespace
                    )
                admin_option_roles = set(OracleEngine._format_identifier(r) for r in user.roles_with_admin_option)
                for role in user.roles:
                    fmttd_role_name = OracleEngine._format_identifier(role)
                    requires_admin_option = fmttd_role_name in admin_option_roles
                    has_role = OracleEngine._user_has_role(cursor, user.name, role)
                    if requires_admin_option:
                        if not has_role or not OracleEngine._user_has_role_with_admin_option(cursor, user.name, role):
                            grant_sql = f"GRANT {fmttd_role_name} TO {fmttd_username} WITH ADMIN OPTION"
                            OracleEngine._execute_ddl(conn, cursor, grant_sql)
                            changed = True
                    elif not has_role:
                        grant_sql = f"GRANT {fmttd_role_name} TO {fmttd_username}"
                        OracleEngine._execute_ddl(conn, cursor, grant_sql)
                        changed = True
                for privilege in user.system_privileges:
                    if not OracleEngine._user_has_sys_priv(cursor, user.name, privilege):
                        grant_priv_sql = f"GRANT {privilege} TO {fmttd_username}"
                        OracleEngine._execute_ddl(conn, cursor, grant_priv_sql)
                        changed = True
                if OracleEngine._ensure_unlimited_quota(cursor, user.name, tablespace_for_quota):
                    changed = True
            if changed:
                conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.critical("Failed to ensure users.", exc_info=True)
            raise ExecutionError("Failed to ensure Oracle users") from exc
        finally:
            if cursor:
                cursor.close()
        return created

    @staticmethod
    def ensure_tables(
        conn: oracledb.Connection | None, tables: Sequence[TableDefinition], *, ddl_sink: DdlSink | None = None
    ) -> list[str]:
        created: list[str] = []
        if not tables:
            return created
        if ddl_sink is not None:
            OracleEngine._require_script_mode(conn)
            OracleEngine._script_tables(tables, ddl_sink)
            return created
        assert conn is not None
        cursor: oracledb.Cursor | None = None
        changed = False
        try:
            cursor = conn.cursor()
            for table in tables:
                fmttd_owner = OracleEngine._format_identifier(table.owner)
                fmttd_table_name = OracleEngine._format_identifier(table.name)
                if not OracleEngine._table_exists(cursor, table.owner, table.name):
                    columns_sql = ",\n        ".join(OracleEngine._column_sql(col) for col in table.columns)
                    sql = f"CREATE TABLE {fmttd_owner}.{fmttd_table_name} (\n        {columns_sql}\n    )"
                    OracleEngine._execute_ddl(conn, cursor, sql)
                    created.append(f"{fmttd_owner}.{fmttd_table_name}")
                    changed = True
                else:
                    # A control table from an earlier version gets the columns added since (existing ones stay)
                    _, existing_columns = OracleEngine.get_table_columns(conn, table.owner, table.name)
                    for col in table.columns:
                        if col.name not in existing_columns:
                            OracleEngine._execute_ddl(
                                conn,
                                cursor,
                                f"ALTER TABLE {fmttd_owner}.{fmttd_table_name} ADD ({OracleEngine._column_sql(col)})",
                            )
                            changed = True
                if table.primary_key:
                    pk_name = f"{table.name}_PK"
                    fmttd_pk_name = OracleEngine._format_identifier(pk_name)
                    if not OracleEngine._constraint_exists(cursor, table.owner, pk_name):
                        cols = ", ".join(OracleEngine._format_identifier(col) for col in table.primary_key)
                        sql = f"ALTER TABLE {fmttd_owner}.{fmttd_table_name} ADD CONSTRAINT {fmttd_pk_name} PRIMARY KEY ({cols})"
                        OracleEngine._execute_ddl(conn, cursor, sql)
                        changed = True
                for index in table.indexes:
                    fmttd_idx_name = OracleEngine._format_identifier(index.name)
                    if OracleEngine._index_exists(cursor, table.owner, index.name):
                        continue
                    cols = ", ".join(OracleEngine._format_identifier(col) for col in index.columns)
                    unique_kw = "UNIQUE " if index.unique else ""
                    sql = f"CREATE {unique_kw}INDEX {fmttd_owner}.{fmttd_idx_name} ON {fmttd_owner}.{fmttd_table_name} ({cols})"
                    OracleEngine._execute_ddl(conn, cursor, sql)
                    changed = True
            if changed:
                conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.critical("Failed to ensure tables.", exc_info=True)
            raise ExecutionError("Failed to ensure Oracle tables") from exc
        finally:
            if cursor:
                cursor.close()
        return created

    @staticmethod
    def _emit_ddl(
        conn: oracledb.Connection,
        cursor: oracledb.Cursor,
        statement: str,
        ddl_sink: DdlSink | None,
        ddl_failures: DdlFailures | None,
    ) -> None:
        """Hand the statement to the sink, or execute it, recording a failure when the caller collects them."""
        if ddl_sink is not None:
            ddl_sink(statement)
            return
        try:
            OracleEngine._execute_ddl(conn, cursor, statement)
        except oracledb.Error as exc:
            if ddl_failures is None:
                raise
            message = " ".join(str(exc).split())
            logger.warning("DDL statement failed and was left pending: %s", message)
            ddl_failures.append((statement, message))

    @staticmethod
    def ensure_table_structure(
        conn: oracledb.Connection | None,
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
            for statement in OracleEngine._create_table_statements(table):
                ddl_sink(statement)
            return
        cursor: oracledb.Cursor | None = None
        logger.debug(f"Ensuring structure for table {table.owner}.{table.name} conn: {conn}")
        try:
            cursor = conn.cursor()
            for statement in OracleEngine._compute_table_structure_delta(conn, cursor, table, reference):
                OracleEngine._emit_ddl(conn, cursor, statement, ddl_sink, ddl_failures)
        except Exception as exc:
            conn.rollback()
            logger.critical("Failed to ensure table structure.", exc_info=True)
            raise ExecutionError("Failed to ensure Oracle table structure") from exc
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def _create_table_statements(table: TableDefinition) -> list[str]:
        """DDL that creates the table with its primary key and desired indexes."""
        fmttd_owner = OracleEngine._format_identifier(table.owner)
        fmttd_table_name = OracleEngine._format_identifier(table.name)
        fmttd_columns_sql = ",\n        ".join(OracleEngine._column_sql(col) for col in table.columns)
        statements = [f"CREATE TABLE {fmttd_owner}.{fmttd_table_name} (\n        {fmttd_columns_sql}\n    )"]
        desired_pk = tuple(table.primary_key or ())
        if desired_pk:
            fmttd_pk = tuple(OracleEngine._format_identifier(c) for c in desired_pk)
            fmttd_cn = OracleEngine._format_identifier(f"{table.name}_PK")
            statements.append(
                f"ALTER TABLE {fmttd_owner}.{fmttd_table_name} ADD CONSTRAINT {fmttd_cn}"
                f" PRIMARY KEY ({', '.join(fmttd_pk)})"
            )
        for index_name, (columns, is_unique) in OracleEngine._prepare_desired_indexes(table).items():
            fmttd_iname = OracleEngine._format_identifier(index_name)
            fmttd_cols = ", ".join(OracleEngine._format_identifier(c) for c in columns)
            unique_clause = "UNIQUE " if is_unique else ""
            statements.append(
                f"CREATE {unique_clause}INDEX {fmttd_owner}.{fmttd_iname}"
                f" ON {fmttd_owner}.{fmttd_table_name} ({fmttd_cols})"
            )
        return statements

    @staticmethod
    def _compute_table_structure_delta(
        conn: oracledb.Connection,
        cursor: oracledb.Cursor,
        table: TableDefinition,
        reference: tuple[str, str] | None = None,
    ) -> list[str]:
        """Compute DDL statements to bring the table to the required structure using accessible views.

        Uses ALL_TABLES, ALL_TAB_COLUMNS, ALL_CONSTRAINTS, ALL_CONS_COLUMNS, ALL_INDEXES and
        ALL_IND_COLUMNS — readable by any user with object-level SELECT access — so this method
        can be called with a non-administrative connection.
        Returns an empty list when no changes are required.
        """
        statements: list[str] = []
        fmttd_owner = OracleEngine._format_identifier(table.owner)
        fmttd_table_name = OracleEngine._format_identifier(table.name)
        # The current structure is read from the reference when one is given, else from the table.
        current_owner, current_name = reference or (table.owner, table.name)

        cursor.execute(
            "SELECT 1 FROM all_tables WHERE owner = :1 AND table_name = :2",
            [current_owner, current_name],
        )
        if cursor.fetchone() is None:
            return OracleEngine._create_table_statements(table)

        # Table exists: check columns
        _, existing_columns = OracleEngine.get_table_columns(conn, current_owner, current_name)
        for column in table.columns:
            column_definition = OracleEngine._column_sql(column)
            if column.name not in existing_columns:
                statements.append(f"ALTER TABLE {fmttd_owner}.{fmttd_table_name} ADD ({column_definition})")
            elif OracleEngine._column_needs_update(existing_columns[column.name], column):
                statements.append(f"ALTER TABLE {fmttd_owner}.{fmttd_table_name} MODIFY ({column_definition})")

        # Get current PK using all_constraints / all_cons_columns
        cursor.execute(
            """
            SELECT constraint_name, index_name
              FROM all_constraints
             WHERE owner = :1 AND table_name = :2 AND constraint_type = 'P'
            """,
            [current_owner, current_name],
        )
        row = cursor.fetchone()
        existing_pk_name: str | None = None
        existing_pk_cols: tuple[str, ...] = ()
        existing_pk_index: str | None = None
        if row:
            existing_pk_name, existing_pk_index = row[0], row[1]
            cursor.execute(
                "SELECT column_name FROM all_cons_columns WHERE owner = :1 AND constraint_name = :2 ORDER BY position",
                [current_owner, existing_pk_name],
            )
            existing_pk_cols = tuple(r[0] for r in cast(Iterable[tuple[str]], cursor))

        # Get current indexes using all_indexes / all_ind_columns
        cursor.execute(
            """
            SELECT i.index_name, i.uniqueness, c.column_name
              FROM all_indexes i
              JOIN all_ind_columns c ON i.owner = c.index_owner AND i.index_name = c.index_name
             WHERE i.owner = :owner AND i.table_name = :table_name
             ORDER BY i.index_name, c.column_position
            """,
            owner=current_owner,
            table_name=current_name,
        )
        idx_map: dict[str, dict[str, Any]] = {}
        for index_name, uniqueness, column_name in cast(Iterable[tuple[str, str, str]], cursor):
            info = idx_map.setdefault(index_name, {"columns": [], "unique": uniqueness == "UNIQUE"})
            info["columns"].append(column_name)
        # Keys and desired_constraint_name are uppercase (Oracle internal storage); keep them consistent.
        effective_indexes: dict[str, tuple[tuple[str, ...], bool]] = {
            name: (tuple(info["columns"]), bool(info["unique"])) for name, info in idx_map.items()
        }

        desired_pk = tuple(table.primary_key or ())
        fmttd_desired_pk = tuple(OracleEngine._format_identifier(c) for c in desired_pk)
        desired_constraint_name = f"{table.name}_PK"
        fmttd_desired_cn = OracleEngine._format_identifier(desired_constraint_name)
        effective_pk_name = existing_pk_name
        effective_pk_index = existing_pk_index

        if desired_pk:
            if not existing_pk_cols:
                existing_index_info = effective_indexes.get(desired_constraint_name)
                if existing_index_info and (existing_index_info[0] != desired_pk or not existing_index_info[1]):
                    statements.append(f"DROP INDEX {fmttd_owner}.{fmttd_desired_cn}")
                    effective_indexes.pop(desired_constraint_name, None)
                statements.append(
                    f"ALTER TABLE {fmttd_owner}.{fmttd_table_name} ADD CONSTRAINT {fmttd_desired_cn}"
                    f" PRIMARY KEY ({', '.join(fmttd_desired_pk)})"
                )
                effective_pk_name = desired_constraint_name
                effective_pk_index = desired_constraint_name
            elif existing_pk_cols != desired_pk:
                if existing_pk_name:
                    # One statement drops the constraint and its index, whether Oracle created the index
                    # implicitly or not; a separate DROP INDEX would fail with ORA-01418 in the implicit case.
                    statements.append(f"ALTER TABLE {fmttd_owner}.{fmttd_table_name} DROP PRIMARY KEY DROP INDEX")
                    # Use the raw (uppercase) name for dict lookup.
                    effective_indexes.pop(existing_pk_index or existing_pk_name, None)
                    effective_pk_name = None
                    effective_pk_index = None
                existing_index_info = effective_indexes.get(desired_constraint_name)
                if existing_index_info and (existing_index_info[0] != desired_pk or not existing_index_info[1]):
                    statements.append(f"DROP INDEX {fmttd_owner}.{fmttd_desired_cn}")
                    effective_indexes.pop(desired_constraint_name, None)
                statements.append(
                    f"ALTER TABLE {fmttd_owner}.{fmttd_table_name} ADD CONSTRAINT {fmttd_desired_cn}"
                    f" PRIMARY KEY ({', '.join(fmttd_desired_pk)})"
                )
                effective_pk_name = desired_constraint_name
                effective_pk_index = desired_constraint_name
        elif existing_pk_cols and existing_pk_name:
            statements.append(f"ALTER TABLE {fmttd_owner}.{fmttd_table_name} DROP PRIMARY KEY DROP INDEX")
            effective_indexes.pop(existing_pk_index or existing_pk_name, None)
            effective_pk_name = None
            effective_pk_index = None

        # Desired indexes
        desired_indexes = OracleEngine._prepare_desired_indexes(table)
        for index_name, (columns, is_unique) in desired_indexes.items():
            fmttd_iname = OracleEngine._format_identifier(index_name)
            existing_index = effective_indexes.get(index_name)
            if existing_index == (columns, is_unique):
                continue
            if existing_index is not None:
                statements.append(f"DROP INDEX {fmttd_owner}.{fmttd_iname}")
                effective_indexes.pop(index_name, None)
            fmttd_cols = ", ".join(OracleEngine._format_identifier(c) for c in columns)
            unique_clause = "UNIQUE " if is_unique else ""
            statements.append(
                f"CREATE {unique_clause}INDEX {fmttd_owner}.{fmttd_iname}"
                f" ON {fmttd_owner}.{fmttd_table_name} ({fmttd_cols})"
            )
            effective_indexes[index_name] = (columns, is_unique)

        # Unmanaged indexes: drop those not in the desired set and not backing the final PK
        expected_indexes: set[str] = set(desired_indexes.keys())
        if effective_pk_name:
            expected_indexes.add(effective_pk_name)
        if effective_pk_index:
            expected_indexes.add(effective_pk_index)
        for index_name in list(effective_indexes.keys()):
            if table.drop_unmanaged_indexes and index_name not in expected_indexes:
                fmttd_iname = OracleEngine._format_identifier(index_name)
                logger.info("Unmanaged index %s on %s.%s will be dropped", fmttd_iname, fmttd_owner, fmttd_table_name)
                statements.append(f"DROP INDEX {fmttd_owner}.{fmttd_iname}")

        return statements

    @staticmethod
    def get_primary_key_columns(
        conn: oracledb.Connection, owner: str, table_name: str, table_cnf: Mapping[str, Any]
    ) -> tuple[str, ...]:
        cursor: oracledb.Cursor | None = None
        try:
            cursor = conn.cursor()
            _, columns, _ = OracleEngine._get_primary_key_info(cursor, owner, table_name)
            if columns:
                return tuple(col for col in columns)
            scope = OracleEngine._dictionary_scope(cursor)
            cursor.execute(
                f"""
                SELECT i.index_name, i.uniqueness, c.column_name, s.distinct_keys
                FROM {scope}_indexes i JOIN {scope}_ind_columns c ON i.owner = c.index_owner AND i.index_name = c.index_name
                LEFT JOIN {scope}_ind_statistics s ON s.owner = i.owner AND s.index_name = i.index_name AND s.partition_name IS NULL
                WHERE i.owner = :owner
                AND i.table_name = :table_name
                ORDER BY i.index_name, c.column_position
                """,
                owner=owner,
                table_name=table_name,
            )
            indexes: dict[str, dict[str, Any]] = {}
            for index_name, uniqueness, column_name, distinct_keys in cast(
                Iterable[tuple[str, str, str, int | None]], cursor
            ):
                index_info = indexes.setdefault(
                    index_name,
                    {
                        "columns": [],
                        "unique": uniqueness == "UNIQUE",
                        "distinct_keys": None,
                    },
                )
                index_info["columns"].append(column_name)
                if distinct_keys is not None:
                    try:
                        index_info["distinct_keys"] = int(distinct_keys)
                    except (TypeError, ValueError):
                        try:
                            index_info["distinct_keys"] = int(float(distinct_keys))
                        except (TypeError, ValueError):
                            index_info["distinct_keys"] = index_info.get("distinct_keys")
            if not indexes:
                return ()
            all_columns = {col for info in indexes.values() for col in info.get("columns", [])}
            columns_metadata = table_cnf.get("columns_metadata", {})
            for col in all_columns:
                if hasattr(table_cnf["metadata"], col):
                    logger.warning(f"Index column '{col}' not found in table definition for {owner}.{table_name}.")
            chosen = OracleEngine._choose_best_index_for_primary_key(indexes, columns_metadata)
            if not chosen:
                return ()
            return tuple(chosen)
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def ensure_roles(
        conn: oracledb.Connection | None, roles: Sequence[RoleDefinition], *, ddl_sink: DdlSink | None = None
    ) -> list[str]:
        created: list[str] = []
        if not roles:
            return created
        if ddl_sink is not None:
            OracleEngine._require_script_mode(conn)
            for role in roles:
                create_role = f"CREATE ROLE {OracleEngine._format_identifier(role.name)}"
                ddl_sink(OracleEngine._tolerant_block(create_role, (-1921,)))
                ddl_sink("")
            return created
        assert conn is not None
        cursor: oracledb.Cursor | None = None
        changed = False
        try:
            cursor = conn.cursor()
            for role in roles:
                fmttd_role_name = OracleEngine._format_identifier(role.name)
                if OracleEngine._role_exists(cursor, role.name):
                    continue
                sql = f"CREATE ROLE {fmttd_role_name}"
                OracleEngine._execute_ddl(conn, cursor, sql)
                created.append(fmttd_role_name)
                changed = True
            if changed:
                conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.critical("Failed to ensure roles.", exc_info=True)
            raise ExecutionError("Failed to ensure Oracle roles") from exc
        finally:
            if cursor:
                cursor.close()
        return created

    @staticmethod
    def ensure_sequences(
        conn: oracledb.Connection | None, sequences: Sequence[SequenceDefinition], *, ddl_sink: DdlSink | None = None
    ) -> list[str]:
        created: list[str] = []
        if not sequences:
            return created
        if ddl_sink is not None:
            OracleEngine._require_script_mode(conn)
            for sequence in sequences:
                ddl_sink(OracleEngine._tolerant_block(OracleEngine._create_sequence_sql(sequence), (-955,)))
                ddl_sink("")
            return created
        assert conn is not None
        cursor: oracledb.Cursor | None = None
        changed = False
        try:
            cursor = conn.cursor()
            for sequence in sequences:
                fmttd_owner = OracleEngine._format_identifier(sequence.owner)
                fmttd_sequence_name = OracleEngine._format_identifier(sequence.name)
                if OracleEngine._sequence_exists(cursor, sequence.owner, sequence.name):
                    continue
                OracleEngine._execute_ddl(conn, cursor, OracleEngine._create_sequence_sql(sequence))
                created.append(f"{fmttd_owner}.{fmttd_sequence_name}")
                changed = True
            if changed:
                conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.critical("Failed to ensure sequences.", exc_info=True)
            raise ExecutionError("Failed to ensure Oracle sequences") from exc
        finally:
            if cursor:
                cursor.close()
        return created

    @staticmethod
    def script_password_prompts(
        users: Sequence[UserDefinition], links: Sequence[DatabaseLinkDefinition], *, ddl_sink: DdlSink
    ) -> None:
        literal = OracleEngine._quote_literal
        for position, user in enumerate(users, start=1):
            prompt = f"Password for user {user.name} (set if it does not exist yet; must match it if it does): "
            ddl_sink(f"accept ldb_user_pwd_{position} char prompt {literal(prompt)} hide")
        for position, link in enumerate(links, start=1):
            prompt = f"Password of {link.username} for database link {link.name}: "
            ddl_sink(f"accept ldb_link_pwd_{position} char prompt {literal(prompt)} hide")

    @staticmethod
    def ensure_database_links(
        conn: oracledb.Connection | None,
        links: Sequence[DatabaseLinkDefinition],
        *,
        ddl_sink: DdlSink | None = None,
        connect_as: tuple[str, str] | None = None,
    ) -> list[str]:
        created: list[str] = []
        if not links:
            return created
        if ddl_sink is not None:
            OracleEngine._require_script_mode(conn)
            if connect_as is None:
                raise ValueError("connect_as (runtime username, dsn) is required to script database links")
            OracleEngine._script_database_links(links, connect_as, ddl_sink)
            return created
        assert conn is not None
        cursor: oracledb.Cursor | None = None
        changed = False
        try:
            cursor = conn.cursor()
            for link in links:
                fmttd_link_name = OracleEngine._format_identifier(link.name)
                if OracleEngine._db_link_exists(cursor, link.name):
                    continue
                fmttd_username = OracleEngine._format_identifier(link.username)
                fmttd_password = OracleEngine._quote(link.password)
                literal_dsn_literal = OracleEngine._quote_literal(link.dsn)
                sql = f"CREATE DATABASE LINK {fmttd_link_name} CONNECT TO {fmttd_username} IDENTIFIED BY ##{fmttd_password}## USING {literal_dsn_literal}"
                OracleEngine._execute_ddl(conn, cursor, sql)
                created.append(fmttd_link_name)
                changed = True
            if changed:
                conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.critical("Failed to ensure database links.", exc_info=True)
            raise ExecutionError("Failed to ensure Oracle database links") from exc
        finally:
            if cursor:
                cursor.close()
        return created

    @staticmethod
    def _grant_sql(role: str, owner: str, table_name: str, privilege: str) -> str:
        fmttd_owner = OracleEngine._format_identifier(owner)
        fmttd_table_name = OracleEngine._format_identifier(table_name)
        return f"GRANT {privilege} ON {fmttd_owner}.{fmttd_table_name} TO {OracleEngine._format_identifier(role)}"

    @staticmethod
    def ensure_table_privileges(
        conn: oracledb.Connection | None,
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
        if conn is None:
            if ddl_sink is None:
                raise ValueError("ensure_table_privileges needs a ddl_sink when conn is None")
            for owner, table_name in tables:
                for privilege in privileges:
                    ddl_sink(OracleEngine._grant_sql(role, owner, table_name, privilege.upper()))
                    granted.append(f"{owner}.{table_name}:{privilege.upper()}")
            return granted
        cursor: oracledb.Cursor | None = None
        changed = False
        try:
            cursor = conn.cursor()
            for owner, table_name in tables:
                fmttd_owner = OracleEngine._format_identifier(owner)
                fmttd_table_name = OracleEngine._format_identifier(table_name)
                for privilege in privileges:
                    privilege_upper = privilege.upper()
                    if OracleEngine._role_has_table_priv(
                        cursor, role.upper(), owner.upper(), table_name.upper(), privilege_upper
                    ):
                        continue
                    sql = OracleEngine._grant_sql(role, owner, table_name, privilege_upper)
                    OracleEngine._emit_ddl(conn, cursor, sql, ddl_sink, ddl_failures)
                    granted.append(f"{fmttd_owner}.{fmttd_table_name}:{privilege_upper}")
                    changed = True
            if changed:
                conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.critical("Failed to ensure table privileges.", exc_info=True)
            raise ExecutionError("Failed to ensure Oracle table privileges") from exc
        finally:
            if cursor:
                cursor.close()
        return granted

    @staticmethod
    def get_missing_table_privileges(
        conn: oracledb.Connection, role: str, tables: Sequence[tuple[str, str]], privileges: Sequence[str]
    ) -> list[tuple[str, str, str]]:
        cursor: oracledb.Cursor | None = None
        missing: list[tuple[str, str, str]] = []
        try:
            cursor = conn.cursor()
            # A role that is not enabled grants nothing, and all_tab_privs would not show it either
            enabled = OracleEngine._role_enabled(cursor, role)
            for owner, table_name in tables:
                for privilege in privileges:
                    privilege_upper = privilege.upper()
                    if not enabled or not OracleEngine._role_has_table_priv(
                        cursor, role, owner, table_name, privilege_upper
                    ):
                        missing.append((owner, table_name, privilege_upper))
        except Exception as exc:
            logger.critical("Failed to read table privileges.", exc_info=True)
            raise ExecutionError("Failed to read Oracle table privileges") from exc
        finally:
            if cursor:
                cursor.close()
        return missing

    @staticmethod
    def is_role_enabled(conn: oracledb.Connection, role: str) -> bool:
        cursor: oracledb.Cursor | None = None
        try:
            cursor = conn.cursor()
            return OracleEngine._role_enabled(cursor, role)
        except Exception as exc:
            logger.critical("Failed to read the enabled roles.", exc_info=True)
            raise ExecutionError("Failed to read the Oracle session roles") from exc
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def _role_enabled(cursor: oracledb.Cursor, role: str) -> bool:
        return OracleEngine._object_exists(cursor, "SELECT 1 FROM session_roles WHERE role = :1", [role])

    @staticmethod
    def _create_sequence_sql(sequence: SequenceDefinition) -> str:
        fmttd_owner = OracleEngine._format_identifier(sequence.owner)
        fmttd_sequence_name = OracleEngine._format_identifier(sequence.name)
        sql = (
            f"CREATE SEQUENCE {fmttd_owner}.{fmttd_sequence_name} "
            f"START WITH {sequence.start_with} INCREMENT BY {sequence.increment_by}"
        )
        sql += f" MINVALUE {sequence.minvalue}" if sequence.minvalue is not None else " NOMINVALUE"
        sql += f" MAXVALUE {sequence.maxvalue}" if sequence.maxvalue is not None else " NOMAXVALUE"
        sql += " CYCLE" if sequence.cycle else " NOCYCLE"
        sql += f" CACHE {sequence.cache}" if sequence.cache is not None else " NOCACHE"
        return sql

    @staticmethod
    def _create_user_sql(user: UserDefinition, password_expression: str) -> str:
        sql = f"CREATE USER {OracleEngine._format_identifier(user.name)} IDENTIFIED BY {password_expression}"
        if user.default_tablespace:
            sql += f" DEFAULT TABLESPACE {OracleEngine._format_identifier(user.default_tablespace)}"
        if user.temporary_tablespace:
            sql += f" TEMPORARY TABLESPACE {OracleEngine._format_identifier(user.temporary_tablespace)}"
        return sql

    @staticmethod
    def _tolerant_block(statement: str, tolerated_errors: Sequence[int]) -> str:
        """PL/SQL block that runs the statement and ignores only the given 'already exists' errors."""
        escaped = statement.replace("'", "''")
        codes = ", ".join(str(code) for code in tolerated_errors)
        return (
            f"begin\n  execute immediate '{escaped}';\nexception\n  when others then\n"
            f"    if sqlcode not in ({codes}) then raise; end if;\nend;\n/"
        )

    @staticmethod
    def _existing_user_notice(username: str) -> str:
        """PL/SQL block that warns when the user already exists, so its password stays unchanged."""
        name = username
        message = f"User {name} already exists: its password was not changed. The runtime password must match it."
        return (
            "declare n number;\nbegin\n"
            f"  select count(*) into n from dba_users where username = {OracleEngine._quote_literal(name)};\n"
            f"  if n > 0 then dbms_output.put_line({OracleEngine._quote_literal(message)}); end if;\n"
            "end;\n/"
        )

    @staticmethod
    def _require_script_mode(conn: oracledb.Connection | None) -> None:
        if conn is not None:
            raise ValueError("a ddl_sink on this method needs conn=None: no database is read when scripting")

    @staticmethod
    def _script_users(users: Sequence[UserDefinition], sink: DdlSink) -> None:
        fmt = OracleEngine._format_identifier
        literal = OracleEngine._quote_literal
        for position, user in enumerate(users, start=1):
            name = fmt(user.name)
            variable = f"ldb_user_pwd_{position}"
            sink(OracleEngine._existing_user_notice(user.name))
            sink(OracleEngine._tolerant_block(OracleEngine._create_user_sql(user, f'"&{variable}"'), (-1920,)))
            admin_option_roles = {fmt(role_name) for role_name in user.roles_with_admin_option}
            for role_name in user.roles:
                option = " WITH ADMIN OPTION" if fmt(role_name) in admin_option_roles else ""
                sink(f"GRANT {fmt(role_name)} TO {name}{option};")
            for privilege in user.system_privileges:
                sink(f"GRANT {privilege} TO {name};")
            if user.default_tablespace:
                sink(f"ALTER USER {name} QUOTA UNLIMITED ON {fmt(user.default_tablespace)};")
            else:
                sink("column ldb_ts new_value ldb_ts noprint")
                sink(
                    "select nvl((select default_tablespace from dba_users where username = "
                    f"{literal(user.name)}),(select property_value from "
                    "database_properties where property_name = 'DEFAULT_PERMANENT_TABLESPACE')) ldb_ts from dual;"
                )
                sink(f"ALTER USER {name} QUOTA UNLIMITED ON &ldb_ts;")
            sink("")

    @staticmethod
    def _script_tables(tables: Sequence[TableDefinition], sink: DdlSink) -> None:
        fmt = OracleEngine._format_identifier
        block = OracleEngine._tolerant_block
        for table in tables:
            owner, table_name = fmt(table.owner), fmt(table.name)
            columns_sql = ",\n        ".join(OracleEngine._column_sql(col) for col in table.columns)
            sink(block(f"CREATE TABLE {owner}.{table_name} (\n        {columns_sql}\n    )", (-955,)))
            for col in table.columns:
                # ORA-01430: the column exists; a table from an earlier version gets the columns added since
                sink(block(f"ALTER TABLE {owner}.{table_name} ADD ({OracleEngine._column_sql(col)})", (-1430,)))
            if table.primary_key:
                cols = ", ".join(fmt(col) for col in table.primary_key)
                pk_sql = (
                    f"ALTER TABLE {owner}.{table_name} ADD CONSTRAINT {fmt(f'{table.name}_PK')} PRIMARY KEY ({cols})"
                )
                sink(block(pk_sql, (-2260, -2264)))
            for index in table.indexes:
                unique = "UNIQUE " if index.unique else ""
                cols = ", ".join(fmt(col) for col in index.columns)
                index_sql = f"CREATE {unique}INDEX {owner}.{fmt(index.name)} ON {owner}.{table_name} ({cols})"
                sink(block(index_sql, (-955,)))
            sink("")

    @staticmethod
    def _script_database_links(
        links: Sequence[DatabaseLinkDefinition], connect_as: tuple[str, str], sink: DdlSink
    ) -> None:
        fmt = OracleEngine._format_identifier
        literal = OracleEngine._quote_literal
        runtime_username, runtime_dsn = connect_as
        # The runtime user is the one ensure_users scripted first in this same script: its password was already
        # asked once (ldb_user_pwd_1) by script_password_prompts, so the DBA is never asked twice for it.
        sink(
            f"prompt Connecting as {runtime_username}. If this fails with ORA-01017, the user already existed with "
            "a password different from the one entered above."
        )
        sink(f'connect {fmt(runtime_username)}/"&ldb_user_pwd_1"@"{runtime_dsn}"')
        for position, link in enumerate(links, start=1):
            variable = f"ldb_link_pwd_{position}"
            create_link = (
                f"CREATE DATABASE LINK {fmt(link.name)} CONNECT TO {fmt(link.username)} "
                f'IDENTIFIED BY "&{variable}" USING {literal(link.dsn)}'
            ).replace("'", "''")
            sink(
                "declare n number;\n"
                "begin\n"
                f"  select count(*) into n from user_db_links where upper(db_link) like upper({literal(link.name)}) || '%';\n"
                f"  if n = 0 then execute immediate '{create_link}'; end if;\n"
                "end;\n/"
            )

    @staticmethod
    def ensure_supporting_objects(
        conn: oracledb.Connection | None, owner: str, *, ddl_sink: DdlSink | None = None
    ) -> list[str]:
        if ddl_sink is not None:
            OracleEngine._require_script_mode(conn)
            lines = [f"alter session set current_schema = {OracleEngine._format_identifier(owner)};", "set define off"]
            for statement in (T_REFERENCING_TABLES_TYPE, CHECK_SAVE_STATUS_PROC, CHECK_REFERENCING_TABLES_PROC):
                body = statement.strip()
                lines += [body if body.endswith(";") and "PROCEDURE" in body else body.rstrip(";"), "/", ""]
            lines.append("set define on")
            ddl_sink("\n".join(lines))
            return []
        assert conn is not None
        cursor: oracledb.Cursor | None = None
        try:
            cursor = conn.cursor()
            cursor.execute(f"ALTER SESSION SET CURRENT_SCHEMA = {OracleEngine._format_identifier(owner)}")
            for statement in (T_REFERENCING_TABLES_TYPE, CHECK_SAVE_STATUS_PROC, CHECK_REFERENCING_TABLES_PROC):
                OracleEngine._execute_ddl(conn, cursor, statement)
            # CREATE OR REPLACE succeeds even when the unit does not compile, so the errors are read back
            cursor.execute(
                """SELECT name, type, line, position, text FROM all_errors
                WHERE owner = :1 AND attribute = 'ERROR'
                AND name IN ('T_REFERENCING_TABLES', 'CHECK_SAVE_STATUS', 'CHECK_REFERENCING_TABLES')
                ORDER BY name, type, sequence""",
                [OracleEngine.get_identifier_str(owner)],
            )
            compile_errors = [f"{row[0]} ({row[1]}) line {row[2]}: {row[4]}" for row in cursor.fetchall()]
            if compile_errors:
                raise ExecutionError(
                    f"Supporting PL/SQL objects of {owner} are invalid after installing them: "
                    + "; ".join(compile_errors)
                )
            return SUPPORTING_OBJECT_LIST
        except ExecutionError:
            logger.critical("Supporting PL/SQL objects of %s do not compile.", owner)
            raise
        except Exception as exc:
            logger.critical("Failed to ensure supporting PL/SQL objects.", exc_info=True)
            raise ExecutionError("Failed to ensure Oracle supporting PL/SQL objects") from exc
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def get_identifier_str(identifier: str) -> str:
        """Returns the identifier string required to query dictionary views.
        Args:
            indentifier: The identifier string to process.
        Returns:
            The identifier string without angle brackets.
        """
        identifier = identifier.strip()
        if identifier.startswith('"') and identifier.endswith('"'):
            return identifier[1:-1]
        return identifier.upper()

    @staticmethod
    def get_ldb_columns_expressions() -> tuple[str, str]:
        """Returns the expressions for the LDB process date and insert date columns.
        Returns:
            A tuple containing the process date expression and insert date expression.
        """
        return "l_process_date", "sysdate"

    @staticmethod
    def _column_sql(column: ColumnDefinition) -> str:
        col_name = OracleEngine._format_identifier(column.name)
        col_type = OracleEngine.get_column_type(column)
        default_clause = f" DEFAULT {column.default}" if column.default is not None else ""
        nullable_clause = "" if column.nullable else " NOT NULL"
        return f"{col_name} {col_type}{default_clause}{nullable_clause}"

    # format / expressions helpers
    @staticmethod
    def format_identifier(name: str) -> str:
        return OracleEngine._format_identifier(name)

    @staticmethod
    def _format_identifier(name: str | None) -> str:
        """Formats an Oracle identifier, quoting if necessary.
        Args:
            name: The identifier name as appears data dictionary.
        Returns:
            Formatted identifier, quoted if necessary."""
        if not name:
            return '""'
        if OracleEngine._REGULAR_IDENTIFIER.fullmatch(name):
            return name.lower()
        return OracleEngine._quote(name)

    @staticmethod
    def _quote(identifier: str) -> str:
        escaped = identifier.replace('"', '""')
        return f'"{escaped}"'

    @staticmethod
    def _quote_literal(value: str) -> str:
        escaped = value.replace("'", "''")
        return f"'{escaped}'"

    # metadata and privilege helpers
    @staticmethod
    def _object_exists(cursor: oracledb.Cursor, query: str, params: Sequence[Any]) -> bool:
        cursor.execute(query, tuple(params))
        return cursor.fetchone() is not None

    @staticmethod
    def _user_has_role(cursor: oracledb.Cursor, username: str, role: str) -> bool:
        return OracleEngine._object_exists(
            cursor, "SELECT 1 FROM dba_role_privs WHERE grantee = :1 AND granted_role = :2", [username, role]
        )

    @staticmethod
    def _user_has_role_with_admin_option(cursor: oracledb.Cursor, username: str, role: str) -> bool:
        return OracleEngine._object_exists(
            cursor,
            "SELECT 1 FROM dba_role_privs WHERE grantee = :1 AND granted_role = :2 AND admin_option = 'YES'",
            [username, role],
        )

    @staticmethod
    def _user_has_sys_priv(cursor: oracledb.Cursor, username: str, privilege: str) -> bool:
        return OracleEngine._object_exists(
            cursor, "SELECT 1 FROM dba_sys_privs WHERE grantee = :1 AND privilege = :2", [username, privilege]
        )

    @staticmethod
    def _role_has_table_priv(cursor: oracledb.Cursor, role: str, owner: str, table_name: str, privilege: str) -> bool:
        # ALL_TAB_PRIVS names the owner column TABLE_SCHEMA; a runtime connection cannot read DBA_TAB_PRIVS.
        scope = OracleEngine._dictionary_scope(cursor)
        owner_column = "owner" if scope == "dba" else "table_schema"
        return OracleEngine._object_exists(
            cursor,
            f"""
            SELECT 1
              FROM {scope}_tab_privs
             WHERE grantee = :grantee
               AND {owner_column} = :owner
               AND table_name = :table_name
               AND privilege = :privilege
            """,
            [role, owner, table_name, privilege],
        )

    @staticmethod
    def _role_exists(cursor: oracledb.Cursor, role: str) -> bool:
        return OracleEngine._object_exists(cursor, "SELECT 1 FROM dba_roles WHERE role = :1", [role])

    @staticmethod
    def _user_exists(cursor: oracledb.Cursor, username: str) -> bool:
        return OracleEngine._object_exists(cursor, "SELECT 1 FROM dba_users WHERE username = :1", [username])

    @staticmethod
    def _get_database_default_tablespace(cursor: oracledb.Cursor) -> str | None:
        cursor.execute(
            "SELECT property_value FROM database_properties WHERE property_name = 'DEFAULT_PERMANENT_TABLESPACE'"
        )
        row = cursor.fetchone()
        if row and row[0]:
            return str(row[0])
        return None

    @staticmethod
    def _get_user_default_tablespace(cursor: oracledb.Cursor, username: str) -> str | None:
        cursor.execute("SELECT default_tablespace FROM dba_users WHERE username = :username", username=username)
        row = cursor.fetchone()
        if row and row[0]:
            return str(row[0])
        return None

    @staticmethod
    def _ensure_unlimited_quota(cursor: oracledb.Cursor, username: str, tablespace: str | None) -> bool:
        if not tablespace:
            return False
        tablespace_name = OracleEngine._format_identifier(tablespace)
        cursor.execute(
            """
            SELECT max_bytes
              FROM dba_ts_quotas
             WHERE username = :username
               AND tablespace_name = :tablespace
            """,
            username=username,
            tablespace=tablespace,
        )
        row = cursor.fetchone()
        if row and row[0] == -1:
            return False
        sql = f"ALTER USER {OracleEngine._format_identifier(username)} QUOTA UNLIMITED ON {tablespace_name}"
        OracleEngine._execute_ddl(cursor.connection, cursor, sql)
        return True

    @staticmethod
    def _table_exists(cursor: oracledb.Cursor, owner: str, table_name: str) -> bool:
        return OracleEngine._object_exists(
            cursor,
            "SELECT 1 FROM dba_tables WHERE owner = :1 AND table_name = :2",
            [owner, table_name],
        )

    @staticmethod
    def _index_exists(cursor: oracledb.Cursor, owner: str, index_name: str) -> bool:
        return OracleEngine._object_exists(
            cursor,
            "SELECT 1 FROM dba_indexes WHERE owner = :1 AND index_name = :2",
            [owner, index_name],
        )

    @staticmethod
    def _constraint_exists(cursor: oracledb.Cursor, owner: str, constraint_name: str) -> bool:
        return OracleEngine._object_exists(
            cursor,
            "SELECT 1 FROM dba_constraints WHERE owner = :1 AND constraint_name = :2",
            [owner, constraint_name],
        )

    @staticmethod
    def _sequence_exists(cursor: oracledb.Cursor, owner: str, sequence_name: str) -> bool:
        return OracleEngine._object_exists(
            cursor,
            "SELECT 1 FROM dba_sequences WHERE sequence_owner = :1 AND sequence_name = :2",
            [owner, sequence_name],
        )

    @staticmethod
    def _register_connection_env(conn: oracledb.Connection, env: str | None) -> None:
        if env:
            OracleEngine._connection_envs[id(conn)] = env
        else:
            OracleEngine._connection_envs.pop(id(conn), None)

    @staticmethod
    def _dictionary_scope(cursor: oracledb.Cursor) -> str:
        """Return the data dictionary view prefix for a cursor: ``dba`` on admin connections, else ``all``.

        ALL_* views are readable by any user, so they are the default when the connection is unknown."""
        connection = getattr(cursor, "connection", None)
        return "dba" if id(connection) in OracleEngine._admin_connections else "all"

    @staticmethod
    def _get_connection_env(conn: oracledb.Connection) -> str | None:
        return OracleEngine._connection_envs.get(id(conn))

    @staticmethod
    def _execute_ddl(conn: oracledb.Connection, cursor: oracledb.Cursor, statement: str) -> None:
        log_statement = OracleEngine._PRIVATE_DDL_MARK.sub("****", statement)
        if len(log_statement) > 100:
            log_statement = log_statement[:100] + " ... [truncated]"
        statement = OracleEngine._PRIVATE_DDL_MARK.sub(lambda m: m.group(1), statement)
        logger.info("Executing %s DDL: %s", OracleEngine._get_connection_env(conn), " ".join(log_statement.split()))
        cursor.execute(statement)

    @staticmethod
    def _db_link_exists(cursor: oracledb.Cursor, name: str) -> bool:
        return OracleEngine._object_exists(cursor, "SELECT 1 FROM user_db_links WHERE db_link = :1", [name])

    @staticmethod
    def _get_primary_key_info(
        cursor: oracledb.Cursor, owner: str, table_name: str
    ) -> tuple[str | None, tuple[str, ...], str | None]:
        scope = OracleEngine._dictionary_scope(cursor)
        cursor.execute(
            f"""
            SELECT constraint_name, index_name
              FROM {scope}_constraints
             WHERE owner = :1
               AND table_name = :2
               AND constraint_type = 'P'
            """,
            [owner, table_name],
        )
        row = cursor.fetchone()
        if not row:
            return None, (), None
        constraint_name, index_name = row
        cursor.execute(
            f"SELECT column_name FROM {scope}_cons_columns WHERE owner = :1 AND constraint_name = :2 ORDER BY position",
            [owner, constraint_name],
        )
        columns = tuple(r[0] for r in cast(Iterable[tuple[str]], cursor))
        return constraint_name, columns, index_name

    @staticmethod
    def _column_needs_update(existing: ColumnDefinition, desired: ColumnDefinition) -> bool:
        desired_type = desired.data_type.lower()
        existing_type = existing.data_type.lower()
        if existing_type != desired_type:
            return True
        if desired_type in ("varchar2", "varchar", "char"):
            desired_length = desired.length or 0
            existing_length = existing.length or 0
            if desired_length and desired_length > existing_length:
                return True
        if desired_type in ("number", "numeric", "decimal"):
            desired_precision = desired.precision
            desired_scale = desired.scale or 0
            existing_precision = existing.precision
            existing_scale = existing.scale or 0
            if desired_precision is not None and existing_precision is not None:
                if existing_precision < desired_precision:
                    return True
            if desired_precision is not None and existing_precision is None:
                # Existing column allows maximum precision; no change required.
                pass
            if desired_scale > existing_scale:
                return True
        if desired_type in ("integer", "int"):
            desired_precision = desired.precision or 10
            existing_precision = existing.precision or 0
            if desired_precision > existing_precision:
                return True
        return False

    @staticmethod
    def _get_table_indexes(
        cursor: oracledb.Cursor, owner: str, table_name: str
    ) -> dict[str, tuple[tuple[str, ...], bool]]:
        scope = OracleEngine._dictionary_scope(cursor)
        cursor.execute(
            f"""
            SELECT i.index_name, i.uniqueness, c.column_name
              FROM {scope}_indexes i JOIN {scope}_ind_columns c ON i.owner = c.index_owner AND i.index_name = c.index_name WHERE i.owner = :owner
               AND i.table_name = :table_name
             ORDER BY i.index_name, c.column_position
            """,
            owner=owner,
            table_name=table_name,
        )
        indexes: dict[str, dict[str, Any]] = {}
        for index_name, uniqueness, column_name in cast(Iterable[tuple[str, str, str]], cursor):
            index_info = indexes.setdefault(index_name, {"columns": [], "unique": uniqueness == "UNIQUE"})
            index_info["columns"].append(column_name)
        return {name: (tuple(info["columns"]), bool(info["unique"])) for name, info in indexes.items()}

    @staticmethod
    def _determine_process_date_column(table: TableDefinition) -> str | None:
        available_columns = {column.name for column in table.columns}
        for candidate in ("LDB_PROCESS_DATE", "LDB_PROCESS_DATE"):
            if candidate in available_columns:
                return candidate
        return None

    @staticmethod
    def _prepare_desired_indexes(
        table: TableDefinition,
    ) -> dict[str, tuple[tuple[str, ...], bool]]:
        desired_pk = tuple(table.primary_key or ())
        process_date_column = OracleEngine._determine_process_date_column(table)
        desired_indexes: dict[str, tuple[tuple[str, ...], bool]] = {}
        for index in table.indexes:
            columns = tuple(index.columns)
            if process_date_column and process_date_column not in columns and (not desired_pk or columns != desired_pk):
                columns = (*columns, process_date_column)
            desired_indexes[index.name] = (columns, index.unique)
        return desired_indexes

    @staticmethod
    def _choose_best_index_for_primary_key(
        indexes: IndexMap, column_metadata: ColumnMetadata
    ) -> tuple[str, ...] | None:
        candidates: list[dict[str, Any]] = []
        for index_name, info in indexes.items():
            columns: Sequence[str] = info.get("columns", [])
            if not columns:
                continue
            column_defs: list[ColumnDefinition] = []
            for col in columns:
                column_def = column_metadata.get(col)
                if column_def is None:
                    column_defs = []
                    break
                column_defs.append(column_def)
            if not column_defs:
                continue
            candidates.append(
                {
                    "name": index_name,
                    "columns": tuple(col.name for col in column_defs),
                    "unique": bool(info.get("unique")),
                    "distinct_keys": info.get("distinct_keys"),
                    "all_not_null": all(not col.nullable for col in column_defs),
                }
            )
        if not candidates:
            return None

        def score(value: Any) -> int:
            if value is None:
                return -1
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value)
            try:
                return int(value)
            except (TypeError, ValueError):
                return -1

        def choose(candidate_list: list[dict[str, Any]]) -> tuple[str, ...] | None:
            if not candidate_list:
                return None
            ordered = sorted(
                candidate_list,
                key=lambda item: (
                    -score(item.get("distinct_keys")),
                    len(item["columns"]),
                    item["name"],
                ),
            )
            best = ordered[0]
            return tuple(best["columns"])

        not_null_unique = [c for c in candidates if c["unique"] and c["all_not_null"]]
        choice = choose(not_null_unique)
        if choice:
            return choice
        unique_candidates = [c for c in candidates if c["unique"]]
        choice = choose(unique_candidates)
        if choice:
            return choice
        non_unique = [c for c in candidates if not c["unique"]]
        return choose(non_unique)

    @staticmethod
    def _drop_constraints_by_type(
        cursor: oracledb.Cursor, owner: str, table_name: str, constraint_types: Sequence[str]
    ) -> list[str]:
        dropped: list[str] = []
        if not constraint_types:
            return dropped
        type_list = ", ".join(f"'{constraint_type}'" for constraint_type in constraint_types)
        cursor.execute(
            f"""
            SELECT constraint_name
              FROM dba_constraints
             WHERE owner = :owner
               AND table_name = :table_name
               AND constraint_type IN ({type_list})
            """,
            owner=owner,
            table_name=table_name,
        )
        fmttd_owner = OracleEngine._format_identifier(owner)
        fmttd_table_name = OracleEngine._format_identifier(table_name)
        for (constraint_name,) in cast(Iterable[tuple[str]], cursor):
            fmttd_constraint_name = OracleEngine._format_identifier(constraint_name)
            sql = f"ALTER TABLE {fmttd_owner}.{fmttd_table_name} DROP CONSTRAINT {fmttd_constraint_name}"
            OracleEngine._execute_ddl(cursor.connection, cursor, sql)
            dropped.append(fmttd_constraint_name)
        return dropped
