from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple
from terminusdb.core.config import Config
from terminusdb.core.logger import get_logger
from terminusdb.core.utils import nvl
from terminusdb.core.yml_loader import load_rows_from_yaml
from terminusdb.db.engine_loader import get_db_engine
from terminusdb.db.engines import DatabaseEngine


def process_table(config: Config, owner: str, table_name: str, plsql_code: str, process_date: str) -> Tuple[str, str, str, int, int, Optional[int], Optional[str]]:
    logger = get_logger()
    conn: Any
    logger.info(f"Processing table {owner}.{table_name}...")
    prev_rows_processed = 0
    engine = get_db_engine(config.db_engine)
    conn: Any = None
    try:
        conn = engine.get_connection(config)
        logger.info(f"Connected to {config.dsn} as {config.user}")
        process_start = engine.get_system_date(conn)
        try:
            logger.info(f"Executing ILM for {owner}.{table_name}...")
            prev_rows_processed = engine.get_rows_processed(conn, owner, table_name, process_date)
            engine.sql_block_run(conn, plsql_code)
            rows_processed = engine.get_rows_processed(conn, owner, table_name, process_date)
            return (owner, table_name, 'TEND', prev_rows_processed, rows_processed, None, None)
        except Exception as e:
            error, = e.args
            try:
                rows_processed = engine.get_rows_processed(conn, owner, table_name, process_date)
                engine.save_error_status(conn, config, owner, table_name, process_date, process_start, error.message, plsql_code)
            except Exception as e:
                logger.critical(f"Error getting rows processed for {owner}.{table_name}:", exc_info=True)
                rows_processed = 0
            return (owner, table_name, 'ERROR', prev_rows_processed, rows_processed, error.code, error.message)
    finally:
        if conn:
            conn.close()

def tdb_print(config: Config, tables_cnf: Dict[Tuple[str, str], Dict[str, Any]]) -> None:
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
                        break  # referencing table not PEND status
            if not all_referencing_tables_ready:
                continue
            print(f"rem table: {owner}.{table_name}")
            print(table_info["sql_block"])
            print("/")
            cd["ctl_status"] = "PEND"
            cycle_printed = True
        if not cycle_printed:
            break  # There are no processes to launch, so go to waiting some process to end
    print("""
spool off
exit 0
""")

def get_next_ready_table(tables_cnf: Dict[Tuple[str, str], Dict[str, Any]], active_tables: set[Tuple[str, str]]) -> Optional[Tuple[str, str, Dict[str, Any]]]: # type: ignore
    logger = get_logger()
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
                    break  # referencing table not TEND status
        if not all_referencing_tables_ready:
            logger.debug(f"Table {owner}.{table_name} has to wait for referencing tables to finish...")
            continue
        return (owner, table_name, table_info)  # Found a table ready to process
    return None  # No tables ready to process
 
def tdb_exec_ilm(config: Config, tables_cnf: Dict[Tuple[str, str], Dict[str, Any]], process_date: str, engine: DatabaseEngine, connection: Any) -> None:
    logger = get_logger()
    processes: List[Future[Tuple[str, str, str, int, int, Optional[int], Optional[str]]]] = []
    active_tables: set[Tuple[str, str]] = set()
    with ProcessPoolExecutor(max_workers=config.parallel_max) as executor:
        process_launched = False
        while True:
            # While there is space in the pool, try to launch new processes
            while len(processes) < config.parallel_max:
                cycle_launched = False
                # Search for a process to launch
                next_ready_table = get_next_ready_table(tables_cnf, active_tables)
                if next_ready_table:
                    owner, table_name, table_info = next_ready_table
                    logger.info(f"Launching background process for {owner}.{table_name}...")
                    future = executor.submit(process_table, config, owner, table_name, table_info['sql_block'], process_date)
                    processes.append(future)
                    active_tables.add((owner, table_name))
                    table_info["conds"][0]["ctl_status"] = "TSTART"
                    cycle_launched = True
                    process_launched = True
                    break  # Launch only one process at a time
                if not cycle_launched:
                    break  # There are no processes to launch, so go to waiting some process to end
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
    if engine.all_status_tend(connection, tables_cnf, process_date):
        if process_launched:
            logger.info("All tables processed successfully.")
        else:
            logger.info("No tables to process.")
        exit(0)
    else:
        logger.error("Some tables did not finish successfully.")
        exit(1)


def process_table_cnf(connection: Any, config: Config, engine: DatabaseEngine, process_date: str) -> Dict[Tuple[str, str], Dict[str, Any]]:
    logger = get_logger()
    logger.info(f"Processing table configuration...")
    if hasattr(config, "tdb_config_file") and config.tdb_config_file:
        tdb_conf_rows = load_rows_from_yaml(config.tdb_config_file)
    else:
        tdb_conf_rows = engine.load_config(connection)
    tdb_ctl_status_rows = engine.get_status(connection, process_date)
    tables_cnf: Dict[Tuple[str, str], Dict[str, Any]] = {}
    # Populate tables_cnf with tdb_conf_rows
    # appends tdb_ctl_status_rows to tables_cnf based on cnf_prod_owner and cnf_table_name
    # and adds ctl_status to the conds in tables_cnf
    # popultes referencing_tables in tables_cnf based on cnf_ref_tables
    for tdb_cnf_row in tdb_conf_rows:
        key = (tdb_cnf_row["cnf_prod_owner"], tdb_cnf_row["cnf_table_name"])
        if key not in tables_cnf: tables_cnf[key] = {"conds": [], "referencing_tables": []}
        tables_cnf[key]["conds"].append(tdb_cnf_row)
        ref_tables = tdb_cnf_row["cnf_ref_tables"]
        if ref_tables:
            for ref_table in ref_tables.split(","):
                ref_table = ref_table.strip()
                if "." in ref_table: ref_owner, ref_table = ref_table.split(".")
                else: ref_owner = tdb_cnf_row["cnf_prod_owner"]
                ref_table, alias = ref_table.split(" ")
                ref_key = (ref_owner.upper(), ref_table.upper())
                if ref_key not in tables_cnf: tables_cnf[ref_key] = {"conds": [], "referencing_tables": []}
                tables_cnf[ref_key]["referencing_tables"].append(key)
    if not config.print_process:
        # Populate ctl_status in tables_cnf based on tdb_ctl_status_rows
        for tdb_ctl_status_row in tdb_ctl_status_rows:
            key = (tdb_ctl_status_row["ctl_owner"], tdb_ctl_status_row["ctl_table_name"])
            if key in tables_cnf:
                for cond in tables_cnf[key]["conds"]:
                    cond["ctl_status"] = tdb_ctl_status_row["ctl_status"]
    # Populate added_conds with own table conds and referencing table conds
    # Populate cond_list with expressions captured from cnf_purge_limit_date_expr and cnf_additional_expr of all conds acumulated in added_conds
    # Populate cond_expr with the cond_list expressions joined by " and "
    for key, table_cnf in tables_cnf.items():
        cnd0 = table_cnf["conds"][0]
        ref_tables = cnd0["cnf_ref_tables"]
        added_conds = [("A", cond) for cond in table_cnf["conds"]]
        if ref_tables:
            for ref_table in ref_tables.split(","):
                ref_table = ref_table.strip()
                if "." in ref_table: ref_owner, ref_table = ref_table.split(".")
                else: ref_owner = tdb_cnf_row["cnf_prod_owner"] # type: ignore
                ref_table, alias = ref_table.split(" ")
                ref_conds = tables_cnf[(ref_owner, ref_table)]["conds"]
                added_conds.extend([(alias, cond) for cond in ref_conds])
        for al, cd in added_conds:
            cond_list: List[str] = []
            pld_expr = nvl(cd["cnf_purge_limit_date_expr"], "")
            addtl_expr = nvl(cd["cnf_additional_expr"], "")
            mkp = nvl(cd["cnf_months_keep_prod"], 9999999999999)
            if addtl_expr:
                cond_list.append(f"({" ".join(addtl_expr.replace("@", al+".").splitlines())})") # type: ignore
            if pld_expr:
                cond_list.append(f"({pld_expr.replace("@", al+".")} < add_months(l_process_date,-{mkp}))") # type: ignore
            cd["cond_expr"] = " and ".join(cond_list)
        # get the maximum cnf_months_keep_hist from all added_conds
        table_cnf["months_keep_hist_max"] = max(cd.get("cnf_months_keep_hist", 0) or 0 for _, cd in added_conds)
        # get where expression from cond_expr of all added_conds joined by " or "
        where_expr = "\n        or ".join([f"({cd['cond_expr']})" for _, cd in added_conds if cd["cond_expr"]])
        # get columns expression to be added to historical table from cnf_purge_limit_date_expr of all added_conds
        table_cnf["other_cols_exprs"] = [f"{cd['cnf_purge_limit_date_expr'].replace('@', f'{al}.')} tdb_date_{al}{cd['cnf_id']}"
            for al, cd in added_conds if cd["cnf_purge_limit_date_expr"] and al != "A"] if config.use_added_cols else []
        # get columns allias to be added to historical table from cnf_purge_limit_date_expr of all added_conds
        table_cnf["other_cols_alias"] = [f"tdb_date_{al}{cd['cnf_id']}"
            for al, cd in added_conds if cd["cnf_purge_limit_date_expr"] and al != "A"] if config.use_added_cols else []
        cnf_prod_owner, cnf_table_name = key
        cnf_join_expr, cnf_prod_orphan_purge = cnd0["cnf_join_expr"], cnd0["cnf_prod_orphan_purge"]
        # get the columns names from DB for cnf_table_name and cnf_prod_owner
        table_columns = engine.get_table_columns(connection, cnf_prod_owner, cnf_table_name)
        # Prepare join_expr changing type of join based on cnf_prod_orphan_purge
        join_expr = cnf_join_expr.replace("@", "left outer" if cnf_prod_orphan_purge == "Y" else "inner") if cnf_join_expr else ""
        # Prepare query_expr with the cnf_table_name, join_expr and where_expr
        table_cnf["query_expr"] = f"""from {cnf_prod_owner.lower()}.{cnf_table_name.lower()} A{f"""
        {join_expr}""" if join_expr else ""}{f"""
        where {where_expr}""" if where_expr else ""}"""
        # if table has lob columns and long type columns, remove long type columns from table_columns
        if cnd0["cnf_long_cols"] and cnd0["cnf_has_lob"] != "N":
            long_cols = [c.strip().lower() for c in cnd0["cnf_long_cols"].split(",")]
            table_columns = list(set([col.lower() for col in table_columns]) - set(long_cols))
        table_cnf["table_columns"] = table_columns
        # get sql_block and add it to tables_cnf
        sql_block = engine.generate_sql_block(config, table_cnf, process_date)
        table_cnf["sql_block"] = sql_block
    return tables_cnf


def tdb_run(config: Config) -> None:
    logger = get_logger()
    connection: Any = None
    engine: Optional[DatabaseEngine] = None
    try:
        engine = get_db_engine(config.db_engine)
        connection = engine.get_connection(config)
        process_date = engine.get_system_date(connection).strftime('%Y%m%d')
        tables_cnf = process_table_cnf(connection, config, engine, process_date)
        if config.print_process:
            tdb_print(config, tables_cnf)
        else:
            tdb_exec_ilm(config, tables_cnf, process_date, engine, connection)
    except Exception:
        logger.critical(f"Error:", exc_info=True)
    finally:
        if engine and connection:
            engine.close_connection(connection)