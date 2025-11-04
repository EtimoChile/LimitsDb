"""Bootstrap database objects required by TerminusDB."""
from __future__ import annotations

import argparse
from typing import Any, Dict, List, Literal

from terminusdb.core.tdb_logger import configure_logger, get_logger, reconfigure_logger
from terminusdb.core.tdb_crypto import load_or_create_key
from terminusdb.core.tdb_params_config import Config, build_config
from terminusdb.db.tdb_engine_loader import get_db_engine
from terminusdb.db.tdb_engines import (
    ColumnDefinition,
    DatabaseLinkDefinition,
    IndexDefinition,
    RoleDefinition,
    SequenceDefinition,
    TableDefinition,
    UserDefinition,
)
from terminusdb.core.tdb_utils import encrypt_secrets_in_place


configure_logger(level="WARNING")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ensure TerminusDB control objects exist in the database.",
    )
    parser.add_argument("--schema", required=True, help="Schema name (folder under schemas/)")
    parser.add_argument("--profile", help="Profile name (e.g., dev, prod)")
    parser.add_argument("--config-dir", help="Configuration root (overrides autodiscovery)")
    parser.add_argument("--config-file", help="Additional configuration overlay")
    parser.add_argument("--set", action="append", default=[], help="Overrides like key=value; supports dotted keys for nesting")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level",
    )
    return parser.parse_args()


def _build_control_tables(owner: str) -> List[TableDefinition]:
    return [
        TableDefinition(
            owner=owner,
            name="tdb_ctl",
            columns=(
                ColumnDefinition("ctl_owner", "string", length=50, nullable=False),
                ColumnDefinition("ctl_table_name", "string", length=50, nullable=False),
                ColumnDefinition("ctl_process_date", "date"),
                ColumnDefinition("ctl_action", "string", length=10, nullable=False),
                ColumnDefinition("ctl_status", "string", length=10, nullable=False),
                ColumnDefinition("ctl_process_start", "date"),
                ColumnDefinition("ctl_process_end", "date"),
                ColumnDefinition("ctl_rows_processed", "number", precision=18, scale=0),
                ColumnDefinition("ctl_plsql", "clob"),
            ),
            primary_key=("ctl_owner", "ctl_table_name"),
        ),
        TableDefinition(
            owner=owner,
            name="tdb_log",
            columns=(
                ColumnDefinition("log_id", "number", precision=18, scale=0, nullable=False),
                ColumnDefinition("log_owner", "string", length=50, nullable=False),
                ColumnDefinition("log_table_name", "string", length=50, nullable=False),
                ColumnDefinition("log_process_date", "date", nullable=False),
                ColumnDefinition("log_action", "string", length=10, nullable=False),
                ColumnDefinition("log_status", "string", length=10, nullable=False),
                ColumnDefinition("log_process_start", "date", nullable=False),
                ColumnDefinition("log_process_end", "date"),
                ColumnDefinition("log_message", "string", length=4000),
                ColumnDefinition("log_rows_processed", "number", precision=18, scale=0),
                ColumnDefinition("log_plsql", "clob"),
            ),
            primary_key=("log_id",),
        ),
        TableDefinition(
            owner=owner,
            name="tdb_cnf",
            columns=(
                ColumnDefinition("cnf_id", "number", precision=18, scale=0, nullable=False),
                ColumnDefinition("cnf_source_owner", "string", length=50),
                ColumnDefinition("cnf_history_owner", "string", length=50),
                ColumnDefinition("cnf_table_name", "string", length=50, nullable=False),
                ColumnDefinition("cnf_retain_months_source", "number", precision=10, scale=0),
                ColumnDefinition("cnf_retain_months_history", "number", precision=10, scale=0),
                ColumnDefinition("cnf_exec_day", "string", length=10),
                ColumnDefinition("cnf_frecuency", "string", length=10),
                ColumnDefinition("cnf_is_active", "char", length=1, default="'Y'"),
                ColumnDefinition("cnf_purge_date_expr", "string", length=100),
                ColumnDefinition("cnf_additional_filter_expr", "string", length=4000),
                ColumnDefinition("cnf_history_addtl_filter_expr", "string", length=4000),
                ColumnDefinition("cnf_source_orphan_purge", "char", length=1, default="'N'"),
                ColumnDefinition("cnf_orphan_check_column", "string", length=4000),
                ColumnDefinition("cnf_has_lob_columns", "char", length=1, default="'N'"),
                ColumnDefinition("cnf_referencing_tables", "string", length=200),
                ColumnDefinition("cnf_join_expr", "string", length=4000),
                ColumnDefinition("cnf_hint_expr", "string", length=4000),
                ColumnDefinition("cnf_long_columns", "string", length=4000),
            ),
            primary_key=("cnf_id",),
            indexes=(
                IndexDefinition(name="cnf_conf_i1", columns=("cnf_source_owner", "cnf_table_name")),
            ),
        ),
    ]


def _build_sequences(owner: str) -> List[SequenceDefinition]:
    return [
        SequenceDefinition(owner=owner, name="tdb_log_id"),
        SequenceDefinition(owner=owner, name="tdb_cnf_id"),
    ]


def run_cli() -> None:
    logger = get_logger("impl")
    try:
        args = _parse_args()
        reconfigure_logger(level=args.log_level)

        load_or_create_key()
        try:
            encrypt_secrets_in_place(
                schema=args.schema,
                profile=getattr(args, "profile", None),
                config_root=getattr(args, "config_dir", None),
            )
        except Exception:  # pragma: no cover - log and continue
            logger.warning(
                "Auto-encrypt failed; continuing. Loader will enforce encrypted secrets.",
                exc_info=True,
            )
        cfg_dict = build_config({}, args)
        config = Config.from_dict(cfg_dict)

        if not config.admin_source_username or not config.admin_source_password:
            raise ValueError("admin_source_username and admin_source_password are required")
        if not config.admin_history_username or not config.admin_history_password:
            raise ValueError("admin_history_username and admin_history_password are required")
        if not config.source_username or not config.source_password:
            raise ValueError("source_username and source_password are required")
        if not config.history_username or not config.history_password:
            raise ValueError("history_username and history_password are required")
        if not config.history_dsn:
            raise ValueError("history_dsn is required to create the database link")
        if not config.source_dsn:
            raise ValueError("source_dsn is required to create the database link")

        engine = get_db_engine(config.db_engine)
    except ValueError as exc:
        logger.error("%s", exc)
        raise SystemExit(1)

    source_roles = [RoleDefinition(name=config.source_role_name)]
    source_privileges = getattr(engine, "REQUIRED_SYSTEM_PRIVILEGES", ())
    history_privileges = getattr(engine, "REQUIRED_SYSTEM_PRIVILEGES", ())

    source_user = UserDefinition(
        name=config.source_username,
        password=config.source_password,
        default_tablespace=config.source_default_tablespace or None,
        roles=(config.source_role_name,),
        roles_with_admin_option=(config.source_role_name,),
        system_privileges=source_privileges,
    )
    history_roles = [RoleDefinition(name=config.history_role_name)]
    history_user = UserDefinition(
        name=config.history_username,
        password=config.history_password,
        default_tablespace=config.history_default_tablespace or None,
        roles=(config.history_role_name,),
        roles_with_admin_option=(config.history_role_name,),
        system_privileges=history_privileges,
    )
    source_tables = _build_control_tables(config.source_username)
    source_sequences = _build_sequences(config.source_username)
    history_tables = _build_control_tables(config.history_username)
    history_sequences = _build_sequences(config.history_username)
    source_db_link = DatabaseLinkDefinition(
        name=config.source_to_history_dblink_name,
        username=config.history_username,
        password=config.history_password,
        dsn=config.history_dsn,
    )
    history_db_link = DatabaseLinkDefinition(
        name=config.history_to_source_dblink_name,
        username=config.source_username,
        password=config.source_password,
        dsn=config.source_dsn,
    )

    summary: Dict[str, List[str]] = {}

    def _get_connection(action: Literal["SOURCE_ILM", "HISTORY_ILM"], *, admin: bool) -> Any:
        previous_action = config.action
        config.action = action
        try:
            return engine.get_connection(config, admin=admin)
        finally:
            config.action = previous_action

    source_admin_conn = _get_connection("SOURCE_ILM", admin=True)
    try:
        summary["source_roles"] = engine.ensure_roles(source_admin_conn, source_roles)
        summary["source_users"] = engine.ensure_users(source_admin_conn, [source_user])
        summary["source_tables"] = engine.ensure_tables(source_admin_conn, source_tables)
        summary["source_sequences"] = engine.ensure_sequences(source_admin_conn, source_sequences)
        engine.ensure_supporting_plsql(source_admin_conn, config.source_username)
    finally:
        engine.close_connection(source_admin_conn)

    history_admin_conn = _get_connection("HISTORY_ILM", admin=True)
    try:
        summary["history_roles"] = engine.ensure_roles(history_admin_conn, history_roles)
        summary["history_users"] = engine.ensure_users(history_admin_conn, [history_user])
        summary["history_tables"] = engine.ensure_tables(history_admin_conn, history_tables)
        summary["history_sequences"] = engine.ensure_sequences(history_admin_conn, history_sequences)
        engine.ensure_supporting_plsql(history_admin_conn, config.history_username)
    finally:
        engine.close_connection(history_admin_conn)

    source_user_conn = _get_connection("SOURCE_ILM", admin=False)
    try:
        summary["source_database_links"] = engine.ensure_database_links(source_user_conn, [source_db_link])
    finally:
        engine.close_connection(source_user_conn)

    history_user_conn = _get_connection("HISTORY_ILM", admin=False)
    try:
        summary["history_database_links"] = engine.ensure_database_links(history_user_conn, [history_db_link])
    finally:
        engine.close_connection(history_user_conn)

    anything_created = any(summary.values())
    for key, values in summary.items():
        if values:
            logger.info("Created %s: %s", key.replace("_", " "), ", ".join(values))
    if not anything_created:
        logger.info("All TerminusDB database objects are already present.")


if __name__ == "__main__":  # pragma: no cover
    run_cli()
