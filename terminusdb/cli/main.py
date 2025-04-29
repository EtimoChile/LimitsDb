import logging
import datetime
import oracledb
from concurrent.futures import ProcessPoolExecutor, as_completed
from terminusdb.db.oracle.plsql_generator import generate_plsql_block
from terminusdb.core.config import DEFAULT_CONFIG, Config
from terminusdb.core.utils import get_logger, load_yaml_config, merge_configs, nvl, configure_logger, parse_args, reconfigure_logger

configure_logger(level="WARNING")

def get_rows_processed(config, owner, table_name, process_date, conn):
    logger = get_logger()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ctl_rows_processed, to_char(ctl_process_date,'YYYYMMDD') ctl_process_date, ctl_status FROM tdb_ctl
            WHERE ctl_owner = :1 AND ctl_table_name = :2
        """, [owner, table_name])
        result = cursor.fetchone()
        rows_processed = 0
        if result and result[1] == process_date:
            rows_processed = result[0]
        else:
            rows_processed = 0
        return rows_processed
    except Exception as e:
        logger.critical(f"Unexpected error in process_table for {owner}.{table_name}:", exc_info=True)
        return 0
    finally:
        if cursor:
            cursor.close()

def save_error_status(config, owner, table_name, process_date, process_start, message, plsql_code, conn):
    logger = get_logger()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT sysdate FROM dual")
        process_end, = cursor.fetchone()
        sqlcode = cursor.var(oracledb.NUMBER)
        out_message = cursor.var(oracledb.STRING)
        process_date = datetime.datetime.strptime(process_date, "%Y%m%d").date()
        cursor.callproc('check_save_status', [ owner, table_name, process_date,
            config.action, 'ERROR', process_start, None, process_end, message, 0, plsql_code, sqlcode, out_message ])
        conn.commit()
    except Exception as e:
        logger.critical(f"Unexpected error in process_table for {owner}.{table_name}:", exc_info=True)
        return 0
    finally:
        if cursor:
            cursor.close()

def process_table(config, owner, table_name, plsql_code, referencing_tables, process_date):
    logger = get_logger()
    conn = cursor = None
    logger.info(f"Processing table {owner}.{table_name}...")
    prev_rows_processed = 0
    try:
        conn = oracledb.connect(user=config.user, password=config.password, dsn=config.dsn)
        logger.info(f"Connected to {config.dsn} as {config.user}")
        cursor = conn.cursor()
        cursor.execute("SELECT sysdate FROM dual")
        process_start, = cursor.fetchone()
        logger.info(f"Executing ILM for {owner}.{table_name}...")
        prev_rows_processed = get_rows_processed(owner, table_name, process_date, conn)
        cursor.setinputsizes(plsql_code=oracledb.CLOB)
        cursor.execute(plsql_code, { "plsql_code": plsql_code })
        rows_processed = get_rows_processed(owner, table_name, process_date, conn)
        return (owner, table_name, 'TEND', prev_rows_processed, rows_processed, None, None)
    except oracledb.DatabaseError as e:
        error, = e.args
        try:
            rows_processed = get_rows_processed(owner, table_name, process_date, conn)
            save_error_status(config, owner, table_name, process_date, process_start, error.message, plsql_code, conn)
        except Exception as e:
            logger.critical(f"Error getting rows processed for {owner}.{table_name}:", exc_info=True)
            rows_processed = 0
        return (owner, table_name, 'ERROR', prev_rows_processed, rows_processed, error.code, error.message)
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def all_status_tend(config, tables_cnf, process_date, conn):
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

def print_tables(config, tables_cnf):
    print("""


whenever oserror exit 1
whenever sqlerror exit 1
set echo on ver off trimspool on
spool tdb_BHE.log
COLUMN process_date NEW_VALUE process_date
SELECT TO_CHAR(SYSDATE, 'YYYYMMDD') process_date FROM DUAL;
    """)
    while True:
        cycle_printed = False
        for (owner, table_name), table_info in tables_cnf.items():
            cd = tables_cnf[(owner, table_name)]["conds"][0]
            if cd["ctl_status"] in ["PEND"]:
                continue
            referencing_tables = table_info['referencing_tables']
            all_referencing_tables_ready = True
            if referencing_tables:
                for ref_owner, ref_table in referencing_tables:
                    cdr = tables_cnf[(ref_owner, ref_table)]["conds"][0]
                    if cdr["ctl_status"] != "PEND":
                        all_referencing_tables_ready = False
                        break # referencing table not PEND status
            if not all_referencing_tables_ready:
                continue
            print(f"rem table: {owner}.{table_name}")
            print(table_info["plsql"])
            print("/")
            cd["ctl_status"] = "PEND"
            cycle_printed = True
        if not cycle_printed:
            break  # There are no processes to launch, so go to waitting some process to end
    print("""
spool off
exit 0
""")

def process_tables(config, tables_cnf, process_date, connection):
    logger = get_logger()
    processes = []
    active_tables = set()
    with ProcessPoolExecutor(max_workers=config.parallel_max) as executor:
        process_launched = False
        while True:
            # While there are space in the pool, try to launch new processes
            while len(processes) < config.parallel_max:
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
                        process_table, config, owner, table_name, table_info['plsql'],
                        referencing_tables, process_date, config
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
                    owner, table_name, status, prev_rows_processed, rows_processed, sqlcode, message = completed_future.result()
                    cdr = tables_cnf[(owner, table_name)]["conds"][0]
                    if status == 'ERROR' and rows_processed == prev_rows_processed:
                        status = 'SKIPPED'
                    logger.info(f"Table {owner}.{table_name} finished with status {status} and {rows_processed - prev_rows_processed} rows processed.")
                    cdr["ctl_status"] = status
                    if status == 'ERROR':
                        logger.error(f"  Error {sqlcode}: {message}")
                    elif status == 'SKIPPED':
                        logger.error(f"  Error {sqlcode}: {message}")
                    active_tables.remove((owner, table_name))
                    processes.remove(completed_future)
                    break
            else:
                break
    if all_status_tend(config, tables_cnf, process_date, connection):
        if process_launched:
            logger.info("All tables processed successfully.")
        else:
            logger.info("No tables to process.")
        exit(0)
    else:
        logger.error("Some tables did not finish successfully.")
        exit(1)

def run_cli():
    logger = get_logger()
    args = parse_args()
    file_config = load_yaml_config(args.config_file)
    raw_config = merge_configs(DEFAULT_CONFIG, file_config, args)
    config = Config(raw_config)
    reconfigure_logger(level=config.log_level)
    logger.debug(f"Default Configuration: {DEFAULT_CONFIG}")
    logger.debug(f"Args Configuration: {args}")
    logger.debug(f"Configuration: {config.as_dict()}")
    try:
        connection = oracledb.connect(user=config.user, password=config.password, dsn=config.dsn)
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
        if config.print_process:
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
                for al, cd in added_conds if cd["cnf_purge_limit_date_expr"] and al != "A"] if config.use_added_cols else []
            other_cols_alias = [f"tdb_date_{al}{cd["cnf_id"]}"
                for al, cd in added_conds if cd["cnf_purge_limit_date_expr"] and al != "A"] if config.use_added_cols else []
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
            has_lob = cnd["cnf_has_lob"] != "N"
            if cnd["cnf_long_cols"] and has_lob:
                long_cols = [c.strip().lower() for c in cnd["cnf_long_cols"].split(",")]
                table_columns = list(set([col.lower for col in table_columns]) - set(long_cols))
            plsql = generate_plsql_block(config, cnf_prod_owner, cnf_hist_owner, cnf_table_name, process_date, cnf_hint_expr,
                query_expr, table_columns, other_cols_exprs, other_cols_alias, referencing_tables, has_lob, cnf_months_keep_hist_max)
            tables_cnf[key]["plsql"] = plsql
        if config.print_process:
            print_tables(config, tables_cnf)
        else:
            process_tables(config, tables_cnf, process_date, connection)
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