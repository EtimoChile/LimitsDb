from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from operator import attrgetter
from typing import Any, Literal, TypedDict

from limitsdb.core.ldb_ilm_config import load_rows_from_yaml, resolve_and_load_ilm_rows
from limitsdb.core.ldb_logger import configure_logger, get_logger, reconfigure_logger
from limitsdb.core.ldb_params_config import Config
from limitsdb.core.ldb_status import Status
from limitsdb.core.ldb_utils import get_effective_credentials, max_ignore_none, nvl
from limitsdb.db.ldb_engine_loader import get_db_engine
from limitsdb.db.ldb_engines import ColumnDefinition, DatabaseEngine, TableDefinition

logger = get_logger("runner")


class _OtherColumn(TypedDict):
    name: str
    expr: str
    metadata: ColumnDefinition


WorkerResult = tuple[str, str, str, int, int, int | None, str | None]


def _exception_details(exc: Exception) -> tuple[int | None, str]:
    """Extract stable error details without assuming an Oracle exception shape."""
    detail: Any = exc.args[0] if len(exc.args) == 1 else exc
    code = getattr(detail, "code", None)
    message = getattr(detail, "message", None)
    if not isinstance(code, int):
        code = None
    if not isinstance(message, str) or not message:
        message = str(exc) or type(exc).__name__
    return code, message


def _credentials_present(config: Config, *, admin: bool, env: Literal["SOURCE", "HISTORY"] | None) -> bool:
    try:
        get_effective_credentials(config, admin=admin, env=env)
        return True
    except ValueError:
        return False


def _open_connection(
    config: Config, engine: DatabaseEngine, *, admin: bool, env: Literal["SOURCE", "HISTORY"] | None, description: str
) -> Any | None:
    try:
        user, _, dsn = get_effective_credentials(config, admin=admin, env=env)
    except ValueError as exc:
        logger.error("%s: %s", description, exc)
        return None
    conn: Any | None = None
    try:
        conn = engine.get_connection(config, admin=admin, env=env)
        engine.get_system_date(conn)
        logger.info("%s: connected to %s as %s", description, dsn, user)
        return conn
    except Exception:
        logger.error("%s: failed to connect to %s as %s", description, dsn, user, exc_info=True)
        if conn:
            engine.close_connection(conn)
        return None


def _load_offline_rows(config: Config) -> list[dict[str, Any]]:
    if config.ilm_config_file:
        return load_rows_from_yaml(config.ilm_config_file)
    return resolve_and_load_ilm_rows(schema=config.schema, profile=config.profile)


def _build_dependency_graph(rows: list[dict[str, Any]]) -> dict[tuple[str, str], set[tuple[str, str]]]:
    graph: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for row in rows:
        key = (row["source_owner"], row["table_name"])
        graph.setdefault(key, set())
    for row in rows:
        key = (row["source_owner"], row["table_name"])
        refs = str(row.get("referencing_tables") or "").split(",")
        for raw_ref in refs:
            ref = raw_ref.strip()
            if not ref:
                continue
            if "." in ref:
                owner_part, remainder = ref.split(".", 1)
            else:
                owner_part, remainder = row["source_owner"], ref
            parts = remainder.strip().split(" ", 1)
            if len(parts) != 2:
                raise ValueError(f"Invalid referencing_tables entry '{ref}' for {owner_part}")
            table_part = parts[0]
            ref_key = (owner_part.strip(), table_part.strip())
            if ref_key not in graph:
                raise ValueError(f"Referenced table {owner_part}.{table_part} not present in ILM configuration")
            graph[ref_key].add(key)
    return graph


def _compute_plan_layers(graph: dict[tuple[str, str], set[tuple[str, str]]]) -> list[list[tuple[str, str]]]:
    remaining = set(graph.keys())
    layers: list[list[tuple[str, str]]] = []
    while remaining:
        ready = sorted([key for key in remaining if not graph[key].intersection(remaining)])
        if not ready:
            cycle = ", ".join(f"{owner}.{table}" for owner, table in sorted(remaining))
            raise ValueError(f"Cyclic dependency detected among tables: {cycle}")
        layers.append(ready)
        for key in ready:
            remaining.remove(key)
    return layers


def _collect_privilege_targets(
    tables_conf_rows: list[dict[str, Any]], owner_key: str, engine: DatabaseEngine
) -> list[tuple[str, str]]:
    tables: set[tuple[str, str]] = set(
        [
            (engine.get_identifier_str(r[owner_key]), engine.get_identifier_str(r["table_name"]))
            for r in tables_conf_rows
        ]
    )
    return sorted(tables)


def _plan_mode(config: Config) -> int:
    rows = _load_offline_rows(config)
    if not rows:
        logger.info("No active ILM tables found for schema %s.", config.schema)
        return 0
    graph = _build_dependency_graph(rows)
    layers = _compute_plan_layers(graph)
    logger.info("ILM execution plan (PLAN mode):")
    for idx, layer in enumerate(layers, start=1):
        tables = ", ".join(f"{owner}.{table}" for owner, table in layer)
        logger.info("  Stage %d (parallel=%d): %s", idx, len(layer), tables)
    logger.info("Plan summary: %d stage(s), %d table(s).", len(layers), len(graph))
    return 0


def _validate_environment(config: Config, engine: DatabaseEngine) -> int:
    logger.info("VALIDATE mode: checking configuration and database connectivity.")
    ok = True
    connections: list[Any] = []
    primary_conn: Any | None = None
    required_specs: list[tuple[str, bool, Literal["SOURCE", "HISTORY"] | None]] = []
    optional_specs: list[tuple[str, bool, Literal["SOURCE", "HISTORY"] | None]] = []
    primary_env = "SOURCE" if config.action == "SOURCE_ILM" else "HISTORY"
    required_specs.append((f"{primary_env} runtime", False, None))
    required_specs.append((f"{primary_env} admin", True, None))
    if config.action == "SOURCE_ILM":
        required_specs.append(("HISTORY admin", True, "HISTORY"))
        optional_specs.append(("HISTORY runtime", False, "HISTORY"))
    else:
        optional_specs.append(("SOURCE runtime", False, "SOURCE"))
        optional_specs.append(("SOURCE admin", True, "SOURCE"))
    for description, admin, env in required_specs:
        desc_label = description.upper()
        conn = _open_connection(config, engine, admin=admin, env=env, description=desc_label)
        if conn is None:
            ok = False
        else:
            connections.append(conn)
            if description == f"{primary_env} runtime":
                primary_conn = conn
    for description, admin, env in optional_specs:
        if not _credentials_present(config, admin=admin, env=env):
            logger.info("%s: credentials not provided; skipping.", description.upper())
            continue
        conn = _open_connection(config, engine, admin=admin, env=env, description=description.upper())
        if conn:
            connections.append(conn)
    try:
        if primary_conn:
            process_date = engine.get_system_date(primary_conn).strftime("%Y%m%d")
            try:
                tables_config = process_tables_cnf(primary_conn, config, engine, process_date)
                logger.info("Configuration ready: %d table(s) evaluated.", len(tables_config))
                _log_where_predicates(tables_config)
            except Exception:
                logger.error("Failed to process ILM configuration.", exc_info=True)
                ok = False
        else:
            logger.error("Primary connection unavailable; skipping configuration validation.")
    finally:
        for conn in connections:
            engine.close_connection(conn)
    return 0 if ok else 1


def _clone_column_definition(name: str, template: ColumnDefinition) -> ColumnDefinition:
    return ColumnDefinition(
        name=name,
        data_type=template.data_type,
        length=template.length,
        precision=template.precision,
        scale=template.scale,
        nullable=template.nullable,
        default=template.default,
    )


def _build_history_table_definition(
    config: Config,
    engine: DatabaseEngine,
    source_connection: Any,
    table_cnf: dict[str, Any],
) -> TableDefinition:
    cnd0 = table_cnf["conds"][0]
    source_owner, history_owner, table_name = cnd0["source_owner"], cnd0["history_owner"], cnd0["table_name"]
    required_columns: list[ColumnDefinition] = sorted(
        table_cnf.get("columns_metadata", {}).values(), key=attrgetter("id")
    )
    for other_column in table_cnf.get("other_columns", []):
        required_columns.append(other_column["metadata"])
    pk_columns = list(engine.get_primary_key_columns(source_connection, source_owner, table_name, table_cnf))
    if config.add_ldb_columns:
        if "LDB_PROCESS_DATE" not in pk_columns:
            pk_columns.append("LDB_PROCESS_DATE")
    primary_key: tuple[str, ...] | None = tuple(pk_columns) if pk_columns else None
    return TableDefinition(
        owner=history_owner,
        name=table_name,
        columns=tuple(required_columns),
        primary_key=primary_key,
    )


def _ensure_history_tables(
    config: Config,
    engine: DatabaseEngine,
    source_admin_connection: Any,
    tables_config: dict[tuple[str, str], dict[str, Any]],
) -> None:
    history_admin_connection: Any | None = None
    try:
        history_admin_connection = engine.get_connection(config, admin=True, env="HISTORY")
        for table_cnf in tables_config.values():
            if table_cnf.get("skip"):
                continue
            table_def = _build_history_table_definition(config, engine, source_admin_connection, table_cnf)
            engine.ensure_table_structure(history_admin_connection, table_def, table_cnf)
    finally:
        if history_admin_connection:
            engine.close_connection(history_admin_connection)


def _initialize_worker_logger(log_level: str) -> None:
    """Ensure background processes emit logs using the configured level."""
    # ``ProcessPoolExecutor`` uses ``spawn`` on Windows, which re-imports the entry module and
    # resets the logger configuration. Re-apply the desired level so worker logs reach stdout.
    configure_logger(level=log_level)
    reconfigure_logger(level=log_level)


def _log_where_predicates(tables_config: dict[tuple[str, str], dict[str, Any]]) -> None:
    any_predicates = False
    for table_info in tables_config.values():
        query_expr = table_info.get("query_expr")
        if not query_expr:
            continue
        if not any_predicates:
            logger.info("WHERE predicates evaluated for ILM:")
            any_predicates = True
        logger.info("\n%s", query_expr)
    if not any_predicates:
        logger.info("There are no WHERE predicates calculated for ILM tables.")


def _append_unique_name(other_columns: list[_OtherColumn], other_column: _OtherColumn) -> None:
    """Appends a column to the other_columns list if its name is not already present."""
    if other_column["name"] not in {col["name"] for col in other_columns}:
        other_columns.append(other_column)


def _get_conf_rows(config: Config, engine: DatabaseEngine, connection: Any) -> list[dict[str, Any]]:
    if config.ilm_config_file:
        ldb_conf_rows = load_rows_from_yaml(config.ilm_config_file)
    else:
        ldb_conf_rows = engine.load_config(connection)
    return ldb_conf_rows


def process_table(config: Config, owner: str, table_name: str, plsql_code: str, process_date: str) -> WorkerResult:
    logger.info(f"Processing table {owner}.{table_name}...")
    prev_rows_processed = 0
    engine: DatabaseEngine | None = None
    conn: Any = None
    process_start: Any = None
    try:
        engine = get_db_engine(config.db_engine)
        conn = engine.get_connection(config)
        user, _, dsn = get_effective_credentials(config, admin=False)
        logger.info(f"Connected to {dsn} as {user}")
        process_start = engine.get_system_date(conn)
        logger.info(f"Executing ILM for {owner}.{table_name}...")
        prev_rows_processed = engine.get_rows_processed(conn, owner, table_name, process_date)
        engine.sql_block_run(conn, plsql_code)
        rows_processed = engine.get_rows_processed(conn, owner, table_name, process_date)
        return (owner, table_name, Status.TABLE_END, prev_rows_processed, rows_processed, None, None)
    except Exception as exc:
        sqlcode, message = _exception_details(exc)
        rows_processed = prev_rows_processed
        logger.error("Worker failed for %s.%s: %s", owner, table_name, message, exc_info=True)
        if engine is not None and conn is not None and process_start is not None:
            try:
                rows_processed = engine.get_rows_processed(conn, owner, table_name, process_date)
                engine.save_error_status(
                    conn, config, owner, table_name, process_date, process_start, message, plsql_code
                )
            except Exception:
                logger.critical("Failed to persist recovery evidence for %s.%s", owner, table_name, exc_info=True)
        return (owner, table_name, Status.ERROR, prev_rows_processed, rows_processed, sqlcode, message)
    finally:
        if engine is not None and conn is not None:
            engine.close_connection(conn)


def generate_script_output(config: Config, tables_config: dict[tuple[str, str], dict[str, Any]]) -> int:
    print(
        f"""
whenever oserror exit 1
whenever sqlerror exit 1
set echo on ver off trimspool on
spool ldb_{config.schema}.log
COLUMN process_date NEW_VALUE process_date
SELECT TO_CHAR(SYSDATE, 'YYYYMMDD') process_date FROM DUAL;
    """
    )
    while True:
        cycle_printed = False
        for (owner, table_name), table_info in tables_config.items():
            if table_info["skip"]:
                continue
            cd = tables_config[(owner, table_name)]["conds"][0]
            if cd["ctl_status"] in ["GENERATED"]:
                continue
            referencing_tables = table_info["referencing_tables"]
            all_referencing_tables_ready = True
            if referencing_tables:
                for ref_owner, ref_table in referencing_tables:
                    cdr = tables_config[(ref_owner, ref_table)]["conds"][0]
                    if cdr["ctl_status"] != "GENERATED":  # print end
                        all_referencing_tables_ready = False
                        break  # referencing table not GENERATED status
            if not all_referencing_tables_ready:
                continue
            print(f"rem table: {owner}.{table_name}")
            print(table_info["sql_block"])
            print("/")
            cd["ctl_status"] = "GENERATED"  # print end
            cycle_printed = True
        if not cycle_printed:
            break  # There are no processes to launch, so go to waiting some process to end
    print("\nspool off\nexit 0\n")
    return 0


def get_next_ready_table(
    tables_config: dict[tuple[str, str], dict[str, Any]], active_tables: set[tuple[str, str]]
) -> tuple[str, str, dict[str, Any]] | None:
    for (owner, table_name), table_cnf in tables_config.items():
        if table_cnf["skip"]:
            continue
        cd = tables_config[(owner, table_name)]["conds"][0]
        if cd["ctl_status"] in [Status.SKIPPED, Status.TABLE_END] or (owner, table_name) in active_tables:
            continue
        referencing_tables = table_cnf["referencing_tables"]
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


def ldb_exec_ilm(
    config: Config,
    tables_config: dict[tuple[str, str], dict[str, Any]],
    process_date: str,
    engine: DatabaseEngine,
    connection: Any,
) -> int:
    processes: list[Future[WorkerResult]] = []
    process_targets: dict[Future[WorkerResult], tuple[str, str]] = {}
    active_tables: set[tuple[str, str]] = set()
    with ProcessPoolExecutor(
        max_workers=config.parallel_max,
        initializer=_initialize_worker_logger,
        initargs=(config.log_level,),
    ) as executor:
        process_launched = False
        while True:
            # While there is space in the pool, try to launch new processes
            while len(processes) < config.parallel_max:
                cycle_launched = False
                # Search for a process to launch
                next_ready_table = get_next_ready_table(tables_config, active_tables)
                if next_ready_table:
                    owner, table_name, table_info = next_ready_table
                    logger.info(f"Launching background process for {owner}.{table_name}...")
                    future = executor.submit(
                        process_table, config, owner, table_name, table_info["sql_block"], process_date
                    )
                    processes.append(future)
                    process_targets[future] = (owner, table_name)
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
                    owner, table_name = process_targets[completed_future]
                    try:
                        owner, table_name, status, prev_rows_processed, rows_processed, sqlcode, message = (
                            completed_future.result()
                        )
                    except Exception as exc:
                        sqlcode, detail = _exception_details(exc)
                        status = Status.ERROR
                        prev_rows_processed = rows_processed = 0
                        message = f"Worker process failed: {detail}"
                        logger.error("Worker process failed for %s.%s", owner, table_name, exc_info=True)
                    logger.debug(f"Process for table {owner}.{table_name} completed with status {status}")
                    cdr = tables_config[(owner, table_name)]["conds"][0]
                    if status == Status.ERROR and rows_processed == prev_rows_processed:
                        status = Status.SKIPPED
                    logger.info(
                        f"Table {owner}.{table_name} finished with status {status} and {rows_processed - prev_rows_processed} rows processed."
                    )
                    cdr["ctl_status"] = status
                    if status == Status.ERROR:
                        logger.error(f"  Error {sqlcode}: {message}")
                    elif status == Status.SKIPPED:
                        logger.error(f"  Error {sqlcode}: {message}")
                    active_tables.remove((owner, table_name))
                    processes.remove(completed_future)
                    del process_targets[completed_future]
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


def process_tables_cnf(
    connection: Any, config: Config, engine: DatabaseEngine, process_date: str
) -> dict[tuple[str, str], dict[str, Any]]:
    logger.info("Processing table configuration...")
    ldb_ctl_status_rows: list[dict[str, Any]] = []
    ldb_conf_rows = _get_conf_rows(config, engine, connection)
    if not config.ilm_config_file:
        ldb_ctl_status_rows = engine.get_status(connection, process_date)
    tables_config: dict[tuple[str, str], dict[str, Any]] = {}
    # Populate tables_config with the raw rows and derive referencing tables from cnf_referencing_tables.
    is_source_mode = config.action == "SOURCE_ILM"
    ldb_process_date_expr, ldb_insert_date_expr = engine.get_ldb_columns_expressions()
    for ldb_cnf_row in ldb_conf_rows:
        ldb_cnf_row["source_owner"] = engine.get_identifier_str(ldb_cnf_row["source_owner"])
        ldb_cnf_row["table_name"] = engine.get_identifier_str(ldb_cnf_row["table_name"])
        ldb_cnf_row["history_owner"] = engine.get_identifier_str(ldb_cnf_row["history_owner"])
        key = (ldb_cnf_row["source_owner"], ldb_cnf_row["table_name"])
        if key not in tables_config:
            tables_config[key] = {"conds": [], "referencing_tables": []}
        tables_config[key]["conds"].append(ldb_cnf_row)
    for ldb_cnf_row in ldb_conf_rows:
        key = (ldb_cnf_row["source_owner"], ldb_cnf_row["table_name"])
        declared_references = ldb_cnf_row["referencing_tables"]
        if declared_references:
            for ref_table in declared_references.split(","):
                ref_table = ref_table.strip()
                if "." in ref_table:
                    ref_owner, ref_table = ref_table.split(".")
                else:
                    ref_owner = ldb_cnf_row["source_owner"]
                ref_table, alias = ref_table.split(" ")
                ref_owner = engine.get_identifier_str(ref_owner)
                ref_table = engine.get_identifier_str(ref_table)
                ref_key = (ref_owner, ref_table)
                if ref_key not in tables_config:
                    raise ValueError(f"Table {ref_owner}.{ref_table} not found in ldb_conf_rows")
                tables_config[ref_key]["referencing_tables"].append(key)
    if not config.generate_script:
        # Inject ctl_status values loaded from the database when resuming a run.
        for ldb_ctl_status_row in ldb_ctl_status_rows:
            key = (ldb_ctl_status_row["ctl_source_owner"], ldb_ctl_status_row["ctl_table_name"])
            if key in tables_config:
                for cond in tables_config[key]["conds"]:
                    cond["ctl_status"] = ldb_ctl_status_row["ctl_status"]
    # Collect each table's active conditions along with inherited referencing-table conditions.
    # Track every expression needed to build the WHERE clause for the combined predicate across all contributing conditions.
    # Persist the resulting predicate in cond_expr so later stages can reuse it.
    for key, table_cnf in tables_config.items():
        cnd0 = table_cnf["conds"][0]
        inherited_references: str = cnd0["referencing_tables"]
        added_conds = [("A", cond) for cond in table_cnf["conds"]]
        table_cnf["table_columns"], table_cnf["columns_metadata"] = engine.get_table_columns(
            connection, cnd0["source_owner"], cnd0["table_name"]
        )
        if inherited_references:
            for ref_table in inherited_references.split(","):
                ref_table = ref_table.strip()
                if "." in ref_table:
                    ref_owner, ref_table_name = ref_table.split(".")
                else:
                    ref_owner, ref_table_name = ldb_cnf_row["source_owner"], ref_table
                ref_table_name, alias = ref_table_name.split(" ")
                ref_owner = engine.get_identifier_str(ref_owner)
                ref_table_name = engine.get_identifier_str(ref_table_name)
                if (ref_owner, ref_table_name) not in tables_config:
                    raise ValueError(f"Table {ref_owner}.{ref_table} not found in ldb_conf_rows")
                ref_conds = tables_config[(ref_owner, ref_table_name)]["conds"]
                added_conds.extend([(alias, cond) for cond in ref_conds])
        # Build the combined WHERE expression from all contributing conditions.
        for al, cd in added_conds:
            cond_list: list[str] = []
            purge_date_expr = nvl(cd["purge_date_expr"], "")
            # Prefer the history-only filter during HISTORY_ILM runs; otherwise use the source filter.
            additional_filter_expr = " ".join(nvl(cd["additional_filter_expr"], "").splitlines())
            history_addtl_filter_expr = " ".join(
                nvl(cd["history_addtl_filter_expr"], additional_filter_expr).splitlines()
            )
            addtl_expr = additional_filter_expr if is_source_mode else history_addtl_filter_expr
            retain_months_source, retain_months_history = cd["retain_months_source"], cd["retain_months_history"]
            if retain_months_source is not None and retain_months_history is not None:
                retain_months_history += retain_months_source
            retain_months = retain_months_source if is_source_mode else retain_months_history
            if purge_date_expr and not retain_months_source:
                raise ValueError(f"retain_months_source is required when cnf_purge_date_expr is set for {key}")
            if retain_months_source and not purge_date_expr:
                raise ValueError(f"purge_date_expr is required when cnf_retain_months_source is set for {key}")
            if retain_months_history and not retain_months_source:
                raise ValueError(f"retain_months_history is required when cnf_retain_months_source is set for {key}")
            alias_prefix = al + "."
            mod_alexp_uac = alias_prefix if is_source_mode or not config.use_added_columns else "A."
            if addtl_expr:
                cond_list.append(addtl_expr.replace("@", mod_alexp_uac))
            if purge_date_expr:
                if is_source_mode or not config.use_added_columns or al == "A":
                    cond_list.append(
                        engine.get_date_condition(purge_date_expr.replace("@", alias_prefix), retain_months)
                    )
                else:
                    cond_list.append(engine.get_date_condition(f"A.ldb_date_{al}", retain_months_history))
            cd["cond_expr"] = " and ".join(cond_list)
            cd["history_addtl_filter_expr"] = addtl_expr
        # get the maximum cnf_retain_months_history from all added_conds
        table_cnf["months_keep_history_max"] = max_ignore_none([cd["retain_months_history"] for _, cd in added_conds])
        # if HISTORY_ILM and cnf_months_keep_history_max is None, skip the table
        if not is_source_mode and table_cnf["months_keep_history_max"] is None:
            table_cnf["skip"] = True
            continue
        table_cnf["skip"] = False
        # get where expression from cond_expr of all added_conds joined by " or "
        if config.action == "SOURCE_ILM" and not any(cd["cond_expr"] for _, cd in added_conds):
            raise ValueError(f"At least one condition must be specified for SOURCE_ILM on table {key}")
        where_expr = "\n   or ".join([f"({cd['cond_expr']})" for _, cd in added_conds if cd["cond_expr"]])
        other_columns: list[_OtherColumn] = []
        table_cnf["other_columns"] = other_columns
        if config.use_added_columns and is_source_mode:
            for al, cd in added_conds:
                columns_metadata = tables_config[(cd["source_owner"], cd["table_name"])]["columns_metadata"]
                if al != "A":
                    # Add the cnf_purge_date_expr to other_columns for referencing tables
                    if cd["purge_date_expr"]:
                        column_metadata = ColumnDefinition(name=f"ldb_date_{al}", data_type="date", nullable=True)
                        _append_unique_name(
                            other_columns,
                            {
                                "name": f"ldb_date_{al}",
                                "expr": nvl(cd["purge_date_expr"], "").replace("@", al + "."),
                                "metadata": column_metadata,
                            },
                        )
                    # Add columns in cnf_history_addtl_filter_expr to other_cols_exprs and other_cols_alias for referencing tables
                    for column_name in engine.get_identifiers_from_expression(cd["history_addtl_filter_expr"] or ""):
                        column_metadata = _clone_column_definition(f"{column_name}_{al}", columns_metadata[column_name])
                        other_column: _OtherColumn = {
                            "name": f"{column_name}_{al}",
                            "expr": f"{al}.{column_name}",
                            "metadata": column_metadata,
                        }
                        _append_unique_name(other_columns, other_column)
        if config.add_ldb_columns and is_source_mode:
            other_columns.append(
                {
                    "name": "LDB_PROCESS_DATE",
                    "expr": ldb_process_date_expr,
                    "metadata": ColumnDefinition(name="LDB_PROCESS_DATE", data_type="date", nullable=False),
                }
            )
            other_columns.append(
                {
                    "name": "LDB_INSERT_DATE",
                    "expr": ldb_insert_date_expr,
                    "metadata": ColumnDefinition(name="LDB_INSERT_DATE", data_type="date", nullable=False),
                }
            )
        cnf_source_owner, cnf_table_name = key
        cnf_join_expr, cnf_source_orphan_purge = cnd0["join_expr"], cnd0["source_orphan_purge"]
        # Prepare join_expr changing type of join based on cnf_source_orphan_purge
        join_expr = (
            cnf_join_expr.replace("@", "left outer" if cnf_source_orphan_purge == "Y" else "inner")
            if cnf_join_expr
            else ""
        )
        # Prepare query_expr with the cnf_table_name, join_expr and where_expr
        base_from = f"from {cnf_source_owner.lower()}.{cnf_table_name.lower()} A"
        join_clause = f"\n   {join_expr}" if join_expr else ""
        where_clause = f"\nwhere {where_expr}" if where_expr else ""
        table_cnf["query_expr"] = base_from + join_clause + where_clause
        table_columns = table_cnf["table_columns"]
        # if table has lob columns and long type columns, remove long type columns from table_columns
        if cnd0["long_columns"] and cnd0["has_lob_columns"] != "N":
            long_columns = [c.strip().lower() for c in cnd0["long_columns"].split(",")]
            table_columns = list(set([col.lower() for col in table_columns]) - set(long_columns))
        table_cnf["table_columns"] = table_columns
        # get sql_block and add it to tables_config
        sql_block = engine.generate_sql_block(config, table_cnf, process_date)
        table_cnf["sql_block"] = sql_block
    return tables_config


def ldb_run(config: Config) -> None:
    connection: Any = None
    admin_connection: Any = None
    engine: DatabaseEngine | None = None
    try:
        engine = get_db_engine(config.db_engine)
        if config.mode == "PLAN":
            rc = _plan_mode(config)
            raise SystemExit(rc)
        if config.mode == "VALIDATE":
            rc = _validate_environment(config, engine)
            raise SystemExit(rc)
        connection = engine.get_connection(config)
        table_privileges = ("SELECT", "INSERT", "UPDATE", "DELETE")
        tables_conf_rows = _get_conf_rows(config, engine, connection)
        source_tables_for_privileges = _collect_privilege_targets(tables_conf_rows, "source_owner", engine)
        if source_tables_for_privileges and _credentials_present(config, admin=True, env="SOURCE"):
            source_admin_priv_conn = engine.get_connection(config, admin=True, env="SOURCE")
            try:
                engine.ensure_table_privileges(
                    source_admin_priv_conn, config.source_role_name, source_tables_for_privileges, table_privileges
                )
            finally:
                engine.close_connection(source_admin_priv_conn)
        process_date = engine.get_system_date(connection).strftime("%Y%m%d")
        tables_config = process_tables_cnf(connection, config, engine, process_date)
        if config.generate_script:
            rc = generate_script_output(config, tables_config)
        else:
            admin_connection = engine.get_connection(config, admin=True)
            _ensure_history_tables(config, engine, admin_connection, tables_config)
            history_tables_for_privileges = _collect_privilege_targets(tables_conf_rows, "history_owner", engine)
            if history_tables_for_privileges and _credentials_present(config, admin=True, env="HISTORY"):
                history_admin_priv_conn = engine.get_connection(config, admin=True, env="HISTORY")
                try:
                    engine.ensure_table_privileges(
                        history_admin_priv_conn,
                        config.history_role_name,
                        history_tables_for_privileges,
                        table_privileges,
                    )
                finally:
                    engine.close_connection(history_admin_priv_conn)
            rc = ldb_exec_ilm(config, tables_config, process_date, engine, connection)
        raise SystemExit(rc)
    finally:
        if engine and connection:
            engine.close_connection(connection)
        if engine and admin_connection:
            engine.close_connection(admin_connection)
