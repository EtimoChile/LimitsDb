import os
from collections.abc import Iterator

import oracledb
import pytest

from limitsdb.core.ldb_errors import ExecutionError
from limitsdb.core.ldb_params_config import Config
from limitsdb.db.ldb_engines import ColumnDefinition, IndexDefinition, RoleDefinition, TableDefinition, UserDefinition
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


def _execute_cleanup_ddl(connection: oracledb.Connection, statement: str, *, missing_error_code: int) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute(statement)
    except oracledb.DatabaseError as exc:
        error = exc.args[0]
        if getattr(error, "code", None) != missing_error_code:
            raise
    finally:
        cursor.close()


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


def test_oracle_engine_recovers_after_partial_ddl_failure(oracle_connection: oracledb.Connection):
    username = os.environ["LDB_ORACLE_TEST_USER"].upper()
    first_table_name = "LDBT_PARTIAL_A"
    second_table_name = "LDBT_PARTIAL_B"
    for table_name in (first_table_name, second_table_name):
        _drop_table_if_present(oracle_connection, table_name)

    first_table = TableDefinition(
        owner=username,
        name=first_table_name,
        columns=(ColumnDefinition(name="ID", data_type="integer", nullable=False),),
        primary_key=("ID",),
    )
    invalid_second_table = TableDefinition(
        owner=username,
        name=second_table_name,
        columns=(ColumnDefinition(name="ID", data_type="unsupported"),),
    )
    valid_second_table = TableDefinition(
        owner=username,
        name=second_table_name,
        columns=(ColumnDefinition(name="ID", data_type="integer", nullable=False),),
        primary_key=("ID",),
    )

    try:
        with pytest.raises(ExecutionError):
            OracleEngine.ensure_tables(oracle_connection, (first_table, invalid_second_table))

        cursor = oracle_connection.cursor()
        try:
            cursor.execute(
                "SELECT table_name FROM user_tables WHERE table_name IN (:1, :2)",
                [first_table_name, second_table_name],
            )
            assert {row[0] for row in cursor} == {first_table_name}
        finally:
            cursor.close()

        recovered = OracleEngine.ensure_tables(oracle_connection, (first_table, valid_second_table))
        assert recovered == [f"{username.lower()}.{second_table_name.lower()}"]
        assert OracleEngine.ensure_tables(oracle_connection, (first_table, valid_second_table)) == []
    finally:
        for table_name in (second_table_name, first_table_name):
            _drop_table_if_present(oracle_connection, table_name)


def test_oracle_privileged_ensures_are_idempotent(oracle_connection: oracledb.Connection):
    owner = os.environ["LDB_ORACLE_TEST_USER"].upper()
    table_name = "LDBT_PRIV_TABLE"
    role_name = "LDBT_PRIV_ROLE"
    user_name = "LDBT_PRIV_USER"
    _execute_cleanup_ddl(oracle_connection, f"DROP USER {user_name} CASCADE", missing_error_code=1918)
    _execute_cleanup_ddl(oracle_connection, f"DROP ROLE {role_name}", missing_error_code=1919)
    _drop_table_if_present(oracle_connection, table_name)

    table = TableDefinition(
        owner=owner,
        name=table_name,
        columns=(ColumnDefinition(name="ID", data_type="integer", nullable=False),),
        primary_key=("ID",),
    )
    role = RoleDefinition(name=role_name)
    user = UserDefinition(
        name=user_name,
        password="LimitsDb_CI_User_2026",
        default_tablespace="USERS",
        roles=(role_name,),
        system_privileges=("CREATE SESSION",),
    )

    try:
        OracleEngine.ensure_tables(oracle_connection, (table,))
        assert OracleEngine.ensure_roles(oracle_connection, (role,)) == [role_name.lower()]
        assert OracleEngine.ensure_roles(oracle_connection, (role,)) == []
        assert OracleEngine.ensure_users(oracle_connection, (user,)) == [user_name.lower()]
        assert OracleEngine.ensure_users(oracle_connection, (user,)) == []
        assert OracleEngine.ensure_table_privileges(
            oracle_connection, role_name, ((owner, table_name),), ("SELECT",)
        ) == [f"{owner.lower()}.{table_name.lower()}:SELECT"]
        assert (
            OracleEngine.ensure_table_privileges(oracle_connection, role_name, ((owner, table_name),), ("SELECT",))
            == []
        )
    finally:
        _execute_cleanup_ddl(oracle_connection, f"DROP USER {user_name} CASCADE", missing_error_code=1918)
        _execute_cleanup_ddl(oracle_connection, f"DROP ROLE {role_name}", missing_error_code=1919)
        _drop_table_if_present(oracle_connection, table_name)
