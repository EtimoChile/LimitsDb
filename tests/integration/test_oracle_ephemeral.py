import os
from collections.abc import Iterator

import oracledb
import pytest

from limitsdb.core.ldb_params_config import Config
from limitsdb.db.ldb_engines import ColumnDefinition, IndexDefinition, TableDefinition
from limitsdb.db.oracle.ldb_engine_impl import OracleEngine

pytestmark = pytest.mark.oracle_integration


def _oracle_config() -> Config:
    values = {
        "dsn": os.getenv("LDB_ORACLE_TEST_DSN"),
        "username": os.getenv("LDB_ORACLE_TEST_USER"),
        "password": os.getenv("LDB_ORACLE_TEST_PASSWORD"),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        pytest.skip(f"ephemeral Oracle is not configured; missing: {', '.join(missing)}")
    return Config(
        schema="oracle-integration",
        source_dsn=values["dsn"],
        source_username=values["username"],
        source_password=values["password"],
    )


@pytest.fixture
def oracle_connection() -> Iterator[oracledb.Connection]:
    connection = OracleEngine.get_connection(_oracle_config())
    try:
        yield connection
    finally:
        OracleEngine.close_connection(connection)


def _drop_table_if_present(connection: oracledb.Connection, table_name: str) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute(f"DROP TABLE {table_name} PURGE")
    except oracledb.DatabaseError as exc:
        error = exc.args[0]
        if getattr(error, "code", None) != 942:
            raise
    finally:
        cursor.close()


def test_oracle_transaction_rollback_is_real(oracle_connection: oracledb.Connection):
    table_name = "LDBT_TX_ROLLBACK"
    _drop_table_if_present(oracle_connection, table_name)
    cursor = oracle_connection.cursor()
    try:
        cursor.execute(f"CREATE TABLE {table_name} (id NUMBER PRIMARY KEY)")
        cursor.execute(f"INSERT INTO {table_name} (id) VALUES (1)")
        oracle_connection.rollback()
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        assert cursor.fetchone()[0] == 0
    finally:
        cursor.close()
        _drop_table_if_present(oracle_connection, table_name)


def test_oracle_engine_table_ensure_is_idempotent(oracle_connection: oracledb.Connection):
    username = os.environ["LDB_ORACLE_TEST_USER"].upper()
    table_name = "LDBT_ENGINE_TABLE"
    _drop_table_if_present(oracle_connection, table_name)
    table = TableDefinition(
        owner=username,
        name=table_name,
        columns=(
            ColumnDefinition(name="ID", data_type="integer", nullable=False),
            ColumnDefinition(name="VALUE", data_type="string", length=40),
        ),
        primary_key=("ID",),
        indexes=(IndexDefinition(name="LDBT_ENGINE_VALUE_IX", columns=("VALUE",)),),
    )
    try:
        first = OracleEngine.ensure_tables(oracle_connection, (table,))
        second = OracleEngine.ensure_tables(oracle_connection, (table,))

        assert len(first) == 1
        assert second == []
        cursor = oracle_connection.cursor()
        try:
            cursor.execute("SELECT COUNT(*) FROM user_tables WHERE table_name = :1", [table_name])
            assert cursor.fetchone()[0] == 1
        finally:
            cursor.close()
    finally:
        _drop_table_if_present(oracle_connection, table_name)
