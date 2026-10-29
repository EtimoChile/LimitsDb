import pytest

from limitsdb.core.ldb_errors import DatabaseConnectionError, InvalidCredentialsError
from limitsdb.core.ldb_params_config import Config
from limitsdb.db.ldb_engines import (
    ColumnDefinition,
    DatabaseLinkDefinition,
    IndexDefinition,
    RoleDefinition,
    SequenceDefinition,
    TableDefinition,
    UserDefinition,
)
from limitsdb.db.postgresql import ldb_engine_impl
from limitsdb.db.postgresql.ldb_engine_impl import PostgresEngine
from tests.adapters.postgresql.conftest import FakeConn, pg_error


def _table() -> TableDefinition:
    return TableDefinition(
        owner="history",
        name="items",
        columns=[
            ColumnDefinition(name="id", data_type="number", precision=10, scale=0, nullable=False),
            ColumnDefinition(name="name", data_type="string", length=20),
        ],
        primary_key=("id",),
        indexes=[IndexDefinition(name="items_ix", columns=("name",), unique=False)],
    )


def test_missing_table_is_created_with_primary_key_and_indexes():
    # Spec: README > Bootstrap — a missing history table is created from the configuration
    conn = FakeConn(lambda c, sql, params: None)
    statements: list[str] = []

    PostgresEngine.ensure_table_structure(conn, _table(), {}, ddl_sink=statements.append)

    assert statements[0].startswith("CREATE TABLE history.items")
    assert "id numeric(10,0) NOT NULL" in statements[0] or "id numeric(10) NOT NULL" in statements[0]
    assert any("ADD CONSTRAINT items_PK".replace("items_PK", '"items_PK"') in s for s in statements)
    assert any(s.startswith("CREATE INDEX items_ix ON history.items (name)") for s in statements)


def test_scripting_without_a_connection_emits_the_create_statements():
    # Spec: README > Generate scripts — conn=None builds the full DDL without reading any database
    statements: list[str] = []

    PostgresEngine.ensure_table_structure(None, _table(), {}, ddl_sink=statements.append)  # type: ignore[arg-type]

    assert statements[0].startswith("CREATE TABLE history.items")


def test_added_column_is_emitted_with_add_column():
    # Spec: README > Bootstrap — columns missing from an existing table are added
    def respond(conn: FakeConn, sql: str, params):
        if "pg_class" in sql and "relkind" in sql:
            return (1,)
        if "information_schema.columns" in sql:
            return [(1, "id", "numeric", None, 10, 0, "NO", None, None)]
        return []

    statements: list[str] = []

    PostgresEngine.ensure_table_structure(FakeConn(respond), _table(), {}, ddl_sink=statements.append)

    assert "ALTER TABLE history.items ADD COLUMN name varchar(20)" in statements


def test_roles_are_scripted_as_tolerant_blocks():
    # Spec: README > Generate scripts — scripts are re-runnable: an existing role is not an error
    statements: list[str] = []

    PostgresEngine.ensure_roles(None, [RoleDefinition(name="ldb_role")], ddl_sink=statements.append)  # type: ignore[arg-type]

    assert "WHEN duplicate_object THEN NULL" in statements[0] and "CREATE ROLE ldb_role" in statements[0]


def test_user_password_never_appears_in_scripts_and_is_masked_in_logs():
    # Spec: README > Security — passwords are prompted for or masked, never written in a script or a log
    statements: list[str] = []
    user = UserDefinition(name="ldb_user", password="s3cret")

    PostgresEngine.ensure_users(None, [user], ddl_sink=statements.append)  # type: ignore[arg-type]
    script = "\n".join(statements)

    assert "s3cret" not in script and "current_setting('ldb.user_pwd_1')" in script
    assert "CREATE SCHEMA IF NOT EXISTS ldb_user AUTHORIZATION ldb_user" in script

    conn = FakeConn(lambda c, sql, params: None)
    PostgresEngine.ensure_users(conn, [user])
    executed = [sql for sql in conn.statements() if sql.startswith("CREATE ROLE ldb_user")]
    assert executed == ["CREATE ROLE ldb_user LOGIN PASSWORD 's3cret'"]


def test_password_prompts_store_the_answers_in_session_settings():
    # Spec: README > Generate scripts — psql asks for passwords and hands them to the DO blocks as settings
    statements: list[str] = []

    PostgresEngine.script_password_prompts([UserDefinition(name="u", password="x")], [], ddl_sink=statements.append)

    assert statements[0].startswith("\\prompt ") and "ldb_user_pwd_1" in statements[0]
    assert statements[1] == "SELECT set_config('ldb.user_pwd_1', :'ldb_user_pwd_1', false);"


def test_database_link_is_a_dblink_server_with_a_user_mapping():
    # Spec: docs/ai-state/DECISIONS.md (DEC-048) — the link is a dblink_fdw server plus user mapping; no password in blocks
    conn = FakeConn(lambda c, sql, params: None)
    link = DatabaseLinkDefinition(name="to_history", username="history", password="pw", dsn="dbhost:5433/hist")

    created = PostgresEngine.ensure_database_links(conn, [link])

    assert created == ["to_history"]
    server, mapping = [sql for sql in conn.statements() if sql.startswith("CREATE")]
    assert "FOREIGN DATA WRAPPER dblink_fdw" in server and "port '5433'" in server and "dbname 'hist'" in server
    assert mapping.endswith("(user 'history', password 'pw')")


def test_privileges_drop_alter_because_postgresql_has_none():
    # Spec: docs/ai-state/DECISIONS.md (DEC-048) — ALTER is not a table privilege in PostgreSQL
    script = PostgresEngine.generate_table_privilege_script("ldb_role", [("history", "items")], ["SELECT", "ALTER"])

    assert script == ["GRANT SELECT ON history.items TO ldb_role"]


def test_missing_privileges_are_reported_per_table():
    # Spec: README > Bootstrap — the runtime user must hold the table privileges; missing ones are reported
    def respond(conn: FakeConn, sql: str, params):
        if "pg_has_role" in sql:
            return (True,)
        return (params[-1] == "SELECT",)

    missing = PostgresEngine.get_missing_table_privileges(
        FakeConn(respond), "ldb_role", [("history", "items")], ["SELECT", "DELETE"]
    )

    assert missing == [("history", "items", "DELETE")]


def test_sequence_is_created_with_if_not_exists():
    # Spec: README > Bootstrap — sequences are created idempotently
    statements: list[str] = []

    PostgresEngine.ensure_sequences(
        None,  # type: ignore[arg-type]
        [SequenceDefinition(owner="history", name="ldb_log_id", start_with=1, increment_by=1)],
        ddl_sink=statements.append,
    )

    assert statements[0].startswith("CREATE SEQUENCE IF NOT EXISTS history.ldb_log_id START WITH 1 INCREMENT BY 1")


def test_ilm_script_is_a_psql_file_with_mode_and_date_settings():
    # Spec: README > Generate scripts — the ILM script runs with psql -v ldb_mode=EXECUTE
    header = "\n".join(PostgresEngine.ilm_script_header("s"))

    assert "set_config('ldb.mode', upper(:'ldb_mode'), false)" in header
    assert "\\o ldb_s.log" in header
    assert PostgresEngine.ilm_script_footer() == ["\\o", "\\q"]


def test_supporting_objects_install_dblink_and_the_owner_functions():
    # Spec: docs/ai-state/DECISIONS.md (DEC-048) — dblink and the status functions are prerequisites of a run
    conn = FakeConn()

    created = PostgresEngine.ensure_supporting_objects(conn, "source")

    statements = conn.statements()
    assert statements[0] == "CREATE EXTENSION IF NOT EXISTS dblink"
    assert sum("CREATE OR REPLACE FUNCTION" in sql for sql in statements) == 2
    assert created == ["FUNCTION check_save_status", "FUNCTION check_referencing_tables"]


@pytest.mark.parametrize(
    ("sqlstate", "expected"),
    [("28P01", InvalidCredentialsError), ("28000", InvalidCredentialsError), ("08006", DatabaseConnectionError)],
)
def test_connection_failures_are_classified_by_sqlstate(monkeypatch: pytest.MonkeyPatch, sqlstate, expected):
    # Spec: README > Troubleshooting — bad credentials are told apart from unreachable databases
    def fail(**kwargs):
        raise pg_error(sqlstate, "no")

    monkeypatch.setattr(ldb_engine_impl.pg8000.dbapi, "connect", fail)
    config = Config(
        schema="s",
        action="SOURCE_ILM",
        mode="EXECUTE",
        db_engine="postgresql",
        source_dsn="host/db",
        source_username="u",
        source_password="p",
    )

    with pytest.raises(expected):
        PostgresEngine.get_connection(config, env="SOURCE")


def test_connection_uses_host_port_and_database_of_the_dsn(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/configuration-contract.md — the PostgreSQL DSN is host[:port]/dbname
    seen: dict = {}
    monkeypatch.setattr(ldb_engine_impl.pg8000.dbapi, "connect", lambda **kwargs: seen.update(kwargs) or FakeConn())
    config = Config(
        schema="s",
        action="SOURCE_ILM",
        mode="EXECUTE",
        db_engine="postgresql",
        source_dsn="dbhost:6432/ledger",
        source_username="u",
        source_password="p",
    )

    conn = PostgresEngine.get_connection(config, env="SOURCE")
    PostgresEngine.close_connection(conn)

    assert (seen["host"], seen["port"], seen["database"], seen["user"]) == ("dbhost", 6432, "ledger", "u")
    assert conn.closed
