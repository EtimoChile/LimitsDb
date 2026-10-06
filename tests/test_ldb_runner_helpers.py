from types import SimpleNamespace

from limitsdb.core import ldb_runner
from limitsdb.core.ldb_status import Status


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
    config = SimpleNamespace(
        db_engine="oracle", action="SOURCE_ILM", source_username="user", source_password="pw", source_dsn="dsn"
    )

    result = ldb_runner.process_table(config, "OWNER", "TABLE", "begin null; end;", "20261006")

    assert result == ("OWNER", "TABLE", Status.ERROR, 3, 3, None, "worker stopped")
    assert calls == {"saved": True, "closed": True}
