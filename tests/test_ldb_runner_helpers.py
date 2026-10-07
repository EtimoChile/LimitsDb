from concurrent.futures import Future
from types import SimpleNamespace

from limitsdb.core import ldb_runner
from limitsdb.core.ldb_params_config import Config
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
