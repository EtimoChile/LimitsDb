from datetime import datetime

import pytest

from limitsdb.core.ldb_params_config import Config
from limitsdb.db.ldb_engine_loader import get_db_engine
from limitsdb.db.ldb_engines import ColumnDefinition, DatabaseEngine, TableDefinition
from limitsdb.db.oracle.ldb_engine_impl import OracleEngine


def test_get_db_engine_returns_oracle_engine():
    engine = get_db_engine("oracle")

    assert engine is OracleEngine


@pytest.mark.parametrize("engine_name", ["postgres", "unknown"])
def test_get_db_engine_raises_for_unsupported_engine(engine_name: str):
    with pytest.raises(ValueError, match=rf"Unsupported database engine: {engine_name}; supported engines: oracle"):
        get_db_engine(engine_name)


def test_database_engine_abstract_stubs_are_callable():
    config = Config(schema="s", mode="PLAN")
    conn = object()

    # Methods that do not need a live connection
    assert DatabaseEngine.get_date_condition("expr", 12) is None
    assert DatabaseEngine.get_fallback_expression("expr", "fallback") is None
    assert DatabaseEngine.rewrite_expression_identifiers("expr", {}, qualifier="A") is None
    col = ColumnDefinition(name="X", data_type="VARCHAR2")
    assert DatabaseEngine.get_column_type(col) is None
    assert DatabaseEngine.generate_sql_block(config, {}, "20261006") is None
    assert DatabaseEngine.get_ldb_columns_expressions() is None
    assert DatabaseEngine.get_identifier_str("OWNER") is None

    # Methods that accept a connection object
    assert DatabaseEngine.get_connection(config) is None
    assert DatabaseEngine.close_connection(conn) is None
    assert DatabaseEngine.get_table_columns(conn, "OWNER", "TABLE") is None
    assert DatabaseEngine.get_identifiers_from_expression("expr") is None
    assert DatabaseEngine.get_system_date(conn) is None
    assert DatabaseEngine.sql_block_run(conn, "BEGIN NULL; END;") is None
    assert DatabaseEngine.get_status(conn, "20261006") is None
    assert DatabaseEngine.get_rows_processed(conn, "OWNER", "TABLE", "20261006") is None
    assert DatabaseEngine.save_error_status(conn, config, "O", "T", "20261006", datetime.now(), "err", "code") is None
    assert DatabaseEngine.all_status_tend(conn, {}, "20261006") is None
    assert DatabaseEngine.get_primary_key_columns(conn, "OWNER", "TABLE", {}) is None
    assert DatabaseEngine.ensure_roles(conn, []) is None
    assert DatabaseEngine.ensure_users(conn, []) is None
    assert DatabaseEngine.ensure_tables(conn, []) is None
    table_def = TableDefinition(owner="O", name="T", columns=())
    assert DatabaseEngine.ensure_table_structure(conn, table_def, {}) is None
    assert DatabaseEngine.ensure_sequences(conn, []) is None
    assert DatabaseEngine.ensure_database_links(conn, []) is None
    assert DatabaseEngine.ensure_table_privileges(conn, "ROLE", [], []) is None
    assert DatabaseEngine.ensure_supporting_objects(conn, "OWNER") is None
