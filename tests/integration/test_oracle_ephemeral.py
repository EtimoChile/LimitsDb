import json
import os
import re
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


def _worker_process_names(result: subprocess.CompletedProcess[str]) -> set[str]:
    return set(re.findall(r"(?:Fork|Spawn)Process-\d+", result.stderr))


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
    parent_additional_filter_expr: str | None = None,
    parent_history_addtl_filter_expr: str | None = None,
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
        "parallel_max: 2",
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
    parent_filter_lines = ""
    if parent_additional_filter_expr is not None:
        parent_filter_lines += f"        additional_filter_expr: {json.dumps(parent_additional_filter_expr)}\n"
    if parent_history_addtl_filter_expr is not None:
        parent_filter_lines += f"        history_addtl_filter_expr: {json.dumps(parent_history_addtl_filter_expr)}\n"
    (schema_dir / "ilm.yml").write_text(
        f"""tables:
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
{parent_filter_lines}  - source_owner: LDBT_E2E_SOURCE
    history_owner: LDBT_E2E_HISTORY
    table_name: ILM_ORDER_LINES
    frecuency: D
    hint_expr: full(A) full(B) use_hash(A B)
    history_hint_expr: full(A)
    referencing_tables: ILM_ORDERS B
    join_expr: "@ JOIN LDBT_E2E_SOURCE.ILM_ORDERS B ON B.ID=A.ORDER_ID"
  - source_owner: LDBT_E2E_SOURCE
    history_owner: LDBT_E2E_HISTORY
    table_name: ILM_EVENTS
    frecuency: D
    hint_expr: full(A)
    conds:
      - is_active: true
        retain_months_source: 2
        retain_months_history: 3
        purge_date_expr: "@CREATED_AT"
""",
        encoding="utf-8",
    )


def _write_orphan_e2e_configuration(
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
    schema_dir = root / "schemas" / "orphan-e2e"
    schema_dir.mkdir(parents=True)
    (schema_dir / "config.yml").write_text(
        "\n".join(
            [
                "db_engine: oracle",
                f"source_dsn: {json.dumps(dsn)}",
                f"source_username: {source_user}",
                f"history_dsn: {json.dumps(dsn)}",
                f"history_username: {history_user}",
                f"admin_source_username: {admin_user}",
                f"admin_history_username: {admin_user}",
                "source_default_tablespace: USERS",
                "history_default_tablespace: USERS",
                "source_to_history_dblink_name: LDBT_ORPH_HIST",
                "history_to_source_dblink_name: LDBT_ORPH_SRC",
                "source_role_name: LDBT_ORPH_SOURCE_ROLE",
                "history_role_name: LDBT_ORPH_HISTORY_ROLE",
                "parallel_max: 1",
                "chunk_size: 2",
                "use_added_columns: true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
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
  - source_owner: LDBT_ORPH_SOURCE
    history_owner: LDBT_ORPH_HISTORY
    table_name: ORPHAN_PARENTS
    frecuency: D
    hint_expr: full(A)
    conds:
      - is_active: true
        retain_months_source: 2
        retain_months_history: 3
        purge_date_expr: "@CREATED_AT"
  - source_owner: LDBT_ORPH_SOURCE
    history_owner: LDBT_ORPH_HISTORY
    table_name: ORPHAN_CHILDREN
    frecuency: D
    hint_expr: full(A) full(B) use_hash(A B)
    history_hint_expr: full(A)
    referencing_tables: ORPHAN_PARENTS B
    join_expr: "@ JOIN LDBT_ORPH_SOURCE.ORPHAN_PARENTS B ON B.ID=A.PARENT_ID"
    source_orphan_purge: true
    orphan_check_column: B.ID
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


def _fetch_tend_log_count(connection: oracledb.Connection, *, owner: str, table_name: str, action: str) -> int:
    cursor = connection.cursor()
    try:
        cursor.execute(
            """SELECT COUNT(*)
                 FROM ldb_log
                WHERE log_owner = :1
                  AND log_table_name = :2
                  AND log_action = :3
                  AND log_status = 'TEND'""",
            [owner, table_name, action],
        )
        return cursor.fetchone()[0]
    finally:
        cursor.close()


def _fetch_column_names(connection: oracledb.Connection, table_name: str) -> set[str]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT column_name FROM user_tab_columns WHERE table_name = :1",
            [table_name.upper()],
        )
        return {row[0] for row in cursor}
    finally:
        cursor.close()


def _fetch_related_filter_values(connection: oracledb.Connection, *, column_name: str) -> list[tuple[int, str]]:
    cursor = connection.cursor()
    try:
        cursor.execute(f"SELECT line_id, {column_name} FROM ilm_order_lines ORDER BY line_id")
        return [(row[0], row[1]) for row in cursor]
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


@pytest.mark.parametrize(
    ("predicate_case", "source_filter", "history_filter"),
    [
        pytest.param("baseline", None, None, id="baseline"),
        pytest.param(
            "distinct_history_filter",
            "@SOURCE_STATE = 'READY'",
            "@HISTORY_STATE = 'PURGE'",
            id="preserves-distinct-history-filter-column",
        ),
        pytest.param(
            "shared_filter_column",
            "@LIFECYCLE_STATE IN ('KEEP', 'PURGE')",
            "@LIFECYCLE_STATE = 'PURGE'",
            id="rewrites-related-history-filter-column",
        ),
    ],
)
def test_limitsdb_happy_path_archives_and_purges_between_schemas(
    oracle_connection: oracledb.Connection,
    tmp_path: Path,
    predicate_case: str,
    source_filter: str | None,
    history_filter: str | None,
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
        parent_additional_filter_expr=source_filter,
        parent_history_addtl_filter_expr=history_filter,
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
                    source_state VARCHAR2(10) NOT NULL,
                    history_state VARCHAR2(10) NOT NULL,
                    lifecycle_state VARCHAR2(10) NOT NULL,
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
            source_cursor.execute(
                """CREATE TABLE ilm_events (
                    event_id NUMBER(10) PRIMARY KEY,
                    created_at DATE NOT NULL,
                    payload VARCHAR2(40) NOT NULL
                )"""
            )
            source_cursor.executemany(
                """INSERT INTO ilm_orders
                       (id, created_at, source_state, history_state, lifecycle_state, payload)
                     VALUES (:1, ADD_MONTHS(TRUNC(SYSDATE), :2), :3, :4, :5, :6)""",
                [
                    (1, -1, "READY", "KEEP", "KEEP", "recent"),
                    (2, -3, "READY", "KEEP", "KEEP", "archive"),
                    (3, -7, "READY", "PURGE", "PURGE", "purge"),
                ],
            )
            source_cursor.executemany(
                "INSERT INTO ilm_order_lines (line_id, order_id, payload) VALUES (:1, :2, :3)",
                [(11, 1, "recent-line"), (21, 2, "archive-line"), (31, 3, "purge-line-a"), (32, 3, "purge-line-b")],
            )
            source_cursor.executemany(
                "INSERT INTO ilm_events (event_id, created_at, payload) VALUES (:1, ADD_MONTHS(TRUNC(SYSDATE), :2), :3)",
                [(101, -1, "recent-event"), (102, -4, "archive-event"), (103, -8, "purge-event")],
            )
            source_connection.commit()
        finally:
            source_cursor.close()

        source_run = _run_cli(
            "ldb-run",
            *common_arguments,
            "--action",
            "SOURCE_ILM",
            "--mode",
            "EXECUTE",
            environment=cli_environment,
        )
        assert len(_worker_process_names(source_run)) >= 2

        history_connection = oracledb.connect(user=history_user, password=history_password, dsn=dsn)
        assert _fetch_ids(source_connection, "ilm_orders") == [1]
        assert _fetch_ids(history_connection, "ilm_orders") == [2, 3]
        assert _fetch_ids(source_connection, "ilm_order_lines", "line_id") == [11]
        assert _fetch_ids(history_connection, "ilm_order_lines", "line_id") == [21, 31, 32]
        assert _fetch_ids(source_connection, "ilm_events", "event_id") == [101]
        assert _fetch_ids(history_connection, "ilm_events", "event_id") == [102, 103]
        if predicate_case == "distinct_history_filter":
            assert "HISTORY_STATE_B" in _fetch_column_names(history_connection, "ILM_ORDER_LINES")
            assert _fetch_related_filter_values(history_connection, column_name="HISTORY_STATE_B") == [
                (21, "KEEP"),
                (31, "PURGE"),
                (32, "PURGE"),
            ]
        elif predicate_case == "shared_filter_column":
            assert "LIFECYCLE_STATE_B" in _fetch_column_names(history_connection, "ILM_ORDER_LINES")
            assert _fetch_related_filter_values(history_connection, column_name="LIFECYCLE_STATE_B") == [
                (21, "KEEP"),
                (31, "PURGE"),
                (32, "PURGE"),
            ]

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
            source_cursor.execute(
                """SELECT ctl_action, ctl_status, ctl_rows_processed
                     FROM ldb_ctl
                    WHERE ctl_owner = :1 AND ctl_table_name = 'ILM_EVENTS'""",
                [source_user],
            )
            assert source_cursor.fetchone() == ("SOURCE_ILM", "TEND", 2)
        finally:
            source_cursor.close()

        for table_name in ("ILM_ORDERS", "ILM_ORDER_LINES", "ILM_EVENTS"):
            assert (
                _fetch_tend_log_count(source_connection, owner=source_user, table_name=table_name, action="SOURCE_ILM")
                == 1
            )

        source_rerun = _run_cli(
            "ldb-run",
            *common_arguments,
            "--action",
            "SOURCE_ILM",
            "--mode",
            "EXECUTE",
            environment=cli_environment,
        )
        assert _worker_process_names(source_rerun) == set()
        assert "No tables to process." in source_rerun.stderr
        assert _fetch_ids(source_connection, "ilm_orders") == [1]
        assert _fetch_ids(history_connection, "ilm_orders") == [2, 3]
        assert _fetch_ids(source_connection, "ilm_order_lines", "line_id") == [11]
        assert _fetch_ids(history_connection, "ilm_order_lines", "line_id") == [21, 31, 32]
        assert _fetch_ids(source_connection, "ilm_events", "event_id") == [101]
        assert _fetch_ids(history_connection, "ilm_events", "event_id") == [102, 103]
        for table_name in ("ILM_ORDERS", "ILM_ORDER_LINES", "ILM_EVENTS"):
            assert (
                _fetch_tend_log_count(source_connection, owner=source_user, table_name=table_name, action="SOURCE_ILM")
                == 1
            )

        history_run = _run_cli(
            "ldb-run",
            *common_arguments,
            "--action",
            "HISTORY_ILM",
            "--mode",
            "EXECUTE",
            environment=cli_environment,
        )
        assert len(_worker_process_names(history_run)) >= 2

        assert _fetch_ids(source_connection, "ilm_orders") == [1]
        assert _fetch_ids(history_connection, "ilm_orders") == [2]
        assert _fetch_ids(source_connection, "ilm_order_lines", "line_id") == [11]
        assert _fetch_ids(history_connection, "ilm_order_lines", "line_id") == [21]
        assert _fetch_ids(source_connection, "ilm_events", "event_id") == [101]
        assert _fetch_ids(history_connection, "ilm_events", "event_id") == [102]
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
                """SELECT ctl_action, ctl_status, ctl_rows_processed
                     FROM ldb_ctl
                    WHERE ctl_owner = :1 AND ctl_table_name = 'ILM_EVENTS'""",
                [source_user],
            )
            assert history_cursor.fetchone() == ("HISTORY_ILM", "TEND", 1)
        finally:
            history_cursor.close()

        for table_name in ("ILM_ORDERS", "ILM_ORDER_LINES", "ILM_EVENTS"):
            assert (
                _fetch_tend_log_count(
                    history_connection, owner=source_user, table_name=table_name, action="HISTORY_ILM"
                )
                == 1
            )

        history_rerun = _run_cli(
            "ldb-run",
            *common_arguments,
            "--action",
            "HISTORY_ILM",
            "--mode",
            "EXECUTE",
            environment=cli_environment,
        )
        assert _worker_process_names(history_rerun) == set()
        assert "No tables to process." in history_rerun.stderr
        assert _fetch_ids(history_connection, "ilm_orders") == [2]
        assert _fetch_ids(history_connection, "ilm_order_lines", "line_id") == [21]
        assert _fetch_ids(history_connection, "ilm_events", "event_id") == [102]
        for table_name in ("ILM_ORDERS", "ILM_ORDER_LINES", "ILM_EVENTS"):
            assert (
                _fetch_tend_log_count(
                    history_connection, owner=source_user, table_name=table_name, action="HISTORY_ILM"
                )
                == 1
            )
    finally:
        if source_connection is not None:
            source_connection.close()
        if history_connection is not None:
            history_connection.close()
        for user in (source_user, history_user):
            _execute_cleanup_ddl(oracle_connection, f"DROP USER {user} CASCADE", missing_error_code=1918)
        for role in (source_role, history_role):
            _execute_cleanup_ddl(oracle_connection, f"DROP ROLE {role}", missing_error_code=1919)


def _fetch_constraint_type(connection: oracledb.Connection, table_name: str, constraint_type: str) -> list[str]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT constraint_name FROM user_constraints WHERE table_name = :1 AND constraint_type = :2",
            [table_name.upper(), constraint_type],
        )
        return [row[0] for row in cursor]
    finally:
        cursor.close()


def _fetch_index_columns(connection: oracledb.Connection, index_name: str) -> list[str]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT column_name FROM user_ind_columns WHERE index_name = :1 ORDER BY column_position",
            [index_name.upper()],
        )
        return [row[0] for row in cursor]
    finally:
        cursor.close()


def _fetch_column_type(connection: oracledb.Connection, table_name: str, column_name: str) -> tuple[str, int | None]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT data_type, data_length FROM user_tab_columns WHERE table_name = :1 AND column_name = :2",
            [table_name.upper(), column_name.upper()],
        )
        row = cursor.fetchone()
        return (row[0], row[1]) if row else ("", None)
    finally:
        cursor.close()


def test_oracle_engine_ensure_table_structure_reconciles_and_is_idempotent(
    oracle_connection: oracledb.Connection,
):
    username = os.environ["LDB_ORACLE_TEST_USER"].upper()
    parent_table = "LDBT_STR_PARENT"
    test_table = "LDBT_STR_TEST"
    _drop_table_if_present(oracle_connection, test_table)
    _drop_table_if_present(oracle_connection, parent_table)

    cursor = oracle_connection.cursor()
    try:
        cursor.execute(f"CREATE TABLE {parent_table} (id NUMBER(10) CONSTRAINT {parent_table}_pk PRIMARY KEY)")
        cursor.execute(
            f"""CREATE TABLE {test_table} (
                id NUMBER(5),
                parent_id NUMBER(10),
                name VARCHAR2(10),
                CONSTRAINT ldbt_str_fk FOREIGN KEY (parent_id) REFERENCES {parent_table}(id)
            )"""
        )
        oracle_connection.commit()
    finally:
        cursor.close()

    desired = TableDefinition(
        owner=username,
        name=test_table,
        columns=(
            ColumnDefinition(name="ID", data_type="number", precision=10, nullable=False),
            ColumnDefinition(name="PARENT_ID", data_type="number", precision=10),
            ColumnDefinition(name="NAME", data_type="varchar2", length=40),
            ColumnDefinition(name="NOTES", data_type="varchar2", length=100),
        ),
        primary_key=("ID",),
        indexes=(IndexDefinition(name="LDBT_STR_NAME_IX", columns=("NAME",)),),
    )

    try:
        OracleEngine.ensure_table_structure(oracle_connection, desired, {})

        dtype, length = _fetch_column_type(oracle_connection, test_table, "NAME")
        assert dtype == "VARCHAR2" and length == 40

        dtype_notes, _ = _fetch_column_type(oracle_connection, test_table, "NOTES")
        assert dtype_notes == "VARCHAR2"

        pk_constraints = _fetch_constraint_type(oracle_connection, test_table, "P")
        assert len(pk_constraints) == 1

        fk_constraints = _fetch_constraint_type(oracle_connection, test_table, "R")
        assert len(fk_constraints) == 1

        assert _fetch_index_columns(oracle_connection, "LDBT_STR_NAME_IX") == ["NAME"]

        # Second call must be idempotent
        OracleEngine.ensure_table_structure(oracle_connection, desired, {})

        assert _fetch_constraint_type(oracle_connection, test_table, "P") == pk_constraints
        assert _fetch_constraint_type(oracle_connection, test_table, "R") == fk_constraints
    finally:
        _drop_table_if_present(oracle_connection, test_table)
        _drop_table_if_present(oracle_connection, parent_table)


def _write_failure_e2e_configuration(
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
    schema_dir = root / "schemas" / "err-e2e"
    schema_dir.mkdir(parents=True)
    (schema_dir / "config.yml").write_text(
        "\n".join(
            [
                "db_engine: oracle",
                f"source_dsn: {json.dumps(dsn)}",
                f"source_username: {source_user}",
                f"history_dsn: {json.dumps(dsn)}",
                f"history_username: {history_user}",
                f"admin_source_username: {admin_user}",
                f"admin_history_username: {admin_user}",
                "source_default_tablespace: USERS",
                "history_default_tablespace: USERS",
                "source_to_history_dblink_name: LDBT_ERR_HIST",
                "history_to_source_dblink_name: LDBT_ERR_SRC",
                "source_role_name: LDBT_ERR_SOURCE_ROLE",
                "history_role_name: LDBT_ERR_HISTORY_ROLE",
                "parallel_max: 1",
                "chunk_size: 10",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
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
  - source_owner: LDBT_ERR_SOURCE
    history_owner: LDBT_ERR_HISTORY
    table_name: ILM_ERRTBL
    frecuency: D
    hint_expr: full(A)
    conds:
      - is_active: true
        retain_months_source: 2
        retain_months_history: 3
        purge_date_expr: "@CREATED_AT"
""",
        encoding="utf-8",
    )


def _fetch_ctl_status(connection: oracledb.Connection, *, owner: str, table_name: str, action: str) -> str | None:
    cursor = connection.cursor()
    try:
        cursor.execute(
            """SELECT ctl_status FROM ldb_ctl
                WHERE ctl_owner = :1 AND ctl_table_name = :2 AND ctl_action = :3""",
            [owner, table_name, action],
        )
        row = cursor.fetchone()
        return str(row[0]) if row else None
    finally:
        cursor.close()


def _fetch_error_log_count(connection: oracledb.Connection, *, owner: str, table_name: str, action: str) -> int:
    cursor = connection.cursor()
    try:
        cursor.execute(
            """SELECT COUNT(*) FROM ldb_log
                WHERE log_owner = :1 AND log_table_name = :2 AND log_action = :3 AND log_status = 'ERROR'""",
            [owner, table_name, action],
        )
        return cursor.fetchone()[0]
    finally:
        cursor.close()


def test_e2e_controlled_failure_records_error_status_and_allows_recovery(
    oracle_connection: oracledb.Connection, tmp_path: Path
):
    source_user = "LDBT_ERR_SOURCE"
    history_user = "LDBT_ERR_HISTORY"
    source_role = "LDBT_ERR_SOURCE_ROLE"
    history_role = "LDBT_ERR_HISTORY_ROLE"
    for user in (source_user, history_user):
        _execute_cleanup_ddl(oracle_connection, f"DROP USER {user} CASCADE", missing_error_code=1918)
    for role in (source_role, history_role):
        _execute_cleanup_ddl(oracle_connection, f"DROP ROLE {role}", missing_error_code=1919)

    dsn = os.environ["LDB_ORACLE_TEST_DSN"]
    admin_user = os.environ["LDB_ORACLE_TEST_USER"]
    admin_password = os.environ["LDB_ORACLE_TEST_PASSWORD"]
    source_password = f"LdbErr_{secrets.token_hex(16)}"
    history_password = f"LdbErr_{secrets.token_hex(16)}"
    config_root = tmp_path / "err-config"
    _write_failure_e2e_configuration(
        config_root,
        dsn=dsn,
        admin_user=admin_user,
        admin_password=admin_password,
        source_user=source_user,
        source_password=source_password,
        history_user=history_user,
        history_password=history_password,
    )
    cli_environment = _e2e_environment(tmp_path / "err-home")
    common_arguments = ("--schema", "err-e2e", "--config-dir", str(config_root))

    source_connection: oracledb.Connection | None = None
    history_connection: oracledb.Connection | None = None
    try:
        _run_cli("ldb-impl", *common_arguments, environment=cli_environment)

        source_connection = oracledb.connect(user=source_user, password=source_password, dsn=dsn)
        source_cursor = source_connection.cursor()
        try:
            # Deliberately omit CREATED_AT so the ILM SQL block fails at runtime (ORA-00904)
            source_cursor.execute("CREATE TABLE ilm_errtbl (id NUMBER(10) PRIMARY KEY, payload VARCHAR2(40) NOT NULL)")
            source_cursor.execute("INSERT INTO ilm_errtbl (id, payload) VALUES (1, 'row-without-date')")
            source_connection.commit()
        finally:
            source_cursor.close()

        failed_run = subprocess.run(
            [
                shutil.which("ldb-run", path=cli_environment.get("PATH")),
                *common_arguments,
                "--action",
                "SOURCE_ILM",
                "--mode",
                "EXECUTE",
            ],
            check=False,
            capture_output=True,
            env=cli_environment,
            text=True,
            timeout=180,
        )
        assert failed_run.returncode != 0, "expected ldb-run to exit non-zero after table failure"

        assert (
            _fetch_ctl_status(source_connection, owner=source_user, table_name="ILM_ERRTBL", action="SOURCE_ILM")
            == "ERROR"
        )
        assert (
            _fetch_error_log_count(source_connection, owner=source_user, table_name="ILM_ERRTBL", action="SOURCE_ILM")
            >= 1
        )

        # Fix: recreate with the correct schema and insert an archivable row
        source_cursor = source_connection.cursor()
        try:
            source_cursor.execute("DROP TABLE ilm_errtbl PURGE")
            source_cursor.execute(
                """CREATE TABLE ilm_errtbl (
                    id NUMBER(10) PRIMARY KEY,
                    created_at DATE NOT NULL,
                    payload VARCHAR2(40) NOT NULL
                )"""
            )
            source_cursor.execute(
                "INSERT INTO ilm_errtbl (id, created_at, payload) VALUES (2, ADD_MONTHS(TRUNC(SYSDATE), -3), 'archivable')"
            )
            source_connection.commit()
        finally:
            source_cursor.close()

        _run_cli(
            "ldb-run", *common_arguments, "--action", "SOURCE_ILM", "--mode", "EXECUTE", environment=cli_environment
        )

        history_connection = oracledb.connect(user=history_user, password=history_password, dsn=dsn)
        assert _fetch_ids(source_connection, "ilm_errtbl") == []
        assert _fetch_ids(history_connection, "ilm_errtbl") == [2]
        assert (
            _fetch_ctl_status(source_connection, owner=source_user, table_name="ILM_ERRTBL", action="SOURCE_ILM")
            == "TEND"
        )
        assert (
            _fetch_tend_log_count(source_connection, owner=source_user, table_name="ILM_ERRTBL", action="SOURCE_ILM")
            == 1
        )
    finally:
        if source_connection is not None:
            source_connection.close()
        if history_connection is not None:
            history_connection.close()
        for user in (source_user, history_user):
            _execute_cleanup_ddl(oracle_connection, f"DROP USER {user} CASCADE", missing_error_code=1918)
        for role in (source_role, history_role):
            _execute_cleanup_ddl(oracle_connection, f"DROP ROLE {role}", missing_error_code=1919)


def test_source_orphan_purge_archives_orphan_and_snapshots_process_date(
    oracle_connection: oracledb.Connection, tmp_path: Path
):
    source_user = "LDBT_ORPH_SOURCE"
    history_user = "LDBT_ORPH_HISTORY"
    source_role = "LDBT_ORPH_SOURCE_ROLE"
    history_role = "LDBT_ORPH_HISTORY_ROLE"
    for user in (source_user, history_user):
        _execute_cleanup_ddl(oracle_connection, f"DROP USER {user} CASCADE", missing_error_code=1918)
    for role in (source_role, history_role):
        _execute_cleanup_ddl(oracle_connection, f"DROP ROLE {role}", missing_error_code=1919)

    dsn = os.environ["LDB_ORACLE_TEST_DSN"]
    admin_user = os.environ["LDB_ORACLE_TEST_USER"]
    admin_password = os.environ["LDB_ORACLE_TEST_PASSWORD"]
    source_password = f"LdbOrph_{secrets.token_hex(16)}"
    history_password = f"LdbOrph_{secrets.token_hex(16)}"
    config_root = tmp_path / "orphan-config"
    _write_orphan_e2e_configuration(
        config_root,
        dsn=dsn,
        admin_user=admin_user,
        admin_password=admin_password,
        source_user=source_user,
        source_password=source_password,
        history_user=history_user,
        history_password=history_password,
    )
    cli_environment = _e2e_environment(tmp_path / "orphan-home")
    common_arguments = ("--schema", "orphan-e2e", "--config-dir", str(config_root))

    source_connection: oracledb.Connection | None = None
    history_connection: oracledb.Connection | None = None
    try:
        _run_cli("ldb-impl", *common_arguments, environment=cli_environment)
        source_connection = oracledb.connect(user=source_user, password=source_password, dsn=dsn)
        source_cursor = source_connection.cursor()
        try:
            source_cursor.execute("CREATE TABLE orphan_parents (id NUMBER(10) PRIMARY KEY, created_at DATE NOT NULL)")
            source_cursor.execute(
                """CREATE TABLE orphan_children (
                    line_id NUMBER(10) PRIMARY KEY,
                    parent_id NUMBER(10) NOT NULL,
                    payload VARCHAR2(40) NOT NULL
                )"""
            )
            source_cursor.execute(
                "INSERT INTO orphan_parents (id, created_at) VALUES (1, ADD_MONTHS(TRUNC(SYSDATE), -1))"
            )
            source_cursor.executemany(
                "INSERT INTO orphan_children (line_id, parent_id, payload) VALUES (:1, :2, :3)",
                [(11, 1, "has-parent"), (99, 999, "orphan")],
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
        assert _fetch_ids(source_connection, "orphan_children", "line_id") == [11]
        assert _fetch_ids(history_connection, "orphan_children", "line_id") == [99]
        history_cursor = history_connection.cursor()
        try:
            history_cursor.execute(
                """SELECT COUNT(*)
                     FROM orphan_children
                    WHERE line_id = 99
                      AND ldb_is_orphan = 'Y'
                      AND TRUNC(ldb_date_b) = TRUNC(SYSDATE)"""
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
