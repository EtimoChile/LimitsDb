from concurrent.futures import Future, ProcessPoolExecutor, as_completed
import re
from typing import Any, Dict, List, Optional, Tuple
from terminusdb.core.tdb_params_config import Config
from terminusdb.core.tdb_logger import get_logger
from terminusdb.core.tdb_utils import get_effective_credentials, max_ignore_none, nvl
from terminusdb.core.tdb_ilm_config import load_rows_from_yaml
from terminusdb.db.tdb_engine_loader import get_db_engine
from terminusdb.db.tdb_engines import DatabaseEngine
from terminusdb.core.tdb_status import Status
logger = get_logger("runner")

def process_table(config: Config, owner: str, table_name: str, plsql_code: str, process_date: str) -> Tuple[str, str, str, int, int, Optional[int], Optional[str]]:
    conn: Any
    logger.info(f"Processing table {owner}.{table_name}...")
    prev_rows_processed = 0
    engine = get_db_engine(config.db_engine)
    conn: Any = None
    try:
        conn = engine.get_connection(config)
        user, _, dsn = get_effective_credentials(config, admin=False)
        logger.info(f"Connected to {dsn} as {user}")
        process_start = engine.get_system_date(conn)
        try:
            logger.info(f"Executing ILM for {owner}.{table_name}...")
            prev_rows_processed = engine.get_rows_processed(conn, owner, table_name, process_date)
            engine.sql_block_run(conn, plsql_code)
            rows_processed = engine.get_rows_processed(conn, owner, table_name, process_date)
            return (owner, table_name, Status.TABLE_END, prev_rows_processed, rows_processed, None, None)
        except Exception as e:
            error, = e.args
            try:
                rows_processed = engine.get_rows_processed(conn, owner, table_name, process_date)
                engine.save_error_status(conn, config, owner, table_name, process_date, process_start, error.message, plsql_code)
            except Exception as e:
                logger.critical(f"Error getting rows processed for {owner}.{table_name}:", exc_info=True)
                rows_processed = 0
            return (owner, table_name, Status.ERROR, prev_rows_processed, rows_processed, error.code, error.message)
    finally:
        if conn:
            conn.close()

def generate_script_output(config: Config, tables_config: Dict[Tuple[str, str], Dict[str, Any]]) -> int:
    print(f"""
whenever oserror exit 1
whenever sqlerror exit 1
set echo on ver off trimspool on
spool tdb_{config.schema}.log
COLUMN process_date NEW_VALUE process_date
SELECT TO_CHAR(SYSDATE, 'YYYYMMDD') process_date FROM DUAL;
    """)
    while True:
        cycle_printed = False
        for (owner, table_name), table_info in tables_config.items():
            if table_info["skip"]: continue
            cd = tables_config[(owner, table_name)]["conds"][0]
            if cd["ctl_status"] in ["GENERATED"]:
                continue
            referencing_tables = table_info['referencing_tables']
            all_referencing_tables_ready = True
            if referencing_tables:
                for ref_owner, ref_table in referencing_tables:
                    cdr = tables_config[(ref_owner, ref_table)]["conds"][0]
                    if cdr["ctl_status"] != "GENERATED": # print end
                        all_referencing_tables_ready = False
                        break  # referencing table not GENERATED status
            if not all_referencing_tables_ready:
                continue
            print(f"rem table: {owner}.{table_name}")
            print(table_info["sql_block"])
            print("/")
            cd["ctl_status"] = "GENERATED" # print end
            cycle_printed = True
        if not cycle_printed:
            break  # There are no processes to launch, so go to waiting some process to end
    print("\nspool off\nexit 0\n")
    return 0

def get_next_ready_table(tables_config: Dict[Tuple[str, str], Dict[str, Any]], active_tables: set[Tuple[str, str]]) -> Optional[Tuple[str, str, Dict[str, Any]]]: # type: ignore
    for (owner, table_name), table_cnf in tables_config.items():
        if table_cnf["skip"]: continue
        cd = tables_config[(owner, table_name)]["conds"][0]
        if cd["ctl_status"] in [Status.SKIPPED, Status.TABLE_END] or (owner, table_name) in active_tables:
            continue
        referencing_tables = table_cnf['referencing_tables']
        all_referencing_tables_ready = True
        if referencing_tables:
            for ref_owner, ref_table in referencing_tables:
                cdr = tables_config[(ref_owner, ref_table)]["conds"][0]
                if cdr["ctl_status"] != Status.TABLE_END:
                    all_referencing_tables_ready = False
                    break  # referencing table not TEND status
        if not all_referencing_tables_ready:
            logger.debug(f"Table {owner}.{table_name} has to wait for referencing tables to finish...")
            continue
        return (owner, table_name, table_cnf)  # Found a table ready to process
    return None  # No tables ready to process
 
def tdb_exec_ilm(config: Config, tables_config: Dict[Tuple[str, str], Dict[str, Any]], process_date: str, engine: DatabaseEngine, connection: Any) -> int:
    processes: List[Future[Tuple[str, str, str, int, int, Optional[int], Optional[str]]]] = []
    active_tables: set[Tuple[str, str]] = set()
    with ProcessPoolExecutor(max_workers=config.parallel_max) as executor:
        process_launched = False
        while True:
            # While there is space in the pool, try to launch new processes
            while len(processes) < config.parallel_max:
                cycle_launched = False
                # Search for a process to launch
                next_ready_table = get_next_ready_table(tables_config, active_tables)
                logger.debug(f"Next ready table: {next_ready_table}")
                if next_ready_table:
                    owner, table_name, table_info = next_ready_table
                    logger.info(f"Launching background process for {owner}.{table_name}...")
                    future = executor.submit(process_table, config, owner, table_name, table_info['sql_block'], process_date)
                    processes.append(future)
                    active_tables.add((owner, table_name))
                    table_info["conds"][0]["ctl_status"] = Status.TABLE_START
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
                    logger.debug(f"Process for table {owner}.{table_name} completed with status {status}")
                    cdr = tables_config[(owner, table_name)]["conds"][0]
                    if status == Status.ERROR and rows_processed == prev_rows_processed:
                        status = Status.SKIPPED
                    logger.info(f"Table {owner}.{table_name} finished with status {status} and {rows_processed - prev_rows_processed} rows processed.")
                    cdr["ctl_status"] = status
                    if status == Status.ERROR:
                        logger.error(f"  Error {sqlcode}: {message}")
                    elif status == Status.SKIPPED:
                        logger.error(f"  Error {sqlcode}: {message}")
                    active_tables.remove((owner, table_name))
                    processes.remove(completed_future)
                    break
            else:
                break
    if engine.all_status_tend(connection, tables_config, process_date):
        if process_launched:
            logger.info("All tables processed successfully.")
        else:
            logger.info("No tables to process.")
        return 0
    else:
        logger.error("Some tables did not finish successfully.")
        return 1


def process_table_cnf(connection: Any, config: Config, engine: DatabaseEngine, process_date: str) -> Dict[Tuple[str, str], Dict[str, Any]]:
    logger.info("Processing table configuration...")
    tdb_ctl_status_rows: List[Dict[str, Any]] = []
    if config.ilm_config_file:
        tdb_conf_rows = load_rows_from_yaml(config.ilm_config_file)
    else:
        tdb_conf_rows = engine.load_config(connection)
        tdb_ctl_status_rows = engine.get_status(connection, process_date)
    tables_config: Dict[Tuple[str, str], Dict[str, Any]] = {}
    # Populate tables_config with the raw rows and derive referencing tables from cnf_referencing_tables.
    is_source_mode = (config.action == "SOURCE_ILM")
    for tdb_cnf_row in tdb_conf_rows:
        key = (tdb_cnf_row["cnf_source_owner"], tdb_cnf_row["cnf_table_name"])
        if key not in tables_config: tables_config[key] = {"conds": [], "referencing_tables": []}
        tables_config[key]["conds"].append(tdb_cnf_row)
    for tdb_cnf_row in tdb_conf_rows:
        key = (tdb_cnf_row["cnf_source_owner"], tdb_cnf_row["cnf_table_name"])
        referencing_tables = tdb_cnf_row["cnf_referencing_tables"]
        if referencing_tables:
            for ref_table in referencing_tables.split(","):
                ref_table = ref_table.strip()
                if "." in ref_table: ref_owner, ref_table = ref_table.split(".")
                else: ref_owner = tdb_cnf_row["cnf_source_owner"]
                ref_table, alias = ref_table.split(" ")
                ref_key = (ref_owner.upper(), ref_table.upper())
                if ref_key not in tables_config:
                    raise ValueError(f"Table {ref_owner}.{ref_table} not found in tdb_conf_rows")
                tables_config[ref_key]["referencing_tables"].append(key)
    if not config.generate_script:
        # Inject ctl_status values loaded from the database when resuming a run.
        for tdb_ctl_status_row in tdb_ctl_status_rows:
            key = (tdb_ctl_status_row["ctl_owner"], tdb_ctl_status_row["ctl_table_name"])
            if key in tables_config:
                for cond in tables_config[key]["conds"]:
                    cond["ctl_status"] = tdb_ctl_status_row["ctl_status"]
    # Collect each table's active conditions along with inherited referencing-table conditions.
    # Track every expression needed to build the WHERE clause for the combined predicate across all contributing conditions.
    # Persist the resulting predicate in cond_expr so later stages can reuse it.
    for key, table_cnf in tables_config.items():
        cnd0 = table_cnf["conds"][0]
        referencing_tables = cnd0["cnf_referencing_tables"]
        added_conds = [("A", cond) for cond in table_cnf["conds"]]
        if referencing_tables:
            for ref_table in referencing_tables.split(","):
                ref_table = ref_table.strip()
                if "." in ref_table: ref_owner, ref_table = ref_table.split(".")
                else: ref_owner = tdb_cnf_row["cnf_source_owner"] # type: ignore
                ref_table, alias = ref_table.split(" ")
                if (ref_owner, ref_table) not in tables_config:
                    raise ValueError(f"Table {ref_owner}.{ref_table} not found in tdb_conf_rows")
                ref_conds = tables_config[(ref_owner, ref_table)]["conds"]
                added_conds.extend([(alias, cond) for cond in ref_conds])
        for al, cd in added_conds:
            cond_list: List[str] = []
            pld_expr = nvl(cd["cnf_purge_date_expr"], "")
            # Prefer the history-only filter during HISTORY_ILM runs; otherwise use the source filter.
            addtl_source_expr = " ".join(nvl(cd["cnf_additional_filter_expr"], "").splitlines())
            addtl_history_expr = " ".join(nvl(cd["cnf_history_addtl_filter_expr"], addtl_source_expr).splitlines())
            addtl_expr = addtl_source_expr if is_source_mode else addtl_history_expr
            months_keep_src, months_keep_hist = cd["cnf_retain_months_source"], cd["cnf_retain_months_history"]
            logger.debug(f"Processing cond for table {key} alias {al}: pld_expr={pld_expr}, months_keep_src={months_keep_src}, months_keep_hist={months_keep_hist}, addtl_expr={addtl_expr}")
            if months_keep_src is not None and months_keep_hist is not None:
                months_keep_hist += months_keep_src
            months_keep = months_keep_src if is_source_mode else months_keep_hist
            if pld_expr and not months_keep_src:
                raise ValueError(f"cnf_retain_months_source is required when cnf_purge_date_expr is set for {key}")
            if months_keep_src and not pld_expr:
                raise ValueError(f"cnf_purge_date_expr is required when cnf_retain_months_source is set for {key}")
            if months_keep_hist and not months_keep_src:
                raise ValueError(f"cnf_retain_months_history is required when cnf_retain_months_source is set for {key}")
            alias_prefix = al+"."
            alias_prefix_mod = alias_prefix if is_source_mode else "A."
            mod_alexp_uac = alias_prefix if is_source_mode and config.use_added_columns else "A."
            if addtl_expr:
                if is_source_mode:
                    cond_list.append(addtl_expr.replace("@", alias_prefix_mod))
                else:
                    cond_list.append(addtl_history_expr.replace('@', mod_alexp_uac))
            if pld_expr:
                if is_source_mode or not config.use_added_columns or al == "A":
                    cond_list.append(engine.get_date_cond(pld_expr.replace("@", alias_prefix), months_keep))
                else:
                    cond_list.append(engine.get_date_cond(f"A.tdb_date_{al}", months_keep_hist))
            cd["cond_expr"] = " and ".join(cond_list)
            cd["addtl_history_expr"] = addtl_history_expr
        # get the maximum cnf_retain_months_history from all added_conds
        table_cnf["months_keep_history_max"] = max_ignore_none([cd["cnf_retain_months_history"] for _, cd in added_conds])
        # if mant_hist and cnf_months_keep_history_max is None, skip the table
        if not is_source_mode and table_cnf["months_keep_history_max"] is None:
            table_cnf["skip"] = True
            continue
        table_cnf["skip"] = False
        # get where expression from cond_expr of all added_conds joined by " or "
        where_expr = "\n   or ".join([f"({cd['cond_expr']})" for _, cd in added_conds if cd["cond_expr"]])
        table_cnf["other_cols_exprs"] = []
        table_cnf["other_cols_alias"] = []
        if config.use_added_columns:
            for al, cd in added_conds:
                if al != "A":
                    # Add the cnf_purge_date_expr to other_cols_exprs and other_cols_alias for referencing tables
                    if f"tdb_date_{al}" not in table_cnf["other_cols_alias"] and cd["cnf_purge_date_expr"]:
                        table_cnf["other_cols_exprs"].append(nvl(cd["cnf_purge_date_expr"], "").replace('@', al+".")+f" tdb_date_{al}") # type: ignore
                        table_cnf["other_cols_alias"].append(f"tdb_date_{al}") # type: ignore
                    # Add columns in cnf_history_addtl_filter_expr to other_cols_exprs and other_cols_alias for referencing tables
                    for col in [match[0] for match in re.findall(r'@("([^"]+)"|[A-Za-z_][A-Za-z0-9_]*)', cd["addtl_history_expr"])]:
                        if col not in table_cnf["other_cols_alias"]:
                            table_cnf["other_cols_exprs"].append(al+'.'+col) # type: ignore
                            table_cnf["other_cols_alias"].append(col) # type: ignore
        cnf_source_owner, cnf_table_name = key
        cnf_join_expr, cnf_source_orphan_purge = cnd0["cnf_join_expr"], cnd0["cnf_source_orphan_purge"]
        # get the columns names from DB for cnf_table_name and cnf_source_owner
        table_columns = engine.get_table_columns(connection, cnf_source_owner, cnf_table_name)
        # Prepare join_expr changing type of join based on cnf_source_orphan_purge
        join_expr = cnf_join_expr.replace("@", "left outer" if cnf_source_orphan_purge == "Y" else "inner") if cnf_join_expr else ""
        # Prepare query_expr with the cnf_table_name, join_expr and where_expr
        base_from = f"from {cnf_source_owner.lower()}.{cnf_table_name.lower()} A"
        join_clause = f"\n   {join_expr}" if join_expr else ""
        where_clause = f"\nwhere {where_expr}" if where_expr else ""
        table_cnf["query_expr"] = base_from + join_clause + where_clause
        # if table has lob columns and long type columns, remove long type columns from table_columns
        if cnd0["cnf_long_columns"] and cnd0["cnf_has_lob_columns"] != "N":
            long_columns = [c.strip().lower() for c in cnd0["cnf_long_columns"].split(",")]
            table_columns = list(set([col.lower() for col in table_columns]) - set(long_columns))
        table_cnf["table_columns"] = table_columns
        # get sql_block and add it to tables_config
        sql_block = engine.generate_sql_block(config, table_cnf, process_date)
        table_cnf["sql_block"] = sql_block
    return tables_config

def tdb_run(config: Config) -> None:
    connection: Any = None
    engine: Optional[DatabaseEngine] = None
    try:
        engine = get_db_engine(config.db_engine)
        connection = engine.get_connection(config)
        process_date = engine.get_system_date(connection).strftime('%Y%m%d')
        tables_config = process_table_cnf(connection, config, engine, process_date)
        if config.generate_script: rc = generate_script_output(config, tables_config)
        else: rc = tdb_exec_ilm(config, tables_config, process_date, engine, connection)
        raise SystemExit(rc)
    except Exception:
        logger.critical("Error:", exc_info=True)
    finally:
        if engine and connection:
            engine.close_connection(connection)