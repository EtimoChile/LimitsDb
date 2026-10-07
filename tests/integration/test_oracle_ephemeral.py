import json
import os
import secrets
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

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


def _run_cli(command: str, *arguments: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    executable = shutil.which(command, path=environment.get("PATH"))
    assert executable is not None, f"installed CLI entry point not found: {command}"
    result = subprocess.run(
        [executable, *arguments],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"{command} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return result


def _write_e2e_configuration(
    root: Path,
    *,
    dsn: str,
    admin_user: str,
    admin_password: str,
    source_user: str,
    source_password: str,
    history_user: str,
    history_password: str,
) -> None:
    schema_dir = root / "schemas" / "e2e"
    schema_dir.mkdir(parents=True)
    config_lines = [
        "db_engine: oracle",
        f"source_dsn: {json.dumps(dsn)}",
        f"source_username: {source_user}",
        f"history_dsn: {json.dumps(dsn)}",
        f"history_username: {history_user}",
        f"admin_source_username: {admin_user}",
        f"admin_history_username: {admin_user}",
        "source_default_tablespace: USERS",
        "history_default_tablespace: USERS",
        "source_to_history_dblink_name: LDBT_E2E_HIST",
        "history_to_source_dblink_name: LDBT_E2E_SRC",
        "source_role_name: LDBT_E2E_SOURCE_ROLE",
        "history_role_name: LDBT_E2E_HISTORY_ROLE",
        "parallel_max: 1",
        "chunk_size: 2",
    ]
    (schema_dir / "config.yml").write_text("\n".join(config_lines) + "\n", encoding="utf-8")
    (schema_dir / "secrets.json").write_text(
        json.dumps(
            {
                "source_password": source_password,
                "history_password": history_password,
                "admin_source_password": admin_password,
                "admin_history_password": admin_password,
            }
        ),
        encoding="utf-8",
    )
    (schema_dir / "ilm.yml").write_text(
        """tables:
  - source_owner: LDBT_E2E_SOURCE
    history_owner: LDBT_E2E_HISTORY
    table_name: ILM_ORDERS
    frecuency: D
    hint_expr: full(A)
    conds:
      - is_active: true
        retain_months_source: 2
        retain_months_history: 3
        purge_date_expr: "@CREATED_AT"
  - source_owner: LDBT_E2E_SOURCE
    history_owner: LDBT_E2E_HISTORY
    table_name: ILM_ORDER_LINES
    frecuency: D
    hint_expr: full(A) full(B) use_hash(A B)
    history_hint_expr: full(A)
    referencing_tables: ILM_ORDERS B
    join_expr: "@ JOIN LDBT_E2E_SOURCE.ILM_ORDERS B ON B.ID=A.ORDER_ID"
""",
        encoding="utf-8",
    )


def _e2e_environment(home: Path) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("LDB_")}
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
    return environment


def _fetch_ids(connection: oracledb.Connection, table_name: str, id_column: str = "id") -> list[int]:
    cursor = connection.cursor()
    try:
        cursor.execute(f"SELECT {id_column} FROM {table_name} ORDER BY {id_column}")
        return [row[0] for row in cursor]
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
        password=f"LdbCI_{secrets.token_hex(16)}",
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


def test_limitsdb_happy_path_archives_and_purges_between_schemas(
    oracle_connection: oracledb.Connection, tmp_path: Path
):
    source_user = "LDBT_E2E_SOURCE"
    history_user = "LDBT_E2E_HISTORY"
    source_role = "LDBT_E2E_SOURCE_ROLE"
    history_role = "LDBT_E2E_HISTORY_ROLE"
    for user in (source_user, history_user):
        _execute_cleanup_ddl(oracle_connection, f"DROP USER {user} CASCADE", missing_error_code=1918)
    for role in (source_role, history_role):
        _execute_cleanup_ddl(oracle_connection, f"DROP ROLE {role}", missing_error_code=1919)

    dsn = os.environ["LDB_ORACLE_TEST_DSN"]
    admin_user = os.environ["LDB_ORACLE_TEST_USER"]
    admin_password = os.environ["LDB_ORACLE_TEST_PASSWORD"]
    source_password = f"LdbE2E_{secrets.token_hex(16)}"
    history_password = f"LdbE2E_{secrets.token_hex(16)}"
    config_root = tmp_path / "config"
    _write_e2e_configuration(
        config_root,
        dsn=dsn,
        admin_user=admin_user,
        admin_password=admin_password,
        source_user=source_user,
        source_password=source_password,
        history_user=history_user,
        history_password=history_password,
    )
    cli_environment = _e2e_environment(tmp_path / "home")
    common_arguments = ("--schema", "e2e", "--config-dir", str(config_root))

    source_connection: oracledb.Connection | None = None
    history_connection: oracledb.Connection | None = None
    try:
        _run_cli("ldb-impl", *common_arguments, environment=cli_environment)

        encrypted_secrets = json.loads((config_root / "schemas" / "e2e" / "secrets.json").read_text("utf-8"))
        assert all(value.startswith("enc:v1:aes256gcm:") for value in encrypted_secrets.values())

        cursor = oracle_connection.cursor()
        try:
            cursor.execute(
                "SELECT username FROM dba_users WHERE username IN (:1, :2)",
                [source_user, history_user],
            )
            assert {row[0] for row in cursor} == {source_user, history_user}
            cursor.execute(
                """SELECT owner, object_name, object_type
                     FROM dba_objects
                    WHERE owner IN (:1, :2)
                      AND object_name IN ('LDB_CTL', 'LDB_LOG', 'LDB_CNF', 'LDB_LOG_ID', 'LDB_CNF_ID',
                                          'CHECK_SAVE_STATUS', 'CHECK_REFERENCING_TABLES', 'T_REFERENCING_TABLES')""",
                [source_user, history_user],
            )
            objects = {(row[0], row[1], row[2]) for row in cursor}
            for owner in (source_user, history_user):
                assert (owner, "LDB_CTL", "TABLE") in objects
                assert (owner, "LDB_LOG", "TABLE") in objects
                assert (owner, "LDB_CNF", "TABLE") in objects
                assert (owner, "LDB_LOG_ID", "SEQUENCE") in objects
                assert (owner, "LDB_CNF_ID", "SEQUENCE") in objects
                assert (owner, "CHECK_SAVE_STATUS", "PROCEDURE") in objects
                assert (owner, "CHECK_REFERENCING_TABLES", "PROCEDURE") in objects
                assert (owner, "T_REFERENCING_TABLES", "TYPE") in objects
        finally:
            cursor.close()

        source_connection = oracledb.connect(user=source_user, password=source_password, dsn=dsn)
        source_cursor = source_connection.cursor()
        try:
            source_cursor.execute(
                """CREATE TABLE ilm_orders (
                    id NUMBER(10) PRIMARY KEY,
                    created_at DATE NOT NULL,
                    payload VARCHAR2(40) NOT NULL
                )"""
            )
            source_cursor.execute(
                """CREATE TABLE ilm_order_lines (
                    line_id NUMBER(10) PRIMARY KEY,
                    order_id NUMBER(10) NOT NULL,
                    payload VARCHAR2(40) NOT NULL,
                    CONSTRAINT ilm_order_lines_order_fk
                        FOREIGN KEY (order_id) REFERENCES ilm_orders (id)
                )"""
            )
            source_cursor.executemany(
                "INSERT INTO ilm_orders (id, created_at, payload) VALUES (:1, ADD_MONTHS(TRUNC(SYSDATE), :2), :3)",
                [(1, -1, "recent"), (2, -3, "archive"), (3, -7, "purge")],
            )
            source_cursor.executemany(
                "INSERT INTO ilm_order_lines (line_id, order_id, payload) VALUES (:1, :2, :3)",
                [(11, 1, "recent-line"), (21, 2, "archive-line"), (31, 3, "purge-line-a"), (32, 3, "purge-line-b")],
            )
            source_connection.commit()
        finally:
            source_cursor.close()

        _run_cli(
            "ldb-run",
            *common_arguments,
            "--action",
            "SOURCE_ILM",
            "--mode",
            "EXECUTE",
            environment=cli_environment,
        )

        history_connection = oracledb.connect(user=history_user, password=history_password, dsn=dsn)
        assert _fetch_ids(source_connection, "ilm_orders") == [1]
        assert _fetch_ids(history_connection, "ilm_orders") == [2, 3]
        assert _fetch_ids(source_connection, "ilm_order_lines", "line_id") == [11]
        assert _fetch_ids(history_connection, "ilm_order_lines", "line_id") == [21, 31, 32]

        source_cursor = source_connection.cursor()
        try:
            source_cursor.execute(
                """SELECT ctl_action, ctl_status, ctl_rows_processed
                     FROM ldb_ctl
                    WHERE ctl_owner = :1 AND ctl_table_name = 'ILM_ORDERS'""",
                [source_user],
            )
            assert source_cursor.fetchone() == ("SOURCE_ILM", "TEND", 2)
            source_cursor.execute(
                """SELECT ctl_action, ctl_status, ctl_rows_processed
                     FROM ldb_ctl
                    WHERE ctl_owner = :1 AND ctl_table_name = 'ILM_ORDER_LINES'""",
                [source_user],
            )
            assert source_cursor.fetchone() == ("SOURCE_ILM", "TEND", 3)
        finally:
            source_cursor.close()

        _run_cli(
            "ldb-run",
            *common_arguments,
            "--action",
            "HISTORY_ILM",
            "--mode",
            "EXECUTE",
            environment=cli_environment,
        )

        assert _fetch_ids(source_connection, "ilm_orders") == [1]
        assert _fetch_ids(history_connection, "ilm_orders") == [2]
        assert _fetch_ids(source_connection, "ilm_order_lines", "line_id") == [11]
        assert _fetch_ids(history_connection, "ilm_order_lines", "line_id") == [21]
        history_cursor = history_connection.cursor()
        try:
            history_cursor.execute(
                """SELECT ctl_action, ctl_status, ctl_rows_processed
                     FROM ldb_ctl
                    WHERE ctl_owner = :1 AND ctl_table_name = 'ILM_ORDERS'""",
                [source_user],
            )
            assert history_cursor.fetchone() == ("HISTORY_ILM", "TEND", 1)
            history_cursor.execute(
                """SELECT ctl_action, ctl_status, ctl_rows_processed
                     FROM ldb_ctl
                    WHERE ctl_owner = :1 AND ctl_table_name = 'ILM_ORDER_LINES'""",
                [source_user],
            )
            assert history_cursor.fetchone() == ("HISTORY_ILM", "TEND", 2)
            history_cursor.execute(
                """SELECT COUNT(*)
                     FROM ldb_log
                    WHERE log_owner = :1
                      AND log_table_name = 'ILM_ORDERS'
                      AND log_action = 'HISTORY_ILM'
                      AND log_status = 'TEND'""",
                [source_user],
            )
            assert history_cursor.fetchone()[0] == 1
            history_cursor.execute(
                """SELECT COUNT(*)
                     FROM ldb_log
                    WHERE log_owner = :1
                      AND log_table_name = 'ILM_ORDER_LINES'
                      AND log_action = 'HISTORY_ILM'
                      AND log_status = 'TEND'""",
                [source_user],
            )
            assert history_cursor.fetchone()[0] == 1
        finally:
            history_cursor.close()
    finally:
        if source_connection is not None:
            source_connection.close()
        if history_connection is not None:
            history_connection.close()
        for user in (source_user, history_user):
            _execute_cleanup_ddl(oracle_connection, f"DROP USER {user} CASCADE", missing_error_code=1918)
        for role in (source_role, history_role):
            _execute_cleanup_ddl(oracle_connection, f"DROP ROLE {role}", missing_error_code=1919)
