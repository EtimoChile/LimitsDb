from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple
import oracledb
from terminusdb.core.tdb_params_config import Config
from terminusdb.core.tdb_status import Status
from terminusdb.core.tdb_utils import get_effective_credentials, indent_lines, join_wrapped, nvl
from terminusdb.db.tdb_engines import (
    ColumnDefinition,
    DatabaseEngine,
    DatabaseLinkDefinition,
    IndexDefinition,
    RoleDefinition,
    SequenceDefinition,
    TableDefinition,
    UserDefinition,
)
from terminusdb.core.tdb_logger import get_logger
logger = get_logger("oracle.engine")

class OracleEngine(DatabaseEngine):
    """Oracle DB engine with methods for connection, configuration loading, and PL/SQL generation."""

    @staticmethod
    def _format_identifier(name: str) -> str:
        if not name:
            raise ValueError("Identifier cannot be empty")
        return name.strip().upper()

    @staticmethod
    def _quote_password(password: str) -> str:
        escaped = password.replace('"', '""')
        return f'"{escaped}"'

    @staticmethod
    def _quote_literal(value: str) -> str:
        escaped = value.replace("'", "''")
        return f"'{escaped}'"

    @staticmethod
    def _column_type_sql(column: ColumnDefinition) -> str:
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
    def _column_sql(column: ColumnDefinition) -> str:
        col_name = OracleEngine._format_identifier(column.name)
        col_type = OracleEngine._column_type_sql(column)
        default_clause = f" DEFAULT {column.default}" if column.default is not None else ""
        nullable_clause = "" if column.nullable else " NOT NULL"
        return f"{col_name} {col_type}{default_clause}{nullable_clause}"

    @staticmethod
    def _object_exists(cursor: oracledb.Cursor, query: str, params: Sequence[Any]) -> bool:  # type: ignore[valid-type]
        cursor.execute(query, params)  # type: ignore[arg-type]
        return cursor.fetchone() is not None

    @staticmethod
    def _user_has_role(cursor: oracledb.Cursor, username: str, role: str) -> bool:  # type: ignore[valid-type]
        return OracleEngine._object_exists(
            cursor,
            "SELECT 1 FROM dba_role_privs WHERE grantee = :1 AND granted_role = :2",
            [username, role],
        )

    @staticmethod
    def _user_has_sys_priv(cursor: oracledb.Cursor, username: str, privilege: str) -> bool:  # type: ignore[valid-type]
        return OracleEngine._object_exists(
            cursor,
            "SELECT 1 FROM dba_sys_privs WHERE grantee = :1 AND privilege = :2",
            [username, privilege],
        )

    @staticmethod
    def _role_exists(cursor: oracledb.Cursor, role: str) -> bool:  # type: ignore[valid-type]
        return OracleEngine._object_exists(cursor, "SELECT 1 FROM dba_roles WHERE role = :1", [role])

    @staticmethod
    def _user_exists(cursor: oracledb.Cursor, username: str) -> bool:  # type: ignore[valid-type]
        return OracleEngine._object_exists(cursor, "SELECT 1 FROM dba_users WHERE username = :1", [username])

    @staticmethod
    def _table_exists(cursor: oracledb.Cursor, owner: str, table_name: str) -> bool:  # type: ignore[valid-type]
        return OracleEngine._object_exists(
            cursor,
            "SELECT 1 FROM dba_tables WHERE owner = :1 AND table_name = :2",
            [owner, table_name],
        )

    @staticmethod
    def _index_exists(cursor: oracledb.Cursor, owner: str, index_name: str) -> bool:  # type: ignore[valid-type]
        return OracleEngine._object_exists(
            cursor,
            "SELECT 1 FROM dba_indexes WHERE owner = :1 AND index_name = :2",
            [owner, index_name],
        )

    @staticmethod
    def _constraint_exists(cursor: oracledb.Cursor, owner: str, constraint_name: str) -> bool:  # type: ignore[valid-type]
        return OracleEngine._object_exists(
            cursor,
            "SELECT 1 FROM dba_constraints WHERE owner = :1 AND constraint_name = :2",
            [owner, constraint_name],
        )

    @staticmethod
    def _sequence_exists(cursor: oracledb.Cursor, owner: str, sequence_name: str) -> bool:  # type: ignore[valid-type]
        return OracleEngine._object_exists(
            cursor,
            "SELECT 1 FROM dba_sequences WHERE sequence_owner = :1 AND sequence_name = :2",
            [owner, sequence_name],
        )

    @staticmethod
    def _db_link_exists(cursor: oracledb.Cursor, name: str) -> bool:  # type: ignore[valid-type]
        return OracleEngine._object_exists(
            cursor,
            "SELECT 1 FROM user_db_links WHERE db_link = :1",
            [name],
        )

    @staticmethod
    def get_connection(config: Config, *, admin: bool=False) -> oracledb.Connection:
        """Returns an Oracle connection using provided config.
        Args:
            config: Database config object.
        Returns:
            An active oracledb.Connection."""
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
        cnd0 = table_cnf["conds"][0]
        source_owner, history_owner, table_name = cnd0["cnf_source_owner"], cnd0["cnf_history_owner"], cnd0["cnf_table_name"]
        hint_expr, has_lob_columns = cnd0["cnf_hint_expr"], cnd0["cnf_has_lob_columns"] == 'Y'
        other_cols_exprs, other_cols_alias, referencing_tables = table_cnf["other_cols_exprs"], table_cnf["other_cols_alias"], table_cnf["referencing_tables"]
        query_expr, table_columns, months_keep_history_max = table_cnf["query_expr"], table_cnf["table_columns"], table_cnf["months_keep_history_max"]
        logger.debug(f"Generating PL/SQL block for table {source_owner}.{table_name} with process date {process_date}")
        referencing_tables = ", ".join([f"'{rt[0]}.{rt[1]}'" for rt in referencing_tables])
        source_ilm = config.action == "SOURCE_ILM"
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
    check_save_status{"" if source_ilm else "@SOURCE"}(l_source_owner, l_table_name, l_process_date, l_action, '{Status.TABLE_START}', l_process_start, null, null, l_message, 0, l_plsql, l_sqlcode, l_out_message);
    if l_sqlcode is not null then raise_application_error(l_sqlcode, l_out_message); end if;"""
        if source_ilm or not config.use_added_columns:
            plsql += f"""
    check_referencing_tables{"" if source_ilm else "@SOURCE"}(l_referencing_tables, l_process_date); commit;"""
        if not has_lob_columns or not source_ilm:
            plsql += f"""
    open c_records;
    loop
        l_chunk_start := sysdate;
        check_save_status{"" if source_ilm else "@SOURCE"}(l_source_owner, l_table_name, l_process_date, l_action, '{Status.CHUNK_START}', null, l_chunk_start, null, l_message, 0, null, l_sqlcode, l_out_message);
        fetch c_records bulk collect into r_rec limit l_chunk_size;
        if r_rec.count <= 0 then
            exit;
        end if;"""
            if (config.mode == "ALL"):
                if source_ilm and nvl(months_keep_history_max, 1) > 0:
                    plsql += f"""
        for i in 1 .. r_rec.count loop
            insert into {history_owner.lower()}.{table_name.lower()}@hist
            ({indent_lines(ins_cols,12)})
            values ({indent_lines(ins_vals,12)});
        end loop;"""
                plsql += f"""
        forall i in 1 .. r_rec.count
            delete from {source_owner.lower()}.{table_name.lower()} where rowid = r_rec(i).rowid;
        l_record_count := l_record_count + r_rec.count;"""
            plsql += f"""
        check_save_status{"" if source_ilm else "@SOURCE"}(l_source_owner, l_table_name, l_process_date, l_action, '{Status.CHUNK_END}', null, l_chunk_start, sysdate, l_message, r_rec.count, null, l_sqlcode, l_out_message);
        commit;
    end loop;
    close c_records;"""
        else:
            cols_select = ", ".join([f"a.{col}" for col in table_columns] + other_cols_exprs + gend_vals)
            if config.mode == "ALL":
                if source_ilm and nvl(months_keep_history_max,0) > 0:
                    plsql += f"""
    insert into {history_owner.lower()}.{table_name.lower()}@hist({indent_lines(ins_cols,4)})
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
    check_save_status{"" if source_ilm else "@SOURCE"}(l_source_owner, l_table_name, l_process_date, l_action, '{Status.TABLE_END}', l_process_start, null, sysdate, l_message, l_record_count, null, l_sqlcode, l_out_message);
    commit;
exception
    when others then
        rollback;
        check_save_status{"" if source_ilm else "@SOURCE"}(l_source_owner, l_table_name, l_process_date, l_action, '{Status.ERROR}', l_process_start, null, sysdate, sqlerrm, l_record_count, null, l_sqlcode, l_out_message);
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
        try:
            if conn:
                conn.close()
        except Exception:
            logger.critical("Failed to close Oracle DB connection.", exc_info=True)

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
                role_name = OracleEngine._format_identifier(role.name)
                if OracleEngine._role_exists(cursor, role_name):
                    continue
                cursor.execute(f"CREATE ROLE {role_name}")  # type: ignore[arg-type]
                created.append(role_name)
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
    def ensure_users(conn: oracledb.Connection, users: Sequence[UserDefinition]) -> List[str]:
        created: List[str] = []
        if not users:
            return created
        cursor: Optional[oracledb.Cursor] = None
        changed = False
        try:
            cursor = conn.cursor()
            for user in users:
                username = OracleEngine._format_identifier(user.name)
                if not OracleEngine._user_exists(cursor, username):
                    sql = f"CREATE USER {username} IDENTIFIED BY {OracleEngine._quote_password(user.password)}"
                    if user.default_tablespace:
                        sql += f" DEFAULT TABLESPACE {OracleEngine._format_identifier(user.default_tablespace)}"
                    if user.temporary_tablespace:
                        sql += f" TEMPORARY TABLESPACE {OracleEngine._format_identifier(user.temporary_tablespace)}"
                    cursor.execute(sql)  # type: ignore[arg-type]
                    created.append(username)
                    changed = True
                for role in user.roles:
                    role_name = OracleEngine._format_identifier(role)
                    if not OracleEngine._user_has_role(cursor, username, role_name):
                        cursor.execute(f"GRANT {role_name} TO {username}")  # type: ignore[arg-type]
                        changed = True
                for privilege in user.system_privileges:
                    privilege_name = privilege.upper()
                    if not OracleEngine._user_has_sys_priv(cursor, username, privilege_name):
                        cursor.execute(f"GRANT {privilege_name} TO {username}")  # type: ignore[arg-type]
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
                owner = OracleEngine._format_identifier(table.owner)
                table_name = OracleEngine._format_identifier(table.name)
                if not OracleEngine._table_exists(cursor, owner, table_name):
                    columns_sql = ",\n        ".join(OracleEngine._column_sql(col) for col in table.columns)
                    cursor.execute(
                        f"CREATE TABLE {owner}.{table_name} (\n        {columns_sql}\n    )"
                    )  # type: ignore[arg-type]
                    created.append(f"{owner}.{table_name}")
                    changed = True
                if table.primary_key:
                    pk_name = OracleEngine._format_identifier(f"{table.name}_pk")
                    if not OracleEngine._constraint_exists(cursor, owner, pk_name):
                        cols = ", ".join(OracleEngine._format_identifier(col) for col in table.primary_key)
                        cursor.execute(
                            f"ALTER TABLE {owner}.{table_name} ADD CONSTRAINT {pk_name} PRIMARY KEY ({cols})"
                        )  # type: ignore[arg-type]
                        changed = True
                for index in table.indexes:
                    idx_name = OracleEngine._format_identifier(index.name)
                    if OracleEngine._index_exists(cursor, owner, idx_name):
                        continue
                    cols = ", ".join(OracleEngine._format_identifier(col) for col in index.columns)
                    unique_kw = "UNIQUE " if index.unique else ""
                    cursor.execute(
                        f"CREATE {unique_kw}INDEX {owner}.{idx_name} ON {owner}.{table_name} ({cols})"
                    )  # type: ignore[arg-type]
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
    def ensure_sequences(conn: oracledb.Connection, sequences: Sequence[SequenceDefinition]) -> List[str]:
        created: List[str] = []
        if not sequences:
            return created
        cursor: Optional[oracledb.Cursor] = None
        changed = False
        try:
            cursor = conn.cursor()
            for sequence in sequences:
                owner = OracleEngine._format_identifier(sequence.owner)
                sequence_name = OracleEngine._format_identifier(sequence.name)
                if OracleEngine._sequence_exists(cursor, owner, sequence_name):
                    continue
                sql = (
                    f"CREATE SEQUENCE {owner}.{sequence_name} "
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
                cursor.execute(sql)  # type: ignore[arg-type]
                created.append(f"{owner}.{sequence_name}")
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
                link_name = OracleEngine._format_identifier(link.name)
                if OracleEngine._db_link_exists(cursor, link_name):
                    continue
                username = OracleEngine._format_identifier(link.username)
                password = OracleEngine._quote_password(link.password)
                dsn_literal = OracleEngine._quote_literal(link.dsn)
                cursor.execute(
                    f"CREATE DATABASE LINK {link_name} CONNECT TO {username} IDENTIFIED BY {password} USING {dsn_literal}"
                )  # type: ignore[arg-type]
                created.append(link_name)
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

