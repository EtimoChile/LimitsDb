import logging
import os
import socket
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from operator import attrgetter
from typing import Any, Literal, TypedDict, cast

from limitsdb.core.ldb_ddl import deliver_ddl
from limitsdb.core.ldb_errors import ConfigurationError
from limitsdb.core.ldb_ilm_config import IlmRule, load_rows_from_yaml
from limitsdb.core.ldb_logger import configure_logger, get_logger, reconfigure_logger
from limitsdb.core.ldb_params_config import Config
from limitsdb.core.ldb_status import Status
from limitsdb.core.ldb_utils import get_effective_credentials, max_ignore_none, nvl
from limitsdb.db.ldb_engine_loader import get_db_engine
from limitsdb.db.ldb_engines import ColumnDefinition, DatabaseEngine, DdlFailures, DdlSink, TableDefinition

logger = get_logger("runner")

# Seconds between liveness queries on the connection that holds the runner locks
_LOCK_PING_SECONDS = 30.0
# ORA-20001/2/3/5/6: the control row belongs to another run (or the rules refused this one) and stays untouched
_FOREIGN_CONTROL_CODES = (20001, 20002, 20003, 20005, 20006)


class _OtherColumn(TypedDict):
    name: str
    expr: str
    metadata: ColumnDefinition


class ProcessedTableConfig(TypedDict, total=False):
    """Mutable derived state for one table during planning and execution."""

    conds: list[IlmRule]
    referencing_tables: list[tuple[str, str]]
    table_columns: list[str]
    columns_metadata: dict[str, ColumnDefinition]
    other_columns: list[_OtherColumn]
    months_keep_history_max: int | None
    skip: bool
    query_expr: str
    sql_block: str
    metadata: Any


TablesConfig = dict[tuple[str, str], ProcessedTableConfig]


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


def _load_offline_rows(config: Config) -> list[IlmRule]:
    if config.context.ilm_config_file:
        return load_rows_from_yaml(config.context.ilm_config_file)
    schema = config.context.schema
    profile = config.context.profile
    name = f"ilm.{profile}.yml" if profile else "ilm.yml"
    raise ConfigurationError(
        f"ILM configuration file '{name}' not found for schema '{schema}'. Provide the file or set LDB_ILM_CONFIG_FILE."
    )


def _build_dependency_graph(rows: list[IlmRule]) -> dict[tuple[str, str], set[tuple[str, str]]]:
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
    tables_conf_rows: list[IlmRule], owner_key: Literal["source_owner", "history_owner"], engine: DatabaseEngine
) -> list[tuple[str, str]]:
    tables: set[tuple[str, str]] = set(
        [
            (engine.get_identifier_str(r[owner_key]), engine.get_identifier_str(r["table_name"]))
            for r in tables_conf_rows
        ]
    )
    return sorted(tables)


def _runtime_owner(config: Config, rule: IlmRule) -> str:
    """Return the schema whose rows are processed for the selected ILM action."""
    return rule["source_owner"] if config.execution.action == "SOURCE_ILM" else rule["history_owner"]


def _runtime_join_expression(config: Config, rule: IlmRule) -> str:
    """Render source joins, avoiding them when history predicates use stored derived columns."""
    join_expr = rule["join_expr"]
    if not join_expr or (config.execution.action == "HISTORY_ILM" and config.execution.use_added_columns):
        return ""
    join_type = "left outer" if rule["source_orphan_purge"] == "Y" else "inner"
    return join_expr.replace("@", join_type)


def _orphan_predicate(rule: IlmRule) -> str:
    """Return the predicate that identifies a missing referenced row."""
    if rule["source_orphan_purge"] != "Y":
        return ""
    columns = [column.strip() for column in (rule["orphan_check_column"] or "").split(",") if column.strip()]
    return " or ".join(f"{column} is null" for column in columns)


def _validate_orphan_configuration(config: Config, rule: IlmRule, key: tuple[str, str]) -> None:
    """Validate the relationship data needed to archive orphan source rows."""
    if rule["source_orphan_purge"] != "Y":
        if rule["orphan_check_column"]:
            raise ValueError(f"source_orphan_purge must be enabled when orphan_check_column is set for {key}")
        return
    missing = [
        name
        for name, value in (
            ("referencing_tables", rule["referencing_tables"]),
            ("join_expr", rule["join_expr"]),
            ("orphan_check_column", rule["orphan_check_column"]),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"{', '.join(missing)} required when source_orphan_purge is enabled for {key}")
    if not config.execution.use_added_columns:
        raise ValueError(f"use_added_columns must be enabled when source_orphan_purge is enabled for {key}")


def _relationship_date_column(alias: str) -> str:
    """Return the stable unquoted name used to persist a related table's retention date."""
    return f"LDB_DATE_{alias.upper()}"


_ORPHAN_MARKER_COLUMN = "LDB_IS_ORPHAN"


def _plan_mode(config: Config) -> int:
    rows = _load_offline_rows(config)
    if not rows:
        logger.info("No active ILM tables found for schema %s.", config.context.schema)
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
    runtime_conns: dict[str, Any] = {}
    required_specs: list[tuple[str, bool, Literal["SOURCE", "HISTORY"] | None]] = []
    optional_specs: list[tuple[str, bool, Literal["SOURCE", "HISTORY"] | None]] = []
    primary_env = "SOURCE" if config.execution.action == "SOURCE_ILM" else "HISTORY"
    required_specs.append((f"{primary_env} runtime", False, None))
    required_specs.append((f"{primary_env} admin", True, None))
    if config.execution.action == "SOURCE_ILM":
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
            if not admin:
                runtime_conns[env or primary_env] = conn
    for description, admin, env in optional_specs:
        if not _credentials_present(config, admin=admin, env=env):
            logger.info("%s: credentials not provided; skipping.", description.upper())
            continue
        conn = _open_connection(config, engine, admin=admin, env=env, description=description.upper())
        if conn:
            connections.append(conn)
            if not admin:
                runtime_conns[env or primary_env] = conn
    try:
        if primary_conn:
            process_date = engine.get_system_date(primary_conn).strftime("%Y%m%d")
            try:
                tables_config = process_tables_cnf(primary_conn, config, engine, process_date)
                logger.info("Configuration ready: %d table(s) evaluated.", len(tables_config))
                _log_where_predicates(tables_config)
                ok = _validate_privileges(config, engine, runtime_conns, tables_config) and ok
            except Exception:
                logger.error("Failed to process ILM configuration.", exc_info=True)
                ok = False
        else:
            logger.error("Primary connection unavailable; skipping configuration validation.")
    finally:
        for conn in connections:
            engine.close_connection(conn)
    return 0 if ok else 1


def _validate_privileges(
    config: Config, engine: DatabaseEngine, runtime_conns: dict[str, Any], tables_config: TablesConfig
) -> bool:
    """Report, read-only, the privileges the runtime users lack through their roles (DEC-033).

    Missing privileges on the action's own environment fail the validation; those on the other one are
    warnings, since PREPARE creates the history objects. Returns False when the validation must fail."""
    targets = {
        "SOURCE": sorted(tables_config),
        "HISTORY": sorted({(cnf["conds"][0]["history_owner"], key[1]) for key, cnf in tables_config.items()}),
    }
    primary_env = "SOURCE" if config.execution.action == "SOURCE_ILM" else "HISTORY"
    ok = True
    any_missing = False
    for env, conn in runtime_conns.items():
        level = logging.ERROR if env == primary_env else logging.WARNING
        try:
            missing = _check_role_privileges(
                config,
                engine,
                conn,
                cast(Literal["SOURCE", "HISTORY"], env),
                targets[env],
                level,
            )
        except Exception:
            logger.error("Failed to check the %s privileges of the runtime user.", env, exc_info=True)
            return False
        if missing:
            any_missing = True
        if missing and env == primary_env:
            ok = False
    if any_missing:
        (logger.warning if ok else logger.error)("Run --mode PREPARE to apply the missing privileges.")
    return ok


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
    table_cnf: ProcessedTableConfig,
) -> TableDefinition:
    cnd0 = table_cnf["conds"][0]
    history_owner, table_name = cnd0["history_owner"], cnd0["table_name"]
    metadata_owner = _runtime_owner(config, cnd0)
    required_columns: list[ColumnDefinition] = sorted(
        table_cnf.get("columns_metadata", {}).values(), key=attrgetter("id")
    )
    for other_column in table_cnf.get("other_columns", []):
        required_columns.append(other_column["metadata"])
    pk_columns = list(engine.get_primary_key_columns(source_connection, metadata_owner, table_name, table_cnf))
    if config.execution.add_ldb_columns:
        if "LDB_PROCESS_DATE" not in pk_columns:
            pk_columns.append("LDB_PROCESS_DATE")
    primary_key: tuple[str, ...] | None = tuple(pk_columns) if pk_columns else None
    return TableDefinition(
        owner=history_owner,
        name=table_name,
        columns=tuple(required_columns),
        primary_key=primary_key,
    )


_TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")


def _app_role(config: Config, env: Literal["SOURCE", "HISTORY"]) -> str:
    """Application role that the environment's tables are granted to."""
    return config.administration.source_role_name if env == "SOURCE" else config.administration.history_role_name


def _ensure_history_tables(
    config: Config,
    engine: DatabaseEngine,
    source_admin_connection: Any,
    tables_config: TablesConfig,
) -> None:
    """Ensure history tables match their required definitions.

    Behaviour by credential state:
    - Reached only in PREPARE, PREVIEW and EXECUTE: all apply DDL, since a run needs the objects in place (DEC-023, DEC-032).
    - Admin HISTORY credentials present, tables up to date: skip admin connection (DEC-020).
    - Admin HISTORY credentials present, delta detected: open admin connection and execute DDL.
    - Admin HISTORY credentials absent, tables up to date: proceed without connecting.
    - Admin HISTORY credentials absent, schema cannot be inspected: exit 1 with the cause.
    - Admin HISTORY credentials absent, delta detected: execute the DDL with the runtime
      HISTORY connection (it succeeds when that user owns the objects or holds the privileges).
      Every statement that fails is written to a file under ``ddl_pending_dir`` and the run
      exits 1 after attempting all statements, so a DBA can apply the file (DEC-021).
    """
    table_defs_and_cnf: list[tuple[Any, ProcessedTableConfig]] = []
    for table_cnf in tables_config.values():
        if table_cnf.get("skip"):
            continue
        table_def = _build_history_table_definition(config, engine, source_admin_connection, table_cnf)
        table_defs_and_cnf.append((table_def, table_cnf))
    if not table_defs_and_cnf:
        return

    admin_present = _credentials_present(config, admin=True, env="HISTORY")

    # Inspect the current state using a read-only (non-admin) HISTORY connection.
    inspect_conn: Any | None = None
    delta_statements: list[str] = []
    inspection_failed = False
    inspection_error = "no runtime HISTORY credentials are configured"
    if _credentials_present(config, admin=False, env="HISTORY"):
        try:
            inspect_conn = engine.get_connection(config, admin=False, env="HISTORY")
            for table_def, table_cnf in table_defs_and_cnf:
                engine.ensure_table_structure(inspect_conn, table_def, table_cnf, ddl_sink=delta_statements.append)
        except Exception as exc:
            logger.debug(
                "Could not inspect history schema with runtime credentials; will use admin path.",
                exc_info=True,
            )
            inspection_failed = True
            inspection_error = " ".join(str(exc.__cause__ or exc).split())
        finally:
            if inspect_conn:
                engine.close_connection(inspect_conn)
    else:
        inspection_failed = True
        logger.debug("No runtime HISTORY credentials available for pre-inspection.")

    if not admin_present:
        if inspection_failed:
            logger.error(
                "History admin credentials are absent and the history schema could not be inspected: %s. "
                "Fix the runtime HISTORY connection or privileges, then rerun.",
                inspection_error,
            )
            raise SystemExit(1)
        if not delta_statements:
            logger.info("History tables are up to date; no admin credentials needed.")
            return
        history_tables = [
            (engine.get_identifier_str(td.owner), engine.get_identifier_str(td.name)) for td, _ in table_defs_and_cnf
        ]
        _apply_history_delta_without_admin(config, engine, table_defs_and_cnf, history_tables, len(delta_statements))
        return

    # Admin credentials present.
    if not inspection_failed and not delta_statements:
        logger.info("History tables are up to date; admin connection not needed.")
        return

    history_admin_connection: Any | None = None
    try:
        history_admin_connection = engine.get_connection(config, admin=True, env="HISTORY")
        admin_connection = history_admin_connection

        def apply_structure(ddl_sink: DdlSink | None, ddl_failures: DdlFailures | None) -> None:
            # Grants for the admin path are applied afterwards by ``ensure_table_privileges``.
            for table_def, table_cnf in table_defs_and_cnf:
                engine.ensure_table_structure(
                    admin_connection, table_def, table_cnf, ddl_sink=ddl_sink, ddl_failures=ddl_failures
                )

        deliver_ddl(
            config,
            apply_structure,
            destination="execute",
            env="HISTORY",
            file_stem="history_ddl",
            description="history table DDL.",
        )
    finally:
        if history_admin_connection:
            engine.close_connection(history_admin_connection)


def _apply_history_delta_without_admin(
    config: Config,
    engine: DatabaseEngine,
    table_defs_and_cnf: list[tuple[Any, ProcessedTableConfig]],
    history_tables: list[tuple[str, str]],
    delta_size: int,
) -> None:
    """Run the history DDL delta and its grants with runtime credentials; failures go to a pending file, exit 1."""
    logger.info("History admin credentials are absent; applying %d DDL change(s) with runtime credentials.", delta_size)
    runtime_conn = engine.get_connection(config, admin=False, env="HISTORY")

    def apply_delta(ddl_sink: DdlSink | None, ddl_failures: DdlFailures | None) -> None:
        for table_def, table_cnf in table_defs_and_cnf:
            engine.ensure_table_structure(
                runtime_conn, table_def, table_cnf, ddl_sink=ddl_sink, ddl_failures=ddl_failures
            )
        engine.ensure_table_privileges(
            runtime_conn,
            _app_role(config, "HISTORY"),
            history_tables,
            _TABLE_PRIVILEGES,
            ddl_sink=ddl_sink,
            ddl_failures=ddl_failures,
        )

    try:
        deliver_ddl(
            config,
            apply_delta,
            destination="execute",
            env="HISTORY",
            file_stem="history_ddl",
            description="history DDL that ldb-run could not apply with its runtime credentials.",
        )
    finally:
        engine.close_connection(runtime_conn)


def _initialize_worker_logger(log_level: str) -> None:
    """Ensure background processes emit logs using the configured level."""
    # ``ProcessPoolExecutor`` uses ``spawn`` on Windows, which re-imports the entry module and
    # resets the logger configuration. Re-apply the desired level so worker logs reach stdout.
    configure_logger(level=log_level)
    reconfigure_logger(level=log_level)


def _log_where_predicates(tables_config: TablesConfig) -> None:
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


def process_table(
    config: Config, owner: str, table_name: str, plsql_code: str, process_date: str, run_id: str
) -> WorkerResult:
    logger.info(f"Processing table {owner}.{table_name}...")
    engine: DatabaseEngine | None = None
    conn: Any = None
    process_start: Any = None
    baseline = 0
    try:
        engine = get_db_engine(config.connections.db_engine)
        conn = engine.get_connection(config)
        user, _, dsn = get_effective_credentials(config, admin=False)
        logger.info(f"Connected to {dsn} as {user}")
        process_start = engine.get_system_date(conn)
        if config.execution.mode != "PREVIEW":
            # An incomplete EXECUTE of this process date is continued: its rows stay in the control counter
            baseline = engine.get_resumed_rows(conn, owner, table_name, process_date)
            if baseline:
                logger.info(f"Resuming {owner}.{table_name}: {baseline} rows were processed by the interrupted run.")
        logger.info(f"Executing ILM for {owner}.{table_name}...")
        engine.sql_block_run(conn, plsql_code, config.execution.mode, run_id)
        rows_processed = engine.get_rows_processed(conn, owner, table_name, process_date)
        finished = Status.TABLE_PREVIEW_END if config.execution.mode == "PREVIEW" else Status.TABLE_END
        return (owner, table_name, finished, baseline, rows_processed, None, None)
    except Exception as exc:
        sqlcode, message = _exception_details(exc)
        rows_processed = baseline
        logger.error("Worker failed for %s.%s: %s", owner, table_name, message, exc_info=True)
        owned_by_other_run = sqlcode in _FOREIGN_CONTROL_CODES
        if engine is not None and conn is not None and process_start is not None and not owned_by_other_run:
            try:
                rows_processed = engine.get_rows_processed(conn, owner, table_name, process_date)
                # The block already persisted its own failure; only one outside it (compile error, lost
                # session...) still needs the ERROR row
                if not engine.error_recorded_since(conn, owner, table_name, process_start):
                    engine.save_error_status(
                        conn,
                        config,
                        owner,
                        table_name,
                        process_date,
                        process_start,
                        message,
                        plsql_code,
                        max(rows_processed - baseline, 0),
                        run_id,
                    )
            except Exception:
                logger.critical("Failed to persist recovery evidence for %s.%s", owner, table_name, exc_info=True)
        return (owner, table_name, Status.ERROR, baseline, rows_processed, sqlcode, message)
    finally:
        if engine is not None and conn is not None:
            engine.close_connection(conn)


def _script_history_ddl(
    config: Config, engine: DatabaseEngine, source_connection: Any, tables_config: TablesConfig, ddl_sink: DdlSink
) -> None:
    """Send the DDL for the history tables to ``ddl_sink`` for the SCRIPT files; nothing is applied (DEC-024).

    The current history structure is read over the admin HISTORY connection, else the runtime one, when it works, so
    the script holds only the missing changes. Otherwise ``history_baseline`` states what to
    assume: EMPTY (no history objects, everything is created) or SOURCE (history holds the same
    objects as source, so only the history-specific differences are emitted).
    """
    table_defs_and_cnf = [
        (_build_history_table_definition(config, engine, source_connection, table_cnf), table_cnf)
        for table_cnf in tables_config.values()
        if not table_cnf.get("skip")
    ]
    # Prefer the admin HISTORY connection, which sees every object, and fall back to the runtime one.
    for admin in (True, False):
        if not _credentials_present(config, admin=admin, env="HISTORY"):
            continue
        history_connection: Any | None = None
        try:
            history_connection = engine.get_connection(config, admin=admin, env="HISTORY")
            # Collected first: a failure while reading a later table must not leave a partial delta in the sink.
            read: list[str] = []
            for table_def, table_cnf in table_defs_and_cnf:
                engine.ensure_table_structure(history_connection, table_def, table_cnf, ddl_sink=read.append)
            for statement in read:
                ddl_sink(statement)
            return
        except Exception as exc:
            logger.warning(
                "History schema could not be read with the %s connection: %s",
                "admin" if admin else "runtime",
                " ".join(str(exc.__cause__ or exc).split()),
            )
        finally:
            if history_connection:
                engine.close_connection(history_connection)
    logger.info("History schema not read; assuming baseline %s.", config.execution.history_baseline)
    for table_def, table_cnf in table_defs_and_cnf:
        if config.execution.history_baseline == "SOURCE":
            reference = (table_cnf["conds"][0]["source_owner"], table_def.name)
            engine.ensure_table_structure(
                source_connection, table_def, table_cnf, reference=reference, ddl_sink=ddl_sink
            )
        else:
            engine.ensure_table_structure(None, table_def, table_cnf, ddl_sink=ddl_sink)


def _write_script_ddl_files(
    config: Config,
    engine: DatabaseEngine,
    source_connection: Any,
    tables_config: TablesConfig,
    tables_conf_rows: list[IlmRule],
    *,
    source_grants_applied: bool = False,
) -> None:
    """Write the one-time DDL and grants of a SCRIPT run to files under ``ddl_pending_dir`` (DEC-025).

    The history file holds the history DDL followed by the history grants; for SOURCE_ILM a second
    file holds the source grants, since the two databases are run by different users. The ILM
    script, which runs many times, goes to stdout and stays free of them.
    """
    history_tables = _collect_privilege_targets(tables_conf_rows, "history_owner", engine)

    def script_history(ddl_sink: DdlSink | None, ddl_failures: DdlFailures | None) -> None:
        assert ddl_sink is not None
        _script_history_ddl(config, engine, source_connection, tables_config, ddl_sink)
        engine.ensure_table_privileges(
            None, _app_role(config, "HISTORY"), history_tables, _TABLE_PRIVILEGES, ddl_sink=ddl_sink
        )

    deliver_ddl(
        config,
        script_history,
        destination="file",
        env="HISTORY",
        file_stem="history_ddl",
        description="history table DDL and grants generated by ldb-run --mode SCRIPT.",
    )
    if config.execution.action == "SOURCE_ILM" and not source_grants_applied:
        source_tables = _collect_privilege_targets(tables_conf_rows, "source_owner", engine)

        def script_source_grants(ddl_sink: DdlSink | None, ddl_failures: DdlFailures | None) -> None:
            assert ddl_sink is not None
            engine.ensure_table_privileges(
                None, _app_role(config, "SOURCE"), source_tables, _TABLE_PRIVILEGES, ddl_sink=ddl_sink
            )

        deliver_ddl(
            config,
            script_source_grants,
            destination="file",
            env="SOURCE",
            file_stem="source_grants",
            description="source table grants generated by ldb-run --mode SCRIPT.",
        )


Privilege = tuple[str, str, str]


def _check_role_privileges(
    config: Config,
    engine: DatabaseEngine,
    connection: Any,
    env: Literal["SOURCE", "HISTORY"],
    tables: list[tuple[str, str]],
    level: int,
) -> list[Privilege]:
    """Return the table privileges the runtime user does not receive through the environment's role.

    Reads only with the runtime connection, so it needs no admin credentials (DEC-033). A role that is not
    enabled for the user gives nothing; both findings are logged at ``level``."""
    if not tables:
        return []
    role = _app_role(config, env)
    emit = logger.error if level >= logging.ERROR else logger.warning
    role_enabled = engine.is_role_enabled(connection, role)
    if not role_enabled:
        emit(
            "%s role %s is not enabled for the runtime user: it is not granted to it or is not a default role. "
            "Run ldb-impl with admin credentials to grant it.",
            env,
            role,
        )
    missing = engine.get_missing_table_privileges(connection, role, tables, _TABLE_PRIVILEGES)
    if missing and role_enabled:
        by_table: dict[tuple[str, str], list[str]] = {}
        for owner, table_name, privilege in missing:
            by_table.setdefault((owner, table_name), []).append(privilege)
        emit(
            "%s runtime user lacks privileges through role %s: %s.",
            env,
            role,
            "; ".join(f"{owner}.{name} ({', '.join(privs)})" for (owner, name), privs in by_table.items()),
        )
    return missing


def _verify_history_privileges(
    config: Config, engine: DatabaseEngine, tables: list[tuple[str, str]], level: int = logging.ERROR
) -> list[Privilege]:
    """Check the history grants over the runtime HISTORY connection, once the objects and grants should exist."""
    if not tables or not _credentials_present(config, admin=False, env="HISTORY"):
        return []
    history_connection = engine.get_connection(config, admin=False, env="HISTORY")
    try:
        return _check_role_privileges(config, engine, history_connection, "HISTORY", tables, level)
    finally:
        engine.close_connection(history_connection)


def _write_source_grants_file(config: Config, engine: DatabaseEngine, missing: list[Privilege], origin: str) -> None:
    """Write the missing source table grants to ``ddl_pending_dir`` for a DBA (no source admin credentials)."""
    by_privilege: dict[str, list[tuple[str, str]]] = {}
    for owner, table_name, privilege in missing:
        by_privilege.setdefault(privilege, []).append((owner, table_name))

    def produce(ddl_sink: DdlSink | None, ddl_failures: DdlFailures | None) -> None:
        assert ddl_sink is not None
        for privilege, tables in by_privilege.items():
            engine.ensure_table_privileges(None, _app_role(config, "SOURCE"), tables, (privilege,), ddl_sink=ddl_sink)

    deliver_ddl(
        config,
        produce,
        destination="file",
        env="SOURCE",
        file_stem="source_grants",
        description=f"missing source table grants found by {origin}.",
    )
    logger.warning(
        "Source admin credentials are absent: the missing source table grants were written to the pending file; "
        "a DBA must apply them and PREPARE must be rerun."
    )


def _invisible_tables(tables_config: TablesConfig) -> list[tuple[str, str]]:
    """Return the tables whose columns the runtime user cannot read (missing, or SELECT not granted)."""
    return sorted(key for key, cnf in tables_config.items() if cnf.get("columns_metadata") == {})


def _prepare_result(tables_config: TablesConfig, missing: list[Privilege]) -> int:
    """Return PREPARE's exit code: 1 when a configured table or privilege is still out of the runtime user's reach."""
    invisible = [f"{owner}.{name}" for owner, name in _invisible_tables(tables_config)]
    if not invisible and not missing:
        logger.info("PREPARE completed: grants and history objects are in place.")
        return 0
    if invisible:
        logger.error(
            "PREPARE incomplete: the runtime user cannot see %s (the table does not exist or SELECT is not "
            "granted). Apply the pending grants and rerun PREPARE.",
            ", ".join(invisible),
        )
    if missing:
        logger.error(
            "PREPARE incomplete: %d table privilege(s) are still missing. Rerun PREPARE after they are applied.",
            len(missing),
        )
    return 1


def generate_script_output(config: Config, tables_config: TablesConfig) -> int:
    print(
        f"""
whenever oserror exit 1
whenever sqlerror exit 1
set echo on ver off trimspool on
spool ldb_{config.context.schema}.log
rem usage: sqlplus user/password@dsn @script EXECUTE|PREVIEW
DEFINE ldb_mode = "&1"
COLUMN ldb_process_date NEW_VALUE ldb_process_date
SELECT TO_CHAR(SYSDATE, 'YYYYMMDD') ldb_process_date FROM DUAL;
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
    tables_config: TablesConfig, active_tables: set[tuple[str, str]]
) -> tuple[str, str, ProcessedTableConfig] | None:
    for (owner, table_name), table_cnf in tables_config.items():
        if table_cnf["skip"]:
            continue
        cd = tables_config[(owner, table_name)]["conds"][0]
        if (
            cd["ctl_status"] in [Status.SKIPPED, Status.TABLE_END, Status.TABLE_PREVIEW_END]
            or (owner, table_name) in active_tables
        ):
            continue
        referencing_tables = table_cnf["referencing_tables"]
        all_referencing_tables_ready = True
        if referencing_tables:
            for ref_owner, ref_table in referencing_tables:
                cdr = tables_config[(ref_owner, ref_table)]["conds"][0]
                if cdr["ctl_status"] not in (Status.TABLE_END, Status.TABLE_PREVIEW_END):
                    all_referencing_tables_ready = False
                    break  # referencing table not TEND status
        if not all_referencing_tables_ready:
            logger.debug(f"Table {owner}.{table_name} has to wait for referencing tables to finish...")
            continue
        return (owner, table_name, table_cnf)  # Found a table ready to process
    return None  # No tables ready to process


def ldb_exec_ilm(
    config: Config,
    tables_config: TablesConfig,
    process_date: str,
    engine: DatabaseEngine,
    connection: Any,
) -> int:
    """Run the tables under the runner mutex: another runner on any of them stops this one before it starts.

    The locks live on a dedicated connection that never commits; ``connection`` only writes the key rows."""
    run_id = str(uuid.uuid4())
    protected = [key for key, table_cnf in tables_config.items() if not table_cnf["skip"]]
    lock_conn: Any = None
    try:
        lock_conn = engine.get_connection(config)
        engine.acquire_run_locks(
            lock_conn, connection, protected, run_id, config.execution.mode, socket.gethostname(), os.getpid()
        )
        logger.info(
            "Runner %s locked %d tables for this run; no other runner can process them until it ends.",
            run_id,
            len(protected),
        )
        return _run_table_pool(config, tables_config, process_date, engine, connection, run_id, lock_conn)
    finally:
        if lock_conn is not None:
            try:
                engine.release_run_locks(lock_conn)
            except Exception:
                logger.warning("Could not release the runner locks cleanly.", exc_info=True)
            engine.close_connection(lock_conn)


def _run_table_pool(
    config: Config,
    tables_config: TablesConfig,
    process_date: str,
    engine: DatabaseEngine,
    connection: Any,
    run_id: str,
    lock_conn: Any,
) -> int:
    processes: list[Future[WorkerResult]] = []
    process_targets: dict[Future[WorkerResult], tuple[str, str]] = {}
    active_tables: set[tuple[str, str]] = set()
    with ProcessPoolExecutor(
        max_workers=config.execution.parallel_max,
        initializer=_initialize_worker_logger,
        initargs=(config.execution.log_level,),
    ) as executor:
        process_launched = False
        while True:
            # While there is space in the pool, try to launch new processes
            while len(processes) < config.execution.parallel_max:
                cycle_launched = False
                # Search for a process to launch
                next_ready_table = get_next_ready_table(tables_config, active_tables)
                if next_ready_table:
                    owner, table_name, table_info = next_ready_table
                    logger.info(f"Launching background process for {owner}.{table_name}...")
                    future = executor.submit(
                        process_table, config, owner, table_name, table_info["sql_block"], process_date, run_id
                    )
                    processes.append(future)
                    process_targets[future] = (owner, table_name)
                    active_tables.add((owner, table_name))
                    table_info["conds"][0]["ctl_status"] = Status.TABLE_START
                    cycle_launched = True
                    process_launched = True
                if not cycle_launched:
                    break  # There are no processes to launch, so go to waiting some process to end
            logger.debug(f"Active processes: {len(processes)}")
            if processes:
                # Wait for some process to finish; while none does, check that the locks are still held
                done, _ = wait(processes, timeout=_LOCK_PING_SECONDS, return_when=FIRST_COMPLETED)
                if not done:
                    engine.check_run_locks(lock_conn)
                    continue
                for completed_future in [future for future in processes if future in done]:
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
                    if status in (Status.ERROR, Status.SKIPPED):
                        logger.error(f"  Error {sqlcode}: {message}" if sqlcode else f"  Error: {message}")
                    active_tables.remove((owner, table_name))
                    processes.remove(completed_future)
                    del process_targets[completed_future]
                    break
            else:
                break
    if config.execution.mode == "PREVIEW":
        all_done = all(
            cnf["skip"] or cnf["conds"][0]["ctl_status"] in (Status.TABLE_END, Status.TABLE_PREVIEW_END)
            for cnf in tables_config.values()
        )
    else:
        all_done = engine.all_status_tend(connection, tables_config, process_date)
    if all_done:
        if process_launched:
            logger.info("All tables processed successfully.")
        else:
            logger.info("No tables to process. Every table already finished for process date %s.", process_date)
        return 0
    else:
        logger.error("Some tables did not finish successfully.")
        return 1


def process_tables_cnf(connection: Any, config: Config, engine: DatabaseEngine, process_date: str) -> TablesConfig:
    logger.info("Processing table configuration...")
    ldb_ctl_status_rows: list[dict[str, Any]] = []
    ldb_conf_rows = _load_offline_rows(config)
    if not config.execution.generate_script:
        ldb_ctl_status_rows = engine.get_status(connection, process_date)
    tables_config: TablesConfig = {}
    # Populate tables_config with the raw rows and derive referencing tables from cnf_referencing_tables.
    is_source_mode = config.execution.action == "SOURCE_ILM"
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
    if not config.execution.generate_script:
        # Inject ctl_status values loaded from the database when resuming a run.
        for ldb_ctl_status_row in ldb_ctl_status_rows:
            key = (ldb_ctl_status_row["ctl_owner"], ldb_ctl_status_row["ctl_table_name"])
            if key in tables_config:
                for cond in tables_config[key]["conds"]:
                    # A PREVIEW leaves TPEND behind; it must not make a later run skip the table
                    loaded = ldb_ctl_status_row["ctl_status"]
                    cond["ctl_status"] = None if loaded == Status.TABLE_PREVIEW_END else loaded
    # Collect each table's active conditions along with inherited referencing-table conditions.
    # Track every expression needed to build the WHERE clause for the combined predicate across all contributing conditions.
    # Persist the resulting predicate in cond_expr so later stages can reuse it.
    for key, table_cnf in tables_config.items():
        cnd0 = table_cnf["conds"][0]
        _validate_orphan_configuration(config, cnd0, key)
        runtime_owner = _runtime_owner(config, cnd0)
        inherited_references = cnd0["referencing_tables"]
        added_conds = [("A", cond) for cond in table_cnf["conds"]]
        orphan_history_date_predicates: list[str] = []
        table_cnf["table_columns"], table_cnf["columns_metadata"] = engine.get_table_columns(
            connection, runtime_owner, cnd0["table_name"]
        )
        if not table_cnf["columns_metadata"]:
            logger.warning(
                "%s.%s is not visible to the runtime user: the table does not exist or SELECT is not granted "
                "(have the grants been applied?). The table is skipped.",
                runtime_owner,
                cnd0["table_name"],
            )
            table_cnf["skip"] = True
            continue
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
                if tables_config[(ref_owner, ref_table_name)].get("columns_metadata") == {}:
                    logger.warning(
                        "%s.%s references %s.%s, which is not visible to the runtime user. The table is skipped.",
                        runtime_owner,
                        cnd0["table_name"],
                        ref_owner,
                        ref_table_name,
                    )
                    table_cnf["skip"] = True
                    break
                ref_conds = tables_config[(ref_owner, ref_table_name)]["conds"]
                added_conds.extend([(alias, cond) for cond in ref_conds])
            if table_cnf.get("skip"):
                continue
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
            mod_alexp_uac = alias_prefix if is_source_mode or not config.execution.use_added_columns else "A."
            if addtl_expr:
                if not is_source_mode and config.execution.use_added_columns and al != "A":
                    related_identifiers = engine.get_identifiers_from_expression(history_addtl_filter_expr)
                    persisted_identifiers = {identifier: f"{identifier}_{al}" for identifier in related_identifiers}
                    cond_list.append(
                        engine.rewrite_expression_identifiers(
                            history_addtl_filter_expr,
                            persisted_identifiers,
                            qualifier="A",
                        )
                    )
                else:
                    cond_list.append(addtl_expr.replace("@", mod_alexp_uac))
            if purge_date_expr:
                if is_source_mode or not config.execution.use_added_columns or al == "A":
                    cond_list.append(
                        engine.get_date_condition(purge_date_expr.replace("@", alias_prefix), cast(int, retain_months))
                    )
                else:
                    relationship_date_predicate = engine.get_date_condition(
                        f"A.{_relationship_date_column(al)}", cast(int, retain_months_history)
                    )
                    cond_list.append(relationship_date_predicate)
                    orphan_history_date_predicates.append(relationship_date_predicate)
            cd["cond_expr"] = " and ".join(cond_list)
        # get the maximum cnf_retain_months_history from all added_conds
        table_cnf["months_keep_history_max"] = max_ignore_none([cd["retain_months_history"] for _, cd in added_conds])
        if cnd0["source_orphan_purge"] == "Y" and nvl(table_cnf["months_keep_history_max"], 0) <= 0:
            raise ValueError(f"positive retain_months_history required when source_orphan_purge is enabled for {key}")
        # if HISTORY_ILM and cnf_months_keep_history_max is None, skip the table
        if not is_source_mode and table_cnf["months_keep_history_max"] is None:
            table_cnf["skip"] = True
            continue
        table_cnf["skip"] = False
        # get where expression from cond_expr of all added_conds joined by " or "
        if config.execution.action == "SOURCE_ILM" and not any(cd["cond_expr"] for _, cd in added_conds):
            raise ValueError(f"At least one condition must be specified for SOURCE_ILM on table {key}")
        predicates = [f"({cd['cond_expr']})" for _, cd in added_conds if cd["cond_expr"]]
        if is_source_mode:
            orphan_predicate = _orphan_predicate(cnd0)
            if orphan_predicate:
                predicates.append(f"({orphan_predicate})")
        elif cnd0["source_orphan_purge"] == "Y":
            if not orphan_history_date_predicates:
                raise ValueError(f"related retention date required when source_orphan_purge is enabled for {key}")
            orphan_dates = "\n      or ".join(f"({predicate})" for predicate in orphan_history_date_predicates)
            predicates.append(f"(A.{_ORPHAN_MARKER_COLUMN} = 'Y' and ({orphan_dates}))")
        where_expr = "\n   or ".join(predicates)
        other_columns: list[_OtherColumn] = []
        table_cnf["other_columns"] = other_columns
        if config.execution.use_added_columns and is_source_mode:
            for al, cd in added_conds:
                columns_metadata = tables_config[(cd["source_owner"], cd["table_name"])]["columns_metadata"]
                if al != "A":
                    # Add the cnf_purge_date_expr to other_columns for referencing tables
                    if cd["purge_date_expr"]:
                        relationship_date_column = _relationship_date_column(al)
                        column_metadata = ColumnDefinition(
                            name=relationship_date_column, data_type="date", nullable=True
                        )
                        _append_unique_name(
                            other_columns,
                            {
                                "name": relationship_date_column,
                                "expr": engine.get_fallback_expression(
                                    nvl(cd["purge_date_expr"], "").replace("@", al + "."),
                                    ldb_process_date_expr,
                                )
                                if cnd0["source_orphan_purge"] == "Y"
                                else nvl(cd["purge_date_expr"], "").replace("@", al + "."),
                                "metadata": column_metadata,
                            },
                        )
                    # Add columns in cnf_history_addtl_filter_expr to other_cols_exprs and other_cols_alias for referencing tables
                    history_filter = " ".join(
                        nvl(cd["history_addtl_filter_expr"], cd["additional_filter_expr"] or "").splitlines()
                    )
                    for column_name in engine.get_identifiers_from_expression(history_filter):
                        column_metadata = _clone_column_definition(f"{column_name}_{al}", columns_metadata[column_name])
                        other_column: _OtherColumn = {
                            "name": f"{column_name}_{al}",
                            "expr": f"{al}.{column_name}",
                            "metadata": column_metadata,
                        }
                        _append_unique_name(other_columns, other_column)
            orphan_predicate = _orphan_predicate(cnd0)
            if orphan_predicate:
                other_columns.append(
                    {
                        "name": _ORPHAN_MARKER_COLUMN,
                        "expr": f"case when {orphan_predicate} then 'Y' else 'N' end",
                        "metadata": ColumnDefinition(
                            name=_ORPHAN_MARKER_COLUMN,
                            data_type="string",
                            length=1,
                            nullable=True,
                        ),
                    }
                )
        if config.execution.add_ldb_columns and is_source_mode:
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
        _, cnf_table_name = key
        join_expr = _runtime_join_expression(config, cnd0)
        # Prepare query_expr with the cnf_table_name, join_expr and where_expr
        base_from = f"from {runtime_owner.lower()}.{cnf_table_name.lower()} A"
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
        engine = get_db_engine(config.connections.db_engine)
        if config.execution.mode == "PLAN":
            rc = _plan_mode(config)
            raise SystemExit(rc)
        if config.execution.mode == "VALIDATE":
            rc = _validate_environment(config, engine)
            raise SystemExit(rc)
        connection = engine.get_connection(config)
        tables_conf_rows = _load_offline_rows(config)
        source_tables_for_privileges = _collect_privilege_targets(tables_conf_rows, "source_owner", engine)
        # The runtime user reads column metadata through these grants, so SCRIPT applies them too when it can
        source_grants_applied = False
        if (
            config.execution.action == "SOURCE_ILM"
            and source_tables_for_privileges
            and _credentials_present(config, admin=True, env="SOURCE")
        ):
            source_admin_priv_conn = engine.get_connection(config, admin=True, env="SOURCE")
            try:
                engine.ensure_table_privileges(
                    source_admin_priv_conn,
                    config.administration.source_role_name,
                    source_tables_for_privileges,
                    _TABLE_PRIVILEGES,
                )
            finally:
                engine.close_connection(source_admin_priv_conn)
            source_grants_applied = True
        source_missing: list[Privilege] = []
        if config.execution.action == "SOURCE_ILM" and source_tables_for_privileges:
            is_prepare = config.execution.mode == "PREPARE"
            source_missing = _check_role_privileges(
                config,
                engine,
                connection,
                "SOURCE",
                source_tables_for_privileges,
                logging.ERROR if is_prepare else logging.WARNING,
            )
            if is_prepare and source_missing and not engine.is_role_enabled(connection, _app_role(config, "SOURCE")):
                # Nothing else can be checked or fixed until the role reaches the runtime user
                logger.error(
                    "PREPARE incomplete: run ldb-impl to grant the role to the runtime user and rerun PREPARE."
                )
                raise SystemExit(1)
            if is_prepare and source_missing and not source_grants_applied:
                _write_source_grants_file(config, engine, source_missing, "ldb-run --mode PREPARE")
        process_date = engine.get_system_date(connection).strftime("%Y%m%d")
        tables_config = process_tables_cnf(connection, config, engine, process_date)
        if config.execution.generate_script:
            _write_script_ddl_files(
                config, engine, connection, tables_config, tables_conf_rows, source_grants_applied=source_grants_applied
            )
            rc = generate_script_output(config, tables_config)
        else:
            if _credentials_present(config, admin=True, env=None):
                admin_connection = engine.get_connection(config, admin=True)
            # Without admin credentials the primary-key metadata is read over the runtime connection.
            _ensure_history_tables(config, engine, admin_connection or connection, tables_config)
            history_tables_for_privileges = _collect_privilege_targets(tables_conf_rows, "history_owner", engine)
            if history_tables_for_privileges and _credentials_present(config, admin=True, env="HISTORY"):
                history_admin_priv_conn = engine.get_connection(config, admin=True, env="HISTORY")
                try:
                    engine.ensure_table_privileges(
                        history_admin_priv_conn,
                        config.administration.history_role_name,
                        history_tables_for_privileges,
                        _TABLE_PRIVILEGES,
                    )
                finally:
                    engine.close_connection(history_admin_priv_conn)
            if config.execution.mode == "PREPARE":
                rc = _prepare_result(
                    tables_config,
                    source_missing + _verify_history_privileges(config, engine, history_tables_for_privileges),
                )
            else:
                rc = ldb_exec_ilm(config, tables_config, process_date, engine, connection)
        raise SystemExit(rc)
    finally:
        if engine and connection:
            engine.close_connection(connection)
        if engine and admin_connection:
            engine.close_connection(admin_connection)
