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


def test_scripted_tables_are_handed_to_their_schema_owner_even_if_they_exist():
    # Spec: README > Bootstrap — the DBA's script leaves the tables owned by the runtime user, so the runner can use
    # them; the ALTER also repairs a table an earlier run left owned by the administrator
    statements: list[str] = []

    PostgresEngine.ensure_tables(None, [_table()], ddl_sink=statements.append)  # type: ignore[arg-type]

    create = next(i for i, s in enumerate(statements) if s.startswith("CREATE TABLE IF NOT EXISTS history.items"))
    assert statements[create + 1] == "ALTER TABLE history.items OWNER TO history;"


def test_supporting_functions_are_handed_to_the_schema_owner():
    # Spec: README > Bootstrap — functions created by the administrator belong to the runtime user's schema owner
    statements: list[str] = []

    PostgresEngine.ensure_supporting_objects(None, "ldb", ddl_sink=statements.append)  # type: ignore[arg-type]

    script = "\n".join(statements)
    assert "ALTER FUNCTION ldb.check_save_status(" in script and ") OWNER TO ldb;" in script
    assert "ALTER FUNCTION ldb.check_referencing_tables(varchar[], timestamp, integer) OWNER TO ldb;" in script


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


def test_created_table_is_handed_to_its_schema_owner():
    # Spec: README > Bootstrap — objects created by the administrator belong to the schema owner, as in Oracle
    statements: list[str] = []

    PostgresEngine.ensure_table_structure(None, _table(), {}, ddl_sink=statements.append)  # type: ignore[arg-type]

    assert "ALTER TABLE history.items OWNER TO history" in statements


def test_derived_column_already_present_is_not_added_again():
    # Spec: README > Bootstrap — a rerun adds nothing: LDB_DATE_B is stored as ldb_date_b in the catalog
    table = _table()
    table = TableDefinition(
        owner=table.owner,
        name=table.name,
        columns=[*table.columns, ColumnDefinition(name="LDB_DATE_B", data_type="date")],
        primary_key=table.primary_key,
        indexes=table.indexes,
    )

    def respond(conn: FakeConn, sql: str, params):
        if "pg_class" in sql and "relkind" in sql:
            return (1,)
        if "information_schema.columns" in sql:
            return [
                (1, "id", "numeric", None, 10, 0, "NO", None, None),
                (2, "name", "character varying", 20, None, None, "YES", None, None),
                (3, "ldb_date_b", "timestamp without time zone", None, None, None, "YES", None, 0),
            ]
        return []

    statements: list[str] = []

    PostgresEngine.ensure_table_structure(FakeConn(respond), table, {}, ddl_sink=statements.append)

    assert not any("ldb_date_b" in s.lower() for s in statements)


def test_primary_key_reports_derived_columns_the_way_the_runner_names_them():
    # Spec: README > Run ILM — the runner appends LDB_PROCESS_DATE only when the key lacks it, so the catalog's
    # lower case name must come back in the runner's form
    rows = [("items_PK", True, True, "id"), ("items_PK", True, True, "ldb_process_date")]
    conn = FakeConn(lambda c, sql, params: rows)

    assert PostgresEngine.get_primary_key_columns(conn, "history", "items", {}) == ("id", "LDB_PROCESS_DATE")


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


def test_created_users_give_the_administrator_the_set_option_before_owning_objects():
    # Spec: docs/ai-state/DECISIONS.md (DEC-048) — PostgreSQL 16 gives a role creator membership without SET, which
    # CREATE SCHEMA ... AUTHORIZATION needs
    user = UserDefinition(name="ldb_user", password="s3cret")
    conn = FakeConn(lambda c, sql, params: None)

    PostgresEngine.ensure_users(conn, [user])
    executed = conn.statements()
    grant = next(i for i, sql in enumerate(executed) if "GRANT ldb_user TO CURRENT_USER WITH SET TRUE" in sql)
    schema = next(i for i, sql in enumerate(executed) if sql.startswith("CREATE SCHEMA IF NOT EXISTS ldb_user"))

    assert "server_version_num" in executed[grant] and grant < schema

    statements: list[str] = []
    PostgresEngine.ensure_users(None, [user], ddl_sink=statements.append)  # type: ignore[arg-type]
    assert any("GRANT ldb_user TO CURRENT_USER WITH SET TRUE" in s for s in statements)


def test_password_prompts_store_the_answers_in_session_settings():
    # Spec: README > Generate scripts — psql asks for passwords and hands them to the DO blocks as settings
    statements: list[str] = []

    PostgresEngine.script_password_prompts([UserDefinition(name="u", password="x")], [], ddl_sink=statements.append)

    assert statements[0].startswith("\\prompt ") and "ldb_user_pwd_1" in statements[0]
    assert statements[1] == "SET ldb.user_pwd_1 = :'ldb_user_pwd_1';"


def test_database_link_is_a_dblink_server_with_a_user_mapping():
    # Spec: docs/ai-state/DECISIONS.md (DEC-048) — the link is a dblink_fdw server plus user mapping; no password in blocks
    conn = FakeConn(lambda c, sql, params: (1,) if "has_foreign_data_wrapper_privilege" in sql else None)
    link = DatabaseLinkDefinition(name="to_history", username="history", password="pw", dsn="dbhost:5433/hist")

    created = PostgresEngine.ensure_database_links(conn, [link])

    assert created == ["to_history"]
    server, mapping = [sql for sql in conn.statements() if sql.startswith("CREATE")]
    assert "FOREIGN DATA WRAPPER dblink_fdw" in server and "port '5433'" in server and "dbname 'hist'" in server
    assert mapping.endswith("(user 'history', password 'pw')")


def test_scripted_link_tells_the_dba_when_dblink_fdw_is_not_usable():
    # Spec: docs/ai-state/DECISIONS.md (DEC-048) — without dblink_fdw the link is skipped, visibly
    statements: list[str] = []
    link = DatabaseLinkDefinition(name="to_history", username="history", password="pw", dsn="dbhost:5433/hist")

    PostgresEngine.ensure_database_links(
        None,
        [link],
        ddl_sink=statements.append,
        connect_as=("adm", "dbhost:5432/src"),  # type: ignore[arg-type]
    )
    block = next(s for s in statements if "CREATE SERVER" in s)

    assert "RAISE NOTICE 'dblink_fdw is not usable: link to_history is not created" in block


def test_scripted_reconnection_reuses_the_password_asked_once_for_the_runtime_user():
    # Spec: DEC-048 — as in Oracle, the DBA types each account's password once: \connect gets it from PGPASSWORD
    statements: list[str] = []
    link = DatabaseLinkDefinition(name="to_history", username="history", password="pw", dsn="dbhost:5433/hist")

    PostgresEngine.ensure_database_links(
        None,
        [link],
        ddl_sink=statements.append,
        connect_as=("adm", "dbhost:5432/src"),  # type: ignore[arg-type]
    )

    connect = next(i for i, s in enumerate(statements) if s.startswith("\\connect"))
    assert statements[connect - 1] == "\\setenv PGPASSWORD :ldb_user_pwd_1"
    assert statements[connect + 1] == "\\setenv PGPASSWORD"


def test_privileges_drop_alter_because_postgresql_has_none():
    # Spec: docs/ai-state/DECISIONS.md (DEC-048) — ALTER is not a table privilege in PostgreSQL
    script = PostgresEngine.generate_table_privilege_script("ldb_role", [("history", "items")], ["SELECT", "ALTER"])

    assert script == ["GRANT USAGE ON SCHEMA history TO ldb_role", "GRANT SELECT ON history.items TO ldb_role"]


def test_table_privileges_include_usage_on_the_schema_of_the_tables():
    # Spec: docs/ai-state/DECISIONS.md (DEC-048) — a role reaches a table only with USAGE on its schema too, which
    # matters when the runtime user is not the owner of the tables
    def respond(conn: FakeConn, sql: str, params):
        return (False,) if "has_table_privilege" in sql else None  # nothing is granted yet

    conn = FakeConn(respond)

    PostgresEngine.ensure_table_privileges(conn, "ldb_role", [("gl", "a"), ("gl", "b")], ["SELECT"])
    executed = [sql for sql in conn.statements() if sql.startswith("GRANT")]

    assert executed == [
        "GRANT USAGE ON SCHEMA gl TO ldb_role",
        "GRANT SELECT ON gl.a TO ldb_role",
        "GRANT SELECT ON gl.b TO ldb_role",
    ]


def test_missing_privileges_are_reported_per_table():
    # Spec: README > Bootstrap — the runtime user must hold the table privileges; missing ones are reported
    def respond(conn: FakeConn, sql: str, params):
        if "pg_has_role" in sql:
            return (True,)
        # The privilege is read for the environment's role, never for an empty grantee
        return (params[0] == "ldb_role" and params[1] == "SELECT",)

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


def test_source_session_knows_the_history_connection_even_without_an_explicit_env(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/ai-state/DECISIONS.md (DEC-048) — the worker connects with get_connection(config): the action says it is SOURCE
    monkeypatch.setattr(ldb_engine_impl.pg8000.dbapi, "connect", lambda **kwargs: FakeConn())
    config = Config(
        schema="s",
        action="SOURCE_ILM",
        mode="EXECUTE",
        db_engine="postgresql",
        source_dsn="dbhost:5432/src",
        source_username="u",
        source_password="p",
        history_dsn="dbhost:5432/hist",
        history_username="h",
        history_password="hp",
    )

    conn = PostgresEngine.get_connection(config)
    try:
        assert "user=h" in PostgresEngine._history_conninfo[id(conn)]
    finally:
        PostgresEngine.close_connection(conn)
    assert id(conn) not in PostgresEngine._history_conninfo


def test_connection_accepts_a_url_dsn_and_uses_tls_when_sslmode_requires_it(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/configuration-contract.md — the DSN may be postgresql://host[:port]/dbname?sslmode=require
    seen: dict = {}
    monkeypatch.setattr(ldb_engine_impl.pg8000.dbapi, "connect", lambda **kwargs: seen.update(kwargs) or FakeConn())
    config = Config(
        schema="s",
        action="SOURCE_ILM",
        mode="EXECUTE",
        db_engine="postgresql",
        source_dsn="postgresql://ep-x.neon.tech/neondb?sslmode=require&channel_binding=require",
        source_username="u",
        source_password="p",
    )

    PostgresEngine.close_connection(PostgresEngine.get_connection(config, env="SOURCE"))

    assert (seen["host"], seen["port"], seen["database"], seen["ssl_context"]) == (
        "ep-x.neon.tech",
        5432,
        "neondb",
        True,
    )


def test_dsn_with_credentials_is_rejected():
    # Spec: credentials live in secrets.json, never in the DSN
    with pytest.raises(ValueError, match="credentials"):
        ldb_engine_impl._parse_dsn("postgresql://user:pwd@host/db")


def test_server_options_carry_the_sslmode_of_the_dsn():
    # Spec: the dblink server reaches the other database with the TLS mode its DSN states
    link = ldb_engine_impl.DatabaseLinkDefinition(
        name="ldb_hist", username="u", password="p", dsn="h:5433/db?sslmode=require"
    )

    assert PostgresEngine._server_options(link) == "host 'h', dbname 'db', port '5433', sslmode 'require'"


def test_database_link_is_skipped_when_dblink_fdw_cannot_be_used():
    # Spec: README > PostgreSQL — hosted services keep dblink_fdw: the link is then opened with a connection string
    conn = FakeConn(lambda c, sql, params: None)
    link = DatabaseLinkDefinition(name="to_history", username="history", password="pw", dsn="dbhost:5433/hist")

    created = PostgresEngine.ensure_database_links(conn, [link])

    assert created == []
    assert not [sql for sql in conn.statements() if sql.startswith("CREATE")]


def test_dblink_wrapper_grant_tolerates_missing_privilege():
    # Spec: README > PostgreSQL — a refused USAGE on dblink_fdw does not stop the bootstrap
    conn = FakeConn()

    PostgresEngine.ensure_supporting_objects(conn, "gl")

    grant = next(sql for sql in conn.statements() if "GRANT USAGE ON FOREIGN DATA WRAPPER" in sql)
    assert "EXCEPTION WHEN insufficient_privilege" in grant


def test_inline_connection_string_quotes_values_and_carries_tls():
    # Spec: README > PostgreSQL — without a foreign server the HISTORY connection is a libpq string
    conninfo = ldb_engine_impl._inline_conninfo("glhst", "p w'd\\", "h:5433/hist?sslmode=require")

    assert conninfo == "host=h port=5433 dbname=hist user=glhst password='p w\\'d\\\\' sslmode=require"


def test_history_connection_string_is_set_only_while_connecting_when_no_server_exists():
    # Spec: README > PostgreSQL — the connection string never stays in the session nor appears in the block text
    conn = FakeConn(lambda c, sql, params: None)
    PostgresEngine._history_conninfo[id(conn)] = "host=h password=secret"

    PostgresEngine._connect_history(conn, conn.cursor(), "DO $ldb$ BEGIN NULL; END $ldb$", "ldb_hist")

    statements = conn.statements()
    assert [sql for sql in statements if "set_config" in sql] == [
        "SELECT set_config('ldb.hist_conn', %s, false)",
        "SELECT set_config('ldb.hist_conn', '', false)",
    ]
    assert "secret" not in "".join(statements)
    PostgresEngine._history_conninfo.pop(id(conn))
