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

# ---------------------------------------------------------------------------
# PLAN mode — R2 exception: _plan_mode is the sole entry point to the PLAN
# mode contract documented in README > Run ILM > --mode PLAN
# ---------------------------------------------------------------------------


def test_plan_mode_exits_without_error_when_ilm_has_no_tables(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM > --mode PLAN — evaluates dependency graph and prints
    # execution plan; empty config is valid and exits 0
    # Given: PLAN mode config with no tables in the ILM file
    config = Config(schema="s", mode="PLAN")
    monkeypatch.setattr(ldb_runner, "_load_offline_rows", lambda current: [])

    # When: PLAN mode is executed
    # Then: exits with code 0 — nothing to plan is not an error
    assert ldb_runner._plan_mode(config) == 0


def test_plan_mode_exits_without_error_for_parent_child_configuration(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: README > Run ILM > --mode PLAN — layered dependencies are shown and
    # the function returns 0
    # Given: two tables where CHILD references PARENT
    rows = [
        {"source_owner": "A", "table_name": "PARENT", "referencing_tables": ""},
        {"source_owner": "A", "table_name": "CHILD", "referencing_tables": "A.PARENT P"},
    ]
    config = Config(schema="s", mode="PLAN")
    monkeypatch.setattr(ldb_runner, "_load_offline_rows", lambda current: rows)

    # When: PLAN mode is executed
    # Then: exits with code 0 — valid layered plan
    assert ldb_runner._plan_mode(config) == 0


# ---------------------------------------------------------------------------
# ILM config loading — R2 exception: _load_offline_rows is the sole entry
# point to the ConfigurationError contract when no ILM file exists
# (docs/exception-handling.md)
# ---------------------------------------------------------------------------


def test_missing_ilm_file_raises_configuration_error():
    # Spec: docs/exception-handling.md — ConfigurationError when no ILM config
    # can be resolved; message must identify the missing file
    # Given: a schema with no ILM file in the search path
    config = Config(schema="no_such_schema", mode="PLAN")

    # When / Then: ConfigurationError names the missing file
    with pytest.raises(ConfigurationError, match=r"ilm\.yml"):
        ldb_runner._load_offline_rows(config)


# ---------------------------------------------------------------------------
# VALIDATE mode — R2 exception: _validate_environment is the sole entry point
# to the VALIDATE mode contract documented in README > Run ILM > --mode VALIDATE
# ---------------------------------------------------------------------------


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


def test_validate_mode_opens_and_closes_all_four_connections(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM > --mode VALIDATE — checks source runtime, source admin,
    # history runtime and history admin connections, then closes all of them
    # Given: a DummyEngine that records opens and closes
    opened = []
    closed = []

    class DummyEngine:
        def get_connection(self, config, *, admin=False, env=None):
            conn = SimpleNamespace(admin=admin, env=env)
            opened.append(conn)
            return conn

        def get_system_date(self, connection):
            return datetime(2026, 10, 7)

        def close_connection(self, connection):
            closed.append(connection)

    monkeypatch.setattr(ldb_runner, "process_tables_cnf", lambda *args: {})

    # When: VALIDATE mode runs
    result = ldb_runner._validate_environment(_validate_config(), DummyEngine())

    # Then: 4 connections were opened and all were closed
    assert result == 0
    assert len(opened) == 4
    assert closed == opened


def test_validate_mode_returns_error_when_required_connection_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: README > Run ILM > --mode VALIDATE — a failed required connection
    # returns exit code 1; remaining connections are still closed
    # Given: a DummyEngine where source admin connection raises
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

    # When: VALIDATE mode runs
    result = ldb_runner._validate_environment(_validate_config(), DummyEngine())

    # Then: exit code 1 and the three successful connections are closed
    assert result == 1
    assert len(closed) == 3


def test_validate_mode_skips_optional_credentials_not_provided(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM > --mode VALIDATE — history runtime credentials are
    # optional for SOURCE_ILM; missing ones are skipped, not treated as errors
    # Given: config with no history runtime credentials (history admin is provided)
    opened = []
    closed = []

    class DummyEngine:
        def get_connection(self, config, *, admin=False, env=None):
            conn = SimpleNamespace(admin=admin, env=env)
            opened.append(conn)
            return conn

        def get_system_date(self, connection):
            return datetime(2026, 10, 7)

        def close_connection(self, connection):
            closed.append(connection)

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

    # When: VALIDATE mode runs
    result = ldb_runner._validate_environment(config, DummyEngine())

    # Then: exactly 3 connections (source runtime, source admin, history admin)
    assert result == 0
    assert len(opened) == 3
    assert closed == opened


def test_validate_mode_returns_error_when_system_date_check_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: README > Run ILM > --mode VALIDATE — system date verification is part
    # of connection validation; failure is reported and connections are closed
    # Given: DummyEngine where get_system_date always raises
    closed = []

    class DummyEngine:
        def get_connection(self, config, *, admin=False, env=None):
            return SimpleNamespace(admin=admin, env=env)

        def get_system_date(self, connection):
            raise RuntimeError("clock unavailable")

        def close_connection(self, connection):
            closed.append(connection)

    monkeypatch.setattr(ldb_runner, "process_tables_cnf", lambda *args: pytest.fail("should not reach"))

    # When: VALIDATE mode runs
    result = ldb_runner._validate_environment(_validate_config(), DummyEngine())

    # Then: exit code 1 and all attempted connections are closed
    assert result == 1
    assert len(closed) == 4


def test_validate_mode_returns_error_when_config_processing_raises(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: README > Run ILM > --mode VALIDATE — config processing errors surface
    # as exit code 1; all connections are still closed
    # Given: process_tables_cnf raises
    closed = []

    class DummyEngine:
        def get_connection(self, config, *, admin=False, env=None):
            return SimpleNamespace(admin=admin, env=env)

        def get_system_date(self, connection):
            return datetime(2026, 10, 7)

        def close_connection(self, connection):
            closed.append(connection)

    monkeypatch.setattr(
        ldb_runner,
        "process_tables_cnf",
        lambda *args: (_ for _ in ()).throw(ValueError("bad config")),
    )

    # When: VALIDATE mode runs
    result = ldb_runner._validate_environment(_validate_config(), DummyEngine())

    # Then: exit code 1 and all connections were closed
    assert result == 1
    assert len(closed) == 4


def test_validate_mode_for_history_ilm_opens_history_then_source_connections(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: README > Run ILM > --mode VALIDATE (HISTORY_ILM) — HISTORY connections
    # are required; SOURCE connections are validated as optional
    # Given: a full HISTORY_ILM config with all credentials
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

    # When: VALIDATE mode runs for HISTORY_ILM
    result = ldb_runner._validate_environment(config, DummyEngine())

    # Then: 4 connections opened and all closed
    assert result == 0
    assert len(opened) == 4
    assert closed == opened


# ---------------------------------------------------------------------------
# process_tables_cnf — public interface
# ---------------------------------------------------------------------------


class _MinimalEngine:
    """Minimal engine stub for tests that only need identifier and column expression support."""

    @staticmethod
    def get_ldb_columns_expressions():
        return "l_process_date", "sysdate"

    @staticmethod
    def get_identifier_str(value):
        return str(value).upper()

    @staticmethod
    def get_status(connection, process_date):
        return []


def _script_config(**kwargs):
    """Return a SCRIPT-mode Config (generate_script=True skips DB status calls)."""
    return Config(
        schema="s",
        mode="SCRIPT",
        source_dsn="dsn",
        source_username="user",
        source_password="pw",
        **kwargs,
    )


def _minimal_row(
    table_name, *, source_orphan_purge="N", orphan_check_column=None, join_expr=None, referencing_tables=""
):
    return {
        "source_owner": "A",
        "history_owner": "H",
        "table_name": table_name,
        "referencing_tables": referencing_tables,
        "source_orphan_purge": source_orphan_purge,
        "orphan_check_column": orphan_check_column,
        "join_expr": join_expr,
    }


def test_reference_to_nonexistent_table_raises_before_processing(monkeypatch: pytest.MonkeyPatch):
    # Spec: limitsdb/resources/ilm.example.yml — all referenced tables must be present
    # in the ILM configuration; unknown references are caught early during config processing
    # Given: T1 references a table that is not in the ILM config
    rows = [_minimal_row("T1", referencing_tables="A.NONEXISTENT r")]
    monkeypatch.setattr(ldb_runner, "_load_offline_rows", lambda config: rows)

    # When / Then: ValueError — reference to an undeclared table is caught
    with pytest.raises(ValueError):
        ldb_runner.process_tables_cnf(object(), _script_config(), _MinimalEngine(), "20261007")


def test_orphan_check_column_required_when_source_orphan_purge_enabled(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: limitsdb/resources/ilm.example.yml — source_orphan_purge=Y requires
    # orphan_check_column; missing it is a configuration error caught at startup
    # Given: a single table with orphan purge enabled but no check column or join
    # (single row so validation triggers before any engine column-fetch)
    rows = [
        _minimal_row(
            "CHILD",
            source_orphan_purge="Y",
            orphan_check_column=None,
        )
    ]
    monkeypatch.setattr(ldb_runner, "_load_offline_rows", lambda config: rows)

    # When / Then: ValueError names the missing orphan_check_column
    with pytest.raises(ValueError, match="orphan_check_column required"):
        ldb_runner.process_tables_cnf(object(), _script_config(), _MinimalEngine(), "20261007")


def test_orphan_check_column_without_purge_flag_raises(monkeypatch: pytest.MonkeyPatch):
    # Spec: limitsdb/resources/ilm.example.yml — orphan_check_column is only valid
    # when source_orphan_purge=Y; an orphan column without the flag is a config error
    # Given: T1 has orphan_check_column but source_orphan_purge disabled
    rows = [_minimal_row("T1", source_orphan_purge="N", orphan_check_column="FK_COL")]
    monkeypatch.setattr(ldb_runner, "_load_offline_rows", lambda config: rows)

    # When / Then: ValueError explains the inconsistency
    with pytest.raises(ValueError, match="source_orphan_purge must be enabled"):
        ldb_runner.process_tables_cnf(object(), _script_config(), _MinimalEngine(), "20261007")


def test_related_history_filter_is_snapshotted_and_rewritten(monkeypatch: pytest.MonkeyPatch):
    # Spec: limitsdb/resources/ilm.example.yml — source: child uses outer join with
    # orphan detection; history: filter uses snapshotted column names, no source join
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

    # Given: SOURCE_ILM config and rules with parent-child orphan relationship
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

    # When: process_tables_cnf runs for SOURCE_ILM
    source_tables = ldb_runner.process_tables_cnf(object(), source_config, DummyEngine(), "20261007")
    source_child = source_tables[("SOURCE", "CHILD")]

    # Then: orphan columns, outer join, and snapshotted filter are all present
    assert {column["name"] for column in source_child["other_columns"]} >= {
        "HISTORY_STATE_B",
        "LDB_DATE_B",
        "LDB_IS_ORPHAN",
    }
    assert "B.SOURCE_STATE = 'READY'" in source_child["query_expr"]
    assert "left outer JOIN SOURCE.PARENT B" in source_child["query_expr"]
    assert "(B.ID is null)" in source_child["query_expr"]
    relationship_date = next(c for c in source_child["other_columns"] if c["name"] == "LDB_DATE_B")
    assert relationship_date["expr"] == "coalesce(B.CREATED_AT, l_process_date)"
    orphan_marker = next(c for c in source_child["other_columns"] if c["name"] == "LDB_IS_ORPHAN")
    assert orphan_marker["expr"] == "case when B.ID is null then 'Y' else 'N' end"

    # Given: HISTORY_ILM config and fresh rules
    loaded_rules = rules()
    history_config = Config(
        schema="s",
        action="HISTORY_ILM",
        mode="SCRIPT",
        history_dsn="dsn",
        history_username="user",
        history_password="password",
    )

    # When: process_tables_cnf runs for HISTORY_ILM
    history_tables = ldb_runner.process_tables_cnf(object(), history_config, DummyEngine(), "20261007")
    history_query = history_tables[("SOURCE", "CHILD")]["query_expr"]

    # Then: history query uses snapshotted columns and orphan marker; no source join
    assert "A.history_state_b = 'PURGE'" in history_query
    assert "A.LDB_IS_ORPHAN = 'Y'" in history_query
    assert "SOURCE.PARENT" not in history_query


# ---------------------------------------------------------------------------
# generate_script_output — public interface
# ---------------------------------------------------------------------------


def test_script_output_orders_parents_before_children_and_skips_flagged_tables(
    capsys: pytest.CaptureFixture[str],
):
    # Spec: README > Run ILM > --mode SCRIPT — script output respects dependency
    # order; tables with skip=True are omitted; output ends with "spool off\nexit 0"
    # Given: a tables config with a parent, a child and a skipped table
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

    # When: script is generated
    result = ldb_runner.generate_script_output(Config(schema="billing", mode="PLAN"), tables)
    output = capsys.readouterr().out

    # Then: exit code 0, ordering respected, skipped table absent, footer present
    assert result == 0
    assert output.index("SOURCE.PARENT") < output.index("SOURCE.CHILD")
    assert "begin parent; end;" in output
    assert "begin child; end;" in output
    assert "begin skipped; end;" not in output
    assert output.endswith("spool off\nexit 0\n\n")


# ---------------------------------------------------------------------------
# get_next_ready_table — public interface
# ---------------------------------------------------------------------------


def test_next_ready_table_skips_tables_already_finished(capsys: pytest.CaptureFixture[str]):
    # Spec: README > Run ILM execution — get_next_ready_table returns the first
    # table whose conds are not yet finished and whose dependencies are all done
    # Given: T1 is unfinished, T2 is already complete
    tables_config = {
        ("A", "T1"): {"skip": False, "conds": [{"ctl_status": None}], "referencing_tables": []},
        ("A", "T2"): {
            "skip": False,
            "conds": [{"ctl_status": Status.TABLE_END}],
            "referencing_tables": [],
        },
    }

    # When: next ready table is queried with no in-progress tables
    ready = ldb_runner.get_next_ready_table(tables_config, set())

    # Then: T1 is returned; T2 is skipped because it is already finished
    assert ready[0:2] == ("A", "T1")


# ---------------------------------------------------------------------------
# ldb_exec_ilm — public interface
# ---------------------------------------------------------------------------


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


def test_tables_with_references_are_processed_after_their_dependencies(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: README > Run ILM > --mode EXECUTE — tables are processed after all tables
    # they reference; PARENT must run before CHILD
    # Given: PARENT and CHILD where CHILD references PARENT
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

    # When: ILM execution runs
    result = ldb_runner.ldb_exec_ilm(_execution_config(), tables_config, "20261006", _StatusEngine(), object())

    # Then: PARENT processed before CHILD, exit code 0
    assert result == 0
    assert launched == [("A", "PARENT"), ("A", "CHILD")]


def test_cyclic_table_dependencies_prevent_successful_execution(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM > --mode EXECUTE — circular dependencies cannot be
    # resolved; the coordinator must detect and report them
    # Given: T1 depends on T2 and T2 depends on T1
    tables_config = {
        ("A", "T1"): _table_config(references=(("A", "T2"),)),
        ("A", "T2"): _table_config(references=(("A", "T1"),)),
    }
    launched = []

    def worker(config, owner, table_name, sql, process_date):
        launched.append((owner, table_name))
        return owner, table_name, Status.TABLE_END, 0, 1, None, None

    monkeypatch.setattr(ldb_runner, "ProcessPoolExecutor", _ImmediateExecutor)
    monkeypatch.setattr(ldb_runner, "process_table", worker)

    # When: ILM execution is attempted
    try:
        result = ldb_runner.ldb_exec_ilm(_execution_config(), tables_config, "20261006", _StatusEngine(), object())
        # If cycle is handled as a failure code rather than an exception:
        assert result != 0, "cyclic dependencies must not exit with code 0"
    except ValueError as exc:
        assert "ycl" in str(exc).lower(), f"unexpected ValueError: {exc}"

    # Then: no tables were processed before the cycle was detected
    assert launched == []


def test_partial_failure_with_progress_is_retried_on_same_run(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM execution — a worker that processed some rows but did not
    # finish is retried; a second attempt from where it left off completes the table
    # Given: PARENT fails on first attempt but succeeds on second
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

    # When: ILM execution runs
    result = ldb_runner.ldb_exec_ilm(_execution_config(), tables_config, "20261006", _StatusEngine(), object())

    # Then: PARENT retried, CHILD ran after PARENT completed, all TABLE_END
    assert result == 0
    assert launched == [("A", "PARENT"), ("A", "PARENT"), ("A", "CHILD")]
    assert tables_config[("A", "PARENT")]["conds"][0]["ctl_status"] == Status.TABLE_END
    assert tables_config[("A", "CHILD")]["conds"][0]["ctl_status"] == Status.TABLE_END


def test_failed_worker_that_makes_no_progress_is_skipped(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM execution — a worker that fails without processing any
    # new rows is marked SKIPPED so the run can still finish remaining tables
    # Given: PARENT always fails with same row counts (no progress)
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

    # When: ILM execution runs
    result = ldb_runner.ldb_exec_ilm(_execution_config(), tables_config, "20261006", _StatusEngine(), object())

    # Then: exit code 1, PARENT is SKIPPED, CHILD never started (blocked by PARENT)
    assert result == 1
    assert launched == [("A", "PARENT")]
    assert tables_config[("A", "PARENT")]["conds"][0]["ctl_status"] == Status.SKIPPED
    assert tables_config[("A", "CHILD")]["conds"][0]["ctl_status"] is None


def test_unexpected_worker_exception_returns_error_code(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/exception-handling.md — ExecutionError: unhandled worker exceptions
    # are captured and the coordinator returns exit code 1
    # Given: a worker that raises unexpectedly
    tables_config = {("A", "PARENT"): _table_config()}

    def crashing_worker(config, owner, table_name, plsql_code, process_date):
        raise RuntimeError("process crashed unexpectedly")

    monkeypatch.setattr(ldb_runner, "ProcessPoolExecutor", _ImmediateExecutor)
    monkeypatch.setattr(ldb_runner, "process_table", crashing_worker)

    # When: ILM execution runs
    result = ldb_runner.ldb_exec_ilm(_execution_config(), tables_config, "20261006", _StatusEngine(), object())

    # Then: exit code 1 — unhandled exceptions never silently succeed
    assert result == 1


# ---------------------------------------------------------------------------
# process_table — public interface
# ---------------------------------------------------------------------------


def test_process_table_returns_table_end_and_closes_connection_on_success(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: README > Run ILM execution — a successful table run returns TABLE_END
    # status and the connection is always closed
    # Given: a DummyEngine where sql_block_run succeeds
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

    # When: process_table runs
    result = ldb_runner.process_table(config, "OWNER", "TABLE", "begin null; end;", "20261006")

    # Then: TABLE_END with before/after row counts and connection closed
    assert result == ("OWNER", "TABLE", Status.TABLE_END, 2, 7, None, None)
    assert calls == {"ran": True, "closed": True}


def test_process_table_returns_error_status_and_closes_connection_on_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: docs/exception-handling.md — ExecutionError: worker failures are recoverable;
    # the error is recorded and the connection is always closed
    # Given: a DummyEngine where sql_block_run fails
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

    # When: process_table runs and the SQL block raises
    result = ldb_runner.process_table(config, "OWNER", "TABLE", "begin null; end;", "20261006")

    # Then: ERROR status returned, error saved, connection closed
    assert result == ("OWNER", "TABLE", Status.ERROR, 3, 3, None, "worker stopped")
    assert calls == {"saved": True, "closed": True}


def test_process_table_returns_error_when_recovery_query_also_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: docs/exception-handling.md — ExecutionError: if the post-failure row
    # count query also fails, the table still returns ERROR (not a crash)
    # Given: get_rows_processed raises on the second call (after the SQL failure)
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

    monkeypatch.setattr(ldb_runner, "get_db_engine", lambda name: DummyEngine())
    config = Config(
        schema="s",
        db_engine="oracle",
        source_username="u",
        source_password="pw",
        source_dsn="dsn",
    )

    # When: process_table runs
    result = ldb_runner.process_table(config, "OWNER", "TABLE", "begin null; end;", "20261006")

    # Then: ERROR status — the table run did not succeed
    assert result[2] == Status.ERROR
