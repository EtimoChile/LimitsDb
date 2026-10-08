from concurrent.futures import Future
from datetime import datetime
from types import SimpleNamespace

import pytest

from limitsdb.core import ldb_runner
from limitsdb.core.ldb_errors import ConfigurationError
from limitsdb.core.ldb_params_config import Config
from limitsdb.core.ldb_status import Status
from limitsdb.db.ldb_engines import ColumnDefinition
from limitsdb.db.oracle.ldb_engine_impl import OracleEngine


def test_build_dependency_graph_and_layers():
    rows = [
        {"source_owner": "A", "table_name": "T1", "referencing_tables": "A.T2 r"},
        {"source_owner": "A", "table_name": "T2", "referencing_tables": ""},
    ]
    graph = ldb_runner._build_dependency_graph(rows)
    assert graph[("A", "T1")] == set()
    assert graph[("A", "T2")] == {("A", "T1")}
    layers = ldb_runner._compute_plan_layers(graph)
    assert layers[0] == [("A", "T1")]
    assert layers[1] == [("A", "T2")]


def test_cycle_detection():
    rows = [
        {"source_owner": "A", "table_name": "T1", "referencing_tables": "A.T2 r"},
        {"source_owner": "A", "table_name": "T2", "referencing_tables": "A.T1 r"},
    ]
    graph = ldb_runner._build_dependency_graph(rows)
    try:
        ldb_runner._compute_plan_layers(graph)
    except ValueError as exc:
        assert "Cyclic" in str(exc)
    else:
        raise AssertionError("cycle not detected")


def test_plan_mode_handles_empty_and_layered_configuration(monkeypatch: pytest.MonkeyPatch):
    config = Config(schema="s", mode="PLAN")
    monkeypatch.setattr(ldb_runner, "_load_offline_rows", lambda current: [])
    assert ldb_runner._plan_mode(config) == 0

    rows = [
        {"source_owner": "A", "table_name": "PARENT", "referencing_tables": ""},
        {"source_owner": "A", "table_name": "CHILD", "referencing_tables": "A.PARENT P"},
    ]
    monkeypatch.setattr(ldb_runner, "_load_offline_rows", lambda current: rows)
    assert ldb_runner._plan_mode(config) == 0


def test_load_offline_rows_raises_when_no_ilm_file():
    config = Config(schema="no_such_schema", mode="PLAN")
    with pytest.raises(ConfigurationError, match=r"ilm\.yml"):
        ldb_runner._load_offline_rows(config)


def _validate_config() -> Config:
    return Config(
        schema="s",
        mode="VALIDATE",
        source_dsn="source-dsn",
        source_username="source-user",
        source_password="source-password",
        history_dsn="history-dsn",
        history_username="history-user",
        history_password="history-password",
        admin_source_username="source-admin",
        admin_source_password="source-admin-password",
        admin_history_username="history-admin",
        admin_history_password="history-admin-password",
    )


def test_validate_environment_checks_required_and_optional_connections(monkeypatch: pytest.MonkeyPatch):
    opened = []
    closed = []

    class DummyEngine:
        def get_connection(self, config, *, admin=False, env=None):
            connection = SimpleNamespace(admin=admin, env=env)
            opened.append(connection)
            return connection

        def get_system_date(self, connection):
            return datetime(2026, 10, 7)

        def close_connection(self, connection):
            closed.append(connection)

    tables = {("SOURCE", "ITEMS"): {"query_expr": "from source.items A\nwhere A.id > 0"}}
    monkeypatch.setattr(ldb_runner, "process_tables_cnf", lambda *args: tables)

    assert ldb_runner._validate_environment(_validate_config(), DummyEngine()) == 0
    assert len(opened) == 4
    assert closed == opened


def test_validate_environment_fails_when_required_connection_is_unavailable(monkeypatch: pytest.MonkeyPatch):
    closed = []

    class DummyEngine:
        def get_connection(self, config, *, admin=False, env=None):
            if admin and env is None:
                raise RuntimeError("source admin unavailable")
            return SimpleNamespace(admin=admin, env=env)

        def get_system_date(self, connection):
            return datetime(2026, 10, 7)

        def close_connection(self, connection):
            closed.append(connection)

    monkeypatch.setattr(ldb_runner, "process_tables_cnf", lambda *args: {})

    assert ldb_runner._validate_environment(_validate_config(), DummyEngine()) == 1
    assert len(closed) == 3


def test_validate_environment_skips_optional_credentials_not_provided(monkeypatch: pytest.MonkeyPatch):
    opened = []
    closed = []

    class DummyEngine:
        def get_connection(self, config, *, admin=False, env=None):
            connection = SimpleNamespace(admin=admin, env=env)
            opened.append(connection)
            return connection

        def get_system_date(self, connection):
            return datetime(2026, 10, 7)

        def close_connection(self, connection):
            closed.append(connection)

    # SOURCE_ILM provides HISTORY admin (required) but no HISTORY runtime credentials (optional)
    config = Config(
        schema="s",
        mode="VALIDATE",
        source_dsn="source-dsn",
        source_username="source-user",
        source_password="source-password",
        admin_source_username="source-admin",
        admin_source_password="source-admin-password",
        history_dsn="history-dsn",
        admin_history_username="history-admin",
        admin_history_password="history-admin-password",
    )
    monkeypatch.setattr(ldb_runner, "process_tables_cnf", lambda *args: {})

    assert ldb_runner._validate_environment(config, DummyEngine()) == 0
    assert len(opened) == 3  # SOURCE runtime, SOURCE admin, HISTORY admin
    assert closed == opened


def test_validate_environment_fails_when_primary_connection_system_date_fails(monkeypatch: pytest.MonkeyPatch):
    closed = []

    class DummyEngine:
        def get_connection(self, config, *, admin=False, env=None):
            return SimpleNamespace(admin=admin, env=env)

        def get_system_date(self, connection):
            raise RuntimeError("clock unavailable")

        def close_connection(self, connection):
            closed.append(connection)

    monkeypatch.setattr(ldb_runner, "process_tables_cnf", lambda *args: pytest.fail("should not reach config"))

    assert ldb_runner._validate_environment(_validate_config(), DummyEngine()) == 1
    assert len(closed) == 4  # all attempted connections closed despite all failing


def test_validate_environment_fails_when_config_processing_raises(monkeypatch: pytest.MonkeyPatch):
    closed = []

    class DummyEngine:
        def get_connection(self, config, *, admin=False, env=None):
            return SimpleNamespace(admin=admin, env=env)

        def get_system_date(self, connection):
            return datetime(2026, 10, 7)

        def close_connection(self, connection):
            closed.append(connection)

    monkeypatch.setattr(ldb_runner, "process_tables_cnf", lambda *args: (_ for _ in ()).throw(ValueError("bad config")))

    assert ldb_runner._validate_environment(_validate_config(), DummyEngine()) == 1
    assert len(closed) == 4


def test_open_connection_rejects_missing_credentials():
    config = Config(schema="s", mode="PLAN")
    engine = SimpleNamespace(get_connection=lambda *args, **kwargs: pytest.fail("connection should not be attempted"))

    assert ldb_runner._open_connection(config, engine, admin=False, env="SOURCE", description="SOURCE RUNTIME") is None


def test_generate_script_orders_dependencies_and_skips_tables(capsys: pytest.CaptureFixture[str]):
    tables = {
        ("SOURCE", "CHILD"): {
            "skip": False,
            "conds": [{"ctl_status": None}],
            "referencing_tables": [("SOURCE", "PARENT")],
            "sql_block": "begin child; end;",
        },
        ("SOURCE", "PARENT"): {
            "skip": False,
            "conds": [{"ctl_status": None}],
            "referencing_tables": [],
            "sql_block": "begin parent; end;",
        },
        ("SOURCE", "SKIPPED"): {
            "skip": True,
            "conds": [{"ctl_status": None}],
            "referencing_tables": [],
            "sql_block": "begin skipped; end;",
        },
    }

    assert ldb_runner.generate_script_output(Config(schema="billing", mode="PLAN"), tables) == 0

    output = capsys.readouterr().out
    assert output.index("SOURCE.PARENT") < output.index("SOURCE.CHILD")
    assert "begin parent; end;" in output
    assert "begin child; end;" in output
    assert "begin skipped; end;" not in output
    assert output.endswith("spool off\nexit 0\n\n")


def test_append_unique_and_get_next_ready():
    other_cols = [{"name": "x"}]
    ldb_runner._append_unique_name(other_cols, {"name": "x"})
    assert len(other_cols) == 1
    ldb_runner._append_unique_name(other_cols, {"name": "y"})
    assert len(other_cols) == 2

    tables_config = {
        ("A", "T1"): {"skip": False, "conds": [{"ctl_status": None}], "referencing_tables": []},
        ("A", "T2"): {"skip": False, "conds": [{"ctl_status": Status.TABLE_END}], "referencing_tables": []},
    }
    ready = ldb_runner.get_next_ready_table(tables_config, set())
    assert ready[0:2] == ("A", "T1")


def test_collect_privilege_targets():
    class DummyEngine:
        def get_identifier_str(self, value):
            return str(value).upper()

    rows = [
        {"source_owner": "a", "history_owner": "h", "table_name": "t1"},
        {"source_owner": "A", "history_owner": "H", "table_name": "T1"},
        {"source_owner": "A", "history_owner": "H", "table_name": "T2"},
    ]
    targets = ldb_runner._collect_privilege_targets(rows, "source_owner", DummyEngine())
    assert targets == [("A", "T1"), ("A", "T2")]


def test_runtime_owner_follows_ilm_action():
    rule = {"source_owner": "SOURCE", "history_owner": "HISTORY"}

    assert ldb_runner._runtime_owner(Config(schema="s", action="SOURCE_ILM", mode="PLAN"), rule) == "SOURCE"
    assert ldb_runner._runtime_owner(Config(schema="s", action="HISTORY_ILM", mode="PLAN"), rule) == "HISTORY"


def test_runtime_join_uses_source_relationship_and_history_derived_columns():
    rule = {
        "join_expr": "@ JOIN SOURCE.PARENT B ON B.ID=A.PARENT_ID",
        "source_orphan_purge": "N",
    }

    source_config = Config(schema="s", action="SOURCE_ILM", mode="PLAN")
    history_config = Config(schema="s", action="HISTORY_ILM", mode="PLAN")

    assert ldb_runner._runtime_join_expression(source_config, rule) == (
        "inner JOIN SOURCE.PARENT B ON B.ID=A.PARENT_ID"
    )
    assert ldb_runner._runtime_join_expression(history_config, rule) == ""
    assert ldb_runner._relationship_date_column("b") == "LDB_DATE_B"

    rule["source_orphan_purge"] = "Y"
    assert ldb_runner._runtime_join_expression(source_config, rule) == (
        "left outer JOIN SOURCE.PARENT B ON B.ID=A.PARENT_ID"
    )


def test_related_history_filter_is_snapshotted_and_rewritten(monkeypatch):
    def rules():
        common = {
            "id": 1,
            "source_owner": "SOURCE",
            "history_owner": "HISTORY",
            "retain_months_source": None,
            "retain_months_history": None,
            "exec_day": None,
            "frecuency": "D",
            "is_active": "Y",
            "purge_date_expr": None,
            "additional_filter_expr": None,
            "history_addtl_filter_expr": None,
            "history_hint_expr": None,
            "source_orphan_purge": "N",
            "orphan_check_column": None,
            "has_lob_columns": "N",
            "referencing_tables": None,
            "join_expr": None,
            "hint_expr": None,
            "long_columns": None,
            "ctl_status": None,
        }
        parent = {
            **common,
            "table_name": "PARENT",
            "retain_months_source": 2,
            "retain_months_history": 3,
            "purge_date_expr": "@CREATED_AT",
            "additional_filter_expr": "@SOURCE_STATE = 'READY'",
            "history_addtl_filter_expr": "@HISTORY_STATE = 'PURGE'",
        }
        child = {
            **common,
            "id": 2,
            "table_name": "CHILD",
            "referencing_tables": "PARENT B",
            "join_expr": "@ JOIN SOURCE.PARENT B ON B.ID=A.PARENT_ID",
            "source_orphan_purge": "Y",
            "orphan_check_column": "B.ID",
        }
        return [parent, child]

    class DummyEngine:
        get_identifier_str = staticmethod(OracleEngine.get_identifier_str)
        get_date_condition = staticmethod(OracleEngine.get_date_condition)
        get_identifiers_from_expression = staticmethod(OracleEngine.get_identifiers_from_expression)
        rewrite_expression_identifiers = staticmethod(OracleEngine.rewrite_expression_identifiers)
        get_fallback_expression = staticmethod(OracleEngine.get_fallback_expression)

        @staticmethod
        def get_ldb_columns_expressions():
            return "l_process_date", "sysdate"

        @staticmethod
        def get_table_columns(connection, owner, table_name):
            names = {
                "PARENT": ["ID", "CREATED_AT", "SOURCE_STATE", "HISTORY_STATE"],
                "CHILD": ["ID", "PARENT_ID", "HISTORY_STATE_B", "LDB_DATE_B"],
            }[table_name]
            return names, {
                name: ColumnDefinition(name=name, data_type="varchar2", id=index, length=20)
                for index, name in enumerate(names, start=1)
            }

        @staticmethod
        def generate_sql_block(config, table_cnf, process_date):
            return table_cnf["query_expr"]

    loaded_rules = rules()
    monkeypatch.setattr(ldb_runner, "_load_offline_rows", lambda config: loaded_rules)
    source_config = Config(
        schema="s",
        action="SOURCE_ILM",
        mode="SCRIPT",
        source_dsn="dsn",
        source_username="user",
        source_password="password",
    )
    source_tables = ldb_runner.process_tables_cnf(object(), source_config, DummyEngine(), "20261007")
    source_child = source_tables[("SOURCE", "CHILD")]

    assert {column["name"] for column in source_child["other_columns"]} >= {
        "HISTORY_STATE_B",
        "LDB_DATE_B",
        "LDB_IS_ORPHAN",
    }
    assert "B.SOURCE_STATE = 'READY'" in source_child["query_expr"]
    assert "left outer JOIN SOURCE.PARENT B" in source_child["query_expr"]
    assert "(B.ID is null)" in source_child["query_expr"]
    relationship_date = next(column for column in source_child["other_columns"] if column["name"] == "LDB_DATE_B")
    assert relationship_date["expr"] == "coalesce(B.CREATED_AT, l_process_date)"
    orphan_marker = next(column for column in source_child["other_columns"] if column["name"] == "LDB_IS_ORPHAN")
    assert orphan_marker["expr"] == "case when B.ID is null then 'Y' else 'N' end"

    loaded_rules = rules()
    history_config = Config(
        schema="s",
        action="HISTORY_ILM",
        mode="SCRIPT",
        history_dsn="dsn",
        history_username="user",
        history_password="password",
    )
    history_tables = ldb_runner.process_tables_cnf(object(), history_config, DummyEngine(), "20261007")
    history_query = history_tables[("SOURCE", "CHILD")]["query_expr"]

    assert "A.history_state_b = 'PURGE'" in history_query
    assert "A.LDB_IS_ORPHAN = 'Y'" in history_query
    assert "SOURCE.PARENT" not in history_query


def test_orphan_configuration_requires_complete_relationship_and_snapshots():
    rule = {
        "source_orphan_purge": "Y",
        "orphan_check_column": None,
        "referencing_tables": "PARENT B",
        "join_expr": "@ JOIN SOURCE.PARENT B ON B.ID=A.PARENT_ID",
    }
    config = Config(schema="s", action="SOURCE_ILM", mode="PLAN")

    try:
        ldb_runner._validate_orphan_configuration(config, rule, ("SOURCE", "CHILD"))
    except ValueError as exc:
        assert "orphan_check_column required" in str(exc)
    else:
        raise AssertionError("missing orphan_check_column was accepted")

    rule["orphan_check_column"] = "B.ID, C.ID"
    no_snapshots = Config(schema="s", action="SOURCE_ILM", mode="PLAN", use_added_columns=False)
    try:
        ldb_runner._validate_orphan_configuration(no_snapshots, rule, ("SOURCE", "CHILD"))
    except ValueError as exc:
        assert "use_added_columns must be enabled" in str(exc)
    else:
        raise AssertionError("orphan purge without derived snapshots was accepted")

    assert ldb_runner._orphan_predicate(rule) == "B.ID is null or C.ID is null"


def test_exception_details_supports_oracle_and_generic_errors():
    oracle_error = SimpleNamespace(code=942, message="table missing")
    assert ldb_runner._exception_details(Exception(oracle_error)) == (942, "table missing")
    assert ldb_runner._exception_details(RuntimeError("worker stopped")) == (None, "worker stopped")


def test_process_table_returns_recoverable_error_for_generic_failure(monkeypatch):
    calls = {"saved": False, "closed": False}

    class DummyEngine:
        def get_connection(self, config):
            return object()

        def get_system_date(self, conn):
            return "start"

        def get_rows_processed(self, conn, owner, table_name, process_date):
            return 3

        def sql_block_run(self, conn, plsql_code):
            raise RuntimeError("worker stopped")

        def save_error_status(self, *args):
            calls["saved"] = True

        def close_connection(self, conn):
            calls["closed"] = True

    monkeypatch.setattr(ldb_runner, "get_db_engine", lambda name: DummyEngine())
    config = Config(
        schema="s",
        db_engine="oracle",
        action="SOURCE_ILM",
        source_username="user",
        source_password="pw",
        source_dsn="dsn",
    )

    result = ldb_runner.process_table(config, "OWNER", "TABLE", "begin null; end;", "20261006")

    assert result == ("OWNER", "TABLE", Status.ERROR, 3, 3, None, "worker stopped")
    assert calls == {"saved": True, "closed": True}


def test_process_table_returns_success_and_closes_connection(monkeypatch):
    calls = {"ran": False, "closed": False}

    class DummyEngine:
        def get_connection(self, config):
            return object()

        def get_system_date(self, conn):
            return "start"

        def get_rows_processed(self, conn, owner, table_name, process_date):
            return 7 if calls["ran"] else 2

        def sql_block_run(self, conn, plsql_code):
            calls["ran"] = True

        def close_connection(self, conn):
            calls["closed"] = True

    monkeypatch.setattr(ldb_runner, "get_db_engine", lambda name: DummyEngine())
    config = Config(
        schema="s",
        db_engine="oracle",
        action="SOURCE_ILM",
        source_username="user",
        source_password="pw",
        source_dsn="dsn",
    )

    result = ldb_runner.process_table(config, "OWNER", "TABLE", "begin null; end;", "20261006")

    assert result == ("OWNER", "TABLE", Status.TABLE_END, 2, 7, None, None)
    assert calls == {"ran": True, "closed": True}


class _ImmediateExecutor:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def submit(self, function, *args):
        future = Future()
        try:
            future.set_result(function(*args))
        except Exception as exc:
            future.set_exception(exc)
        return future


class _StatusEngine:
    def all_status_tend(self, connection, tables_config, process_date):
        return all(table["conds"][0]["ctl_status"] == Status.TABLE_END for table in tables_config.values())


def _table_config(*, references=()):
    return {
        "skip": False,
        "conds": [{"ctl_status": None}],
        "referencing_tables": list(references),
        "sql_block": "begin null; end;",
    }


def _execution_config():
    return Config(
        schema="s",
        mode="EXECUTE",
        parallel_max=2,
        source_dsn="dsn",
        source_username="user",
        source_password="password",
    )


def test_ldb_exec_ilm_respects_dependencies(monkeypatch):
    launched = []
    tables_config = {
        ("A", "PARENT"): _table_config(),
        ("A", "CHILD"): _table_config(references=(("A", "PARENT"),)),
    }

    def successful_worker(config, owner, table_name, plsql_code, process_date):
        launched.append((owner, table_name))
        return owner, table_name, Status.TABLE_END, 0, 1, None, None

    monkeypatch.setattr(ldb_runner, "ProcessPoolExecutor", _ImmediateExecutor)
    monkeypatch.setattr(ldb_runner, "process_table", successful_worker)

    result = ldb_runner.ldb_exec_ilm(_execution_config(), tables_config, "20261006", _StatusEngine(), object())

    assert result == 0
    assert launched == [("A", "PARENT"), ("A", "CHILD")]


def test_ldb_exec_ilm_retries_partial_failure_that_made_progress(monkeypatch):
    launched = []
    parent_attempts = 0
    tables_config = {
        ("A", "PARENT"): _table_config(),
        ("A", "CHILD"): _table_config(references=(("A", "PARENT"),)),
    }

    def progress_then_success(config, owner, table_name, plsql_code, process_date):
        nonlocal parent_attempts
        launched.append((owner, table_name))
        if table_name == "PARENT":
            parent_attempts += 1
            if parent_attempts == 1:
                return owner, table_name, Status.ERROR, 2, 3, 20001, "partial failure"
            return owner, table_name, Status.TABLE_END, 3, 4, None, None
        return owner, table_name, Status.TABLE_END, 0, 1, None, None

    monkeypatch.setattr(ldb_runner, "ProcessPoolExecutor", _ImmediateExecutor)
    monkeypatch.setattr(ldb_runner, "process_table", progress_then_success)

    result = ldb_runner.ldb_exec_ilm(_execution_config(), tables_config, "20261006", _StatusEngine(), object())

    assert result == 0
    assert launched == [("A", "PARENT"), ("A", "PARENT"), ("A", "CHILD")]
    assert tables_config[("A", "PARENT")]["conds"][0]["ctl_status"] == Status.TABLE_END
    assert tables_config[("A", "CHILD")]["conds"][0]["ctl_status"] == Status.TABLE_END


def test_build_dependency_graph_no_dot_notation():
    rows = [
        {"source_owner": "A", "table_name": "T1", "referencing_tables": "T2 r"},
        {"source_owner": "A", "table_name": "T2", "referencing_tables": ""},
    ]
    graph = ldb_runner._build_dependency_graph(rows)
    assert graph[("A", "T2")] == {("A", "T1")}


def test_build_dependency_graph_invalid_ref_format_raises():
    rows = [
        {"source_owner": "A", "table_name": "T1", "referencing_tables": "A.T2"},
        {"source_owner": "A", "table_name": "T2", "referencing_tables": ""},
    ]
    with pytest.raises(ValueError, match="Invalid referencing_tables"):
        ldb_runner._build_dependency_graph(rows)


def test_build_dependency_graph_ref_not_in_config_raises():
    rows = [{"source_owner": "A", "table_name": "T1", "referencing_tables": "A.NONEXISTENT r"}]
    with pytest.raises(ValueError, match="not present in ILM configuration"):
        ldb_runner._build_dependency_graph(rows)


def test_validate_orphan_config_check_column_without_purge_raises():
    config = Config(schema="s", mode="PLAN")
    rule = {"source_orphan_purge": "N", "orphan_check_column": "FK_COL"}
    with pytest.raises(ValueError, match="source_orphan_purge must be enabled"):
        ldb_runner._validate_orphan_configuration(config, rule, ("A", "T1"))


def test_validate_environment_history_ilm_adds_source_optional_specs(monkeypatch: pytest.MonkeyPatch):
    opened = []
    closed = []

    class DummyEngine:
        def get_connection(self, config, *, admin=False, env=None):
            conn = SimpleNamespace(admin=admin, env=env)
            opened.append(conn)
            return conn

        def get_system_date(self, conn):
            return datetime(2026, 10, 7)

        def close_connection(self, conn):
            closed.append(conn)

    monkeypatch.setattr(ldb_runner, "process_tables_cnf", lambda *args: {})
    config = Config(
        schema="s",
        action="HISTORY_ILM",
        mode="VALIDATE",
        source_dsn="source-dsn",
        source_username="source-user",
        source_password="source-password",
        history_dsn="history-dsn",
        history_username="history-user",
        history_password="history-password",
        admin_source_username="source-admin",
        admin_source_password="source-admin-password",
        admin_history_username="history-admin",
        admin_history_password="history-admin-password",
    )
    result = ldb_runner._validate_environment(config, DummyEngine())
    assert result == 0
    assert len(opened) == 4  # HISTORY required x2 + SOURCE optional x2
    assert closed == opened


def test_build_history_table_without_ldb_columns():
    config = Config(schema="s", mode="PLAN", add_ldb_columns=False)

    class DummyEngine:
        def get_primary_key_columns(self, conn, owner, table_name, table_cnf):
            return ("ID",)

    table_cnf = {
        "conds": [{"history_owner": "H", "table_name": "T1", "source_owner": "S"}],
        "columns_metadata": {"ID": ColumnDefinition(name="ID", data_type="NUMBER", id=1)},
        "other_columns": [],
    }
    result = ldb_runner._build_history_table_definition(config, DummyEngine(), object(), table_cnf)
    assert result.primary_key == ("ID",)


def test_build_history_table_ldb_process_date_already_in_pk():
    config = Config(schema="s", mode="PLAN")

    class DummyEngine:
        def get_primary_key_columns(self, conn, owner, table_name, table_cnf):
            return ("ID", "LDB_PROCESS_DATE")

    table_cnf = {
        "conds": [{"history_owner": "H", "table_name": "T1", "source_owner": "S"}],
        "columns_metadata": {"ID": ColumnDefinition(name="ID", data_type="NUMBER", id=1)},
        "other_columns": [],
    }
    result = ldb_runner._build_history_table_definition(config, DummyEngine(), object(), table_cnf)
    assert result.primary_key is not None
    assert result.primary_key.count("LDB_PROCESS_DATE") == 1


def test_log_where_predicates_with_query_expr(monkeypatch: pytest.MonkeyPatch):
    logged: list[str] = []
    monkeypatch.setattr(
        ldb_runner,
        "logger",
        SimpleNamespace(info=lambda msg, *a, **k: logged.append(str(msg)), error=lambda *a, **k: None),
    )
    tables_config = {
        ("A", "T1"): {"query_expr": "SELECT * FROM A.T1 WHERE id > 0"},
        ("A", "T2"): {"query_expr": ""},
    }
    ldb_runner._log_where_predicates(tables_config)
    assert any("WHERE predicates" in m for m in logged)


def test_process_table_recovery_fails_gracefully(monkeypatch: pytest.MonkeyPatch):
    call_count = [0]

    class DummyEngine:
        def get_connection(self, config):
            return object()

        def get_system_date(self, conn):
            return "start"

        def get_rows_processed(self, conn, owner, table_name, process_date):
            call_count[0] += 1
            if call_count[0] == 1:
                return 0
            raise RuntimeError("recovery DB unavailable")

        def sql_block_run(self, conn, plsql_code):
            raise RuntimeError("primary failure")

        def close_connection(self, conn):
            pass

    engine_obj = DummyEngine()
    monkeypatch.setattr(ldb_runner, "get_db_engine", lambda name: engine_obj)
    config = Config(schema="s", db_engine="oracle", source_username="u", source_password="pw", source_dsn="dsn")
    result = ldb_runner.process_table(config, "OWNER", "TABLE", "begin null; end;", "20261006")
    assert result[2] == Status.ERROR


def test_ldb_exec_ilm_handles_unexpected_worker_exception(monkeypatch: pytest.MonkeyPatch):
    tables_config = {("A", "PARENT"): _table_config()}

    def crashing_worker(config, owner, table_name, plsql_code, process_date):
        raise RuntimeError("process crashed unexpectedly")

    monkeypatch.setattr(ldb_runner, "ProcessPoolExecutor", _ImmediateExecutor)
    monkeypatch.setattr(ldb_runner, "process_table", crashing_worker)

    result = ldb_runner.ldb_exec_ilm(_execution_config(), tables_config, "20261006", _StatusEngine(), object())
    assert result == 1


def test_ldb_exec_ilm_stops_retrying_when_failure_makes_no_progress(monkeypatch):
    launched = []
    tables_config = {
        ("A", "PARENT"): _table_config(),
        ("A", "CHILD"): _table_config(references=(("A", "PARENT"),)),
    }

    def no_progress_failure(config, owner, table_name, plsql_code, process_date):
        launched.append((owner, table_name))
        return owner, table_name, Status.ERROR, 2, 2, 20001, "no progress"

    monkeypatch.setattr(ldb_runner, "ProcessPoolExecutor", _ImmediateExecutor)
    monkeypatch.setattr(ldb_runner, "process_table", no_progress_failure)

    result = ldb_runner.ldb_exec_ilm(_execution_config(), tables_config, "20261006", _StatusEngine(), object())

    assert result == 1
    assert launched == [("A", "PARENT")]
    assert tables_config[("A", "PARENT")]["conds"][0]["ctl_status"] == Status.SKIPPED
    assert tables_config[("A", "CHILD")]["conds"][0]["ctl_status"] is None
