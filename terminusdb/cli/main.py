from typing import Any, Dict, List, Tuple, Optional
from concurrent.futures import ProcessPoolExecutor, Future, as_completed
from terminusdb.db.engine_loader import get_db_engine
from terminusdb.core.yml_loader import load_rows_from_yaml
from terminusdb.core.config import DEFAULT_CONFIG
from terminusdb.core.utils import get_logger, Config, load_yaml_config, merge_configs, nvl, configure_logger, parse_args, reconfigure_logger
from terminusdb.db.engines import DatabaseEngine  # Import get_engine from the appropriate module

configure_logger(level="WARNING")

def process_table(config: Config, owner: str, table_name: str, plsql_code: str, referencing_tables: List[Tuple[str, str]], process_date: str) -> Tuple[str, str, str, int, int, Optional[int], Optional[str]]:
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
            engine.sp_run(conn, process_date, owner, table_name)
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

def print_tables(config: Config, tables_cnf: Dict[Tuple[str, str], Dict[str, Any]]) -> None:
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

def process_tables(config: Config, tables_cnf: Dict[Tuple[str, str], Dict[str, Any]], process_date: str, engine: DatabaseEngine, connection: Any) -> None:
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
                        logger.debug(f"Table {owner}.{table_name} is waiting for referencing tables to finish...")
                        continue
                    logger.info(f"Launching background process for {owner}.{table_name}...")
                    future = executor.submit(
                        process_table, config, owner, table_name, table_info['plsql'],
                        referencing_tables, process_date
                    )
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

def run_cli() -> None:
    logger = get_logger()
    args = parse_args()
    file_config = load_yaml_config(args.config_file)
    raw_config = merge_configs(DEFAULT_CONFIG, file_config, args)
    config = Config(raw_config)
    reconfigure_logger(level=config.log_level)
    connection: Any = None
    engine: Optional[DatabaseEngine] = None
    try:
        engine = get_db_engine(config.db_engine)
        connection = engine.get_connection(config)
        process_date = engine.get_system_date(connection).strftime('%Y%m%d')
        if hasattr(config, "tdb_config_file") and config.tdb_config_file:
            tdb_conf_rows = load_rows_from_yaml(config.tdb_config_file)
        else:
            tdb_conf_rows = engine.load_config(connection)
        tdb_ctl_status_rows = engine.get_status(connection, process_date)
        if config.print_process:
            process_date = "&process_date"
        tables_cnf: Dict[Tuple[str, str], Dict[str, Any]] = {}
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
        for tdb_ctl_row in tdb_ctl_status_rows:
            key = (tdb_ctl_row["ctl_prod_owner"], tdb_ctl_row["ctl_table_name"])
            if key in tables_cnf:
                for cond in tables_cnf[key]["conds"]:
                    cond["ctl_status"] = tdb_ctl_row["ctl_status"]
        for key, table_cnf in tables_cnf.items():
            cnd = table_cnf["conds"][0]
            ref_tables = cnd["cnf_ref_tables"]
            added_conds = [("A", cond) for cond in table_cnf["conds"]]
            referencing_tables = table_cnf["referencing_tables"]
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
            cnf_months_keep_hist_max = max(cd.get("cnf_months_keep_hist", 0) or 0 for _, cd in added_conds)
            where_expr = "\n        or ".join([f"({cd['cond_expr']})" for _, cd in added_conds if cd["cond_expr"]])
            other_cols_exprs = [f"{cd['cnf_purge_limit_date_expr'].replace('@', f'{al}.')} tdb_date_{al}{cd['cnf_id']}"
                for al, cd in added_conds if cd["cnf_purge_limit_date_expr"] and al != "A"] if config.use_added_cols else []
            other_cols_alias = [f"tdb_date_{al}{cd['cnf_id']}"
                for al, cd in added_conds if cd["cnf_purge_limit_date_expr"] and al != "A"] if config.use_added_cols else []
            cnf_table_name, cnf_prod_owner, cnf_hist_owner = cnd["cnf_table_name"], cnd["cnf_prod_owner"], cnd["cnf_hist_owner"]
            cnf_join_expr, cnf_prod_orphan_purge, cnf_hint_expr = cnd["cnf_join_expr"], cnd["cnf_prod_orphan_purge"], cnd["cnf_hint_expr"]
            table_columns = engine.get_table_columns(connection, cnf_table_name, cnf_prod_owner)
            join_expr = cnf_join_expr.replace("@", "left outer" if cnf_prod_orphan_purge == "Y" else "inner") if cnf_join_expr else ""
            query_expr = f"""from {cnf_table_name.lower()} A{f"""
            {join_expr}""" if join_expr else ""}{f"""
            where {where_expr}""" if where_expr else ""}"""
            has_lob = cnd["cnf_has_lob"] != "N"
            if cnd["cnf_long_cols"] and has_lob:
                long_cols = [c.strip().lower() for c in cnd["cnf_long_cols"].split(",")]
                table_columns = list(set([col.lower() for col in table_columns]) - set(long_cols))
            sql_block = engine.generate_proc(config, cnf_prod_owner, cnf_hist_owner, cnf_table_name, process_date, cnf_hint_expr,
                query_expr, table_columns, other_cols_exprs, other_cols_alias, referencing_tables, has_lob, cnf_months_keep_hist_max)
            tables_cnf[key]["sql_block"] = sql_block
        if config.print_process:
            print_tables(config, tables_cnf)
        else:
            process_tables(config, tables_cnf, process_date, engine, connection)
    except Exception as e:
        logger.critical(f"Error:", exc_info=True)
    finally:
        if engine and connection:
            engine.close_connection(connection)

if __name__ == "__main__":
    run_cli()