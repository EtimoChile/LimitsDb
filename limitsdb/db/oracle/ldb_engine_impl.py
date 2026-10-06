from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Literal, Set, cast
import re
import oracledb
from limitsdb.core.ldb_params_config import Config
from limitsdb.core.ldb_status import Status
from limitsdb.core.ldb_utils import get_effective_credentials, indent_lines, join_wrapped, nvl
from limitsdb.db.ldb_engines import ColumnDefinition, ColumnMetadata, DatabaseEngine, DatabaseLinkDefinition, IndexMap, RoleDefinition, SequenceDefinition, TableDefinition, UserDefinition
from limitsdb.core.ldb_logger import get_logger

SUPPORTING_OBJECT_LIST = ["TYPE t_referencing_tables", "PROCEDURE check_save_status", "PROCEDURE check_referencing_tables"]
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
    p_sqlcode       IN OUT NUMBER,
    p_out_message   OUT VARCHAR2
)
IS
    l_status         VARCHAR2(10);
    l_process_start  DATE;
    l_process_date   DATE;
    l_rowid          ROWID;
    in_use           EXCEPTION;
    PRAGMA EXCEPTION_INIT(in_use, -54);
BEGIN
    IF NVL(p_sqlcode, 0) NOT IN (-20001, -20002, -20003) THEN
        BEGIN
            SELECT ctl_status, ctl_process_date, ctl_process_start, ROWID
              INTO l_status, l_process_date, l_process_start, l_rowid
              FROM ldb_ctl
             WHERE ctl_owner = p_owner
               AND ctl_table_name = p_table_name
             FOR UPDATE NOWAIT;

            IF p_status = 'TSTART'
               AND l_status != 'TEND'
               AND (SYSDATE - l_process_start) * 3600 * 24 < 5 THEN
                p_sqlcode := -20002;
                p_out_message := 'Table ' || p_table_name || ' is currently in process without lock';
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

            UPDATE ldb_ctl
               SET ctl_process_date   = p_process_date,
                   ctl_action         = p_action,
                   ctl_status         = p_status,
                   ctl_process_start  = NVL(p_process_start, ctl_process_start),
                   ctl_process_end    = p_process_end,
                   ctl_rows_processed = DECODE(p_status, 'TSTART', 0, ctl_rows_processed) +
                                         p_rows_processed,
                   ctl_plsql          = NVL(p_plsql, ctl_plsql)
             WHERE ROWID = l_rowid;
        EXCEPTION
            WHEN in_use THEN
                p_sqlcode := -20001;
                p_out_message := 'Table ' || p_table_name || ' is currently in process';
                RETURN;
            WHEN NO_DATA_FOUND THEN
                INSERT INTO ldb_ctl (
                    ctl_owner,
                    ctl_table_name,
                    ctl_process_date,
                    ctl_action,
                    ctl_status,
                    ctl_process_start,
                    ctl_process_end,
                    ctl_rows_processed,
                    ctl_plsql
                )
                VALUES (
                    p_owner,
                    p_table_name,
                    p_process_date,
                    p_action,
                    p_status,
                    l_process_start,
                    NULL,
                    0,
                    p_plsql
                );
        END;
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
    p_process_date       IN DATE
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
               AND ctl_status = 'TEND';
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
    _connection_envs: Dict[int, str] = {}
    REQUIRED_SYSTEM_PRIVILEGES: Tuple[str, ...] = (
        "CREATE SESSION", "ALTER SESSION", "CREATE TABLE", "CREATE PROCEDURE", "CREATE TYPE", "CREATE DATABASE LINK", "CREATE SEQUENCE", "RESUMABLE",
        "ALTER USER", "CREATE SYNONYM", "CREATE VIEW", "CREATE ROLE", "CREATE TRIGGER", "CREATE MATERIALIZED VIEW", "QUERY REWRITE"
    )
    _REGULAR_IDENTIFIER = re.compile(r'[A-Z_#$][A-Z0-9_#$]*')  #only DD uppercase identifiers are unquoted
    _PREFIXED_IDENTIFIER = re.compile(r'@("(?:""|[^"])*"|[A-Za-z_#$][A-Za-z0-9_#$]*)')
    _PRIVATE_DDL_MARK = re.compile(r'##(.*?)##')

    # public API
    @staticmethod
    def get_connection(config: Config, *, admin: bool = False, env: Optional[Literal["SOURCE", "HISTORY"]] = None) -> oracledb.Connection:
        """Returns an Oracle connection using provided config.
        Args:
            config: Database config object.
            admin: Whether to use admin credentials.
        Returns:
            An active oracledb.Connection."""
        try:
            user, password, dsn = get_effective_credentials(config, admin=admin, env=env)
            connection = oracledb.connect(user=user, password=password, dsn=dsn)  # type: ignore
            OracleEngine._register_connection_env(connection, env)
            return connection
        except Exception:
            logger.critical("Failed to connect to Oracle DB.", exc_info=True)
            raise

    @staticmethod
    def close_connection(conn: oracledb.Connection) -> None:
        """Closes the Oracle connection.
        Args:
            conn: Active Oracle connection."""
        try:
            if conn:
                OracleEngine._register_connection_env(conn, None)
                conn.close()
        except Exception:
            logger.critical("Failed to close Oracle DB connection.", exc_info=True)

    @staticmethod
    def load_config(conn: oracledb.Connection) -> List[Dict[str, Any]]:
        """Loads configuration from ldb_conf.
        Args:
            conn: Active Oracle connection.
        Returns:
            List of configuration rows."""
        cursor: Optional[oracledb.Cursor] = None
        try:
            cursor = conn.cursor()
            cursor.execute( # type: ignore
                """SELECT cnf_id, cnf_source_owner, cnf_history_owner, cnf_table_name, cnf_retain_months_source,
                       cnf_retain_months_history, cnf_exec_day, cnf_frecuency, cnf_is_active, cnf_purge_date_expr,
                       cnf_additional_filter_expr, cnf_history_addtl_filter_expr, cnf_source_orphan_purge, cnf_orphan_check_column, cnf_has_lob_columns,
                       cnf_referencing_tables, cnf_join_expr, cnf_hint_expr, cnf_history_hint_expr, cnf_long_columns, null ctl_status
                FROM ldb_conf
                WHERE cnf_is_active = 'Y'""")
            cols = [cast(str, col[0]).lower().removeprefix("cnf_") for col in cursor.description]  # type: ignore
            return [dict(zip(cols, row)) for row in cursor.fetchall()]  # type: ignore
        except Exception:
            logger.critical("Failed to load configuration from Oracle.", exc_info=True)
            raise
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def get_table_columns(conn: oracledb.Connection, owner: str, table_name: str) -> Tuple[List[str], Dict[str, ColumnDefinition]]:
        """Retrieves column names and metadata for a given table.
        Args:
            conn: Active Oracle connection.
            owner: Schema owner of the table.
            table_name: Table name.
        Returns:
            Tuple containing a list of column names and a dict of column metadata."""
        cursor: Optional[oracledb.Cursor] = None
        columns: List[str] = []
        metadata: Dict[str, ColumnDefinition] = {}
        try:
            cursor = conn.cursor()
            cursor.execute( # type: ignore
                """select column_id, column_name, data_type, data_length, data_precision, data_scale, nullable, data_default, char_length, char_used
                from all_tab_columns
                where  owner = :1 and table_name = :2 and column_id is not null
                order by column_id""", [owner, table_name])
            for row in cast(Iterable[Tuple[int, str, str, Optional[int], Optional[int], Optional[int], str, Optional[str], Optional[str], str]],
                            cursor):
                column_id, column_name, data_type, data_length, data_precision, data_scale, nullable, data_default, char_length, char_used = row
                dtype = data_type.lower()
                length: Optional[int] = None
                if dtype in ("varchar2", "varchar", "char"):
                    if char_used == "C" and char_length is not None:
                        length = int(char_length)
                    elif data_length is not None:
                        length = int(data_length)
                precision = int(data_precision) if data_precision is not None else None
                scale = int(data_scale) if data_scale is not None else None
                metadata[column_name] = ColumnDefinition(
                    name=column_name, data_type=dtype, id=column_id, length=length, precision=precision, scale=scale, nullable=(nullable == "Y"),
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
    def get_identifiers_from_expression(expression: str) -> Set[str]:
        """ Returns a set of @prefixxed identifiers found in the given expression.
        Args:
            expression: The expression string to process.
        Returns:
            A set of identifier strings found in the expression."""
        identifiers: Set[str] = set()
        for match in OracleEngine._PREFIXED_IDENTIFIER.finditer(expression):
            ident = match.group(1)
            if ident.startswith('"') and ident.endswith('"'):
                ident = ident[1:-1].replace('""', '"')
            else:
                ident = ident.upper()
            identifiers.add(ident)
        return identifiers

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
    def generate_sql_block(config: Config, table_cnf: Dict[str, Any], process_date: str) -> str:
        """Generates a PL/SQL block for table processing with optional chunking and LOB handling.
        Args:
            config: Configuration object.
            table_cnf: Processed table configuration object.
            process_date: Process date in 'YYYYMMDD' format.
        Returns:
            PL/SQL block as string."""
        cnd0 = table_cnf["conds"][0]
        source_owner, history_owner, table_name = cnd0["source_owner"], cnd0["history_owner"], cnd0["table_name"]
        history_hint_expr = cnd0.get("history_hint_expr")
        hint_expr = cnd0["hint_expr"]
        if config.action == "HISTORY_ILM":
            hint_expr = nvl(history_hint_expr, hint_expr)
        has_lob_columns = cnd0["has_lob_columns"] == 'Y'
        other_columns, referencing_tables = table_cnf["other_columns"], table_cnf["referencing_tables"]
        other_cols_alias = [col["name"] for col in other_columns]
        other_cols_exprs = [col["expr"] for col in other_columns]
        query_expr, table_columns, months_keep_history_max = table_cnf["query_expr"], table_cnf["table_columns"], table_cnf["months_keep_history_max"]
        referencing_tables = ", ".join([f"'{rt[0]}.{rt[1]}'" for rt in referencing_tables])
        source_ilm = config.action == "SOURCE_ILM"
        if config.add_ldb_columns:
            gend_cols = ["LDB_PROCESS_DATE", "LDB_INSERT_DATE"]
            gend_vals = ["l_process_date", "sysdate"]
        else:
            gend_cols = gend_vals = []
        ins_cols = join_wrapped(", ", table_columns + other_cols_alias + gend_cols, 200)
        ins_vals = join_wrapped(", ", [f"r_rec(i).{col}" for col in table_columns + other_cols_alias] + gend_vals, 200)
        if config.generate_script:
            process_date = "&process_date"
        plsql = f"""declare
    l_source_owner varchar2(50) := '{source_owner}';
    l_history_owner varchar2(50) := '{history_owner}';
    l_action varchar2(10) := '{config.action}';
    l_mode varchar2(10) := '{config.mode}';
    l_message varchar2(200) := case
        when l_mode = 'EXECUTE' then null
        when l_mode = 'SCRIPT' then 'SCRIPT'
        when l_mode = 'PREVIEW' then 'PREVIEW'
        else l_mode
    end;
    l_table_name varchar2(50) := '{table_name}';
    l_process_date date := to_date('{process_date}', 'YYYYMMDD');
    l_referencing_tables t_referencing_tables := t_referencing_tables({referencing_tables});
    l_process_start date;
    l_record_count pls_integer := 0;
    l_plsql clob := {'null' if config.generate_script else ':plsql_code'};
    l_sqlcode number := null;
    l_out_message varchar2(200) := null;"""
        if not has_lob_columns:
            cols_expr = ", ".join(["A.*"] + other_cols_exprs)
            plsql += f"""
    l_chunk_size pls_integer := {config.chunk_size}; l_chunk_start date;
    cursor c_records is
        select /*+ {hint_expr} */ A.rowid{", " + cols_expr if source_ilm else ""}
        {indent_lines(query_expr, 8)};
    type t_records is table of c_records%rowtype index by pls_integer; r_rec t_records;"""
        plsql += f"""
begin
    l_process_start := sysdate;
    check_save_status(l_source_owner, l_table_name, l_process_date, l_action, '{Status.TABLE_START}', l_process_start, null, null, l_message, 0, l_plsql, l_sqlcode, l_out_message);
    if l_sqlcode is not null then raise_application_error(l_sqlcode, l_out_message); end if;"""
        if source_ilm or not config.use_added_columns:
            plsql += """
    check_referencing_tables(l_referencing_tables, l_process_date); commit;"""
        if not has_lob_columns or not source_ilm:
            plsql += f"""
    open c_records;
    loop
        l_chunk_start := sysdate;
        check_save_status(l_source_owner, l_table_name, l_process_date, l_action, '{Status.CHUNK_START}', null, l_chunk_start, null, l_message, 0, null, l_sqlcode, l_out_message);
        fetch c_records bulk collect into r_rec limit l_chunk_size;
        if r_rec.count <= 0 then
            exit;
        end if;"""
            if (config.mode in ("EXECUTE", "SCRIPT")):
                if source_ilm and nvl(months_keep_history_max, 1) > 0:
                    plsql += f"""
        for i in 1 .. r_rec.count loop
            insert into {OracleEngine._format_identifier(history_owner)}.{OracleEngine._format_identifier(table_name)}@{OracleEngine._format_identifier(config.source_to_history_dblink_name)}
            ({indent_lines(ins_cols, 12)})
            values ({indent_lines(ins_vals, 12)});
        end loop;"""
                plsql += f"""
        forall i in 1 .. r_rec.count
            delete from {OracleEngine._format_identifier(source_owner)}.{OracleEngine._format_identifier(table_name)} where rowid = r_rec(i).rowid;
        l_record_count := l_record_count + r_rec.count;"""
            plsql += f"""
        check_save_status(l_source_owner, l_table_name, l_process_date, l_action, '{Status.CHUNK_END}', null, l_chunk_start, sysdate, l_message, r_rec.count, null, l_sqlcode, l_out_message);
        commit;
    end loop;
    close c_records;"""
        else:
            cols_select = ", ".join([f"a.{col}" for col in table_columns] + other_cols_exprs + gend_vals)
            if config.mode in ("EXECUTE", "SCRIPT"):
                if source_ilm and nvl(months_keep_history_max, 0) > 0:
                    plsql += f"""
    insert into {OracleEngine._format_identifier(history_owner)}.{OracleEngine._format_identifier(table_name)}@{OracleEngine._format_identifier(config.source_to_history_dblink_name)}({indent_lines(ins_cols, 4)})
    select /*+ {hint_expr} */ {indent_lines(cols_select, 4)}
    {indent_lines(query_expr, 4)};"""
                plsql += f"""
    delete from {OracleEngine._format_identifier(source_owner)}.{OracleEngine._format_identifier(table_name)} where rowid in
    (select /*+ {hint_expr} */ a.rowid
    {indent_lines(query_expr, 4)});
    l_record_count := sql%rowcount;"""
            else:
                plsql += f"""
    select /*+ {hint_expr} */ count(*) into l_record_count
    {indent_lines(query_expr, 4)};"""
        plsql += f"""
    check_save_status(l_source_owner, l_table_name, l_process_date, l_action, '{Status.TABLE_END}', l_process_start, null, sysdate, l_message, l_record_count, null, l_sqlcode, l_out_message);
    commit;
exception
    when others then
        rollback;
        check_save_status(l_source_owner, l_table_name, l_process_date, l_action, '{Status.ERROR}', l_process_start, null, sysdate, sqlerrm, l_record_count, null, l_sqlcode, l_out_message);
        commit;
        if l_sqlcode not in (-20003) then
            raise;
        end if;
end;"""
        return plsql

    @staticmethod
    def get_system_date(conn: oracledb.Connection) -> datetime:
        """Returns the current system date from Oracle.
        Args:
            conn: Active Oracle connection.
        Returns:
            Current system date as Python date."""
        cursor: Optional[oracledb.Cursor] = None
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT SYSDATE FROM dual")  # type: ignore
            sysdate: datetime = cursor.fetchone()[0]
            return sysdate
        except Exception:
            logger.critical("Failed to retrieve system date from Oracle.", exc_info=True)
            raise
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def sql_block_run(conn: oracledb.Connection, plsql_code: str) -> None:
        """Executes a PL/SQL block on the database.
        Args:
            conn: Active Oracle connection.
            plsql_code: The PL/SQL block to execute."""
        cursor: Optional[oracledb.Cursor] = None
        try:
            cursor = conn.cursor()
            cursor.setinputsizes(plsql_code=oracledb.CLOB)  # type: ignore
            cursor.execute(plsql_code, {"plsql_code": plsql_code})  # type: ignore
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def get_status(conn: oracledb.Connection, process_date: str) -> List[Dict[str, Any]]:
        """Loads status from ldb_ctl for the given process date.
        Args:
            conn: Active Oracle connection.
            process_date: Date in 'YYYYMMDD' format.
        Returns:
            List of status rows."""
        cursor: Optional[oracledb.Cursor] = None
        try:
            cursor = conn.cursor()
            cursor.execute( # type: ignore
                """SELECT ctl_owner, ctl_table_name, ctl_status
                FROM ldb_ctl
                WHERE ctl_process_date = TO_DATE(:1, 'YYYYMMDD')""", [process_date])
            cols = [col[0].lower() for col in cursor.description]  # type: ignore
            return [dict(zip(cols, row)) for row in cursor.fetchall()]  # type: ignore
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
        cursor: Optional[oracledb.Cursor] = None
        try:
            cursor = conn.cursor()
            cursor.execute( # type: ignore
                """SELECT ctl_rows_processed
                FROM ldb_ctl WHERE ctl_owner = :1 AND ctl_table_name = :2 AND ctl_process_date = TO_DATE(:3, 'YYYYMMDD')""",
                [owner, table_name, process_date])
            result = cursor.fetchone()
            return result[0] if result else 0
        except Exception:
            logger.critical(f"Unexpected error in get_rows_processed for {owner}.{table_name}:", exc_info=True)
            return 0
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def save_error_status(
        conn: oracledb.Connection, config: Config, owner: str, table_name: str, process_date: str, process_start: datetime, message: str,
        plsql_code: str
    ) -> None:
        cursor: Optional[oracledb.Cursor] = None
        try:
            cursor = conn.cursor()
            process_end = OracleEngine.get_system_date(conn)
            sqlcode = cursor.var(oracledb.NUMBER)  # type: ignore
            out_message = cursor.var(oracledb.STRING)  # type: ignore
            cursor.callproc( # type: ignore
                'check_save_status',
                [
                    owner,
                    table_name,
                    datetime.strptime(process_date, "%Y%m%d").date(),
                    config.action,
                    Status.ERROR,
                    process_start,
                    None,
                    process_end,
                    message,
                    0,
                    plsql_code,
                    sqlcode,
                    out_message
                ]
            )
            conn.commit()
        except Exception:
            logger.critical(f"Unexpected error in process_table for {owner}.{table_name}:", exc_info=True)
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def all_status_tend(conn: oracledb.Connection, tables_config: Dict[Tuple[str, str], Any], process_date: str) -> bool:
        """Checks if all referenced tables have status "Status.TABLE_END" in ldb_ctl.
        Args:
            conn: Active Oracle connection.
            tables_config: Dict of (owner, table_name) keys representing configured tables.
            process_date: Processing date in 'YYYYMMDD' format.
        Returns:
            True if all tables have status "Status.TABLE_END" for the given process date, False otherwise."""
        cursor: Optional[oracledb.Cursor] = None
        try:
            cursor = conn.cursor()
            for (owner, table_name), table_cnf in tables_config.items():
                if table_cnf["skip"]:
                    continue
                cursor.execute( # type: ignore
                    f"""SELECT ctl_status FROM ldb_ctl
                    WHERE ctl_owner = :1 AND ctl_table_name = :2
                    AND ctl_process_date = TO_DATE(:3, 'YYYYMMDD') AND ctl_status = '{Status.TABLE_END}'""",
                    [owner, table_name, process_date])
                if not cursor.fetchone():
                    return False
            return True
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def ensure_users(conn: oracledb.Connection, users: Sequence[UserDefinition]) -> List[str]:
        created: List[str] = []
        if not users:
            return created
        cursor: Optional[oracledb.Cursor] = None
        changed = False
        try:
            cursor = conn.cursor()
            database_default_tablespace = OracleEngine._get_database_default_tablespace(cursor)
            for user in users:
                fmttd_username = OracleEngine._format_identifier(user.name)
                fmttd_default_tablespace = OracleEngine._format_identifier(user.default_tablespace)
                tablespace_for_quota = user.default_tablespace
                if not OracleEngine._user_exists(cursor, user.name):
                    sql = f"CREATE USER {fmttd_username} IDENTIFIED BY ##{OracleEngine._quote(user.password)}##"
                    if user.default_tablespace:
                        sql += f" DEFAULT TABLESPACE {fmttd_default_tablespace}"
                    if user.temporary_tablespace:
                        sql += f" TEMPORARY TABLESPACE {OracleEngine._format_identifier(user.temporary_tablespace)}"
                    logger.debug(f"Creating user with SQL: {sql}")
                    OracleEngine._execute_ddl(conn, cursor, sql)
                    created.append(fmttd_username)
                    changed = True
                    if not user.default_tablespace:
                        tablespace_for_quota = OracleEngine._get_user_default_tablespace(cursor, user.name) or database_default_tablespace
                else:
                    tablespace_for_quota = OracleEngine._get_user_default_tablespace(cursor, user.name) or database_default_tablespace
                admin_option_roles = set(OracleEngine._format_identifier(r) for r in user.roles_with_admin_option)
                for role in user.roles:
                    fmttd_role_name = OracleEngine._format_identifier(role)
                    requires_admin_option = role in admin_option_roles
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
        except Exception:
            conn.rollback()
            logger.critical("Failed to ensure users.", exc_info=True)
            raise
        finally:
            if cursor:
                cursor.close()
        return created

    @staticmethod
    def ensure_tables(conn: oracledb.Connection, tables: Sequence[TableDefinition]) -> List[str]:
        created: List[str] = []
        if not tables:
            return created
        cursor: Optional[oracledb.Cursor] = None
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
        except Exception:
            conn.rollback()
            logger.critical("Failed to ensure tables.", exc_info=True)
            raise
        finally:
            if cursor:
                cursor.close()
        return created

    @staticmethod
    def ensure_table_structure(conn: oracledb.Connection, table: TableDefinition, table_cnf: Dict[str, Any]) -> None:
        cursor: Optional[oracledb.Cursor] = None
        fmttd_owner = OracleEngine._format_identifier(table.owner)
        fmttd_table_name = OracleEngine._format_identifier(table.name)
        logger.debug(f"Ensuring structure for table {table.owner}.{table.name} conn: {conn}")
        try:
            cursor = conn.cursor()
            if not OracleEngine._table_exists(cursor, table.owner, table.name):
                fmttd_columns_sql = ",\n        ".join(OracleEngine._column_sql(col) for col in table.columns)
                sql = f"CREATE TABLE {fmttd_owner}.{fmttd_table_name} (\n        {fmttd_columns_sql}\n    )"
                OracleEngine._execute_ddl(conn, cursor, sql)
            else:
                _, existing_columns = OracleEngine.get_table_columns(conn, table.owner, table.name)
                for column in table.columns:
                    column_definition = OracleEngine._column_sql(column)
                    if column.name not in existing_columns:
                        sql = f"ALTER TABLE {fmttd_owner}.{fmttd_table_name} ADD ({column_definition})"
                        OracleEngine._execute_ddl(conn, cursor, sql)
                    else:
                        existing = existing_columns[column.name]
                        if OracleEngine._column_needs_update(existing, column):
                            sql = f"ALTER TABLE {fmttd_owner}.{fmttd_table_name} MODIFY ({column_definition})"
                            OracleEngine._execute_ddl(conn, cursor, sql)
            existing_indexes = OracleEngine._get_table_indexes(cursor, fmttd_owner, fmttd_table_name)
            desired_pk = tuple((table.primary_key or ()))
            fmttd_desired_pk = tuple(OracleEngine._format_identifier(col) for col in desired_pk)
            existing_pk_name, existing_pk_cols, existing_pk_index = OracleEngine._get_primary_key_info(cursor, table.owner, table.name)
            fmttd_existing_pk_cols = tuple(OracleEngine._format_identifier(col) for col in existing_pk_cols)
            fmttd_existing_pk_index = (OracleEngine._format_identifier(existing_pk_index) if existing_pk_index else None)
            desired_constraint_name = f"{table.name}_PK"
            fmttd_desired_constraint_name = OracleEngine._format_identifier(desired_constraint_name)
            if desired_pk:
                if not existing_pk_cols:
                    existing_index_info = existing_indexes.get(desired_constraint_name)
                    if existing_index_info and (existing_index_info[0] != desired_pk or not existing_index_info[1]):
                        drop_sql = f"DROP INDEX {fmttd_owner}.{fmttd_desired_constraint_name}"
                        OracleEngine._execute_ddl(conn, cursor, drop_sql)
                        existing_indexes.pop(desired_constraint_name, None)
                    add_pk_sql = f"ALTER TABLE {fmttd_owner}.{fmttd_table_name} ADD CONSTRAINT {fmttd_desired_constraint_name} PRIMARY KEY ({', '.join(fmttd_desired_pk)})"
                    OracleEngine._execute_ddl(conn, cursor, add_pk_sql)
                elif existing_pk_cols != desired_pk:
                    if existing_pk_name:
                        drop_pk_sql = f"ALTER TABLE {fmttd_owner}.{fmttd_table_name} DROP CONSTRAINT {OracleEngine._format_identifier(existing_pk_name)}"
                        OracleEngine._execute_ddl(conn, cursor, drop_pk_sql)
                        index_to_drop = fmttd_existing_pk_index or existing_pk_name
                        if index_to_drop:
                            existing_indexes = OracleEngine._get_table_indexes(cursor, table.owner, table.name)
                        if index_to_drop and index_to_drop in existing_indexes:
                            drop_index_sql = f"DROP INDEX {fmttd_owner}.{index_to_drop}"
                            OracleEngine._execute_ddl(conn, cursor, drop_index_sql)
                            existing_indexes.pop(index_to_drop, None)
                    existing_index_info = existing_indexes.get(desired_constraint_name)
                    if existing_index_info and (existing_index_info[0] != desired_pk or not existing_index_info[1]):
                        drop_conflict_sql = f"DROP INDEX {fmttd_owner}.{fmttd_desired_constraint_name}"
                        OracleEngine._execute_ddl(conn, cursor, drop_conflict_sql)
                        existing_indexes.pop(desired_constraint_name, None)
                    recreate_pk_sql = f"ALTER TABLE {fmttd_owner}.{fmttd_table_name} ADD CONSTRAINT {fmttd_desired_constraint_name} PRIMARY KEY ({', '.join(fmttd_desired_pk)})"
                    OracleEngine._execute_ddl(conn, cursor, recreate_pk_sql)
            elif fmttd_existing_pk_cols and existing_pk_name:
                drop_unexpected_pk_sql = f"ALTER TABLE {fmttd_owner}.{fmttd_table_name} DROP CONSTRAINT {existing_pk_name}"
                OracleEngine._execute_ddl(conn, cursor, drop_unexpected_pk_sql)
                index_to_drop = fmttd_existing_pk_index or existing_pk_name
                if index_to_drop:
                    existing_indexes = OracleEngine._get_table_indexes(cursor, table.owner, table.name)
                if index_to_drop and index_to_drop in existing_indexes:
                    drop_unexpected_index_sql = f"DROP INDEX {fmttd_owner}.{index_to_drop}"
                    OracleEngine._execute_ddl(conn, cursor, drop_unexpected_index_sql)
                    existing_indexes.pop(index_to_drop, None)
            existing_indexes = OracleEngine._get_table_indexes(cursor, table.owner, table.name)
            desired_indexes = OracleEngine._prepare_desired_indexes(table)
            for index_name, (columns, is_unique) in desired_indexes.items():
                fmttd_index_name = OracleEngine._format_identifier(index_name)
                fmttd_columns = tuple(OracleEngine._format_identifier(col) for col in columns)
                existing_index = existing_indexes.get(index_name)
                if existing_index == (columns, is_unique):
                    continue
                if existing_index is not None:
                    drop_mismatch_index_sql = f"DROP INDEX {fmttd_owner}.{fmttd_index_name}"
                    OracleEngine._execute_ddl(conn, cursor, drop_mismatch_index_sql)
                fmttd_columns_sql = ", ".join(fmttd_columns)
                unique_clause = "UNIQUE " if is_unique else ""
                create_index_sql = f"CREATE {unique_clause}INDEX {fmttd_owner}.{index_name} ON {fmttd_owner}.{fmttd_table_name} ({fmttd_columns_sql})"
                OracleEngine._execute_ddl(conn, cursor, create_index_sql)
            final_pk_name, _, final_pk_index = OracleEngine._get_primary_key_info(cursor, fmttd_owner, fmttd_table_name)
            expected_indexes = set(desired_indexes.keys())
            if final_pk_name:
                expected_indexes.add(final_pk_name)
            if final_pk_index:
                expected_indexes.add(final_pk_index)
            existing_indexes = OracleEngine._get_table_indexes(cursor, fmttd_owner, fmttd_table_name)
            for index_name in list(existing_indexes.keys()):
                if index_name not in expected_indexes:
                    fmttd_index_name = OracleEngine._format_identifier(index_name)
                    logger.info("Dropping unmanaged index %s on %s.%s", fmttd_index_name, fmttd_owner, fmttd_table_name)
                    drop_unmanaged_index_sql = f"DROP INDEX {fmttd_owner}.{fmttd_index_name}"
                    OracleEngine._execute_ddl(conn, cursor, drop_unmanaged_index_sql)
        except Exception:
            conn.rollback()
            logger.critical("Failed to ensure table structure.", exc_info=True)
            raise
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def get_primary_key_columns(conn: oracledb.Connection, owner: str, table_name: str, table_cnf: Dict[str, Any]) -> Tuple[str, ...]:
        cursor: Optional[oracledb.Cursor] = None
        try:
            cursor = conn.cursor()
            _, columns, _ = OracleEngine._get_primary_key_info(cursor, owner, table_name)
            if columns:
                return tuple(col for col in columns)
            cursor.execute( # type: ignore
                """
                SELECT i.index_name, i.uniqueness, c.column_name, s.distinct_keys
                FROM dba_indexes i JOIN dba_ind_columns c ON i.owner = c.index_owner AND i.index_name = c.index_name
                LEFT JOIN dba_ind_statistics s ON s.owner = i.owner AND s.index_name = i.index_name AND s.partition_name IS NULL
                WHERE i.owner = :owner
                AND i.table_name = :table_name
                ORDER BY i.index_name, c.column_position
                """,
                owner=owner,
                table_name=table_name,
            )
            indexes: Dict[str, Dict[str, Any]] = {}
            for index_name, uniqueness, column_name, distinct_keys in cast(Iterable[Tuple[str, str, str, Optional[int]]], cursor):
                index_info = indexes.setdefault(index_name, {"columns": [], "unique": uniqueness == "UNIQUE", "distinct_keys": None, })
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
    def ensure_roles(conn: oracledb.Connection, roles: Sequence[RoleDefinition]) -> List[str]:
        created: List[str] = []
        if not roles:
            return created
        cursor: Optional[oracledb.Cursor] = None
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
        except Exception:
            conn.rollback()
            logger.critical("Failed to ensure roles.", exc_info=True)
            raise
        finally:
            if cursor:
                cursor.close()
        return created

    @staticmethod
    def ensure_sequences(conn: oracledb.Connection, sequences: Sequence[SequenceDefinition]) -> List[str]:
        created: List[str] = []
        if not sequences:
            return created
        cursor: Optional[oracledb.Cursor] = None
        changed = False
        try:
            cursor = conn.cursor()
            for sequence in sequences:
                fmttd_owner = OracleEngine._format_identifier(sequence.owner)
                fmttd_sequence_name = OracleEngine._format_identifier(sequence.name)
                if OracleEngine._sequence_exists(cursor, sequence.owner, sequence.name):
                    continue
                sql = (
                    f"CREATE SEQUENCE {fmttd_owner}.{fmttd_sequence_name} "
                    f"START WITH {sequence.start_with} INCREMENT BY {sequence.increment_by}"
                )
                if sequence.minvalue is not None:
                    sql += f" MINVALUE {sequence.minvalue}"
                else:
                    sql += " NOMINVALUE"
                if sequence.maxvalue is not None:
                    sql += f" MAXVALUE {sequence.maxvalue}"
                else:
                    sql += " NOMAXVALUE"
                sql += " CYCLE" if sequence.cycle else " NOCYCLE"
                if sequence.cache is not None:
                    sql += f" CACHE {sequence.cache}"
                else:
                    sql += " NOCACHE"
                OracleEngine._execute_ddl(conn, cursor, sql)
                created.append(f"{fmttd_owner}.{fmttd_sequence_name}")
                changed = True
            if changed:
                conn.commit()
        except Exception:
            conn.rollback()
            logger.critical("Failed to ensure sequences.", exc_info=True)
            raise
        finally:
            if cursor:
                cursor.close()
        return created

    @staticmethod
    def ensure_database_links(conn: oracledb.Connection, links: Sequence[DatabaseLinkDefinition]) -> List[str]:
        created: List[str] = []
        if not links:
            return created
        cursor: Optional[oracledb.Cursor] = None
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
        except Exception:
            conn.rollback()
            logger.critical("Failed to ensure database links.", exc_info=True)
            raise
        finally:
            if cursor:
                cursor.close()
        return created

    @staticmethod
    def ensure_table_privileges(conn: oracledb.Connection, role: str, tables: Sequence[Tuple[str, str]], privileges: Sequence[str]) -> List[str]:
        cursor: Optional[oracledb.Cursor] = None
        granted: List[str] = []
        changed = False
        if not tables:
            return granted
        fmt_role = OracleEngine._format_identifier(role)
        try:
            cursor = conn.cursor()
            for owner, table_name in tables:
                fmttd_owner = OracleEngine._format_identifier(owner)
                fmttd_table_name = OracleEngine._format_identifier(table_name)
                for privilege in privileges:
                    privilege_upper = privilege.upper()
                    if OracleEngine._role_has_table_priv(cursor, role.upper(), owner.upper(), table_name.upper(), privilege_upper):
                        continue
                    sql = f"GRANT {privilege_upper} ON {fmttd_owner}.{fmttd_table_name} TO {fmt_role}"
                    OracleEngine._execute_ddl(conn, cursor, sql)
                    granted.append(f"{fmttd_owner}.{fmttd_table_name}:{privilege_upper}")
                    changed = True
            if changed:
                conn.commit()
        except Exception:
            conn.rollback()
            logger.critical("Failed to ensure table privileges.", exc_info=True)
            raise
        finally:
            if cursor:
                cursor.close()
        return granted

    @staticmethod
    def ensure_supporting_objects(conn: oracledb.Connection, owner: str) -> List[str]:
        cursor: Optional[oracledb.Cursor] = None
        try:
            cursor = conn.cursor()
            cursor.execute(f"ALTER SESSION SET CURRENT_SCHEMA = {OracleEngine._format_identifier(owner)}")  # type: ignore[arg-type]
            for statement in (T_REFERENCING_TABLES_TYPE, CHECK_SAVE_STATUS_PROC, CHECK_REFERENCING_TABLES_PROC):
                OracleEngine._execute_ddl(conn, cursor, statement)
            return SUPPORTING_OBJECT_LIST
        except Exception:
            logger.critical("Failed to ensure supporting PL/SQL objects.", exc_info=True)
            raise
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def get_identifier_str(identifier: str) -> str:
        """ Returns the identifier string required to query dictionary views.
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
    def get_ldb_columns_expressions() -> Tuple[str, str]:
        """ Returns the expressions for the LDB process date and insert date columns.
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
    def _format_identifier(name: Optional[str]) -> str:
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
        escaped = identifier.replace("\"", "\"\"")
        return f'"{escaped}"'

    @staticmethod
    def _quote_literal(value: str) -> str:
        escaped = value.replace("'", "''")
        return f"'{escaped}'"

    # metadata and privilege helpers
    @staticmethod
    def _object_exists(cursor: oracledb.Cursor, query: str, params: Sequence[Any]) -> bool:  # type: ignore[valid-type]
        cursor.execute(query, params)  # type: ignore[arg-type]
        return cursor.fetchone() is not None

    @staticmethod
    def _user_has_role(cursor: oracledb.Cursor, username: str, role: str) -> bool:  # type: ignore[valid-type]
        return OracleEngine._object_exists(cursor, "SELECT 1 FROM dba_role_privs WHERE grantee = :1 AND granted_role = :2", [username, role])

    @staticmethod
    def _user_has_role_with_admin_option(cursor: oracledb.Cursor, username: str, role: str) -> bool:  # type: ignore[valid-type]
        return OracleEngine._object_exists(
            cursor, "SELECT 1 FROM dba_role_privs WHERE grantee = :1 AND granted_role = :2 AND admin_option = 'YES'", [username, role]
        )

    @staticmethod
    def _user_has_sys_priv(cursor: oracledb.Cursor, username: str, privilege: str) -> bool:  # type: ignore[valid-type]
        return OracleEngine._object_exists(cursor, "SELECT 1 FROM dba_sys_privs WHERE grantee = :1 AND privilege = :2", [username, privilege])

    @staticmethod
    def _role_has_table_priv(cursor: oracledb.Cursor, role: str, owner: str, table_name: str, privilege: str) -> bool:  # type: ignore[valid-type]
        return OracleEngine._object_exists(
            cursor, """
            SELECT 1
              FROM dba_tab_privs
             WHERE grantee = :grantee
               AND owner = :owner
               AND table_name = :table_name
               AND privilege = :privilege
            """, [role, owner, table_name, privilege],
        )

    @staticmethod
    def _role_exists(cursor: oracledb.Cursor, role: str) -> bool:  # type: ignore[valid-type]
        return OracleEngine._object_exists(cursor, "SELECT 1 FROM dba_roles WHERE role = :1", [role])

    @staticmethod
    def _user_exists(cursor: oracledb.Cursor, username: str) -> bool:  # type: ignore[valid-type]
        return OracleEngine._object_exists(cursor, "SELECT 1 FROM dba_users WHERE username = :1", [username])

    @staticmethod
    def _get_database_default_tablespace(cursor: oracledb.Cursor) -> Optional[str]:  # type: ignore[valid-type]
        cursor.execute("SELECT property_value FROM database_properties WHERE property_name = 'DEFAULT_PERMANENT_TABLESPACE'")  # type: ignore
        row = cursor.fetchone()
        if row and row[0]:
            return str(row[0])
        return None

    @staticmethod
    def _get_user_default_tablespace(cursor: oracledb.Cursor, username: str) -> Optional[str]:  # type: ignore[valid-type]
        cursor.execute("SELECT default_tablespace FROM dba_users WHERE username = :username", username=username)  # type: ignore
        row = cursor.fetchone()
        if row and row[0]:
            return str(row[0])
        return None

    @staticmethod
    def _ensure_unlimited_quota(cursor: oracledb.Cursor, username: str, tablespace: Optional[str]) -> bool:  # type: ignore[valid-type]
        if not tablespace:
            return False
        tablespace_name = OracleEngine._format_identifier(tablespace)
        cursor.execute( # type: ignore
            """
            SELECT max_bytes
              FROM dba_ts_quotas
             WHERE username = :username
               AND tablespace_name = :tablespace
            """,
            username=username,
            tablespace=tablespace_name,
        )
        row = cursor.fetchone()
        if row and row[0] == -1:
            return False
        sql = f"ALTER USER {username} QUOTA UNLIMITED ON {tablespace_name}"
        OracleEngine._execute_ddl(cursor.connection, cursor, sql)
        return True

    @staticmethod
    def _table_exists(cursor: oracledb.Cursor, owner: str, table_name: str) -> bool:  # type: ignore[valid-type]
        return OracleEngine._object_exists(cursor, "SELECT 1 FROM dba_tables WHERE owner = :1 AND table_name = :2", [owner, table_name], )

    @staticmethod
    def _index_exists(cursor: oracledb.Cursor, owner: str, index_name: str) -> bool:  # type: ignore[valid-type]
        return OracleEngine._object_exists(cursor, "SELECT 1 FROM dba_indexes WHERE owner = :1 AND index_name = :2", [owner, index_name], )

    @staticmethod
    def _constraint_exists(cursor: oracledb.Cursor, owner: str, constraint_name: str) -> bool:  # type: ignore[valid-type]
        return OracleEngine._object_exists(
            cursor, "SELECT 1 FROM dba_constraints WHERE owner = :1 AND constraint_name = :2", [owner, constraint_name],
        )

    @staticmethod
    def _sequence_exists(cursor: oracledb.Cursor, owner: str, sequence_name: str) -> bool:  # type: ignore[valid-type]
        return OracleEngine._object_exists(
            cursor, "SELECT 1 FROM dba_sequences WHERE sequence_owner = :1 AND sequence_name = :2", [owner, sequence_name],
        )

    @staticmethod
    def _register_connection_env(conn: oracledb.Connection, env: Optional[str]) -> None:
        if env:
            OracleEngine._connection_envs[id(conn)] = env
        else:
            OracleEngine._connection_envs.pop(id(conn), None)

    @staticmethod
    def _get_connection_env(conn: oracledb.Connection) -> Optional[str]:
        return OracleEngine._connection_envs.get(id(conn))

    @staticmethod
    def _execute_ddl(conn: oracledb.Connection, cursor: oracledb.Cursor, statement: str) -> None:
        log_statement = OracleEngine._PRIVATE_DDL_MARK.sub("****", statement)
        if len(log_statement) > 100:
            log_statement = log_statement[:100] + " ... [truncated]"
        statement = OracleEngine._PRIVATE_DDL_MARK.sub(lambda m: m.group(1), statement)
        logger.info("Executing %s DDL: %s", OracleEngine._get_connection_env(conn), " ".join(log_statement.split()))
        cursor.execute(statement)  # type: ignore[arg-type]

    @staticmethod
    def _db_link_exists(cursor: oracledb.Cursor, name: str) -> bool:
        return OracleEngine._object_exists(cursor, "SELECT 1 FROM user_db_links WHERE db_link = :1", [name])

    @staticmethod
    def _get_primary_key_info(cursor: oracledb.Cursor, owner: str, table_name: str) -> Tuple[Optional[str], Tuple[str, ...], Optional[str]]:
        cursor.execute(  # type: ignore
            """
            SELECT constraint_name, index_name
              FROM dba_constraints
             WHERE owner = :1
               AND table_name = :2
               AND constraint_type = 'P'
            """, [owner, table_name]
        )
        cursor.execute(  # type: ignore
            """
            SELECT constraint_name, index_name
              FROM dba_constraints
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
        cursor.execute( # type: ignore
            "SELECT column_name FROM dba_cons_columns WHERE owner = :1 AND constraint_name = :2 ORDER BY position",
            [owner, constraint_name],
        )
        columns = tuple(r[0] for r in cast(Iterable[Tuple[str]], cursor))
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
    def _get_table_indexes(cursor: oracledb.Cursor, owner: str,
                           table_name: str) -> Dict[str, Tuple[Tuple[str, ...], bool]]:  # type: ignore[valid-type]
        cursor.execute(  # type: ignore[arg-type]
            """
            SELECT i.index_name, i.uniqueness, c.column_name
              FROM dba_indexes i JOIN dba_ind_columns c ON i.owner = c.index_owner AND i.index_name = c.index_name WHERE i.owner = :owner
               AND i.table_name = :table_name
             ORDER BY i.index_name, c.column_position
            """,
            owner=owner,
            table_name=table_name,
        )
        indexes: Dict[str, Dict[str, Any]] = {}
        for index_name, uniqueness, column_name in cast(Iterable[Tuple[str, str, str]], cursor):
            index_info = indexes.setdefault(index_name, {"columns": [], "unique": uniqueness == "UNIQUE"})
            index_info["columns"].append(column_name)
        return {name: (tuple(info["columns"]), bool(info["unique"])) for name, info in indexes.items()}

    @staticmethod
    def _determine_process_date_column(table: TableDefinition) -> Optional[str]:
        available_columns = {column.name for column in table.columns}
        for candidate in ("LDB_PROCESS_DATE", "LDB_PROCESS_DATE"):
            if candidate in available_columns:
                return candidate
        return None

    @staticmethod
    def _prepare_desired_indexes(table: TableDefinition, ) -> Dict[str, Tuple[Tuple[str, ...], bool]]:
        desired_pk = tuple((table.primary_key or ()))
        process_date_column = OracleEngine._determine_process_date_column(table)
        desired_indexes: Dict[str, Tuple[Tuple[str, ...], bool]] = {}
        for index in table.indexes:
            columns = tuple(index.columns)
            if (process_date_column and process_date_column not in columns and (not desired_pk or columns != desired_pk)):
                columns = columns + (process_date_column, )
            desired_indexes[index.name] = (columns, index.unique)
        return desired_indexes

    @staticmethod
    def _choose_best_index_for_primary_key(indexes: IndexMap, column_metadata: ColumnMetadata) -> Optional[Tuple[str, ...]]:
        candidates: List[Dict[str, Any]] = []
        for index_name, info in indexes.items():
            columns: Sequence[str] = info.get("columns", [])
            if not columns:
                continue
            column_defs: List[ColumnDefinition] = []
            for col in columns:
                column_def = column_metadata.get(col)
                if column_def is None:
                    column_defs = []
                    break
                column_defs.append(column_def)
            if not column_defs:
                continue
            candidates.append({
                "name": index_name,
                "columns": tuple(col.name for col in column_defs),
                "unique": bool(info.get("unique")),
                "distinct_keys": info.get("distinct_keys"),
                "all_not_null": all(not col.nullable for col in column_defs),
            })
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

        def choose(candidate_list: List[Dict[str, Any]]) -> Optional[Tuple[str, ...]]:
            if not candidate_list:
                return None
            ordered = sorted(candidate_list, key=lambda item: (-score(item.get("distinct_keys")), len(item["columns"]), item["name"], ), )
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
    def _drop_constraints_by_type(cursor: oracledb.Cursor, owner: str, table_name: str,
                                  constraint_types: Sequence[str]) -> List[str]:  # type: ignore[valid-type]
        dropped: List[str] = []
        if not constraint_types:
            return dropped
        type_list = ", ".join(f"'{constraint_type}'" for constraint_type in constraint_types)
        cursor.execute(  # type: ignore[arg-type]
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
        for constraint_name, in cast(Iterable[Tuple[str]], cursor):
            fmttd_constraint_name = OracleEngine._format_identifier(constraint_name)
            sql = f"ALTER TABLE {fmttd_owner}.{fmttd_table_name} DROP CONSTRAINT {fmttd_constraint_name}"
            OracleEngine._execute_ddl(cursor.connection, cursor, sql)
            dropped.append(fmttd_constraint_name)
        return dropped
