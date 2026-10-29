import json
import os
import secrets
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pg8000.dbapi
import pytest

pytestmark = pytest.mark.postgres_integration

SOURCE_DB = "ldbt_src"
HISTORY_DB = "ldbt_hist"
SOURCE_USER = "ldbt_source"
HISTORY_USER = "ldbt_history"
ROLES = ("ldbt_source_role", "ldbt_history_role")


def _server() -> tuple[str, int]:
    host_port = os.getenv("LDB_PG_TEST_HOST")
    if not host_port or not os.getenv("LDB_PG_TEST_PASSWORD"):
        pytest.skip("ephemeral PostgreSQL is not configured; set LDB_PG_TEST_HOST and LDB_PG_TEST_PASSWORD")
    host, _, port = host_port.partition(":")
    return host, int(port or 5432)


def _connect(database: str, user: str | None = None, password: str | None = None) -> pg8000.dbapi.Connection:
    host, port = _server()
    return pg8000.dbapi.connect(
        host=host,
        port=port,
        database=database,
        user=user or os.environ["LDB_PG_TEST_USER"],
        password=password or os.environ["LDB_PG_TEST_PASSWORD"],
    )


def _admin_execute(statement: str) -> None:
    connection = _connect("postgres")
    connection.autocommit = True
    try:
        cursor = connection.cursor()
        cursor.execute(statement)
        cursor.close()
    finally:
        connection.close()


def _fetch(connection: pg8000.dbapi.Connection, sql: str, params: list | None = None) -> list[tuple]:
    cursor = connection.cursor()
    try:
        cursor.execute(sql, params or [])
        return [tuple(row) for row in cursor.fetchall()]
    finally:
        cursor.close()
        # End the read transaction: an open one would hold locks that block the runner's ALTER TABLE
        connection.rollback()


@pytest.fixture
def clean_databases() -> Iterator[None]:
    _server()

    def reset() -> None:
        for database in (SOURCE_DB, HISTORY_DB):
            _admin_execute(f"DROP DATABASE IF EXISTS {database}")
        for role in (SOURCE_USER, HISTORY_USER, *ROLES):
            _admin_execute(f"DROP ROLE IF EXISTS {role}")

    reset()
    for database in (SOURCE_DB, HISTORY_DB):
        _admin_execute(f"CREATE DATABASE {database}")
    yield
    reset()


def _write_configuration(
    root: Path,
    *,
    source_password: str,
    history_password: str,
    source_filter: str | None = None,
    history_filter: str | None = None,
) -> None:
    host_port = os.environ["LDB_PG_TEST_HOST"]
    admin_user = os.environ["LDB_PG_TEST_USER"]
    admin_password = os.environ["LDB_PG_TEST_PASSWORD"]
    schema_dir = root / "schemas" / "e2e"
    schema_dir.mkdir(parents=True)
    (schema_dir / "config.yml").write_text(
        "\n".join(
            [
                "db_engine: postgresql",
                f"source_dsn: {json.dumps(f'{host_port}/{SOURCE_DB}')}",
                f"source_username: {SOURCE_USER}",
                f"history_dsn: {json.dumps(f'{host_port}/{HISTORY_DB}')}",
                f"history_username: {HISTORY_USER}",
                f"admin_source_username: {admin_user}",
                f"admin_history_username: {admin_user}",
                "source_to_history_dblink_name: ldbt_e2e_hist",
                "history_to_source_dblink_name: ldbt_e2e_src",
                f"source_role_name: {ROLES[0]}",
                f"history_role_name: {ROLES[1]}",
                "parallel_max: 2",
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
    filter_lines = ""
    if source_filter is not None:
        filter_lines += f"        additional_filter_expr: {json.dumps(source_filter)}\n"
    if history_filter is not None:
        filter_lines += f"        history_addtl_filter_expr: {json.dumps(history_filter)}\n"
    (schema_dir / "ilm.yml").write_text(
        f"""tables:
  - source_owner: {SOURCE_USER}
    history_owner: {HISTORY_USER}
    table_name: ilm_orders
    frecuency: D
    conds:
      - is_active: true
        retain_months_source: 2
        retain_months_history: 3
        purge_date_expr: "@CREATED_AT"
{filter_lines}  - source_owner: {SOURCE_USER}
    history_owner: {HISTORY_USER}
    table_name: ilm_order_lines
    frecuency: D
    referencing_tables: ilm_orders B
    join_expr: "@ JOIN {SOURCE_USER}.ilm_orders B ON B.ID=A.ORDER_ID"
""",
        encoding="utf-8",
    )


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


def _ids(connection: pg8000.dbapi.Connection, table: str, column: str = "id") -> list[int]:
    return [int(row[0]) for row in _fetch(connection, f"SELECT {column} FROM {table} ORDER BY {column}")]


def _control_status(connection: pg8000.dbapi.Connection, table: str) -> tuple | None:
    rows = _fetch(
        connection,
        'SELECT "CTL_ACTION", "CTL_STATUS", "CTL_ROWS_PROCESSED" FROM "LDB_CTL" WHERE lower("CTL_TABLE_NAME") = %s',
        [table],
    )
    return rows[0] if rows else None


@pytest.mark.parametrize(
    ("snapshot_column", "source_filter", "history_filter"),
    [
        pytest.param(None, None, None, id="baseline"),
        pytest.param(
            "history_state_b",
            "@SOURCE_STATE = 'READY'",
            "@HISTORY_STATE = 'PURGE'",
            id="distinct-history-filter-column",
        ),
        pytest.param(
            "lifecycle_state_b",
            "@LIFECYCLE_STATE IN ('KEEP', 'PURGE')",
            "@LIFECYCLE_STATE = 'PURGE'",
            id="shared-filter-column",
        ),
    ],
)
def test_source_and_history_ilm_move_and_purge_rows_through_dblink(
    clean_databases: None,
    tmp_path: Path,
    snapshot_column: str | None,
    source_filter: str | None,
    history_filter: str | None,
):
    # Spec: README § Run ILM (SOURCE_ILM, HISTORY_ILM) — SOURCE_ILM moves rows past the source retention to history
    # through the database link; HISTORY_ILM purges rows past the history retention; both are idempotent on re-run;
    # filter columns of the parent are snapshotted on the history children in lower case (the default case)
    # Given: users and control objects bootstrapped by ldb-impl on two databases of one server
    source_password = f"LdbE2E_{secrets.token_hex(11)}"
    history_password = f"LdbE2E_{secrets.token_hex(11)}"
    config_root = tmp_path / "config"
    _write_configuration(
        config_root,
        source_password=source_password,
        history_password=history_password,
        source_filter=source_filter,
        history_filter=history_filter,
    )
    environment = {key: value for key, value in os.environ.items() if not key.startswith("LDB_")}
    environment["HOME"] = environment["USERPROFILE"] = str(tmp_path / "home")
    common = ("--schema", "e2e", "--config-dir", str(config_root))

    _run_cli("ldb-impl", *common, environment=environment)

    secrets_on_disk = json.loads((config_root / "schemas" / "e2e" / "secrets.json").read_text("utf-8"))
    assert all(value.startswith("enc:v1:aes256gcm:") for value in secrets_on_disk.values())

    source = _connect(SOURCE_DB, SOURCE_USER, source_password)
    history: pg8000.dbapi.Connection | None = None
    try:
        # And: source tables with rows of different ages
        cursor = source.cursor()
        cursor.execute(
            f"CREATE TABLE {SOURCE_USER}.ilm_orders (id numeric(10) PRIMARY KEY, created_at timestamp(0) NOT NULL,"
            " source_state varchar(10) NOT NULL, history_state varchar(10) NOT NULL,"
            " lifecycle_state varchar(10) NOT NULL, payload varchar(40) NOT NULL)"
        )
        cursor.execute(
            f"CREATE TABLE {SOURCE_USER}.ilm_order_lines (line_id numeric(10) PRIMARY KEY,"
            f" order_id numeric(10) NOT NULL REFERENCES {SOURCE_USER}.ilm_orders (id), payload varchar(40) NOT NULL)"
        )
        orders = [(1, 1, "KEEP", "recent"), (2, 3, "KEEP", "archive"), (3, 7, "PURGE", "purge")]
        for order_id, months, state, payload in orders:
            cursor.execute(
                f"INSERT INTO {SOURCE_USER}.ilm_orders VALUES (%s, date_trunc('day', localtimestamp)"
                " - (%s * interval '1 month'), 'READY', %s, %s, %s)",
                [order_id, months, state, state, payload],
            )
        for line in [(11, 1, "recent-line"), (21, 2, "archive-line"), (31, 3, "purge-line-a"), (32, 3, "purge-line-b")]:
            cursor.execute(f"INSERT INTO {SOURCE_USER}.ilm_order_lines VALUES (%s, %s, %s)", list(line))
        source.commit()
        cursor.close()

        # When: SOURCE_ILM previews
        _run_cli("ldb-run", *common, "--action", "SOURCE_ILM", "--mode", "PREVIEW", environment=environment)

        # Then: nothing moved and the table is marked as previewed only
        assert _ids(source, f"{SOURCE_USER}.ilm_orders") == [1, 2, 3]
        status = _control_status(source, "ilm_orders")
        assert status is not None and status[:2] == ("SOURCE_ILM", "PTEND")

        # When: SOURCE_ILM executes
        _run_cli("ldb-run", *common, "--action", "SOURCE_ILM", "--mode", "EXECUTE", environment=environment)

        # Then: old rows moved to the history database, recent rows stayed
        history = _connect(HISTORY_DB, HISTORY_USER, history_password)
        assert _ids(source, f"{SOURCE_USER}.ilm_orders") == [1]
        assert _ids(history, f"{HISTORY_USER}.ilm_orders") == [2, 3]
        assert _ids(source, f"{SOURCE_USER}.ilm_order_lines", "line_id") == [11]
        assert _ids(history, f"{HISTORY_USER}.ilm_order_lines", "line_id") == [21, 31, 32]
        assert _control_status(source, "ilm_orders") == ("SOURCE_ILM", "TEND", 2)
        assert _control_status(source, "ilm_order_lines") == ("SOURCE_ILM", "TEND", 3)

        if snapshot_column is not None:
            # And: the parent filter column was added to the history children under its default-case name
            rows = _fetch(
                history, f"SELECT line_id, {snapshot_column} FROM {HISTORY_USER}.ilm_order_lines ORDER BY line_id"
            )
            assert [(int(line), value) for line, value in rows] == [(21, "KEEP"), (31, "PURGE"), (32, "PURGE")]

        # When: SOURCE_ILM runs again on the same day
        _run_cli("ldb-run", *common, "--action", "SOURCE_ILM", "--mode", "EXECUTE", environment=environment)

        # Then: nothing changes
        assert _ids(history, f"{HISTORY_USER}.ilm_orders") == [2, 3]

        # When: HISTORY_ILM runs
        _run_cli("ldb-run", *common, "--action", "HISTORY_ILM", "--mode", "EXECUTE", environment=environment)

        # Then: only the rows past the history retention are purged
        assert _ids(history, f"{HISTORY_USER}.ilm_orders") == [2]
        assert _ids(history, f"{HISTORY_USER}.ilm_order_lines", "line_id") == [21]
    finally:
        source.close()
        if history is not None:
            history.close()
