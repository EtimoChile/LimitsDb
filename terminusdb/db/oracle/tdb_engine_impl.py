from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import oracledb
from terminusdb.core.tdb_params_config import Config
from terminusdb.core.tdb_status import Status
from terminusdb.core.tdb_utils import get_effective_credentials, indent_lines, join_wrapped, nvl
from terminusdb.db.tdb_engines import DatabaseEngine
from terminusdb.core.tdb_logger import get_logger

class OracleEngine(DatabaseEngine):
    """Oracle DB engine with methods for connection, configuration loading, and PL/SQL generation."""

    @staticmethod
    def get_connection(config: Config, *, admin: bool=False) -> oracledb.Connection:
        """Returns an Oracle connection using provided config.
        Args:
            config: Database config object.
        Returns:
            An active oracledb.Connection."""
        logger = get_logger("oracle.engine")
        try:
            user, password, dsn = get_effective_credentials(config, admin=admin)
            return oracledb.connect(user=user, password=password, dsn=dsn) # type: ignore
        except Exception:
            logger.critical("Failed to connect to Oracle DB.", exc_info=True)
            raise

    @staticmethod
    def get_system_date(conn: oracledb.Connection) -> datetime:
        """Returns the current system date from Oracle.
        Args:
            conn: Active Oracle connection.
        Returns:
            Current system date as Python date."""
        logger = get_logger("oracle.engine")
        cursor: Optional[oracledb.Cursor] = None
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT SYSDATE FROM dual") # type: ignore
            sysdate: datetime = cursor.fetchone()[0]
            return sysdate
        except Exception:
            logger.critical("Failed to retrieve system date from Oracle.", exc_info=True)
            raise
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def load_config(conn: oracledb.Connection) -> List[Dict[str, Any]]:
        """Loads configuration from tdb_conf.
        Args:
            conn: Active Oracle connection.
        Returns:
            List of configuration rows."""
        logger = get_logger("oracle.engine")
        cursor: Optional[oracledb.Cursor] = None
        try:
            cursor = conn.cursor()
            cursor.execute( # type: ignore
                """SELECT cnf_id, cnf_source_owner, cnf_history_owner, cnf_table_name, cnf_retain_months_source,
                       cnf_retain_months_history, cnf_exec_day, cnf_frecuency, cnf_is_active, cnf_purge_date_expr,
                       cnf_additional_filter_expr, cnf_history_addtl_filter_expr, cnf_source_orphan_purge, cnf_orphan_check_column, cnf_has_lob_columns,
                       cnf_referencing_tables, cnf_join_expr, cnf_hint_expr, cnf_long_columns, null ctl_status
                FROM tdb_conf
                WHERE cnf_is_active = 'Y'""")
            cols = [col[0].lower() for col in cursor.description] # type: ignore
            return [dict(zip(cols, row)) for row in cursor.fetchall()] # type: ignore
        except Exception:
            logger.critical("Failed to load configuration from Oracle.", exc_info=True)
            raise
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def get_status(conn: oracledb.Connection, process_date: str) -> List[Dict[str, Any]]:
        """Loads status from tdb_ctl for the given process date.
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
                FROM tdb_ctl
                WHERE ctl_process_date = TO_DATE(:1, 'YYYYMMDD')""", [process_date])
            cols = [col[0].lower() for col in cursor.description] # type: ignore
            return [dict(zip(cols, row)) for row in cursor.fetchall()] # type: ignore
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def generate_sql_block(config: Config, table_cnf: Dict[str, Any], process_date: str) -> str:
        """Generates a PL/SQL block for table processing with optional chunking and LOB handling.
        Args:
            config: Configuration object.
            table_cnf: Processed table configuration object.
            process_date: Process date in 'YYYYMMDD' format.
        Returns:
            PL/SQL block as string."""
        logger = get_logger("oracle.engine")
        cnd0 = table_cnf["conds"][0]
        source_owner, history_owner, table_name = cnd0["cnf_source_owner"], cnd0["cnf_history_owner"], cnd0["cnf_table_name"]
        source_ilm = config.action == "SOURCE_ILM"
        history_dblink = config.history_dblink_name.lower()
        history_dblink_suffix = f"@{history_dblink}" if history_dblink else ""
        source_dblink = config.source_dblink_name.upper()
        source_dblink_suffix = f"@{source_dblink}" if source_dblink else ""
        save_status_proc = f"check_save_status{'' if source_ilm else source_dblink_suffix}"
        check_refs_proc = f"check_referencing_tables{'' if source_ilm else source_dblink_suffix}"
        hint_expr, has_lob_columns = cnd0["cnf_hint_expr"], cnd0["cnf_has_lob_columns"] == 'Y'
        other_cols_exprs, other_cols_alias, referencing_tables = table_cnf["other_cols_exprs"], table_cnf["other_cols_alias"], table_cnf["referencing_tables"]
        query_expr, table_columns, months_keep_history_max = table_cnf["query_expr"], table_cnf["table_columns"], table_cnf["months_keep_history_max"]
        logger.debug(f"Generating PL/SQL block for table {source_owner}.{table_name} with process date {process_date}")
        referencing_tables = ", ".join([f"'{rt[0]}.{rt[1]}'" for rt in referencing_tables])
        if config.add_tdb_columns:
            gend_cols = ["tdb_process_date", "tdb_insert_date"]
            gend_vals = ["l_process_date", "sysdate"]
        else:
            gend_cols = gend_vals = []
        ins_cols = join_wrapped(", ", table_columns + other_cols_alias + gend_cols, 200)
        ins_vals = join_wrapped(", ", [f"r_rec(i).{col}" for col in table_columns + other_cols_alias] + gend_vals, 200)
        if config.generate_script: process_date = "&process_date"
        plsql = f"""declare
    l_source_owner varchar2(50) := '{source_owner}';
    l_history_owner varchar2(50) := '{history_owner}';
    l_action varchar2(10) := '{config.action}';
    l_mode varchar2(10) := '{config.mode}';
    l_message varchar2(200) := case when l_mode = 'ALL' then null else 'QUERY ONLY' end;
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
    {save_status_proc}(l_source_owner, l_table_name, l_process_date, l_action, '{Status.TABLE_START}', l_process_start, null, null, l_message, 0, l_plsql, l_sqlcode, l_out_message);
    if l_sqlcode is not null then raise_application_error(l_sqlcode, l_out_message); end if;"""
        if source_ilm or not config.use_added_columns:
            plsql += f"""
    {check_refs_proc}(l_referencing_tables, l_process_date); commit;"""
        if not has_lob_columns or not source_ilm:
            plsql += f"""
    open c_records;
    loop
        l_chunk_start := sysdate;
        {save_status_proc}(l_source_owner, l_table_name, l_process_date, l_action, '{Status.CHUNK_START}', null, l_chunk_start, null, l_message, 0, null, l_sqlcode, l_out_message);
        fetch c_records bulk collect into r_rec limit l_chunk_size;
        if r_rec.count <= 0 then
            exit;
        end if;"""
            if (config.mode == "ALL"):
                if source_ilm and nvl(months_keep_history_max, 1) > 0:
                    plsql += f"""
        for i in 1 .. r_rec.count loop
            insert into {history_owner.lower()}.{table_name.lower()}{history_dblink_suffix}
            ({indent_lines(ins_cols,12)})
            values ({indent_lines(ins_vals,12)});
        end loop;"""
                plsql += f"""
        forall i in 1 .. r_rec.count
            delete from {source_owner.lower()}.{table_name.lower()} where rowid = r_rec(i).rowid;
        l_record_count := l_record_count + r_rec.count;"""
            plsql += f"""
        {save_status_proc}(l_source_owner, l_table_name, l_process_date, l_action, '{Status.CHUNK_END}', null, l_chunk_start, sysdate, l_message, r_rec.count, null, l_sqlcode, l_out_message);
        commit;
    end loop;
    close c_records;"""
        else:
            cols_select = ", ".join([f"a.{col}" for col in table_columns] + other_cols_exprs + gend_vals)
            if config.mode == "ALL":
                if source_ilm and nvl(months_keep_history_max,0) > 0:
                    plsql += f"""
    insert into {history_owner.lower()}.{table_name.lower()}{history_dblink_suffix}({indent_lines(ins_cols,4)})
    select /*+ {hint_expr} */ {indent_lines(cols_select,4)}
    {indent_lines(query_expr, 4)};"""
                plsql += f"""
    delete from {source_owner.lower()}.{table_name.lower()} where rowid in
    (select /*+ {hint_expr} */ a.rowid
    {indent_lines(query_expr, 4)});
    l_record_count := sql%rowcount;"""
            else:
                plsql += f"""
    select /*+ {hint_expr} */ count(*) into l_record_count
    {indent_lines(query_expr, 4)};"""
        plsql += f"""
    {save_status_proc}(l_source_owner, l_table_name, l_process_date, l_action, '{Status.TABLE_END}', l_process_start, null, sysdate, l_message, l_record_count, null, l_sqlcode, l_out_message);
    commit;
exception
    when others then
        rollback;
        {save_status_proc}(l_source_owner, l_table_name, l_process_date, l_action, '{Status.ERROR}', l_process_start, null, sysdate, sqlerrm, l_record_count, null, l_sqlcode, l_out_message);
        commit;
        if l_sqlcode not in (-20003) then
            raise;
        end if;
end;"""
        return plsql

    @staticmethod
    def get_rows_processed(conn: oracledb.Connection, owner: str, table_name: str, process_date: str) -> int:
        """Returns rows processed for a given table and process date from tdb_ctl.
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
                FROM tdb_ctl WHERE ctl_owner = :1 AND ctl_table_name = :2 AND ctl_process_date = TO_DATE(:3, 'YYYYMMDD')""",
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
    def save_error_status(conn: oracledb.Connection, config: Config, owner: str, table_name: str, process_date: str, process_start: datetime, message: str, plsql_code: str) -> None:
        logger = get_logger("oracle.engine")
        cursor: Optional[oracledb.Cursor] = None
        try:
            cursor = conn.cursor()
            process_end = OracleEngine.get_system_date(conn)
            sqlcode = cursor.var(oracledb.NUMBER) # type: ignore
            out_message = cursor.var(oracledb.STRING) # type: ignore
            cursor.callproc('check_save_status', [owner, table_name, datetime.strptime(process_date, "%Y%m%d").date(), # type: ignore
                config.action, Status.ERROR, process_start, None, process_end, message, 0, plsql_code, sqlcode, out_message])
            conn.commit()
        except Exception:
            logger.critical(f"Unexpected error in process_table for {owner}.{table_name}:", exc_info=True)
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
    def all_status_tend(conn: oracledb.Connection, tables_config: Dict[Tuple[str, str], Any], process_date: str) -> bool:
        """Checks if all referenced tables have status "Status.TABLE_END" in tdb_ctl.
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
                if table_cnf["skip"]: continue
                cursor.execute( # type: ignore
                    f"""SELECT ctl_status FROM tdb_ctl
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
    def get_table_columns(conn: oracledb.Connection, owner: str, table_name: str) -> List[str]:
        """Returns a list of column names for a given table in the specified schema.
        Args:
            conn: Active Oracle connection.
            owner: Schema owner of the table.
            table_name: Table name.
        Returns:
            List of column names in lowercase."""
        cursor: Optional[oracledb.Cursor] = None
        try:
            cursor = conn.cursor()
            cursor.execute( # type: ignore
                """select lower(column_name) column_name
                from all_tab_columns
                where  owner = upper(:1) and table_name = upper(:2) and column_id is not null
                order by column_id""", [owner, table_name])
            return [row[0] for row in cursor.fetchall()] # type: ignore
        finally:
            if cursor:
                cursor.close()
    @staticmethod
    def get_date_cond(date_expr: str, months_keep_src: int) -> str:
        """Returns a date condition for the given date expression and months to keep.
        Args:
            date_expr: Date expression to evaluate.
            months_keep_src: Months to keep.
        Returns:
            Date condition as string."""
        return f"({date_expr} < add_months(l_process_date,-{months_keep_src}))"

    @staticmethod
    def close_connection(conn: oracledb.Connection) -> None:
        """Closes the Oracle connection.
        Args:
            conn: Active Oracle connection."""
        logger = get_logger("oracle.engine")
        try:
            if conn:
                conn.close()
        except Exception:
            logger.critical("Failed to close Oracle DB connection.", exc_info=True)

