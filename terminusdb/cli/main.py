import logging
import traceback
import oracledb
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED, as_completed

# Configurar logger profesional
logger = logging.getLogger("terminusdb")
logger.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler()
formatter = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(processName)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

parallel_max = 10  # Adjust to your needs
dsn = "leon.etimo.cl:1521/alpha"
user = "tdb"
password = "etm1tdb"
action = "MANT_PROD" # "MANT_PROD" or "MANT_HIST"
mode = "ALL" # "ALL" or "QUERY ONLY"
chunk_size = 100000 # Chunk size for processing
use_added_cols = False # If True, adds additional columns to the destination table
do_process_tables = True  # If True, executes the PL/SQL block; if False, only prints it


def nvl(value, default):
    return default if value is None else value

def get_rows_processed(owner, table_name, process_date, action, process_start, message, plsql_code, conn):
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ctl_rows_processed FROM tdb_ctl
            WHERE ctl_owner = :1 AND ctl_table_name = :2 AND ctl_process_date = TO_DATE(:3, 'YYYYMMDD')
        """, [owner, table_name, process_date])
        result = cursor.fetchone()
        if result:
            return result[0]
        cursor.execute("SELECT sysdate FROM dual")
        process_end, = cursor.fetchone()
        sqlcode = cursor.var(oracledb.NUMBER)
        out_message = cursor.var(oracledb.STRING)
        cursor.callproc('check_save_status', [ owner, table_name, oracledb.Date.fromisoformat(process_start.strftime('%Y-%m-%d')),
            action, 'ERROR', process_start, None, process_end, message, 0, plsql_code, sqlcode, out_message ])
        conn.commit()
        return 0
    except oracledb.DatabaseError as e:
        logger.critical(f"Unexpected error in process_table for {owner}.{table_name}:", exc_info=True)
        return 0
    finally:
        if cursor:
            cursor.close()

def process_table(owner, table_name, plsql_code, referencing_tables, process_date, dsn, user, password):
    conn = cursor = None
    logger.info(f"Processing table {owner}.{table_name}...")
    try:
        conn = oracledb.connect(user=user, password=password, dsn=dsn)
        logger.info(f"Connected to {dsn} as {user}")
        cursor = conn.cursor()
        cursor.execute("SELECT sysdate FROM dual")
        process_start, = cursor.fetchone()
        logger.info(f"Executing ILM for {owner}.{table_name}...")
        cursor.setinputsizes(plsql_code=oracledb.CLOB)
        cursor.execute(plsql_code, { "plsql_code": plsql_code })
        rows_processed = get_rows_processed(owner, table_name, process_date, action, process_start, None, plsql_code, conn)
        return (owner, table_name, 'TEND', rows_processed, None, None)
    except oracledb.DatabaseError as e:
        error, = e.args
        rows_processed = get_rows_processed(owner, table_name, process_date, action, process_start, error.message, plsql_code, conn)
        return (owner, table_name, 'ERROR', rows_processed, error.code, error.message)
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def all_status_tend(tables_cnf, process_date, conn):
    #Checks if all tables are in TEND status for process_date
    try:
        cursor = conn.cursor()
        for (owner, table_name) in tables_cnf.keys():
            cursor.execute("""
                SELECT ctl_status FROM tdb_ctl
                WHERE ctl_owner = :1 AND ctl_table_name = :2 AND ctl_process_date = TO_DATE(:3, 'YYYYMMDD')
            """, [owner, table_name, process_date])
            result = cursor.fetchone()
            if not result or result[0] != 'TEND':
                return False
        return True
    finally:
        if 'cursor' in locals():
            cursor.close()

def process_tables(tables_cnf, process_date, connection):
    processes = []
    active_tables = set()
    with ProcessPoolExecutor(max_workers=parallel_max) as executor:
        process_launched = False
        while True:
            # While there are space in the pool, try to launch new processes
            while len(processes) < parallel_max:
                cycle_launched = False
                # Search for a process to launch
                for (owner, table_name), table_info in tables_cnf.items():
                    cd = tables_cnf[(owner, table_name)]["conds"][0]
                    if cd["ctl_status"] in ["SKIPPED", "TEND"] or (owner, table_name) in active_tables:
                        continue
                    referencing_tables = table_info['referencing_tables']
                    all_referencing_tables_ready = True
                    if referencing_tables:
                        for ref_owner, ref_table in referencing_tables:
                            cdr = tables_cnf[(ref_owner, ref_table)]["conds"][0]
                            if cdr["ctl_status"] != "TEND":
                                all_referencing_tables_ready = False
                                break # referencing table not TEND status
                    if not all_referencing_tables_ready:
                        logger.debug(f"Table {owner}.{table_name} is waiting for referencing tables to finish...")
                        continue
                    logger.info(f"Launching background process for {owner}.{table_name}...")
                    future = executor.submit(
                        process_table, owner, table_name, table_info['plsql'],
                        referencing_tables, process_date, dsn, user, password
                    )
                    processes.append(future)
                    active_tables.add((owner, table_name))
                    table_info["conds"][0]["ctl_status"] = "TSTART"
                    cycle_launched = True
                    process_launched = True
                    break  # Launch only one process at a time
                if not cycle_launched:
                    break  # There are no processes to launch, so go to waitting some process to end
            logger.debug(f"Active processes: {len(processes)}")
            if processes:
                # Wait for some process to finish
                for completed_future in as_completed(processes):
                    owner, table_name, status, rows_processed, sqlcode, message = completed_future.result()
                    cdr = tables_cnf[(owner, table_name)]["conds"][0]
                    if status == 'ERROR' and rows_processed == 0:
                        status = 'SKIPPED'
                    logger.info(f"Table {owner}.{table_name} finished with status {status} and {rows_processed} rows processed.")
                    cdr["ctl_status"] = status
                    if status == 'ERROR':
                        logger.info(f"  Error {sqlcode}: {message}")
                    elif status == 'SKIPPED':
                        logger.info(f"  Skipped {sqlcode}: {message}")
                    active_tables.remove((owner, table_name))
                    processes.remove(completed_future)
                    break
            else:
                break
    if all_status_tend(tables_cnf, process_date, connection):
        if process_launched:
            logger.info("All tables processed successfully.")
        else:
            logger.info("No tables to process.")
        exit(0)
    else:
        logger.warn("Some tables did not finish successfully.")
        exit(1)

def run_cli():
    try:
        connection = oracledb.connect(user=user, password=password, dsn=dsn)
        cursor = connection.cursor()
        cursor.execute("select to_char(sysdate,'YYYYMMDD') from dual")
        process_date, = cursor.fetchone()
        cursor.execute("""select cnf_id, cnf_prod_owner, cnf_hist_owner, cnf_table_name, cnf_months_keep_prod, cnf_months_keep_hist, cnf_exec_day, cnf_frecuency, 
            cnf_is_active, cnf_purge_limit_date_expr, cnf_additional_expr, cnf_prod_orphan_purge, cnf_orphan_chk_column, cnf_has_lob, 
            cnf_ref_tables, cnf_join_expr, cnf_hint_expr, cnf_long_cols, ctl_status
            from tdb_conf cnf
            left outer join tdb_ctl ctl on (cnf_prod_owner = ctl_owner  and cnf_table_name = ctl_table_name and ctl_process_date = to_date(:1, 'YYYYMMDD'))
            where cnf_is_active = 'Y'""", [process_date])
        columns = [col[0].lower() for col in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        if not do_process_tables:
            process_date = "&process_date"
        tables_cnf = {}
        for row in rows:
            key = (row["cnf_prod_owner"], row["cnf_table_name"])
            if key not in tables_cnf: tables_cnf[key] = { "conds": [], "referencing_tables": [] }
            tables_cnf[key]["conds"].append(row)
            ref_tables = row["cnf_ref_tables"]
            if ref_tables:
                for ref_table in ref_tables.split(","):
                    ref_table = ref_table.strip()
                    if "." in ref_table: ref_owner, ref_table = ref_table.split(".")
                    else: ref_owner = row["cnf_prod_owner"]
                    ref_table, alias = ref_table.split(" ")
                    ref_key = (ref_owner.upper(), ref_table.upper())
                    if ref_key not in tables_cnf: tables_cnf[ref_key] = { "conds": [], "referencing_tables": [] }
                    tables_cnf[ref_key]["referencing_tables"].append(key)
        if not do_process_tables:
            print("""


                whenever oserror exit 1
                whenever sqlerror exit 1
                set echo on ver off trimspool on
                spool tdb_BHE.log
                COLUMN process_date NEW_VALUE process_date
                SELECT TO_CHAR(SYSDATE, 'YYYYMMDD') process_date FROM DUAL;
            """)
        for key, table_cnf in tables_cnf.items():
            cnd = table_cnf["conds"][0]
            ref_tables = cnd["cnf_ref_tables"]
            added_conds = [("A", cond) for cond in table_cnf["conds"]]
            referencing_tables = table_cnf["referencing_tables"]
            if ref_tables:
                for ref_table in ref_tables.split(","):
                    ref_table = ref_table.strip()
                    if "." in ref_table: ref_owner, ref_table = ref_table.split(".")
                    else: ref_owner = row["cnf_prod_owner"]
                    ref_table, alias = ref_table.split(" ")
                    ref_conds = tables_cnf[(ref_owner, ref_table)]["conds"]
                    added_conds.extend([(alias, cond) for cond in ref_conds])
            for al, cd in added_conds:
                cond_list = []
                pld_expr = nvl(cd["cnf_purge_limit_date_expr"], "")
                addtl_expr = nvl(cd["cnf_additional_expr"], "")
                mkp = nvl(cd["cnf_months_keep_prod"], 9999999999999)
                if addtl_expr:
                    cond_list.append(f"({" ".join(addtl_expr.replace("@", f"{al}.").splitlines())})")
                if pld_expr:
                    cond_list.append(f"({pld_expr.replace("@", f"{al}.")} < add_months(l_process_date,-{mkp}))")
                cd["cond_expr"] = " and ".join(cond_list)
            cnf_months_keep_hist_max = max(cd.get("cnf_months_keep_hist", 0) or 0 for al, cd in added_conds)
            where_expr = "\n        or ".join([f"({cd["cond_expr"]})" for al, cd in added_conds if cd["cond_expr"]])
            other_cols_exprs = [f"{cd["cnf_purge_limit_date_expr"].replace("@", f"{al}.")} tdb_date_{al}{cd["cnf_id"]}"
                for al, cd in added_conds if cd["cnf_purge_limit_date_expr"] and al != "A"] if use_added_cols else []
            other_cols_alias = [f"tdb_date_{al}{cd["cnf_id"]}"
                for al, cd in added_conds if cd["cnf_purge_limit_date_expr"] and al != "A"] if use_added_cols else []
            cnf_table_name, cnf_prod_owner, cnf_hist_owner = cnd["cnf_table_name"], cnd["cnf_prod_owner"], cnd["cnf_hist_owner"]
            cnf_join_expr, cnf_prod_orphan_purge, cnf_hint_expr = cnd["cnf_join_expr"], cnd["cnf_prod_orphan_purge"], cnd["cnf_hint_expr"]
            cursor.execute("""select lower(column_name) column_name
                from all_tab_columns
                where table_name = upper(:1) and owner = upper(:2) and column_id is not null
                order by column_id""", (cnf_table_name, cnf_prod_owner))
            table_columns = [row[0] for row in cursor.fetchall()]
            join_expr = cnf_join_expr.replace("@", "left outer" if cnf_prod_orphan_purge == "Y" else "inner") if cnf_join_expr else ""
            query_expr = f"""from {cnf_table_name.lower()} A{f"""
            {join_expr}""" if join_expr else ""}{f"""
            where {where_expr}""" if where_expr else ""}"""
            if cnd["cnf_long_cols"] and cnd["cnf_has_lob"] != "N":
                long_cols = [c.strip().lower() for c in cnd["cnf_long_cols"].split(",")]
                table_columns = list(set([col.lower for col in table_columns]) - set(long_cols))
            plsql = f"""declare
                    l_prod_owner varchar2(50):= '{cnf_prod_owner}';
                    l_hist_owner varchar2(50):= '{cnf_hist_owner}';
                    l_action varchar2(10):= '{action}';
                    l_mode varchar2(10):= '{mode}';
                    l_message varchar2(200) := case when l_mode = 'ALL' then null else 'QUERY ONLY' end;
                    l_table_name varchar2(50):= '{cnf_table_name}';
                    l_process_date date := to_date('{process_date}', 'YYYYMMDD');
                    l_referencing_tables t_referencing_tables := t_referencing_tables({", ".join([f"'{rt[0]+"."+rt[1]}'" for rt in referencing_tables])});
                    l_process_start date;
                    l_record_count pls_integer := 0;
                    l_plsql clob := {"null" if not do_process_tables else ":plsql_code"};
                    l_sqlcode number := null;
                    l_out_message varchar2(200) := null;{f"""
                    l_chunk_size pls_integer := {chunk_size};
                    l_chunk_start date;
                    cursor c_records is 
                        select /*+ {cnf_hint_expr} */ A.rowid,
                        {", ".join([lde for lde in ["A.*"]+other_cols_exprs])}
                        {query_expr};
                    type t_records is table of c_records%rowtype index by pls_integer;
                    r_rec t_records;""" if cnd["cnf_has_lob"] == "N" else ""}
                begin
                    l_process_start := sysdate;
                    check_save_status(l_prod_owner, l_table_name, l_process_date, l_action, 'TSTART', l_process_start, null, null, l_message, 0, l_plsql, l_sqlcode, l_out_message);
                    if l_sqlcode is not null then
                        raise_application_error(l_sqlcode, l_out_message);
                    end if;
                    check_referencing_tables(l_referencing_tables, l_process_date);
                    commit;{f"""
                    open c_records;
                    loop
                        l_chunk_start := sysdate;
                        check_save_status(l_prod_owner, l_table_name, l_process_date, l_action, 'CSTART', null, l_chunk_start, null, l_message, 0, null, l_sqlcode, l_out_message);
                        fetch c_records bulk collect into r_rec limit l_chunk_size;
                        if r_rec.count > 0 then
                            if (l_mode = 'ALL') then
                                {f"""for i in 1 .. r_rec.count loop
                                    insert into {cnf_table_name.lower()}@hist ({", ".join([col.lower() for col in table_columns+other_cols_alias])})
                                    values ({", ".join([f"r_rec(i).{col.lower()}" for col in table_columns+other_cols_alias])});
                                end loop;""" if action == "MANT_PROD" and cnf_months_keep_hist_max>0 else ""}
                                forall i in 1 .. r_rec.count
                                    delete from {cnf_table_name.lower()} where rowid = r_rec(i).rowid;
                            end if;
                            l_record_count := l_record_count + r_rec.count;
                            check_save_status(l_prod_owner, l_table_name, l_process_date, l_action, 'CEND', null, l_chunk_start, sysdate, l_message, r_rec.count, null, l_sqlcode, l_out_message);
                            commit;
                        else
                            exit;
                        end if;
                    end loop;
                    close c_records;""" if cnd["cnf_has_lob"] == "N" else f"""
                    if (l_mode = 'ALL') then
                        insert into {cnf_table_name.lower()}@hist ({", ".join([col.lower() for col in table_columns+other_cols_alias])})
                        select /*+ {cnf_hint_expr} */
                        {", ".join([f"a.{col.lower()}" for col in table_columns]+[col.lower() for col in other_cols_exprs])}
                        {query_expr};
                        delete from {cnf_table_name.lower()} where rowid in
                        (select /*+ {cnf_hint_expr} */ a.rowid
                        {query_expr});
                        l_record_count := sql%rowcount;
                    else
                        select /*+ {cnf_hint_expr} */ count(*) into l_record_count
                        {query_expr};
                    end if;"""}
                    check_save_status(l_prod_owner, l_table_name, l_process_date, l_action, 'TEND', l_process_start, null, sysdate, l_message, l_record_count, null, l_sqlcode, l_out_message);
                    commit;
                exception
                when others then
                    rollback;
                    check_save_status(l_prod_owner, l_table_name, l_process_date, l_action, 'ERROR', l_process_start, null, sysdate, sqlerrm, l_record_count, null, l_sqlcode, l_out_message);
                    commit;
                    if l_sqlcode not in (-20003) then
                        raise;
                    end if;
                end;"""
            if not do_process_tables:
                print(f"rem table: {cnf_table_name}")
                print(plsql)
                print("/")
            tables_cnf[key]["plsql"] = plsql
        if not do_process_tables:
            print("""spool off
    exit 0
    """)
        else:
            process_tables(tables_cnf, process_date, connection)
    except oracledb.DatabaseError as e:
        error, = e.args
        logger.critical(f"Database error:", exc_info=True)
    except Exception as e:
        logger.critical(f"Error:", exc_info=True)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
    
if __name__ == "__main__":
    run_cli()