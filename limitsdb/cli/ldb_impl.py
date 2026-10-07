"""Bootstrap database objects required by LimitsDb."""

from __future__ import annotations

import argparse

from limitsdb.core.ldb_crypto import load_or_create_key
from limitsdb.core.ldb_errors import LimitsDbError
from limitsdb.core.ldb_logger import configure_logger, get_logger, reconfigure_logger
from limitsdb.core.ldb_params_config import Config, build_config
from limitsdb.core.ldb_utils import encrypt_secrets_in_place
from limitsdb.db.ldb_engine_loader import get_db_engine
from limitsdb.db.ldb_engines import (
    ColumnDefinition,
    DatabaseLinkDefinition,
    IndexDefinition,
    RoleDefinition,
    SequenceDefinition,
    TableDefinition,
    UserDefinition,
)

configure_logger(level="WARNING")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ensure LimitsDb control objects exist in the database.",
    )
    parser.add_argument("--schema", required=True, help="Schema name (folder under schemas/)")
    parser.add_argument("--profile", help="Profile name (e.g., dev, prod)")
    parser.add_argument("--config-dir", help="Configuration root (overrides autodiscovery)")
    parser.add_argument("--config-file", help="Additional configuration overlay")
    parser.add_argument(
        "--set", action="append", default=[], help="Overrides like key=value; supports dotted keys for nesting"
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Logging level"
    )
    return parser.parse_args()


def _build_control_tables(owner: str) -> list[TableDefinition]:
    return [
        TableDefinition(
            owner=owner,
            name="LDB_CTL",
            columns=(
                ColumnDefinition("CTL_OWNER", "string", length=50, nullable=False),
                ColumnDefinition("CTL_TABLE_NAME", "string", length=50, nullable=False),
                ColumnDefinition("CTL_PROCESS_DATE", "date"),
                ColumnDefinition("CTL_ACTION", "string", length=11, nullable=False),
                ColumnDefinition("CTL_STATUS", "string", length=10, nullable=False),
                ColumnDefinition("CTL_PROCESS_START", "date"),
                ColumnDefinition("CTL_PROCESS_END", "date"),
                ColumnDefinition("CTL_ROWS_PROCESSED", "number", precision=18, scale=0),
                ColumnDefinition("CTL_PLSQL", "clob"),
            ),
            primary_key=("CTL_OWNER", "CTL_TABLE_NAME"),
        ),
        TableDefinition(
            owner=owner,
            name="LDB_LOG",
            columns=(
                ColumnDefinition("LOG_ID", "number", precision=18, scale=0, nullable=False),
                ColumnDefinition("LOG_OWNER", "string", length=50, nullable=False),
                ColumnDefinition("LOG_TABLE_NAME", "string", length=50, nullable=False),
                ColumnDefinition("LOG_PROCESS_DATE", "date", nullable=False),
                ColumnDefinition("LOG_ACTION", "string", length=11, nullable=False),
                ColumnDefinition("LOG_STATUS", "string", length=10, nullable=False),
                ColumnDefinition("LOG_PROCESS_START", "date", nullable=False),
                ColumnDefinition("LOG_PROCESS_END", "date"),
                ColumnDefinition("LOG_MESSAGE", "string", length=4000),
                ColumnDefinition("LOG_ROWS_PROCESSED", "number", precision=18, scale=0),
                ColumnDefinition("LOG_PLSQL", "clob"),
            ),
            primary_key=("LOG_ID",),
        ),
        TableDefinition(
            owner=owner,
            name="LDB_CNF",
            columns=(
                ColumnDefinition("CNF_ID", "number", precision=18, scale=0, nullable=False),
                ColumnDefinition("CNF_SOURCE_OWNER", "string", length=50),
                ColumnDefinition("CNF_HISTORY_OWNER", "string", length=50),
                ColumnDefinition("CNF_TABLE_NAME", "string", length=50, nullable=False),
                ColumnDefinition("CNF_RETAIN_MONTHS_SOURCE", "number", precision=10, scale=0),
                ColumnDefinition("CNF_RETAIN_MONTHS_HISTORY", "number", precision=10, scale=0),
                ColumnDefinition("CNF_EXEC_DAY", "string", length=10),
                ColumnDefinition("CNF_FRECUENCY", "string", length=10),
                ColumnDefinition("CNF_IS_ACTIVE", "char", length=1, default="'Y'"),
                ColumnDefinition("CNF_PURGE_DATE_EXPR", "string", length=100),
                ColumnDefinition("CNF_ADDITIONAL_FILTER_EXPR", "string", length=4000),
                ColumnDefinition("CNF_HISTORY_ADDTL_FILTER_EXPR", "string", length=4000),
                ColumnDefinition("CNF_SOURCE_ORPHAN_PURGE", "char", length=1, default="'N'"),
                ColumnDefinition("CNF_ORPHAN_CHECK_COLUMN", "string", length=4000),
                ColumnDefinition("CNF_HAS_LOB_COLUMNS", "char", length=1, default="'N'"),
                ColumnDefinition("CNF_REFERENCING_TABLES", "string", length=200),
                ColumnDefinition("CNF_JOIN_EXPR", "string", length=4000),
                ColumnDefinition("CNF_HINT_EXPR", "string", length=4000),
                ColumnDefinition("CNF_HISTORY_HINT_EXPR", "string", length=4000),
                ColumnDefinition("CNF_LONG_COLUMNS", "string", length=4000),
            ),
            primary_key=("CNF_ID",),
            indexes=(IndexDefinition(name="CNF_CONF_I1", columns=("CNF_SOURCE_OWNER", "CNF_TABLE_NAME")),),
        ),
    ]


def _build_sequences(owner: str) -> list[SequenceDefinition]:
    return [
        SequenceDefinition(owner=owner, name="LDB_LOG_ID"),
        SequenceDefinition(owner=owner, name="LDB_CNF_ID"),
    ]


def run_cli() -> None:
    logger = get_logger("impl")
    try:
        args = _parse_args()
        reconfigure_logger(level=args.log_level)

        load_or_create_key()
        encrypt_secrets_in_place(
            schema=args.schema,
            profile=getattr(args, "profile", None),
            config_root=getattr(args, "config_dir", None),
        )
        cfg_dict = build_config({}, args)
        config = Config.from_dict(cfg_dict)
        connections = config.connections
        administration = config.administration

        if not administration.source.username or not administration.source.password:
            raise ValueError("admin_source_username and admin_source_password are required")
        if not administration.history.username or not administration.history.password:
            raise ValueError("admin_history_username and admin_history_password are required")
        if not connections.source.username or not connections.source.password:
            raise ValueError("source_username and source_password are required")
        if not connections.history.username or not connections.history.password:
            raise ValueError("history_username and history_password are required")
        if not connections.history.dsn:
            raise ValueError("history_dsn is required to create the database link")
        if not connections.source.dsn:
            raise ValueError("source_dsn is required to create the database link")

        engine = get_db_engine(connections.db_engine)
    except (LimitsDbError, ValueError) as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc

    source_roles = [RoleDefinition(name=administration.source_role_name)]
    source_privileges = getattr(engine, "REQUIRED_SYSTEM_PRIVILEGES", ())
    history_privileges = getattr(engine, "REQUIRED_SYSTEM_PRIVILEGES", ())

    source_user = UserDefinition(
        name=connections.source.username,
        password=connections.source.password,
        default_tablespace=administration.source_default_tablespace or None,
        roles=(administration.source_role_name,),
        roles_with_admin_option=(administration.source_role_name,),
        system_privileges=source_privileges,
    )
    history_roles = [RoleDefinition(name=administration.history_role_name)]
    history_user = UserDefinition(
        name=connections.history.username,
        password=connections.history.password,
        default_tablespace=administration.history_default_tablespace or None,
        roles=(administration.history_role_name,),
        roles_with_admin_option=(administration.history_role_name,),
        system_privileges=history_privileges,
    )
    source_tables = _build_control_tables(connections.source.username)
    source_sequences = _build_sequences(connections.source.username)
    history_tables = _build_control_tables(connections.history.username)
    history_sequences = _build_sequences(connections.history.username)
    source_db_link = DatabaseLinkDefinition(
        name=administration.source_to_history_dblink_name,
        username=connections.history.username,
        password=connections.history.password,
        dsn=connections.history.dsn,
    )
    history_db_link = DatabaseLinkDefinition(
        name=administration.history_to_source_dblink_name,
        username=connections.source.username,
        password=connections.source.password,
        dsn=connections.source.dsn,
    )

    summary: dict[str, list[str]] = {}

    source_admin_conn = engine.get_connection(config, admin=True, env="SOURCE")
    try:
        summary["source_roles"] = engine.ensure_roles(source_admin_conn, source_roles)
        summary["source_users"] = engine.ensure_users(source_admin_conn, [source_user])
        summary["source_tables"] = engine.ensure_tables(source_admin_conn, source_tables)
        summary["source_sequences"] = engine.ensure_sequences(source_admin_conn, source_sequences)
        summary["source_supporting_objects"] = engine.ensure_supporting_objects(
            source_admin_conn, connections.source.username
        )
    finally:
        engine.close_connection(source_admin_conn)

    history_admin_conn = engine.get_connection(config, admin=True, env="HISTORY")
    try:
        summary["history_roles"] = engine.ensure_roles(history_admin_conn, history_roles)
        summary["history_users"] = engine.ensure_users(history_admin_conn, [history_user])
        summary["history_tables"] = engine.ensure_tables(history_admin_conn, history_tables)
        summary["history_sequences"] = engine.ensure_sequences(history_admin_conn, history_sequences)
        summary["history_supporting_objects"] = engine.ensure_supporting_objects(
            history_admin_conn, connections.history.username
        )
    finally:
        engine.close_connection(history_admin_conn)

    source_user_conn = engine.get_connection(config, admin=False, env="SOURCE")
    try:
        summary["source_database_links"] = engine.ensure_database_links(source_user_conn, [source_db_link])
    finally:
        engine.close_connection(source_user_conn)

    history_user_conn = engine.get_connection(config, admin=False, env="HISTORY")
    try:
        summary["history_database_links"] = engine.ensure_database_links(history_user_conn, [history_db_link])
    finally:
        engine.close_connection(history_user_conn)

    anything_created = any(summary.values())
    for key, values in summary.items():
        if values:
            logger.info("Created %s: %s", key.replace("_", " "), ", ".join(values))
    if not anything_created:
        logger.info("All LimitsDb database objects are already present.")


if __name__ == "__main__":  # pragma: no cover
    run_cli()
