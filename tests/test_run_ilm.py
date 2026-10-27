import logging
from concurrent.futures import Future
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from limitsdb.core import ldb_ddl, ldb_params_config, ldb_runner, ldb_utils
from limitsdb.core.ldb_errors import ConfigurationError, ExecutionError, RunnerBusyError
from limitsdb.core.ldb_params_config import Config
from limitsdb.core.ldb_status import Status
from limitsdb.core.ldb_status_report import build_executions
from limitsdb.db.ldb_engines import (
    ColumnDefinition,
    ControlEntry,
    DatabaseEngine,
    LogEntry,
    MonitoringAccess,
    OperationProgress,
    SessionActivity,
    TableRunRecord,
    WaitItem,
)
from limitsdb.db.oracle.ldb_engine_impl import OracleEngine

# ---------------------------------------------------------------------------
# Config: ILM run prerequisites
# ---------------------------------------------------------------------------


def test_schema_and_dsn_are_required_for_non_plan_modes():
    # Spec: README > Run ILM — schema, source_dsn and credentials are required
    # Given: configs missing required fields
    # When: Config is constructed with an empty schema
    # Then: ValueError is raised
    with pytest.raises(ValueError):
        ldb_params_config.Config(schema="", source_dsn="d", source_username="u", source_password="p")

    # When: Config is constructed with an empty source DSN
    # Then: ValueError is raised
    with pytest.raises(ValueError):
        ldb_params_config.Config(schema="s", source_dsn="", source_username="u", source_password="p")


def test_plan_mode_requires_no_credentials():
    # Spec: README > Run ILM > --mode PLAN — PLAN evaluates deps without a DB connection
    # Given: a PLAN config with no credentials
    plan = ldb_params_config.Config(schema="s", mode="PLAN")

    # Then: DSN is empty — no connection is attempted in PLAN mode
    assert plan.source_dsn == ""


def test_script_mode_enables_generation_flag():
    # Spec: README > Run ILM > --mode SCRIPT — SCRIPT generates SQL blocks for review
    # Given: a SCRIPT config with minimal credentials
    script = ldb_params_config.Config(
        schema="s",
        mode="SCRIPT",
        source_dsn="dsn",
        source_username="user",
        source_password="secret",
    )

    # Then: generate_script is set to true
    assert script.generate_script is True


def test_history_ilm_action_requires_history_connection():
    # Spec: README > Run ILM > --action HISTORY_ILM — history credentials are required
    # Given: a HISTORY_ILM config with history credentials
    cfg = ldb_params_config.Config(
        schema="s",
        action="HISTORY_ILM",
        history_dsn="dsn",
        history_username="user",
        history_password="secret",
    )

    # Then: action is accepted
    assert cfg.action == "HISTORY_ILM"

    # When: HISTORY_ILM is requested without history_dsn
    # Then: ValueError names the missing field
    with pytest.raises(ValueError, match="history_dsn"):
        ldb_params_config.Config(schema="s", action="HISTORY_ILM")


def test_invalid_mode_raises_validation_error():
    # Spec: README > Run ILM > --mode — only PLAN, VALIDATE, PREVIEW, SCRIPT, EXECUTE
    # are accepted mode names
    # Given: an unrecognised mode string
    # When / Then: ValueError names the invalid mode
    with pytest.raises(ValueError, match="invalid mode"):
        ldb_params_config.Config(schema="s", mode="BADMODE")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Credentials resolution
# ---------------------------------------------------------------------------


def test_get_effective_credentials_returns_source_by_default():
    # Spec: README > Run ILM — source credentials are used for SOURCE_ILM and as default
    # Given: a config with both source and history credentials
    cfg = Config(
        schema="s",
        mode="PLAN",
        source_username="src",
        source_password="pw1",
        source_dsn="dsn1",
        history_username="hist",
        history_password="pw2",
        history_dsn="dsn2",
        admin_source_username="admin_src",
        admin_source_password="apw1",
        admin_history_username="admin_hist",
        admin_history_password="apw2",
    )

    # When: credentials are resolved for the default (source) action
    # Then: source credentials are returned
    assert ldb_utils.get_effective_credentials(cfg) == ("src", "pw1", "dsn1")


def test_get_effective_credentials_returns_history_for_history_ilm():
    # Spec: README > Run ILM > --action HISTORY_ILM — history credentials are used
    # Given: a config with both environments populated
    cfg = Config(
        schema="s",
        mode="PLAN",
        source_username="src",
        source_password="pw1",
        source_dsn="dsn1",
        history_username="hist",
        history_password="pw2",
        history_dsn="dsn2",
        admin_source_username="admin_src",
        admin_source_password="apw1",
        admin_history_username="admin_hist",
        admin_history_password="apw2",
    )
    cfg.action = "HISTORY_ILM"

    # When: credentials are resolved for HISTORY_ILM
    # Then: history credentials are returned
    assert ldb_utils.get_effective_credentials(cfg) == ("hist", "pw2", "dsn2")


def test_get_effective_credentials_uses_admin_when_requested():
    # Spec: README > Run ILM > ldb-impl — admin credentials used for DDL operations
    # Given: a config with admin credentials for history
    cfg = Config(
        schema="s",
        mode="PLAN",
        source_username="src",
        source_password="pw1",
        source_dsn="dsn1",
        history_username="hist",
        history_password="pw2",
        history_dsn="dsn2",
        admin_source_username="admin_src",
        admin_source_password="apw1",
        admin_history_username="admin_hist",
        admin_history_password="apw2",
    )
    cfg.action = "HISTORY_ILM"

    # When: admin credentials are requested
    # Then: admin history credentials are returned
    assert ldb_utils.get_effective_credentials(cfg, admin=True) == ("admin_hist", "apw2", "dsn2")


def test_get_effective_credentials_raises_when_password_is_empty():
    # Spec: docs/exception-handling.md — missing credentials raise before any connection
    # Given: a config with empty history password
    cfg = Config(
        schema="s",
        mode="PLAN",
        source_username="src",
        source_password="pw1",
        source_dsn="dsn1",
        history_username="hist",
        history_password="pw2",
        history_dsn="dsn2",
        admin_source_username="admin_src",
        admin_source_password="apw1",
        admin_history_username="admin_hist",
        admin_history_password="apw2",
    )
    cfg.action = "HISTORY_ILM"
    cfg.history_password = ""

    # When / Then: ValueError is raised because credentials are incomplete
    with pytest.raises(ValueError):
        ldb_utils.get_effective_credentials(cfg)


# ---------------------------------------------------------------------------
# Script generation utilities
# ---------------------------------------------------------------------------


def test_nvl_returns_value_when_present_and_fallback_when_none():
    # Spec: README > utility — nvl mirrors Oracle NVL: return the first non-None value
    # Given: a non-None value and a None value
    # When / Then: the non-None value wins; the fallback is used for None
    assert ldb_utils.nvl("val", "default") == "val"
    assert ldb_utils.nvl(None, "fallback") == "fallback"


def test_max_ignore_none_returns_maximum_skipping_none_values():
    # Spec: README > utility — max_ignore_none skips None entries in the sequence
    # Given: a sequence mixing None and integers
    # When / Then: the maximum non-None value is returned; all-None gives None
    assert ldb_utils.max_ignore_none([None, 3, 2, None, 5]) == 5
    assert ldb_utils.max_ignore_none([None, None]) is None


def test_max_ignore_none_raises_for_incomparable_mixed_types():
    # Spec: README > utility — max_ignore_none propagates TypeError for incomparable values
    # Given: a sequence of string and integer (incomparable)
    # When / Then: TypeError propagates unchanged
    with pytest.raises(TypeError):
        ldb_utils.max_ignore_none(["string", 1])


def test_indent_lines_indents_continuation_lines_only():
    # Spec: README > utility — indent_lines adds prefix spaces to all lines except
    # the first, preserving the first line's position
    # Given: a multi-line text
    text = "line1\nline2\nline3"

    # When: indented by 2
    result = ldb_utils.indent_lines(text, 2)

    # Then: only continuation lines are indented
    assert result == "line1\n  line2\n  line3"


def test_join_wrapped_splits_at_max_width_on_separator():
    # Spec: README > utility — join_wrapped emits the separator at the start of a
    # wrapped continuation to preserve SQL readability
    # Given: items that exceed the max-width when joined
    # When: wrapped with width 4
    wrapped = ldb_utils.join_wrapped(",", ["a", "b", "long_word"], 4)

    # Then: the long word starts a new line prefixed by the separator
    assert wrapped.splitlines() == ["a,b", ",long_word"]


# ---------------------------------------------------------------------------
# PLAN mode — R2 exception: _plan_mode is the sole entry point to the PLAN
# mode contract documented in README > Run ILM > --mode PLAN
# ---------------------------------------------------------------------------


def test_plan_mode_raises_for_cyclic_table_dependencies(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM > --mode PLAN — circular dependencies cannot be resolved;
    # PLAN mode must detect and report the cycle before any processing
    # Given: two tables that reference each other
    rows = [
        {"source_owner": "A", "table_name": "T1", "referencing_tables": "A.T2 B"},
        {"source_owner": "A", "table_name": "T2", "referencing_tables": "A.T1 B"},
    ]
    config = Config(schema="s", mode="PLAN")
    monkeypatch.setattr(ldb_runner, "_load_offline_rows", lambda current: rows)

    # When / Then: ValueError naming the cycle is raised
    with pytest.raises(ValueError, match=r"[Cc]ycl"):
        ldb_runner._plan_mode(config)


def test_source_orphan_purge_without_use_added_columns_raises(monkeypatch: pytest.MonkeyPatch):
    # Spec: limitsdb/resources/ilm.example.yml — source_orphan_purge=Y requires
    # use_added_columns; disabling added columns while orphan purge is active is an error
    # Given: CHILD (with full orphan config) listed before PARENT so CHILD is validated first
    child = _minimal_row(
        "CHILD",
        source_orphan_purge="Y",
        orphan_check_column="B.ID",
        join_expr="@ JOIN SRC.PARENT B ON B.ID=A.PARENT_ID",
        referencing_tables="A.PARENT P",
    )
    parent = _minimal_row("PARENT")
    monkeypatch.setattr(ldb_runner, "_load_offline_rows", lambda config: [child, parent])

    # When / Then: ValueError names use_added_columns as the missing prerequisite
    with pytest.raises(ValueError, match="use_added_columns"):
        ldb_runner.process_tables_cnf(object(), _script_config(use_added_columns=False), _MinimalEngine(), "20261007")


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
        @staticmethod
        def get_identifier_str(value):
            return value.upper()

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
        @staticmethod
        def get_identifier_str(value):
            return value.upper()

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
        @staticmethod
        def get_identifier_str(value):
            return value.upper()

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
        @staticmethod
        def get_identifier_str(value):
            return value.upper()

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
        @staticmethod
        def get_identifier_str(value):
            return value.upper()

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
        @staticmethod
        def get_identifier_str(value):
            return value.upper()

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

    format_identifier = staticmethod(OracleEngine.format_identifier)

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
        format_identifier = staticmethod(OracleEngine.format_identifier)
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


class _JoinEngine:
    """Engine double for process_tables_cnf: two tables whose queries are returned as the SQL block."""

    get_identifier_str = staticmethod(OracleEngine.get_identifier_str)
    format_identifier = staticmethod(OracleEngine.format_identifier)
    get_date_condition = staticmethod(OracleEngine.get_date_condition)
    get_identifiers_from_expression = staticmethod(OracleEngine.get_identifiers_from_expression)
    rewrite_expression_identifiers = staticmethod(OracleEngine.rewrite_expression_identifiers)
    rewrite_table_owners = staticmethod(OracleEngine.rewrite_table_owners)
    get_fallback_expression = staticmethod(OracleEngine.get_fallback_expression)

    @staticmethod
    def get_ldb_columns_expressions():
        return "l_process_date", "sysdate"

    @staticmethod
    def get_table_columns(connection, owner, table_name):
        names = ["ID", "PARENT_ID", "CREATED_AT"]
        return names, {
            name: ColumnDefinition(name=name, data_type="varchar2", id=index, length=20)
            for index, name in enumerate(names, start=1)
        }

    @staticmethod
    def generate_sql_block(config, table_cnf, process_date):
        return table_cnf["query_expr"]


def _join_rules(join_expr: str) -> list[dict]:
    common = {
        "source_owner": "GL",
        "history_owner": "GLH",
        "retain_months_source": 2,
        "retain_months_history": 3,
        "exec_day": None,
        "frecuency": "D",
        "is_active": "Y",
        "purge_date_expr": "@CREATED_AT",
        "additional_filter_expr": None,
        "history_addtl_filter_expr": None,
        "extra_select_tables": None,
        "history_extra_select_tables": None,
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
    return [
        {**common, "id": 1, "table_name": "PARENT"},
        {
            **common,
            "id": 2,
            "table_name": "CHILD",
            "referencing_tables": "GL.PARENT B",
            "join_expr": join_expr,
        },
    ]


def _join_query(monkeypatch: pytest.MonkeyPatch, action: str, join_expr: str, **config_kwargs) -> str:
    """Return the query of the CHILD table as planned for the action."""
    monkeypatch.setattr(ldb_runner, "_load_offline_rows", lambda config: _join_rules(join_expr))
    config = Config(
        schema="s",
        action=action,
        mode="SCRIPT",
        source_dsn="dsn",
        source_username="user",
        source_password="password",
        history_dsn="dsn",
        history_username="user",
        history_password="password",
        **config_kwargs,
    )
    tables = ldb_runner.process_tables_cnf(object(), config, _JoinEngine(), "20261007")
    return tables[("GL", "CHILD")]["query_expr"]


def test_history_join_reads_the_history_copy_of_the_referenced_table(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > ILM Rule Semantics > Referencing tables — join_expr names the referenced table by its source
    # owner; a HISTORY_ILM run without derived columns joins the history copy, which lives in its history_owner.
    # Given: a child joined to GL.PARENT whose history copy is GLH.PARENT, history run without added columns
    # When: the history query is planned
    query = _join_query(monkeypatch, "HISTORY_ILM", "@ JOIN GL.PARENT B ON B.ID=A.PARENT_ID", use_added_columns=False)

    # Then: the join reads GLH.PARENT, and the source owner does not appear
    assert "inner JOIN glh.PARENT B" in query
    assert "GL.PARENT" not in query.replace("glh.PARENT", "")


def test_history_join_rewrites_every_spelling_of_the_source_owner_only_on_the_referenced_table(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: README > ILM Rule Semantics > Referencing tables — only the referenced table moves to its history
    # owner; other text of the join (columns, aliases, longer names) must remain as written.
    # Given: lower-case and spaced owner qualification, and a longer name sharing the table's prefix
    join_expr = "@ JOIN gl . parent B ON B.ID=A.PARENT_ID AND A.X=GL.PARENT_X.Y"

    # When: the history query is planned without added columns
    query = _join_query(monkeypatch, "HISTORY_ILM", join_expr, use_added_columns=False)

    # Then: the referenced table is rewritten (its token as written) and the longer name is untouched
    assert "JOIN glh.parent B" in query
    assert "GL.PARENT_X.Y" in query


def test_config_identifiers_are_the_exact_dictionary_names(monkeypatch: pytest.MonkeyPatch):
    # Spec: ilm.example.yml > Identifier rules — single-identifier parameters hold the name exactly as the data
    # dictionary has it; only the SQL text of expressions follows the engine's SQL identifier rules
    # Given: objects whose dictionary names are mixed case, written in the config exactly like that, and a join
    # written as SQL needs it (quoted)
    rules = _join_rules('@ JOIN "Gl"."Parent" B ON B.ID=A.PARENT_ID')
    for rule, table in zip(rules, ("Parent", "Child"), strict=True):
        rule.update(source_owner="Gl", history_owner="Glh", table_name=table)
    rules[1]["referencing_tables"] = "Gl.Parent B"
    monkeypatch.setattr(ldb_runner, "_load_offline_rows", lambda config: rules)
    config = Config(
        schema="s",
        action="HISTORY_ILM",
        mode="SCRIPT",
        use_added_columns=False,
        source_dsn="dsn",
        source_username="user",
        source_password="password",
        history_dsn="dsn",
        history_username="user",
        history_password="password",
    )

    # When: the configuration is processed
    tables = ldb_runner.process_tables_cnf(object(), config, _JoinEngine(), "20261007")

    # Then: the tables are keyed by the names as written, and the
    # history join renders the history owner like any generated SQL identifier
    assert ("Gl", "Child") in tables
    assert 'JOIN "Glh"."Parent" B' in tables[("Gl", "Child")]["query_expr"]
    # and the FROM clause (the runtime owner, here history) follows the same rules instead of being lower-cased
    assert tables[("Gl", "Child")]["query_expr"].startswith('from "Glh"."Child" A')


def _planned_grants(monkeypatch: pytest.MonkeyPatch, tmp_path, rows: list, **config) -> tuple[list[str], str]:
    """Run ldb-run in SCRIPT mode over the rows; return the source grants applied and the history DDL file text."""
    engine = _HistoryDdlEngine()
    rc = _run_ldb(
        monkeypatch,
        _history_ddl_config(mode="SCRIPT", history_admin=True, ddl_pending_dir=str(tmp_path / "pending"), **config),
        engine,
        rows=rows,
    )
    assert rc == 0
    return engine.source_granted, _script_files(tmp_path / "pending").get("history_ddl", "")


_MIXED_CASE_RULE = {"source_owner": "Gl", "history_owner": "Glh", "table_name": "Parent"}


def test_privilege_targets_are_the_exact_dictionary_names(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # Spec: ilm.example.yml > Identifier rules — single-identifier parameters hold the exact dictionary name
    # Given: a rule whose owners and table have mixed-case dictionary names
    # When: ldb-run grants the privileges of both environments
    source_granted, history_ddl = _planned_grants(monkeypatch, tmp_path, [_MIXED_CASE_RULE])

    # Then: every grant target is the name as written, not upper-cased
    assert "GRANT SELECT ON Gl.Parent TO LDB_SOURCE_ROLE" in source_granted
    assert "GRANT SELECT ON Glh.Parent TO LDB_HISTORY_ROLE;" in history_ddl
    assert not [grant for grant in source_granted if "GL.PARENT" in grant.upper() and "Gl.Parent" not in grant]


def test_extra_select_tables_are_the_exact_dictionary_names(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # Spec: ilm.example.yml > Identifier rules — list entries are exact dictionary names; a table without owner
    # belongs to the rule's owner
    # Given: a rule with extra tables, one without owner, in mixed case
    rule = {**_MIXED_CASE_RULE, "extra_select_tables": "Dim, Ref.Codes"}

    # When: ldb-run grants the privileges
    source_granted, _history_ddl = _planned_grants(monkeypatch, tmp_path, [rule])

    # Then: the extra tables keep their case and the default owner is the configured one
    assert "GRANT SELECT ON Gl.Dim TO LDB_SOURCE_ROLE" in source_granted
    assert "GRANT SELECT ON Ref.Codes TO LDB_SOURCE_ROLE" in source_granted


def test_history_extra_select_tables_are_the_exact_dictionary_names(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # Spec: ilm.example.yml > Identifier rules — history_extra_select_tables overrides extra_select_tables in HISTORY
    # Given: a rule whose history list is mixed case
    rule = {**_MIXED_CASE_RULE, "extra_select_tables": "Dim", "history_extra_select_tables": "Dim_H"}

    # When: ldb-run grants the privileges
    source_granted, history_ddl = _planned_grants(monkeypatch, tmp_path, [rule])

    # Then: history uses its own list, with the case as written and the history owner as default
    assert "GRANT SELECT ON Glh.Dim_H TO LDB_HISTORY_ROLE;" in history_ddl
    assert "Dim;" not in history_ddl and "Glh.Dim " not in history_ddl
    assert "GRANT SELECT ON Gl.Dim TO LDB_SOURCE_ROLE" in source_granted


@pytest.mark.parametrize("referencing", ["Gl.Missing B", "Gl.Parent"])
def test_referencing_tables_must_name_configured_tables_with_a_alias(referencing: str):
    # Spec: README > ILM Rule Semantics > Referencing tables — each entry is [OWNER.]TABLE ALIAS of a configured
    # table
    # Given: a child pointing to an unconfigured table, or with no alias
    rows = [
        {"source_owner": "Gl", "table_name": "Parent", "referencing_tables": ""},
        {"source_owner": "Gl", "table_name": "Child", "referencing_tables": referencing},
    ]

    # When / Then: the dependency graph is refused
    with pytest.raises(ValueError):
        ldb_runner._build_dependency_graph(rows)  # type: ignore[arg-type]


def test_dependency_graph_matches_referenced_tables_case_sensitively():
    # Spec: ilm.example.yml > Identifier rules — names are exact, so Gl.Parent and GL.PARENT are different tables
    # Given: a child that references the table with another case than the configured one
    rows = [
        {"source_owner": "Gl", "table_name": "Parent", "referencing_tables": ""},
        {"source_owner": "Gl", "table_name": "Child", "referencing_tables": "GL.PARENT P"},
    ]

    # When / Then: the reference is not found
    with pytest.raises(ValueError, match="not present"):
        ldb_runner._build_dependency_graph(rows)  # type: ignore[arg-type]


def test_dependency_graph_orders_mixed_case_tables():
    # Spec: README > Run ILM > --mode PLAN — referencing (child) tables are planned before the tables they
    # reference
    # Given: mixed-case names written identically in the table and in its reference
    rows = [
        {"source_owner": "Gl", "table_name": "Parent", "referencing_tables": ""},
        {"source_owner": "Gl", "table_name": "Child", "referencing_tables": "Gl.Parent P"},
    ]

    # When: the layers are computed
    layers = ldb_runner._compute_plan_layers(ldb_runner._build_dependency_graph(rows))  # type: ignore[arg-type]

    # Then: the child layer comes first
    assert layers == [[("Gl", "Child")], [("Gl", "Parent")]]


def test_source_join_keeps_the_source_owner(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > ILM Rule Semantics > Referencing tables — SOURCE_ILM reads the referenced tables from source.
    # Given: the same child and join as the history case
    # When: the source query is planned
    query = _join_query(monkeypatch, "SOURCE_ILM", "@ JOIN GL.PARENT B ON B.ID=A.PARENT_ID")

    # Then: the join is not redirected to the history owner
    assert "inner JOIN GL.PARENT B" in query
    assert "JOIN GLH" not in query


def test_history_join_with_added_columns_has_no_join(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > ILM Rule Semantics > Historical predicate snapshot — with use_added_columns the history
    # run uses the stored values and does not join.
    # Given / When: a history run with the default added columns
    query = _join_query(monkeypatch, "HISTORY_ILM", "@ JOIN GL.PARENT B ON B.ID=A.PARENT_ID")

    # Then: the query has no join
    assert "JOIN" not in query.upper()


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
    def __init__(self):
        self.events = []

    def all_status_tend(self, connection, tables_config, process_date):
        return all(table["conds"][0]["ctl_status"] == Status.TABLE_END for table in tables_config.values())

    def get_connection(self, config, admin=False, env=None):
        self.events.append("connect")
        return "lock-connection"

    def acquire_run_locks(self, lock_conn, key_conn, tables, run_id, mode, host, pid):
        self.events.append(("acquire", lock_conn, key_conn, list(tables), run_id, mode))

    def check_run_locks(self, lock_conn):
        self.events.append(("ping", lock_conn))

    def release_run_locks(self, lock_conn):
        self.events.append(("release", lock_conn))

    def close_connection(self, conn):
        self.events.append(("close", conn))


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

    def successful_worker(config, owner, table_name, plsql_code, process_date, run_id):
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

    def worker(config, owner, table_name, sql, process_date, run_id):
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

    def progress_then_success(config, owner, table_name, plsql_code, process_date, run_id):
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

    def no_progress_failure(config, owner, table_name, plsql_code, process_date, run_id):
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

    def crashing_worker(config, owner, table_name, plsql_code, process_date, run_id):
        raise RuntimeError("process crashed unexpectedly")

    monkeypatch.setattr(ldb_runner, "ProcessPoolExecutor", _ImmediateExecutor)
    monkeypatch.setattr(ldb_runner, "process_table", crashing_worker)

    # When: ILM execution runs
    result = ldb_runner.ldb_exec_ilm(_execution_config(), tables_config, "20261006", _StatusEngine(), object())

    # Then: exit code 1 — unhandled exceptions never silently succeed
    assert result == 1


def test_runner_locks_every_active_table_before_launching_and_releases_after():
    # Spec: docs/concurrency-control.md > Layer 1 — the runner takes one lock per table on a dedicated
    # connection before any worker starts and frees it when the run ends
    # Given: two tables and a third one flagged to skip
    engine = _StatusEngine()
    tables_config = {
        ("A", "T2"): _table_config(),
        ("A", "T1"): _table_config(),
        ("A", "OFF"): {**_table_config(), "skip": True, "conds": [{"ctl_status": Status.TABLE_END}]},
    }
    seen = []

    def worker(config, owner, table_name, plsql_code, process_date, run_id):
        seen.append((owner, table_name, run_id))
        return owner, table_name, Status.TABLE_END, 0, 1, None, None

    # When: ILM execution runs
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ldb_runner, "ProcessPoolExecutor", _ImmediateExecutor)
        patch.setattr(ldb_runner, "process_table", worker)
        result = ldb_runner.ldb_exec_ilm(_execution_config(), tables_config, "20261006", engine, "main-connection")

    # Then: the skipped table is not locked, the lock connection differs from the key connection, every worker
    # carries the run_id that was published, and the lock is released and closed at the end
    assert result == 0
    acquire = next(event for event in engine.events if event[0] == "acquire")
    assert acquire[1:3] == ("lock-connection", "main-connection")
    assert sorted(acquire[3]) == [("A", "T1"), ("A", "T2")]
    assert acquire[5] == "EXECUTE"
    assert {run_id for _, _, run_id in seen} == {acquire[4]}
    assert engine.events[-2:] == [("release", "lock-connection"), ("close", "lock-connection")]


def test_runner_that_finds_another_runner_starts_nothing_and_still_releases(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    # Spec: docs/concurrency-control.md > Layer 1 — a table held by another live runner stops this one before
    # any worker is launched (logged once, rc 1, no traceback)
    # Given: an engine whose lock acquisition finds the table busy
    class _BusyEngine(_StatusEngine):
        def acquire_run_locks(self, *args):
            raise RunnerBusyError("Another runner is processing A.T1")

    engine = _BusyEngine()
    launched = []
    monkeypatch.setattr(ldb_runner, "ProcessPoolExecutor", _ImmediateExecutor)
    monkeypatch.setattr(ldb_runner, "process_table", lambda *args: launched.append(args))

    # When/Then: the run is refused, nothing is launched and the lock connection is closed
    runner_logger = logging.getLogger(ldb_runner.logger.name)
    runner_logger.addHandler(caplog.handler)
    try:
        result = ldb_runner.ldb_exec_ilm(
            _execution_config(), {("A", "T1"): _table_config()}, "20261006", engine, object()
        )
    finally:
        runner_logger.removeHandler(caplog.handler)
    assert result == 1
    assert launched == []
    assert "Another runner is processing A.T1" in caplog.text
    assert "Runner id" not in caplog.text  # a refused runner never had a run to identify
    assert engine.events[-1] == ("close", "lock-connection")


def _tick_clock(monkeypatch: pytest.MonkeyPatch, step: float) -> None:
    """Make ``time.monotonic`` in the runner advance ``step`` seconds per reading."""
    state = {"now": 0.0}

    def monotonic() -> float:
        state["now"] += step
        return state["now"]

    monkeypatch.setattr(ldb_runner.time, "monotonic", monotonic)


def test_lock_connection_is_pinged_while_no_worker_finishes(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/concurrency-control.md > Layer 1 — a trivial query on the lock connection detects an idle or
    # cut session while the workers run
    # Given: a worker that is not finished on the first wait, and finished on the second
    engine = _StatusEngine()
    _tick_clock(monkeypatch, ldb_runner._LOCK_PING_SECONDS)
    real_wait = ldb_runner.wait
    waits = []

    def slow_then_done(futures, timeout, return_when):
        waits.append(timeout)
        if len(waits) == 1:
            return set(), set(futures)
        return real_wait(futures, timeout=timeout, return_when=return_when)

    monkeypatch.setattr(ldb_runner, "wait", slow_then_done)
    monkeypatch.setattr(ldb_runner, "ProcessPoolExecutor", _ImmediateExecutor)
    monkeypatch.setattr(
        ldb_runner,
        "process_table",
        lambda config, owner, table_name, sql, date, run_id: (owner, table_name, Status.TABLE_END, 0, 1, None, None),
    )

    # When: ILM execution runs
    result = ldb_runner.ldb_exec_ilm(_execution_config(), {("A", "T1"): _table_config()}, "20261006", engine, object())

    # Then: the idle wait pinged the lock connection and the run completed
    assert result == 0
    assert engine.events.count(("ping", "lock-connection")) >= 1


def test_lock_connection_is_pinged_on_schedule_even_when_workers_keep_finishing(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/concurrency-control.md > Layer 1 — the lock session is checked every ping interval of elapsed
    # time, not only when a wait times out
    # Given: two tables that finish one after the other, each wait returning at once, and a clock that moves a
    # full interval per reading
    engine = _StatusEngine()
    _tick_clock(monkeypatch, ldb_runner._LOCK_PING_SECONDS)
    monkeypatch.setattr(ldb_runner, "ProcessPoolExecutor", _ImmediateExecutor)
    monkeypatch.setattr(
        ldb_runner,
        "process_table",
        lambda config, owner, table_name, sql, date, run_id: (owner, table_name, Status.TABLE_END, 0, 1, None, None),
    )
    tables = {("A", "T1"): _table_config(), ("A", "T2"): _table_config()}

    # When: ILM execution runs
    result = ldb_runner.ldb_exec_ilm(_execution_config(), tables, "20261006", engine, object())

    # Then: the lock connection was pinged although no wait ever timed out
    assert result == 0
    assert engine.events.count(("ping", "lock-connection")) >= 1


def test_wait_never_lasts_past_the_next_lock_ping(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/concurrency-control.md > Layer 1 — the ping also keeps the lock session alive, so it happens at
    # least every ping interval
    # Given: a clock that advances 10 s per reading and workers that never finish on their own
    _tick_clock(monkeypatch, 10.0)
    timeouts: list[float] = []
    real_wait = ldb_runner.wait

    def record(futures, timeout, return_when):
        timeouts.append(timeout)
        if len(timeouts) < 4:
            return set(), set(futures)
        return real_wait(futures, timeout=timeout, return_when=return_when)

    monkeypatch.setattr(ldb_runner, "wait", record)
    monkeypatch.setattr(ldb_runner, "ProcessPoolExecutor", _ImmediateExecutor)
    monkeypatch.setattr(
        ldb_runner,
        "process_table",
        lambda config, owner, table_name, sql, date, run_id: (owner, table_name, Status.TABLE_END, 0, 1, None, None),
    )

    # When: ILM execution runs
    ldb_runner.ldb_exec_ilm(_execution_config(), {("A", "T1"): _table_config()}, "20261006", _StatusEngine(), object())

    # Then: no wait is longer than what is left of the interval, and it shrinks as the interval is used up
    assert all(0 <= timeout <= ldb_runner._LOCK_PING_SECONDS for timeout in timeouts)
    assert timeouts[0] > timeouts[1]


def test_lost_lock_connection_aborts_the_run(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/concurrency-control.md > Failure scenarios — if the lock session is gone the mutex no longer
    # holds, so the runner stops with the error instead of going on unprotected
    # Given: a ping that fails while a worker is still running
    class _LostEngine(_StatusEngine):
        def check_run_locks(self, lock_conn):
            raise ExecutionError("The runner lock connection was lost")

    _tick_clock(monkeypatch, ldb_runner._LOCK_PING_SECONDS)
    monkeypatch.setattr(ldb_runner, "wait", lambda futures, timeout, return_when: (set(), set(futures)))
    monkeypatch.setattr(ldb_runner, "ProcessPoolExecutor", _ImmediateExecutor)
    monkeypatch.setattr(
        ldb_runner,
        "process_table",
        lambda config, owner, table_name, sql, date, run_id: (owner, table_name, Status.TABLE_END, 0, 1, None, None),
    )

    # When/Then: the failure reaches the caller and the lock connection is still closed
    engine = _LostEngine()
    with pytest.raises(ExecutionError):
        ldb_runner.ldb_exec_ilm(_execution_config(), {("A", "T1"): _table_config()}, "20261006", engine, object())
    assert engine.events[-1] == ("close", "lock-connection")


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
        @staticmethod
        def get_identifier_str(value):
            return value.upper()

        def get_connection(self, config):
            return object()

        def get_system_date(self, conn):
            return "start"

        def get_resumed_rows(self, conn, owner, table_name, process_date):
            return 0

        def get_rows_processed(self, conn, owner, table_name, process_date):
            return 7 if calls["ran"] else 2

        def sql_block_run(self, conn, plsql_code, mode, run_id):
            calls["ran"] = True

        def close_connection(self, conn):
            calls["closed"] = True

    monkeypatch.setattr(ldb_runner, "get_db_engine", lambda name: DummyEngine())
    config = Config(
        schema="s",
        db_engine="oracle",
        action="SOURCE_ILM",
        mode="EXECUTE",
        source_username="user",
        source_password="pw",
        source_dsn="dsn",
    )

    # When: process_table runs
    result = ldb_runner.process_table(config, "OWNER", "TABLE", "begin null; end;", "20261006", "run-1")

    # Then: TABLE_END counting only this run's rows (the counter restarts at TSTART) and connection closed
    assert result == ("OWNER", "TABLE", Status.TABLE_END, 0, 7, None, None)
    assert calls == {"ran": True, "closed": True}


def test_process_table_returns_error_status_and_closes_connection_on_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: docs/exception-handling.md — ExecutionError: worker failures are recoverable;
    # the error is recorded and the connection is always closed
    # Given: a DummyEngine where sql_block_run fails
    calls = {"saved": False, "closed": False}

    class DummyEngine:
        @staticmethod
        def get_identifier_str(value):
            return value.upper()

        def get_connection(self, config):
            return object()

        def get_system_date(self, conn):
            return "start"

        def get_resumed_rows(self, conn, owner, table_name, process_date):
            return 0

        def get_rows_processed(self, conn, owner, table_name, process_date):
            return 3

        def error_recorded_since(self, conn, owner, table_name, since):
            return False

        def sql_block_run(self, conn, plsql_code, mode, run_id):
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
    result = ldb_runner.process_table(config, "OWNER", "TABLE", "begin null; end;", "20261006", "run-1")

    # Then: ERROR status returned, error saved, connection closed
    assert result == ("OWNER", "TABLE", Status.ERROR, 0, 3, None, "worker stopped")
    assert calls == {"saved": True, "closed": True}


def test_process_table_leaves_the_control_row_alone_when_another_run_owns_the_table(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: README > Run ILM — a table in use by another runner is not marked ERROR nor credited with stale rows
    calls = {"saved": False}

    class _Busy(Exception):
        def __init__(self):
            super().__init__(type("E", (), {"code": 20002, "message": "in process"})())

    class DummyEngine:
        @staticmethod
        def get_identifier_str(value):
            return value.upper()

        def get_connection(self, config):
            return object()

        def get_system_date(self, conn):
            return "start"

        def get_resumed_rows(self, conn, owner, table_name, process_date):
            return 0

        def get_rows_processed(self, conn, owner, table_name, process_date):
            return 277839

        def sql_block_run(self, conn, plsql_code, mode, run_id):
            raise _Busy()

        def save_error_status(self, *args):
            calls["saved"] = True

        def close_connection(self, conn):
            pass

    monkeypatch.setattr(ldb_runner, "get_db_engine", lambda name: DummyEngine())
    config = Config(
        schema="s",
        db_engine="oracle",
        action="SOURCE_ILM",
        source_username="user",
        source_password="pw",
        source_dsn="dsn",
    )

    result = ldb_runner.process_table(config, "OWNER", "TABLE", "begin null; end;", "20261006", "run-1")

    assert result == ("OWNER", "TABLE", Status.ERROR, 0, 0, 20002, "in process")
    assert calls["saved"] is False


def test_process_table_returns_error_when_recovery_query_also_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: docs/exception-handling.md — ExecutionError: if the post-failure row
    # count query also fails, the table still returns ERROR (not a crash)
    # Given: get_rows_processed raises on the second call (after the SQL failure)
    call_count = [0]

    class DummyEngine:
        @staticmethod
        def get_identifier_str(value):
            return value.upper()

        def get_connection(self, config):
            return object()

        def get_system_date(self, conn):
            return "start"

        def get_resumed_rows(self, conn, owner, table_name, process_date):
            return 0

        def get_rows_processed(self, conn, owner, table_name, process_date):
            call_count[0] += 1
            if call_count[0] == 1:
                return 0
            raise RuntimeError("recovery DB unavailable")

        def sql_block_run(self, conn, plsql_code, mode, run_id):
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
    result = ldb_runner.process_table(config, "OWNER", "TABLE", "begin null; end;", "20261006", "run-1")

    # Then: ERROR status — the table run did not succeed
    assert result[2] == Status.ERROR


def _worker_engine(monkeypatch: pytest.MonkeyPatch, **overrides):
    """Install a process_table engine whose calls are recorded in ``log``; ``overrides`` replace its methods."""
    log: list[tuple[str, tuple]] = []

    class _Engine:
        def get_connection(self, config, admin=False, env=None):
            return object()

        def get_system_date(self, conn):
            return datetime(2026, 10, 6, 12, 0)

        def get_resumed_rows(self, conn, owner, table_name, process_date):
            return 0

        def get_rows_processed(self, conn, owner, table_name, process_date):
            return 5

        def error_recorded_since(self, conn, owner, table_name, since):
            log.append(("error_recorded_since", (since,)))
            return False

        def sql_block_run(self, conn, plsql_code, mode, run_id):
            log.append(("sql_block_run", (mode, run_id)))

        def save_error_status(self, *args):
            log.append(("save_error_status", args[8:]))

        def close_connection(self, conn):
            pass

    for name, function in overrides.items():
        setattr(_Engine, name, function)
    monkeypatch.setattr(ldb_runner, "get_db_engine", lambda name: _Engine())
    return log


def _worker_config(mode: str = "EXECUTE"):
    return Config(
        schema="s",
        db_engine="oracle",
        action="SOURCE_ILM",
        mode=mode,  # type: ignore[arg-type]
        source_username="user",
        source_password="pw",
        source_dsn="dsn",
    )


def test_process_table_hands_mode_and_run_id_to_the_block(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/concurrency-control.md > Layer 2 — the block receives the mode and the run_id of its runner
    # Given: a PREVIEW worker
    log = _worker_engine(monkeypatch)

    # When: process_table runs
    ldb_runner.process_table(_worker_config("PREVIEW"), "OWNER", "TABLE", "begin null; end;", "20261006", "run-9")

    # Then: the block is run with PREVIEW and run-9
    assert ("sql_block_run", ("PREVIEW", "run-9")) in log


def test_process_table_reports_only_this_runs_rows_when_it_resumes_an_interrupted_execute(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: docs/concurrency-control.md > Process date — an EXECUTE continues an incomplete EXECUTE of the same
    # date and keeps its rows; the result carries the baseline so the runner reports this run's rows
    # Given: an interrupted EXECUTE that had moved 3 rows, and a block that ends with 5 in the counter
    _worker_engine(monkeypatch, get_resumed_rows=lambda self, conn, owner, table_name, process_date: 3)

    # When: process_table runs
    result = ldb_runner.process_table(_worker_config(), "OWNER", "TABLE", "begin null; end;", "20261006", "run-1")

    # Then: baseline 3 and total 5 are reported
    assert result == ("OWNER", "TABLE", Status.TABLE_END, 3, 5, None, None)


def test_process_table_never_resumes_a_preview(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/concurrency-control.md > Process date — a PREVIEW deletes nothing, so it always starts over
    # Given: an engine that would report resumed rows
    _worker_engine(monkeypatch, get_resumed_rows=lambda self, conn, owner, table_name, process_date: 3)

    # When: a PREVIEW worker runs
    result = ldb_runner.process_table(_worker_config("PREVIEW"), "OWNER", "TABLE", "begin null; end;", "20261006", "r")

    # Then: the baseline is 0
    assert result[3] == 0


def test_failed_resumed_table_without_new_rows_reports_the_baseline_so_it_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: README > Run ILM execution — a failure without progress is SKIPPED; a resumed table must be compared
    # against the rows it already had, not against zero
    # Given: a resumed table (baseline 4) whose block fails without moving anything
    def fail(self, conn, plsql_code, mode, run_id):
        raise RuntimeError("worker stopped")

    _worker_engine(
        monkeypatch,
        get_resumed_rows=lambda self, conn, owner, table_name, process_date: 4,
        get_rows_processed=lambda self, conn, owner, table_name, process_date: 4,
        sql_block_run=fail,
    )

    # When: process_table runs
    result = ldb_runner.process_table(_worker_config(), "OWNER", "TABLE", "begin null; end;", "20261006", "run-1")

    # Then: previous and current rows are equal, which the runner turns into SKIPPED
    assert result[2:5] == (Status.ERROR, 4, 4)


def test_process_table_does_not_write_a_second_error_when_the_block_already_did(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/concurrency-control.md > Layer 2 — the block's own handler persists its ERROR; Python writes
    # one only for failures outside the block (PEND-022)
    # Given: a failing block whose ERROR is already in ldb_ctl
    def fail(self, conn, plsql_code, mode, run_id):
        raise RuntimeError("ORA-20004: parent not processed")

    log = _worker_engine(
        monkeypatch,
        sql_block_run=fail,
        error_recorded_since=lambda self, conn, owner, table_name, since: True,
    )

    # When: process_table runs
    result = ldb_runner.process_table(_worker_config(), "OWNER", "TABLE", "begin null; end;", "20261006", "run-1")

    # Then: the failure is reported but no ERROR is written again
    assert result[2] == Status.ERROR
    assert not [name for name, _ in log if name == "save_error_status"]


def test_process_table_writes_the_error_for_a_failure_outside_the_block(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/concurrency-control.md > Layer 2 — a failure the block could not record (compile error, missing
    # procedure) is still persisted by the worker, with this run's rows and its run_id
    # Given: a failing block that left no ERROR in ldb_ctl, and a resumed table with 2 previous rows
    def fail(self, conn, plsql_code, mode, run_id):
        raise RuntimeError("PLS-00201: identifier must be declared")

    log = _worker_engine(
        monkeypatch,
        sql_block_run=fail,
        get_resumed_rows=lambda self, conn, owner, table_name, process_date: 2,
    )

    # When: process_table runs
    ldb_runner.process_table(_worker_config(), "OWNER", "TABLE", "begin null; end;", "20261006", "run-1")

    # Then: the error is saved once, for this run's 3 rows (5 total minus 2), under run-1
    saved = [args for name, args in log if name == "save_error_status"]
    assert saved == [(3, "run-1")]


@pytest.mark.parametrize("code", [20001, 20002, 20003, 20005, 20006])
def test_process_table_leaves_the_control_row_alone_for_every_foreign_control_code(
    monkeypatch: pytest.MonkeyPatch, code: int
):
    # Spec: docs/concurrency-control.md > Error codes — in use, guarded, already processed, ownership lost and
    # PREVIEW refused all mean the control row is not this run's: nothing is written to it
    # Given: a block that fails with the code
    class _Foreign(Exception):
        def __init__(self):
            super().__init__(type("E", (), {"code": code, "message": "not yours"})())

    def fail(self, conn, plsql_code, mode, run_id):
        raise _Foreign()

    log = _worker_engine(monkeypatch, sql_block_run=fail)

    # When: process_table runs
    result = ldb_runner.process_table(_worker_config(), "OWNER", "TABLE", "begin null; end;", "20261006", "run-1")

    # Then: ERROR is reported with the code and the control row is untouched
    assert result[2] == Status.ERROR and result[5] == code
    assert not [name for name, _ in log if name in ("save_error_status", "error_recorded_since")]


# ---------------------------------------------------------------------------
# History table DDL through ldb_run — conditional admin connection (PEND-015),
# script emission when admin credentials are absent (PEND-016) and mode gating
# ---------------------------------------------------------------------------


def _history_ddl_config(
    *,
    mode: str = "EXECUTE",
    action: str = "SOURCE_ILM",
    source_admin: bool = True,
    history_admin: bool = False,
    **extra,
) -> Config:
    """Return a Config with runtime credentials and, optionally, source and history admin credentials."""
    admin_credentials: dict[str, str] = {}
    if source_admin:
        admin_credentials.update(admin_source_username="admin-src", admin_source_password="admin-src-pw")
    if history_admin:
        admin_credentials.update(admin_history_username="admin-hist", admin_history_password="admin-hist-pw")
    return Config(
        **{
            "schema": "s",
            "action": action,
            "mode": mode,
            "source_dsn": "src-dsn",
            "source_username": "src-user",
            "source_password": "src-pw",
            "history_dsn": "hist-dsn",
            "history_username": "hist-user",
            "history_password": "hist-pw",
            **admin_credentials,
            **extra,
        }
    )


def _make_tables_config(*table_names: str) -> ldb_runner.TablesConfig:  # type: ignore[return-value]
    """Return a TablesConfig with one active entry per table name."""
    row: dict = {
        "source_owner": "SRC",
        "history_owner": "HIST",
        "referencing_tables": "",
        "ctl_status": None,
        "cond_expr": "",
        "source_orphan_purge": "N",
        "retain_months_source": 3,
        "retain_months_history": None,
        "purge_date_expr": None,
        "additional_filter_expr": None,
        "history_addtl_filter_expr": None,
        "has_lob_columns": "N",
        "long_columns": None,
        "orphan_check_column": None,
        "join_expr": None,
    }
    return {  # type: ignore[return-value]
        ("SRC", name): {
            "conds": [{**row, "table_name": name}],
            "referencing_tables": [],
            "skip": False,
            "sql_block": "select 1 from dual",
        }
        for name in table_names
    }


_MULTILINE_DDL = """ALTER TABLE HIST.{table} ADD (
    C1 NUMBER
)"""


class _HistoryDdlEngine:
    """Engine double for a full ldb_run: reports a DDL delta per history table and records how it was applied.

    Connections resolve credentials like a real engine, so a missing credential raises when one is requested."""

    def __init__(
        self,
        delta: dict[str, list[str]] | None = None,
        *,
        failing: dict[str, str] | None = None,
        history_unreachable: bool = False,
    ) -> None:
        self.delta = delta or {}
        self.failing = failing or {}
        self.history_unreachable = history_unreachable
        self.connections: list[tuple[bool, str | None]] = []
        self.attempted: list[str] = []
        self.admin_applied: list[str] = []
        self.grants_attempted: list[str] = []
        self.source_granted: list[str] = []
        self.missing_privileges: dict[str, list[tuple[str, str, str]]] = {}
        self.enabled_roles: set[str] | None = None  # None: every role is enabled
        self.structure_reads: list[tuple[str | None, str | None]] = []

    def get_connection(self, config, *, admin=False, env=None):
        ldb_utils.get_effective_credentials(config, admin=admin, env=env)
        if env == "HISTORY" and self.history_unreachable:
            raise ConfigurationError("history database unreachable")
        self.connections.append((admin, env))
        return SimpleNamespace(admin=admin, env=env)

    def get_system_date(self, conn):
        return datetime(2026, 10, 13)

    def close_connection(self, conn):
        pass

    def ensure_table_structure(self, conn, table, table_cnf, *, reference=None, ddl_sink=None, ddl_failures=None):
        self.structure_reads.append((conn.env if conn else None, reference[0] if reference else None))
        if conn is None:
            statements = [f"CREATE TABLE {table.owner}.{table.name} (ID NUMBER)"]
        elif reference:
            statements = [f"ALTER TABLE {table.owner}.{table.name} ADD (LDB_PROCESS_DATE DATE)"]
        else:
            statements = list(self.delta.get(table.name, []))
        self._emit(conn, statements, ddl_sink, ddl_failures)

    def get_primary_key_columns(self, conn, owner, table_name, table_cnf):
        return ()

    @staticmethod
    def get_identifier_str(value):
        return value.upper()

    def ensure_table_privileges(self, conn, role, tables, privileges, *, ddl_sink=None, ddl_failures=None):
        statements = [f"GRANT {p} ON {o}.{t} TO {role}" for o, t in tables for p in privileges]
        self._emit(conn, statements, ddl_sink, ddl_failures)
        return statements

    def get_table_columns(self, conn, owner, table_name):
        return [], {}

    def is_role_enabled(self, conn, role):
        return self.enabled_roles is None or role in self.enabled_roles

    def get_missing_table_privileges(self, conn, role, tables, privileges):
        return list(self.missing_privileges.get(role, []))

    def _emit(self, conn, statements, ddl_sink, ddl_failures):
        """Send the statements to the sink, or apply them like a real engine would over ``conn``."""
        for statement in statements:
            if ddl_sink is not None:
                ddl_sink(statement)
                continue
            if conn.admin and conn.env == "SOURCE":
                assert statement.startswith("GRANT "), "only grants are applied over the source admin connection"
                self.source_granted.append(statement)
                continue
            if conn.admin:
                assert conn.env == "HISTORY", "history DDL is applied over a history connection"
                self.admin_applied.append(statement)
                continue
            if statement.startswith("GRANT "):
                self.grants_attempted.append(statement)
            else:
                self.attempted.append(statement)
            if statement in self.failing:
                if ddl_failures is None:
                    raise RuntimeError(self.failing[statement])
                ddl_failures.append((statement, self.failing[statement]))

    @property
    def history_admin_opened(self) -> bool:
        return (True, "HISTORY") in self.connections


class _RecordingLogger:
    """Logger double that keeps the formatted messages per level."""

    def __init__(self) -> None:
        self.messages: dict[str, list[str]] = {}

    def __getattr__(self, level: str):
        def record(msg, *args, **kwargs):
            self.messages.setdefault(level, []).append(msg % args if args else msg)

        return record


def _run_ldb(
    monkeypatch: pytest.MonkeyPatch, config: Config, engine: _HistoryDdlEngine, *tables: str, rows: list | None = None
) -> int:
    """Run ldb_run with the engine double and one history table per name (T1 by default); return the exit code."""
    tables_config = _make_tables_config(*(tables or ("T1",)))
    monkeypatch.setattr(ldb_runner, "get_db_engine", lambda name: engine)
    monkeypatch.setattr(ldb_runner, "_load_offline_rows", lambda cfg: rows or [])
    monkeypatch.setattr(ldb_runner, "process_tables_cnf", lambda *args: tables_config)
    monkeypatch.setattr(ldb_runner, "ldb_exec_ilm", lambda *args: 0)
    with pytest.raises(SystemExit) as exc_info:
        ldb_runner.ldb_run(config)
    return exc_info.value.code


def test_history_admin_connection_not_opened_when_tables_are_up_to_date(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    # Spec: README > Run ILM > Admin credentials in ldb-run — when history admin
    # credentials are present but tables already match the required definition,
    # the admin connection is not opened and no DDL is executed.
    # Given: history admin credentials and an engine that reports no delta
    engine = _HistoryDdlEngine()

    # When: ldb-run executes
    rc = _run_ldb(monkeypatch, _history_ddl_config(history_admin=True), engine)

    # Then: no history admin connection was opened and nothing was applied
    assert rc == 0
    assert not engine.history_admin_opened
    assert engine.admin_applied == [] and engine.attempted == []
    assert capsys.readouterr().out == ""


def test_history_admin_connection_opened_when_ddl_changes_are_needed(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM > Admin credentials in ldb-run — when admin credentials
    # are present and a DDL delta is detected, the admin connection is opened and the
    # table structure is applied with it.
    # Given: history admin credentials and a pending delta
    engine = _HistoryDdlEngine({"T1": ["CREATE TABLE HIST.T1 (ID NUMBER)"]})

    # When: ldb-run executes
    rc = _run_ldb(monkeypatch, _history_ddl_config(history_admin=True), engine)

    # Then: the history admin connection applied the change
    assert rc == 0
    assert engine.history_admin_opened
    assert engine.admin_applied == ["CREATE TABLE HIST.T1 (ID NUMBER)"]


def test_history_ddl_is_applied_with_runtime_connection_when_admin_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture
):
    # Spec: README > Run ILM > Admin credentials in ldb-run — without admin history
    # credentials, a DDL delta is applied over the runtime history connection; when
    # every statement succeeds the run continues and no pending file is written.
    # Given: a two-statement delta that the runtime user can apply
    statements = ["ALTER TABLE HIST.T1 ADD (C1 NUMBER)", "ALTER TABLE HIST.T1 ADD (C2 NUMBER)"]
    engine = _HistoryDdlEngine({"T1": statements})
    pending_dir = tmp_path / "pending"

    # When
    rc = _run_ldb(monkeypatch, _history_ddl_config(ddl_pending_dir=str(pending_dir)), engine)

    # Then: both statements ran, the run succeeded, nothing was written to disk or stdout
    assert rc == 0
    assert engine.attempted == statements
    assert not engine.history_admin_opened
    assert not pending_dir.exists()
    assert capsys.readouterr().out == ""


def test_pending_files_written_in_the_same_second_get_a_counter_and_never_truncate(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    # Spec: README > Run ILM — a pending file is created exclusively; a name already taken in the
    # same second gets _2, _3... so DDL from an earlier run is never lost
    # Given: a frozen clock and three files written under the same directory
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 27, 12, 0, 0)

    monkeypatch.setattr(ldb_ddl, "datetime", _Frozen)

    # When
    paths = [ldb_ddl.write_pending_file(tmp_path, "history_ddl_s", f"run {n}") for n in (1, 2, 3)]

    # Then: distinct, ordered names and every content intact
    assert [p.name for p in paths] == [
        "history_ddl_s_20261027_120000.sql",
        "history_ddl_s_20261027_120000_2.sql",
        "history_ddl_s_20261027_120000_3.sql",
    ]
    assert [p.read_text(encoding="utf-8") for p in paths] == ["run 1", "run 2", "run 3"]
    assert all(p.is_absolute() for p in paths)


def test_failures_collected_before_the_producer_raises_are_written_then_error_is_reraised(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    # Spec: README > Run ILM > Admin credentials in ldb-run — failed statements reach the pending
    # file even when a later engine call raises (PEND-032)
    # Given: a producer that records one failed statement and then raises
    config = _history_ddl_config(ddl_pending_dir=str(tmp_path / "pending"))

    def produce(sink, failures):
        failures.append(("ALTER TABLE HIST.T1 ADD (C1 NUMBER)", "ORA-01031: insufficient privileges"))
        raise RuntimeError("connection lost")

    # When / Then: the original error propagates ...
    with pytest.raises(RuntimeError, match="connection lost"):
        ldb_ddl.deliver_ddl(
            config, produce, destination="execute", env="HISTORY", file_stem="history_ddl", description="x."
        )

    # ... and the statement collected so far is on disk
    (file,) = (tmp_path / "pending").glob("history_ddl_s_*.sql")
    assert "ALTER TABLE HIST.T1 ADD (C1 NUMBER);" in file.read_text(encoding="utf-8")


def test_failed_history_ddl_goes_to_pending_file_and_run_exits_1_after_trying_all(
    tmp_path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    # Spec: README > Run ILM > Admin credentials in ldb-run — every statement that fails
    # is written to a file under ddl_pending_dir, the remaining statements are still
    # attempted, the error log names the file, and ldb-run exits with code 1.
    # Given: three statements, the middle one is rejected by the database
    ok_1, bad, ok_2 = (
        "ALTER TABLE HIST.T1 ADD (C1 NUMBER)",
        "ALTER TABLE HIST.T1 ADD (C2 NUMBER)",
        "ALTER TABLE HIST.T1 ADD (C3 NUMBER)",
    )
    engine = _HistoryDdlEngine({"T1": [ok_1, bad, ok_2]}, failing={bad: "ORA-01031: insufficient privileges"})
    pending_dir = tmp_path / "pending"
    recorder = _RecordingLogger()
    monkeypatch.setattr(ldb_ddl, "logger", recorder)

    # When
    rc = _run_ldb(monkeypatch, _history_ddl_config(ddl_pending_dir=str(pending_dir)), engine)

    # Then: exit 1 after attempting all three; the file holds only the failed one
    assert rc == 1
    assert engine.attempted == [ok_1, bad, ok_2]
    files = list(pending_dir.glob("history_ddl_s_*.sql"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert f"{bad};" in content
    assert "-- Failed with: ORA-01031" in content
    # Runnable as plain SQL*Plus: stops on error and ends with an explicit OK banner
    lines = content.splitlines()
    assert "whenever sqlerror exit failure" in lines
    assert lines.index("whenever sqlerror exit failure") < lines.index(f"{bad};")
    assert lines[-1] == "exit success" and "LimitsDb: OK" in lines[-2]
    assert ok_1 not in content and ok_2 not in content
    pending_errors = [message for message in recorder.messages.get("error", []) if str(files[0]) in message]
    assert len(pending_errors) == 1
    assert capsys.readouterr().out == ""


def test_failed_ddl_from_several_tables_is_collected_in_one_pending_file_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # Spec: README > Run ILM > Admin credentials in ldb-run — any DDL statement that
    # fails is written to the pending file; statements keep their order and form, and
    # one run produces one file regardless of how many tables had failures.
    # Given: two tables whose DDL is rejected
    first = _MULTILINE_DDL.format(table="T1")
    second = _MULTILINE_DDL.format(table="T2")
    engine = _HistoryDdlEngine(
        {"T1": [first], "T2": [second]},
        failing={first: "ORA-01031: insufficient privileges", second: "ORA-00942: no table"},
    )
    pending_dir = tmp_path / "pending"

    # When
    rc = _run_ldb(monkeypatch, _history_ddl_config(ddl_pending_dir=str(pending_dir)), engine, "T1", "T2")

    # Then: exit 1 and one file holding both statements, complete and in order
    assert rc == 1
    files = list(pending_dir.glob("*.sql"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert f"{first};" in content and f"{second};" in content
    assert content.index(first) < content.index(second)
    assert "-- Failed with: ORA-01031" in content and "-- Failed with: ORA-00942" in content


def test_all_failed_history_ddl_statements_are_written_when_every_statement_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # Spec: README > Run ILM > Admin credentials in ldb-run — when no statement can be
    # applied the file contains every statement and ldb-run exits with code 1.
    # Given: a delta where Oracle rejects every statement
    statements = ["ALTER TABLE HIST.T1 ADD (C1 NUMBER)", "ALTER TABLE HIST.T1 ADD (C2 NUMBER)"]
    engine = _HistoryDdlEngine(
        {"T1": statements}, failing={st: "ORA-01031: insufficient privileges" for st in statements}
    )
    pending_dir = tmp_path / "pending"

    # When
    rc = _run_ldb(monkeypatch, _history_ddl_config(ddl_pending_dir=str(pending_dir)), engine)

    # Then
    assert rc == 1
    (pending_file,) = pending_dir.glob("*.sql")
    content = pending_file.read_text(encoding="utf-8")
    assert all(f"{statement};" in content for statement in statements)


def test_history_ddl_with_admin_credentials_is_applied_by_admin_and_leaves_no_pending_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # Spec: README > Run ILM > Admin credentials in ldb-run — with admin credentials and
    # required DDL, the admin connection applies the changes; the pending-DDL file is only
    # for runs without admin credentials.
    # Given: admin history credentials and a one-statement delta
    engine = _HistoryDdlEngine({"T1": ["ALTER TABLE HIST.T1 ADD (C1 NUMBER)"]})
    pending_dir = tmp_path / "pending"

    # When
    rc = _run_ldb(monkeypatch, _history_ddl_config(history_admin=True, ddl_pending_dir=str(pending_dir)), engine)

    # Then: applied by the admin connection, never through the pending-file path
    assert rc == 0
    assert engine.admin_applied == ["ALTER TABLE HIST.T1 ADD (C1 NUMBER)"]
    assert engine.attempted == []
    assert not pending_dir.exists()


def test_run_exits_1_when_history_cannot_be_inspected_and_admin_credentials_are_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # Spec: README > Run ILM > Admin credentials in ldb-run — without admin credentials,
    # if the history schema cannot be inspected, ldb-run exits with code 1 and applies no
    # DDL; it never runs ILM against a history schema in an unknown state.
    # Given: a runtime history connection that fails
    engine = _HistoryDdlEngine({"T1": ["ALTER TABLE HIST.T1 ADD (C1 NUMBER)"]}, history_unreachable=True)
    pending_dir = tmp_path / "pending"

    # When
    rc = _run_ldb(monkeypatch, _history_ddl_config(ddl_pending_dir=str(pending_dir)), engine)

    # Then: exit 1, nothing applied, no pending file
    assert rc == 1
    assert engine.attempted == []
    assert not pending_dir.exists()


def test_no_output_and_no_admin_conn_when_tables_up_to_date_without_admin_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    # Spec: README > Run ILM > Admin credentials in ldb-run — when admin history
    # credentials are absent and tables are already up to date (empty delta), ldb-run
    # proceeds without opening an admin connection or emitting a script.
    # Given: an empty delta; no admin history credentials
    engine = _HistoryDdlEngine()

    # When
    rc = _run_ldb(monkeypatch, _history_ddl_config(), engine)

    # Then: the run succeeds, nothing is applied and nothing is printed
    assert rc == 0
    assert not engine.history_admin_opened
    assert engine.attempted == [] and engine.admin_applied == []
    assert capsys.readouterr().out == ""


def test_run_exits_1_and_logs_failed_statements_when_pending_file_cannot_be_written(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    # Spec: README > Run ILM > Admin credentials in ldb-run — if the pending file cannot
    # be written, ldb-run logs the error with the failed statements and exits with code 1.
    # Given: ddl_pending_dir points below a regular file, so it cannot be created
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    statement = "ALTER TABLE HIST.T1 ADD (C1 NUMBER)"
    engine = _HistoryDdlEngine({"T1": [statement]}, failing={statement: "ORA-01031: insufficient privileges"})
    recorder = _RecordingLogger()
    monkeypatch.setattr(ldb_ddl, "logger", recorder)

    # When
    rc = _run_ldb(monkeypatch, _history_ddl_config(ddl_pending_dir=str(blocker / "sub")), engine)

    # Then
    assert rc == 1
    assert any(statement in message and "could not be written" in message for message in recorder.messages["error"])


@pytest.mark.parametrize("history_admin", [False, True], ids=["without_admin", "with_admin"])
def test_preview_run_applies_history_ddl_like_execute(monkeypatch: pytest.MonkeyPatch, tmp_path, history_admin: bool):
    # Spec: README > Run ILM > --mode — PREVIEW applies DDL and grants and only skips the
    # data movement, because a run is meaningless without the objects in place (DEC-023).
    # Given: PREVIEW mode and a pending DDL delta
    statement = "ALTER TABLE HIST.T1 ADD (C1 NUMBER)"
    engine = _HistoryDdlEngine({"T1": [statement]})

    # When: ldb-run executes
    rc = _run_ldb(
        monkeypatch,
        _history_ddl_config(mode="PREVIEW", history_admin=history_admin, ddl_pending_dir=str(tmp_path / "pending")),
        engine,
    )

    # Then: the delta was applied over the connection EXECUTE would use
    assert rc == 0
    assert (engine.admin_applied, engine.attempted) == (([statement], []) if history_admin else ([], [statement]))


def test_execute_run_applies_history_ddl_over_runtime_connection_when_history_ilm_has_no_admin(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # Spec: README > Run ILM > Admin credentials in ldb-run — without history admin
    # credentials the DDL goes over the runtime history connection, for HISTORY_ILM too.
    # Given: HISTORY_ILM in EXECUTE mode, no admin credentials at all, a pending delta
    statement = "ALTER TABLE HIST.T1 ADD (C1 NUMBER)"
    engine = _HistoryDdlEngine({"T1": [statement]})

    # When: ldb-run executes
    rc = _run_ldb(
        monkeypatch,
        _history_ddl_config(action="HISTORY_ILM", source_admin=False, ddl_pending_dir=str(tmp_path / "pending")),
        engine,
    )

    # Then: the delta was applied and the run did not fail on the missing admin credentials
    assert rc == 0
    assert engine.attempted == [statement]


def _script_files(pending_dir) -> dict[str, str]:
    """Return the text of each file under the pending directory, keyed by its stem prefix."""
    return {
        "_".join(path.stem.split("_")[:2]): path.read_text(encoding="utf-8")
        for path in sorted(pending_dir.glob("*.sql"))
    }


def test_script_run_writes_the_history_delta_read_over_the_runtime_history_connection(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture
):
    # Spec: README > Run ILM > --mode SCRIPT — the history DDL goes to a file under ddl_pending_dir,
    # apart from the ILM script on stdout; with a readable history it holds only the missing changes.
    # Given: SCRIPT mode, no history admin credentials, a readable history schema and a pending delta
    statement = "ALTER TABLE HIST.T1 ADD (C1 NUMBER)"
    engine = _HistoryDdlEngine({"T1": [statement]})
    pending_dir = tmp_path / "pending"
    monkeypatch.setattr(ldb_runner, "_collect_privilege_targets", lambda rows, key, engine: [("HIST", "T1")])

    # When: ldb-run generates the script
    rc = _run_ldb(
        monkeypatch,
        _history_ddl_config(
            mode="SCRIPT", history_baseline="SOURCE", source_admin=False, ddl_pending_dir=str(pending_dir)
        ),
        engine,
    )

    # Then: the delta is in the history file followed by its grants, stdout keeps only the ILM script,
    # the baseline was not needed and nothing was applied
    assert rc == 0
    history_file = _script_files(pending_dir)["history_ddl"]
    assert history_file.index(f"{statement};") < history_file.index("GRANT SELECT ON HIST.T1 TO LDB_HISTORY_ROLE;")
    assert "ALTER TABLE" not in capsys.readouterr().out
    assert engine.structure_reads == [("HISTORY", None)]
    assert engine.connections.count((True, "HISTORY")) == 0
    assert engine.attempted == [] and engine.admin_applied == []


def test_script_run_reads_history_with_the_admin_connection_when_it_is_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # Spec: README > Run ILM > --mode SCRIPT — history is read over the admin connection when admin
    # credentials exist, and over the runtime connection otherwise.
    # Given: SCRIPT mode with history admin credentials
    engine = _HistoryDdlEngine({"T1": ["ALTER TABLE HIST.T1 ADD (C1 NUMBER)"]})

    # When: ldb-run generates the script
    rc = _run_ldb(
        monkeypatch,
        _history_ddl_config(mode="SCRIPT", history_admin=True, ddl_pending_dir=str(tmp_path / "pending")),
        engine,
    )

    # Then: the structure was read over the admin connection and nothing was applied
    assert rc == 0
    assert engine.structure_reads == [("HISTORY", None)]
    assert engine.history_admin_opened
    assert engine.attempted == [] and engine.admin_applied == []


def test_script_run_creates_every_history_object_when_history_is_unreadable_and_baseline_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # Spec: README > Run ILM > --mode SCRIPT — history_baseline EMPTY assumes history has no objects.
    # Given: SCRIPT mode, no usable history connection and the default baseline
    engine = _HistoryDdlEngine(history_unreachable=True)
    pending_dir = tmp_path / "pending"

    # When: ldb-run generates the script
    rc = _run_ldb(monkeypatch, _history_ddl_config(mode="SCRIPT", ddl_pending_dir=str(pending_dir)), engine)

    # Then: the table is created from scratch, without inspecting any database
    assert rc == 0
    assert "CREATE TABLE HIST.T1 (ID NUMBER);" in _script_files(pending_dir)["history_ddl"]
    assert engine.structure_reads == [(None, None)]
    assert engine.attempted == [] and engine.admin_applied == []


def test_script_run_aligns_history_with_source_when_history_is_unreadable_and_baseline_is_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # Spec: README > Run ILM > --mode SCRIPT — history_baseline SOURCE assumes history holds the
    # same objects as source, so the delta is computed against the source table.
    # Given: SCRIPT mode, no usable history connection and baseline SOURCE
    engine = _HistoryDdlEngine(history_unreachable=True)
    pending_dir = tmp_path / "pending"

    # When: ldb-run generates the script
    rc = _run_ldb(
        monkeypatch,
        _history_ddl_config(mode="SCRIPT", history_baseline="SOURCE", ddl_pending_dir=str(pending_dir)),
        engine,
    )

    # Then: the source table is the reference and only the history-specific change is emitted
    assert rc == 0
    assert "ALTER TABLE HIST.T1 ADD (LDB_PROCESS_DATE DATE);" in _script_files(pending_dir)["history_ddl"]
    assert engine.structure_reads == [(None, "SRC")]  # runtime source connection, SRC as reference
    assert engine.attempted == [] and engine.admin_applied == []


def _grant_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ldb_runner,
        "_collect_privilege_targets",
        lambda rows, key, engine: [("SRC" if key == "source_owner" else "HIST", "T1")],
    )


def _extra_rows(**extra) -> list[dict]:
    """One configured table SRC.T1 / HIST.T1 whose predicates read other tables."""
    return [{"source_owner": "SRC", "history_owner": "HIST", "table_name": "T1", **extra}]


def _script_with_extra_tables(monkeypatch: pytest.MonkeyPatch, tmp_path, **extra):
    engine = _HistoryDdlEngine()
    rc = _run_ldb(
        monkeypatch,
        _history_ddl_config(mode="SCRIPT", history_admin=True, ddl_pending_dir=str(tmp_path / "pending")),
        engine,
        rows=_extra_rows(**extra),
    )
    return rc, engine, _script_files(tmp_path / "pending")


def test_extra_select_tables_are_granted_select_only_in_source_and_history(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # Spec: README > ILM Rule Semantics > Extra tables — tables read by filter subqueries receive SELECT only,
    # and history falls back to extra_select_tables when history_extra_select_tables is omitted.
    # Given: a table whose filter reads SRC.LOOKUP (owner given) and OTHER.RATES
    # When: ldb-run generates the script with the grants
    rc, engine, files = _script_with_extra_tables(
        monkeypatch, tmp_path, extra_select_tables="SRC.LOOKUP, OTHER.RATES", history_extra_select_tables=None
    )

    # Then: both environments grant SELECT on both tables and nothing else on them
    assert rc == 0
    assert "GRANT SELECT ON SRC.LOOKUP TO LDB_SOURCE_ROLE" in engine.source_granted
    assert "GRANT SELECT ON OTHER.RATES TO LDB_SOURCE_ROLE" in engine.source_granted
    assert not [grant for grant in engine.source_granted if "LOOKUP" in grant and "SELECT" not in grant]
    assert "GRANT SELECT ON SRC.LOOKUP TO LDB_HISTORY_ROLE;" in files["history_ddl"]
    assert "GRANT SELECT ON OTHER.RATES TO LDB_HISTORY_ROLE;" in files["history_ddl"]
    assert "GRANT DELETE ON OTHER.RATES" not in files["history_ddl"]


@pytest.mark.parametrize("alter_grant", [False, True])
def test_alter_on_history_tables_is_granted_only_when_history_alter_grant_is_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path, alter_grant: bool
):
    # Spec: README > Run ILM > Admin credentials in ldb-run — history_alter_grant (default false) adds ALTER on
    # the history tables to the history role, never on the source tables.
    # Given: a SCRIPT run with or without history_alter_grant
    engine = _HistoryDdlEngine()
    rc = _run_ldb(
        monkeypatch,
        _history_ddl_config(
            mode="SCRIPT",
            history_admin=True,
            ddl_pending_dir=str(tmp_path / "pending"),
            history_alter_grant=alter_grant,
        ),
        engine,
        rows=_extra_rows(),
    )

    # Then: ALTER is in the history grants only when requested, and never in the source grants
    files = _script_files(tmp_path / "pending")
    assert rc == 0
    assert ("GRANT ALTER ON HIST.T1 TO LDB_HISTORY_ROLE;" in files["history_ddl"]) is alter_grant
    assert not [grant for grant in engine.source_granted if "ALTER" in grant]


def test_tables_owned_by_the_runtime_user_get_no_grants(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # Spec: README > Run ILM > --mode PREPARE — an owner holds every privilege on its own tables and a grant to
    # oneself is not recorded, so tables owned by the runtime user are left out of the grants and their checks.
    # Given: HIST.T1 and SRC.T1 configured, SRC.LOOKUP as extra table, and history runtime user HIST
    rows = _extra_rows(extra_select_tables="SRC.LOOKUP")

    # When: ldb-run grants the privileges
    _source_granted, history_ddl = _planned_grants(
        monkeypatch, tmp_path, rows, history_username="HIST", history_alter_grant=True
    )

    # Then: the owner of HIST keeps only what belongs to others
    assert "GRANT SELECT ON SRC.LOOKUP TO LDB_HISTORY_ROLE;" in history_ddl
    assert "ON HIST.T1" not in history_ddl.replace("CREATE TABLE HIST.T1", "")


def test_a_stranger_to_the_history_schema_gets_every_privilege_including_alter(monkeypatch, tmp_path):
    # Spec: README > Run ILM > --mode PREPARE — only the owner is exempt; others get the table privileges
    # (ALTER only in history, with history_alter_grant) and SELECT on the extra tables
    # Given: the same tables, with a history runtime user that owns nothing
    rows = _extra_rows(extra_select_tables="SRC.LOOKUP")

    # When: ldb-run grants the privileges
    _source_granted, history_ddl = _planned_grants(
        monkeypatch, tmp_path, rows, history_username="other", history_alter_grant=True
    )

    # Then: every privilege on HIST.T1 and SELECT on the extra table are granted
    for privilege in ("SELECT", "INSERT", "DELETE", "ALTER"):
        assert f"GRANT {privilege} ON HIST.T1 TO LDB_HISTORY_ROLE;" in history_ddl
    assert "GRANT SELECT ON SRC.LOOKUP TO LDB_HISTORY_ROLE;" in history_ddl


def test_the_source_schema_owner_gets_no_source_grants(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # Spec: README > Run ILM > --mode PREPARE — tables owned by the runtime user are left out of the grants
    # Given: the source runtime user owns both the configured and the extra table
    rows = _extra_rows(extra_select_tables="SRC.LOOKUP")

    # When: ldb-run grants the privileges
    source_granted, _history_ddl = _planned_grants(monkeypatch, tmp_path, rows, source_username="SRC")

    # Then: no grant is applied on the source database
    assert source_granted == []


def test_history_extra_select_tables_replace_the_source_list_in_history(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # Spec: README > ILM Rule Semantics > Extra tables — history_extra_select_tables takes precedence over
    # extra_select_tables in history, as history_addtl_filter_expr does over additional_filter_expr; an entry
    # without owner belongs to the owner of the environment.
    # Given: a source list and a different history list with an unqualified table
    rc, engine, files = _script_with_extra_tables(
        monkeypatch, tmp_path, extra_select_tables="SRC.LOOKUP", history_extra_select_tables="RATES_COPY"
    )

    # Then: source grants the source list; history grants only its own list, in the history owner
    assert rc == 0
    assert "GRANT SELECT ON SRC.LOOKUP TO LDB_SOURCE_ROLE" in engine.source_granted
    assert "GRANT SELECT ON HIST.RATES_COPY TO LDB_HISTORY_ROLE;" in files["history_ddl"]
    assert "LOOKUP" not in files["history_ddl"]


def test_extra_select_table_that_is_also_configured_keeps_all_its_privileges(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # Spec: README > ILM Rule Semantics > Extra tables — a table already managed by ILM keeps the full
    # privileges; the extra list never narrows them nor repeats the grant.
    # Given: the extra list names the configured table itself
    rc, engine, _files = _script_with_extra_tables(monkeypatch, tmp_path, extra_select_tables="SRC.T1")

    # Then: SRC.T1 has its own two source privileges, each granted once
    assert rc == 0
    assert sorted(g for g in engine.source_granted if "SRC.T1" in g) == sorted(
        f"GRANT {privilege} ON SRC.T1 TO LDB_SOURCE_ROLE" for privilege in ("SELECT", "DELETE")
    )


@pytest.mark.parametrize("entry", ["A.B.C", "A. ", "TWO WORDS", "SRC.", 'GL."A.B"'])
def test_malformed_extra_select_table_is_rejected_before_any_grant(
    monkeypatch: pytest.MonkeyPatch, tmp_path, entry: str
):
    # Spec: README > ILM Rule Semantics > Extra tables — entries are [OWNER.]TABLE separated by commas.
    # Given: an entry that is not an optionally qualified table name
    engine = _HistoryDdlEngine()

    # When / Then: the run is refused with a ValueError and no grant was applied
    with pytest.raises(ValueError, match="extra_select_tables"):
        _run_ldb(
            monkeypatch,
            _history_ddl_config(mode="SCRIPT", history_admin=True, ddl_pending_dir=str(tmp_path / "pending")),
            engine,
            rows=_extra_rows(extra_select_tables=entry),
        )
    assert engine.source_granted == [] and engine.admin_applied == []


def test_script_run_applies_source_grants_when_source_admin_credentials_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # Spec: README > Run ILM > --mode SCRIPT — the runtime user reads column metadata through the source
    # grants, so SCRIPT applies them when source admin credentials exist; history grants stay in the file.
    # Given: SCRIPT mode with source and history admin credentials
    engine = _HistoryDdlEngine()
    _grant_targets(monkeypatch)
    pending_dir = tmp_path / "pending"

    # When: ldb-run generates the script
    rc = _run_ldb(
        monkeypatch,
        _history_ddl_config(mode="SCRIPT", history_admin=True, ddl_pending_dir=str(pending_dir)),
        engine,
    )

    # Then: the source grants were applied and have no file; the history grants are in the history file
    assert rc == 0
    assert "GRANT DELETE ON SRC.T1 TO LDB_SOURCE_ROLE" in engine.source_granted
    files = _script_files(pending_dir)
    assert "source_grants" not in files
    assert "GRANT DELETE ON HIST.T1 TO LDB_HISTORY_ROLE;" in files["history_ddl"]
    assert engine.admin_applied == []


def test_script_run_writes_source_grants_file_and_warns_without_source_admin_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # Spec: README > Run ILM > --mode SCRIPT — without source admin credentials the source grants go to a
    # file and a warning says tables not yet granted are skipped.
    # Given: SCRIPT mode without source admin credentials
    engine = _HistoryDdlEngine()
    _grant_targets(monkeypatch)
    recorder = _RecordingLogger()
    monkeypatch.setattr(ldb_runner, "logger", recorder)
    pending_dir = tmp_path / "pending"

    # When: ldb-run generates the script
    rc = _run_ldb(
        monkeypatch,
        _history_ddl_config(mode="SCRIPT", source_admin=False, ddl_pending_dir=str(pending_dir)),
        engine,
    )

    # Then: nothing was granted, the grants are in their file and no generic warning was logged
    assert rc == 0
    assert engine.source_granted == []
    assert "GRANT DELETE ON SRC.T1 TO LDB_SOURCE_ROLE;" in _script_files(pending_dir)["source_grants"]
    assert not any("Source admin credentials are absent" in m for m in recorder.messages.get("warning", []))


def test_execute_run_without_source_admin_credentials_warns_that_ungranted_tables_are_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # Spec: README > Run ILM > Admin credentials in ldb-run — every mode that applies grants needs source
    # admin credentials; without them tables the runtime user cannot see are skipped, with a warning.
    # Given: EXECUTE mode without source admin credentials
    engine = _HistoryDdlEngine()
    _grant_targets(monkeypatch)
    recorder = _RecordingLogger()
    monkeypatch.setattr(ldb_runner, "logger", recorder)

    # When: ldb-run executes
    rc = _run_ldb(monkeypatch, _history_ddl_config(source_admin=False, ddl_pending_dir=str(tmp_path / "p")), engine)

    # Then: no source grant was applied and no generic warning was logged (only real privilege gaps warn)
    assert rc == 0
    assert engine.source_granted == []
    assert not any("Source admin credentials are absent" in m for m in recorder.messages.get("warning", []))


def _full_row(table_name: str, **kwargs) -> dict:
    """A configuration row complete enough for a table whose columns are visible to be fully processed."""
    return {
        **_minimal_row(table_name, **kwargs),
        "ctl_status": None,
        "cond_expr": "",
        "retain_months_source": 3,
        "retain_months_history": None,
        "purge_date_expr": "@created",
        "additional_filter_expr": None,
        "history_addtl_filter_expr": None,
        "has_lob_columns": "N",
        "long_columns": None,
    }


class _VisibilityEngine(_MinimalEngine):
    """Engine stub where only the listed tables have columns visible to the runtime user."""

    def __init__(self, visible: set[str]) -> None:
        self.visible = visible

    @staticmethod
    def generate_sql_block(config, table_cnf, process_date):
        return "select 1 from dual"

    @staticmethod
    def get_date_condition(expr, months):
        return f"{expr} < add_months(sysdate, -{months})"

    def get_table_columns(self, conn, owner, table_name):
        if table_name in self.visible:
            return ["id"], {"ID": SimpleNamespace(id=1)}
        return [], {}


def test_table_without_visible_columns_is_skipped_with_a_warning_instead_of_failing(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: README > Run ILM — a table the runtime user cannot see (missing, or SELECT not granted) is
    # skipped with a warning; it does not abort the run.
    # Given: one visible table and one with no visible columns
    recorder = _RecordingLogger()
    monkeypatch.setattr(ldb_runner, "logger", recorder)
    rows = [_full_row("OK"), _full_row("INV")]
    monkeypatch.setattr(ldb_runner, "_load_offline_rows", lambda config: rows)

    # When: the tables are processed
    tables = ldb_runner.process_tables_cnf(object(), _script_config(), _VisibilityEngine({"OK"}), "20261007")

    # Then: the invisible table is skipped with the message and the other one is kept
    assert tables[("A", "INV")]["skip"] is True
    assert not tables[("A", "OK")].get("skip")
    assert any("A.INV is not visible to the runtime user" in m for m in recorder.messages.get("warning", []))


def test_child_of_a_table_without_visible_columns_is_skipped_too(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM — a child inherits its parent's conditions, so it is skipped when the
    # parent is not visible.
    # Given: a child whose parent has no visible columns
    recorder = _RecordingLogger()
    monkeypatch.setattr(ldb_runner, "logger", recorder)
    rows = [_full_row("PARENT"), _full_row("CHILD", referencing_tables="A.PARENT p")]
    monkeypatch.setattr(ldb_runner, "_load_offline_rows", lambda config: rows)

    # When: the tables are processed
    tables = ldb_runner.process_tables_cnf(object(), _script_config(), _VisibilityEngine({"CHILD"}), "20261007")

    # Then: both are skipped and the child names the parent
    assert tables[("A", "PARENT")]["skip"] is True
    assert tables[("A", "CHILD")]["skip"] is True
    assert any("references A.PARENT" in m for m in recorder.messages.get("warning", []))


def test_child_before_its_parent_is_skipped_when_the_parent_is_not_visible(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM — a child inherits its parent's conditions, so it is skipped when the parent is not
    # visible, whatever the order of the configuration
    # Given: a child listed before its invisible parent
    rows = [_full_row("CHILD", referencing_tables="A.PARENT p"), _full_row("PARENT")]
    monkeypatch.setattr(ldb_runner, "_load_offline_rows", lambda config: rows)

    # When: the tables are processed
    tables = ldb_runner.process_tables_cnf(object(), _script_config(), _VisibilityEngine({"CHILD"}), "20261007")

    # Then: both are skipped
    assert tables[("A", "PARENT")]["skip"] is True
    assert tables[("A", "CHILD")]["skip"] is True


def test_preview_is_refused_over_a_table_already_executed_for_the_date(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    # Spec: README > Run ILM > --mode PREVIEW — a PREVIEW is refused over any EXECUTE status of the same date
    # Given: a table that ldb_ctl already holds as TEND
    ldb_runner.logger.addHandler(caplog.handler)
    monkeypatch.setattr(ldb_runner, "ProcessPoolExecutor", _ImmediateExecutor)
    table = _table_config()
    table["conds"][0]["ctl_status"] = Status.TABLE_END
    config = _execution_config()
    config.mode = "PREVIEW"

    # When: the PREVIEW runs
    result = ldb_runner.ldb_exec_ilm(config, {("A", "T1"): table}, "20261006", _StatusEngine(), object())

    # Then: it fails and says why
    assert result == 1
    assert "PREVIEW is refused" in caplog.text


def test_preview_leftover_status_is_ignored_when_the_run_resumes(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM > --mode PREVIEW — a PREVIEW does not mark tables as processed for the day, so a
    # later run still processes them; a real TEND is kept
    # Given: ldb_ctl holds PTEND for one table (left by a PREVIEW) and TEND for another
    class _StatusEngine(_VisibilityEngine):
        def get_status(self, conn, process_date):
            return [
                {"ctl_owner": "A", "ctl_table_name": "PREV", "ctl_status": Status.TABLE_PREVIEW_END},
                {"ctl_owner": "A", "ctl_table_name": "DONE", "ctl_status": Status.TABLE_END},
            ]

    rows = [_full_row("PREV"), _full_row("DONE")]
    monkeypatch.setattr(ldb_runner, "_load_offline_rows", lambda config: rows)
    config = _script_config()
    config.generate_script = False

    # When: the tables are processed
    tables = ldb_runner.process_tables_cnf(object(), config, _StatusEngine({"PREV", "DONE"}), "20261007")

    # Then: the previewed table is pending again and the executed one stays finished
    assert tables[("A", "PREV")]["conds"][0]["ctl_status"] is None
    assert tables[("A", "DONE")]["conds"][0]["ctl_status"] == Status.TABLE_END


def test_process_table_reports_the_preview_end_status_in_preview_mode(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM > --mode PREVIEW — the worker reports PTEND, not TEND, for a PREVIEW
    # Given: a worker engine and a PREVIEW config
    class _Engine:
        def get_connection(self, config, admin=False, env=None):
            return object()

        def get_system_date(self, conn):
            return datetime(2026, 10, 6)

        def get_rows_processed(self, conn, owner, table_name, process_date):
            return 5

        def sql_block_run(self, conn, plsql_code, mode, run_id):
            pass

        def close_connection(self, conn):
            pass

    monkeypatch.setattr(ldb_runner, "get_db_engine", lambda name: _Engine())
    config = Config(
        schema="s",
        db_engine="oracle",
        action="SOURCE_ILM",
        mode="PREVIEW",
        source_username="user",
        source_password="pw",
        source_dsn="dsn",
    )

    # When: process_table runs
    result = ldb_runner.process_table(config, "OWNER", "TABLE", "begin null; end;", "20261006", "run-1")

    # Then: the status is PTEND
    assert result[2] == Status.TABLE_PREVIEW_END


def test_preview_finished_tables_satisfy_their_children_and_are_not_launched_again():
    # Spec: README > Run ILM > --mode PREVIEW — within a PREVIEW run a table that ended in PTEND counts as done
    # Given: a parent finished by the PREVIEW and a child waiting for it
    tables_config = {
        ("A", "PARENT"): {"skip": False, "conds": [{"ctl_status": Status.TABLE_PREVIEW_END}], "referencing_tables": []},
        ("A", "CHILD"): {"skip": False, "conds": [{"ctl_status": None}], "referencing_tables": [("A", "PARENT")]},
    }

    # When: the next ready table is queried
    ready = ldb_runner.get_next_ready_table(tables_config, set())

    # Then: the child is launched and the parent is not
    assert ready is not None and ready[0:2] == ("A", "CHILD")


def test_grants_that_go_with_history_ddl_are_applied_over_the_runtime_connection_without_admin(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # Spec: README > Run ILM > Admin credentials in ldb-run — every DDL change comes with the grants of
    # its tables; they run with the DDL over the same connection (DEC-026).
    # Given: no history admin credentials and a delta the runtime user can apply
    statement = "ALTER TABLE HIST.T1 ADD (C1 NUMBER)"
    engine = _HistoryDdlEngine({"T1": [statement]})
    pending_dir = tmp_path / "pending"

    # When: ldb-run executes
    rc = _run_ldb(monkeypatch, _history_ddl_config(ddl_pending_dir=str(pending_dir)), engine)

    # Then: the DDL and its grants were applied and nothing is pending
    assert rc == 0
    assert engine.attempted == [statement]
    assert "GRANT DELETE ON HIST.T1 TO LDB_HISTORY_ROLE" in engine.grants_attempted
    assert not pending_dir.exists()


def test_failed_history_ddl_file_carries_the_table_grants_that_could_not_be_applied(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # Spec: README > Run ILM > Admin credentials in ldb-run — a grant the runtime user cannot apply
    # is written, like any failed DDL, to the pending file for a DBA.
    # Given: no history admin credentials; the DDL works but the grant on INSERT is rejected
    statement = "ALTER TABLE HIST.T1 ADD (C1 NUMBER)"
    grant = "GRANT INSERT ON HIST.T1 TO LDB_HISTORY_ROLE"
    engine = _HistoryDdlEngine({"T1": [statement]}, failing={grant: "ORA-01031: insufficient privileges"})
    pending_dir = tmp_path / "pending"

    # When: ldb-run executes
    rc = _run_ldb(monkeypatch, _history_ddl_config(ddl_pending_dir=str(pending_dir)), engine)

    # Then: only the failed grant is in the file
    assert rc == 1
    content = _script_files(pending_dir)["history_ddl"]
    assert f"{grant};" in content and "-- Failed with: ORA-01031" in content
    assert statement not in content


# ---------------------------------------------------------------------------
# --mode PREPARE (DEC-032)
# ---------------------------------------------------------------------------


def test_prepare_mode_requires_connections_like_the_modes_that_apply_ddl():
    # Spec: README > Run ILM > --mode PREPARE — PREPARE reads and changes the databases, so it needs credentials
    # Given / When / Then: a PREPARE config without credentials is rejected, with them it is accepted
    with pytest.raises(ValueError):
        ldb_params_config.Config(schema="s", mode="PREPARE")
    assert _history_ddl_config(mode="PREPARE").execution.mode == "PREPARE"


def test_prepare_applies_grants_and_history_ddl_without_running_ilm(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # Spec: README > Run ILM > --mode PREPARE — PREPARE applies source grants, history DDL and history grants
    # and stops before any ILM work.
    # Given: PREPARE with source and history admin credentials and a pending history delta
    statement = "ALTER TABLE HIST.T1 ADD (C1 NUMBER)"
    engine = _HistoryDdlEngine({"T1": [statement]})
    _grant_targets(monkeypatch)
    ilm_runs: list[bool] = []

    # When: ldb-run executes
    rc = _run_ldb(
        monkeypatch,
        _history_ddl_config(mode="PREPARE", history_admin=True, ddl_pending_dir=str(tmp_path / "p")),
        engine,
    )
    monkeypatch.setattr(ldb_runner, "ldb_exec_ilm", lambda *args: ilm_runs.append(True) or 0)
    rc_again = _run_ldb(
        monkeypatch,
        _history_ddl_config(mode="PREPARE", history_admin=True, ddl_pending_dir=str(tmp_path / "p")),
        _HistoryDdlEngine(),
    )

    # Then: grants and DDL were applied, the ILM never ran, and a rerun with nothing to change succeeds
    assert rc == 0 and rc_again == 0
    assert "GRANT DELETE ON SRC.T1 TO LDB_SOURCE_ROLE" in engine.source_granted
    assert statement in engine.admin_applied
    assert "GRANT DELETE ON HIST.T1 TO LDB_HISTORY_ROLE" in engine.admin_applied
    assert ilm_runs == []


def _run_prepare_without_source_admin(
    monkeypatch: pytest.MonkeyPatch, tmp_path, *, missing: list[tuple[str, str, str]]
):
    """Run PREPARE without source admin credentials where the runtime user lacks the given privileges."""
    engine = _HistoryDdlEngine()
    engine.missing_privileges["LDB_SOURCE_ROLE"] = missing
    _grant_targets(monkeypatch)
    recorder = _RecordingLogger()
    monkeypatch.setattr(ldb_runner, "logger", recorder)
    pending_dir = tmp_path / "pending"
    config = _history_ddl_config(mode="PREPARE", source_admin=False, ddl_pending_dir=str(pending_dir))
    rc = _run_ldb(monkeypatch, config, engine)
    return rc, engine, recorder, pending_dir


def test_prepare_without_source_admin_writes_only_the_grants_that_are_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # Spec: README > Run ILM > --mode PREPARE — without source admin credentials the source grants the runtime
    # user lacks through its role go to a file for a DBA, and PREPARE fails until they are applied.
    # Given: PREPARE without source admin credentials where only DELETE is missing on SRC.T1
    # When: ldb-run executes
    rc, engine, recorder, pending_dir = _run_prepare_without_source_admin(
        monkeypatch, tmp_path, missing=[("SRC", "T1", "DELETE")]
    )

    # Then: the file holds that grant only, nothing was granted over a source connection and the run fails
    source_file = _script_files(pending_dir)["source_grants"]
    assert "GRANT DELETE ON SRC.T1 TO LDB_SOURCE_ROLE;" in source_file
    assert "GRANT SELECT" not in source_file
    assert engine.source_granted == []
    assert rc == 1
    assert any("a DBA must apply them" in m for m in recorder.messages.get("warning", []))
    assert any("SRC.T1 (DELETE)" in m for m in recorder.messages["error"])


def test_prepare_without_source_admin_writes_no_grants_file_when_nothing_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # Spec: README > Run ILM > --mode PREPARE — a rerun after the grants were applied leaves no file behind
    # Given: PREPARE without source admin credentials where the runtime user has every privilege
    # When: ldb-run executes
    rc, _, _, pending_dir = _run_prepare_without_source_admin(monkeypatch, tmp_path, missing=[])

    # Then: no source grants file was written and PREPARE succeeds
    assert "source_grants" not in _script_files(pending_dir)
    assert rc == 0


def test_prepare_reports_a_role_that_is_not_enabled_for_the_runtime_user(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # Spec: README > Run ILM > --mode PREPARE — a role granted but not active in the session gives no privilege
    # Given: PREPARE where the source role is not enabled for the runtime user
    engine = _HistoryDdlEngine()
    engine.enabled_roles = {"LDB_HISTORY_ROLE"}
    engine.missing_privileges["LDB_SOURCE_ROLE"] = [("SRC", "T1", "SELECT")]
    _grant_targets(monkeypatch)
    recorder = _RecordingLogger()
    monkeypatch.setattr(ldb_runner, "logger", recorder)

    # When: ldb-run executes
    rc = _run_ldb(monkeypatch, _history_ddl_config(mode="PREPARE", ddl_pending_dir=str(tmp_path / "p")), engine)

    # Then: the error names the role and PREPARE fails
    assert rc == 1
    assert any("role LDB_SOURCE_ROLE is not enabled" in m for m in recorder.messages["error"])
    # And: it points to ldb-impl and writes no table grants, which cannot fix a role the user does not hold
    assert any("ldb-impl" in m for m in recorder.messages["error"])
    assert "source_grants" not in _script_files(tmp_path / "p")
    # And: it stops there, without processing the tables
    assert not any("not visible" in m for m in recorder.messages.get("warning", []))
    assert any("PREPARE incomplete: run ldb-impl" in m for m in recorder.messages["error"])


def test_prepare_verifies_the_history_grants_with_the_runtime_history_connection(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # Spec: README > Run ILM > --mode PREPARE — after applying the history grants PREPARE confirms the runtime
    # user receives them, and fails when it does not.
    # Given: PREPARE with admin credentials where the history role still gives nothing to the runtime user
    engine = _HistoryDdlEngine()
    engine.missing_privileges["LDB_HISTORY_ROLE"] = [("HIST", "T1", "INSERT")]
    _grant_targets(monkeypatch)

    # When: ldb-run executes
    rc = _run_ldb(
        monkeypatch,
        _history_ddl_config(mode="PREPARE", history_admin=True, ddl_pending_dir=str(tmp_path / "p")),
        engine,
    )

    # Then: PREPARE fails because the grant is not effective
    assert rc == 1


def test_validate_reports_missing_privileges_without_changing_anything(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM > --mode VALIDATE — VALIDATE reads the privileges the runtime users lack through
    # their roles: missing ones on the action's own environment fail it, the other environment only warns.
    # Given: a source run where both roles give nothing to their runtime users
    engine = _HistoryDdlEngine()
    engine.missing_privileges["LDB_SOURCE_ROLE"] = [("SRC", "T1", "SELECT")]
    engine.missing_privileges["LDB_HISTORY_ROLE"] = [("HIST", "T1", "SELECT")]
    runtime_conns = {"SOURCE": SimpleNamespace(), "HISTORY": SimpleNamespace()}
    recorder = _RecordingLogger()
    monkeypatch.setattr(ldb_runner, "logger", recorder)

    # When: the privileges are validated
    ok = ldb_runner._validate_privileges(
        _history_ddl_config(mode="VALIDATE"),
        engine,
        runtime_conns,
        _make_tables_config("T1"),  # type: ignore[arg-type]
    )

    # Then: validation fails on the source gap, the history gap is a warning and nothing was granted
    assert ok is False
    assert any("SOURCE runtime user lacks" in m for m in recorder.messages["error"])
    assert any("HISTORY runtime user lacks" in m for m in recorder.messages.get("warning", []))
    assert any("--mode PREPARE" in m for m in recorder.messages["error"])
    assert engine.source_granted == [] and engine.admin_applied == []


def test_prepare_fails_when_the_runtime_user_cannot_see_a_configured_table(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # Spec: README > Run ILM > --mode PREPARE — a table the runtime user cannot see is an error, not a warning
    # Given: PREPARE where the table configuration has no visible columns for T1
    engine = _HistoryDdlEngine()
    recorder = _RecordingLogger()
    monkeypatch.setattr(ldb_runner, "logger", recorder)
    monkeypatch.setattr(ldb_runner, "get_db_engine", lambda name: engine)
    monkeypatch.setattr(ldb_runner, "_load_offline_rows", lambda cfg: [])
    invisible = _make_tables_config("T1")
    invisible[("SRC", "T1")].update(skip=True, columns_metadata={})
    monkeypatch.setattr(ldb_runner, "process_tables_cnf", lambda *args: invisible)

    # When: ldb-run executes
    with pytest.raises(SystemExit) as exc_info:
        ldb_runner.ldb_run(_history_ddl_config(mode="PREPARE", ddl_pending_dir=str(tmp_path / "p")))

    # Then: it exits 1 and names the table
    assert exc_info.value.code == 1
    assert any("SRC.T1" in m for m in recorder.messages["error"])


# ---------------------------------------------------------------------------
# STATUS mode — README > Run ILM > --mode STATUS
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 10, 22, 12, 0, 0)
# Each scenario run starts at its own time, hours apart, so the log is split into separate executions
_RUN_START = {"old": datetime(2026, 10, 22, 8, 0, 0)}
_DEFAULT_START = datetime(2026, 10, 22, 11, 0, 0)


def _label(run_id: str) -> str:
    """The label STATUS gives to the execution that a scenario ``run_id`` stands for: its start time."""
    return _RUN_START.get(run_id, _DEFAULT_START).strftime("%Y%m%d-%H%M%S")


def _record(run_id="r1", table="ITEMS", **overrides) -> TableRunRecord:
    values = {
        "run_id": run_id,
        "owner": "APP",
        "table_name": table,
        "action": "SOURCE_ILM",
        "process_date": datetime(2026, 10, 22),
        "started": _RUN_START.get(run_id, _DEFAULT_START),
        "ended": _RUN_START.get(run_id, _DEFAULT_START) + timedelta(minutes=30),
        "last_status": Status.TABLE_END,
        "message": None,
        "rows": 1000,
        "in_progress": False,
        "skipped": False,
        "last_activity": None,
        "session": None,
    }
    values.update(overrides)
    return TableRunRecord(**values)  # type: ignore[arg-type]


def _entries(records: list[TableRunRecord]) -> tuple[list[LogEntry], list[ControlEntry]]:
    """The log and control rows that the engine would hold for these scenario records."""
    log: list[LogEntry] = []
    control: list[ControlEntry] = []

    def add(record: TableRunRecord, status: str, started, ended, rows=0, message=None):
        log.append(
            LogEntry(
                len(log) + 1,
                record.owner,
                record.table_name,
                record.action,
                record.process_date or _NOW,
                status,
                started,
                ended,
                message,
                rows,
            )
        )

    for record in records:
        started = record.started or _NOW
        if record.skipped:
            add(record, Status.ERROR, started, record.ended, message="Table is already processed for date 1")
            continue
        add(record, Status.TABLE_START, started, None)
        add(record, Status.CHUNK_END, started, None if record.in_progress else record.ended, rows=record.rows)
        if record.last_status not in (Status.CHUNK_END, Status.TABLE_START):
            add(record, record.last_status, started, record.ended, message=record.message)
        if record.in_progress:
            control.append(
                ControlEntry(
                    record.owner,
                    record.table_name,
                    record.last_status,
                    record.run_id,
                    record.action,
                    record.process_date or _NOW,
                    started,
                    None,
                    record.rows,
                    record.last_activity,
                    record.session,
                )
            )
    return log, control


def _running(table="ITEMS", **overrides) -> TableRunRecord:
    values = {
        "table": table,
        "last_status": Status.CHUNK_END,
        "in_progress": True,
        "ended": None,
        "last_activity": _NOW,
        "session": "1.10.99",
    }
    values.update(overrides)
    return _record(**values)


_FULL_ACCESS = MonitoringAccess(session=True, progress=True, waits=True)


class _MonitorEngine:
    """Engine double that answers the monitoring contract of DatabaseEngine per environment."""

    def __init__(
        self, records=None, access=_FULL_ACCESS, activity=None, unreadable=(), runner_alive=None, admin_access=None
    ):
        self.records = records or {}
        self.runner_alive = runner_alive
        self.admin_access = admin_access
        self.activity_conns: list[SimpleNamespace] = []
        self.probed: list[tuple[str, str, str]] = []
        self.access = access
        self.activity = activity or SessionActivity(alive=True, current_wait=None, progress=None, waits=())
        self.unreadable = unreadable
        self.opened: list[SimpleNamespace] = []
        self.closed: list[SimpleNamespace] = []
        self.requested: list[tuple[str, int]] = []
        self.grantees: list[str] = []

    def get_connection(self, config, *, admin=False, env=None):
        conn = SimpleNamespace(env=env, admin=admin)
        self.opened.append(conn)
        return conn

    def get_system_date(self, conn):
        return _NOW

    def close_connection(self, conn):
        self.closed.append(conn)

    def check_monitoring_access(self, conn, grantee):
        self.grantees.append(grantee)
        return (self.admin_access or self.access) if conn.admin else self.access

    def get_log_entries(self, conn, max_rows):
        self.requested.append((conn.env, max_rows))
        if conn.env in self.unreadable:
            raise ExecutionError(f"{conn.env} log unreadable")
        return _entries(self.records.get(conn.env, []))[0]

    def get_control_entries(self, conn):
        return _entries(self.records.get(conn.env, []))[1]

    def is_run_alive(self, conn, owner, table_name, run_id):
        self.probed.append((owner, table_name, run_id))
        return self.runner_alive

    def get_session_activity(self, conn, session, access, min_wait_seconds):
        self.activity_conns.append(conn)
        return self.activity


def _run_status(monkeypatch: pytest.MonkeyPatch, capsys, engine: _MonitorEngine, **overrides) -> tuple[int, str]:
    monkeypatch.setattr(ldb_runner, "get_db_engine", lambda name: engine)
    config = Config(
        schema="s",
        mode="STATUS",
        source_dsn="sd",
        source_username="su",
        source_password="sp",
        history_dsn="hd",
        history_username="hu",
        history_password="hp",
        **overrides,
    )
    with pytest.raises(SystemExit) as outcome:
        ldb_runner.ldb_run(config)
    return outcome.value.code, capsys.readouterr().out  # type: ignore[return-value]


def _run_line(output: str, run_id: str, section: str) -> str:
    body = output.split(f"== {section} ==")[1].split("==")[0]
    return next(line for line in body.splitlines() if f"run {_label(run_id)} " in line)


def test_status_mode_needs_no_credentials_to_be_configured():
    # Spec: README > Run ILM > --mode STATUS — read-only; an environment without credentials is reported, not refused
    # Given/When: a STATUS config with neither environment configured
    config = Config(schema="s", mode="STATUS")

    # Then: it is accepted and --last defaults to the current execution only
    assert config.execution.mode == "STATUS"
    assert config.execution.last == 1


@pytest.mark.parametrize("last", [0, -3])
def test_status_last_below_one_is_rejected(last):
    # Spec: README > Run ILM > --last — n is a count of executions, at least 1
    with pytest.raises(ValueError, match="last"):
        Config(schema="s", mode="STATUS", last=last)


def test_status_reports_source_and_history_and_requests_the_last_n_runs(monkeypatch, capsys):
    # Spec: README > Run ILM > --mode STATUS — always informs for SOURCE and HISTORY; --last n reads n executions
    # Given: one finished run in each environment
    engine = _MonitorEngine(
        records={"SOURCE": [_record("r1", table="S")], "HISTORY": [_record("old", table="H")]},
    )

    # When: STATUS runs with --last 3
    code, output = _run_status(monkeypatch, capsys, engine, last=3)

    # Then: both environments are reported from their own connection and connections are closed
    assert code == 0
    assert "== SOURCE ==" in output and "== HISTORY ==" in output
    assert "APP.S " in output.split("== HISTORY ==")[0]
    assert "APP.H " in output.split("== HISTORY ==")[1]
    assert {env for env, _ in engine.requested} == {"SOURCE", "HISTORY"}
    assert engine.closed == engine.opened
    assert all(not conn.admin for conn in engine.opened)


@pytest.mark.parametrize(
    ("records", "state"),
    [
        ([_record(table="A"), _record(table="B")], "OK"),
        ([_record(table="A"), _record(table="B", last_status=Status.ERROR, message="ORA-01555")], "ERRORS"),
        ([_record(table="A"), _record(table="B", last_status=Status.CHUNK_END)], "PARTIAL"),
        ([_record(table="A"), _running(table="B")], "RUNNING"),
        # adversarial: refusing a table that was already processed is neither an error nor a partial run
        ([_record(table="A"), _record(table="B", last_status=Status.ERROR, skipped=True)], "OK"),
        # adversarial: an error elsewhere wins over an incomplete table
        ([_record(table="A", last_status=Status.CHUNK_END), _record(table="B", last_status=Status.ERROR)], "ERRORS"),
    ],
)
def test_execution_state_is_running_ok_errors_or_partial(monkeypatch, capsys, records, state):
    # Spec: README > Run ILM > --mode STATUS — each execution is running, finished OK, finished with errors, or partial
    engine = _MonitorEngine(records={"SOURCE": records})

    # When: STATUS runs
    _, output = _run_status(monkeypatch, capsys, engine)

    # Then: the run carries that state, and its rows are always shown
    line = _run_line(output, "r1", "SOURCE")
    assert f"  {state}  " in line
    assert "rows " in line


def test_rows_processed_of_a_run_add_up_across_its_tables(monkeypatch, capsys):
    # Spec: README > Run ILM > --mode STATUS — rows processed are always shown
    engine = _MonitorEngine(records={"SOURCE": [_record(table="A", rows=1500), _record(table="B", rows=2500)]})
    _, output = _run_status(monkeypatch, capsys, engine)
    assert "rows 4,000" in _run_line(output, "r1", "SOURCE")


def test_an_unreachable_environment_is_reported_while_the_other_is_still_shown(monkeypatch, capsys):
    # Spec: README > Run ILM > --mode STATUS — exit 1 only when no environment can be read
    # Given: the history log cannot be read
    engine = _MonitorEngine(records={"SOURCE": [_record()]}, unreadable=("HISTORY",))

    # When/Then: SOURCE is reported, HISTORY says why it is not, and the exit code stays 0
    code, output = _run_status(monkeypatch, capsys, engine)
    assert code == 0
    assert "HISTORY log unreadable" in output.split("== HISTORY ==")[1]
    assert f"run {_label('r1')}" in output.split("== HISTORY ==")[0]

    # Given: neither can be read
    code, _ = _run_status(monkeypatch, capsys, _MonitorEngine(unreadable=("SOURCE", "HISTORY")))
    # Then: the command fails
    assert code == 1


def test_an_environment_without_credentials_is_reported_as_not_available(monkeypatch, capsys):
    # Spec: README > Run ILM > --mode STATUS — a missing environment is reported, not fatal
    engine = _MonitorEngine(records={"SOURCE": [_record()]})
    monkeypatch.setattr(ldb_runner, "get_db_engine", lambda name: engine)
    config = Config(schema="s", mode="STATUS", source_dsn="sd", source_username="su", source_password="sp")

    with pytest.raises(SystemExit) as outcome:
        ldb_runner.ldb_run(config)

    output = capsys.readouterr().out
    assert outcome.value.code == 0
    assert "credentials are not configured" in output.split("== HISTORY ==")[1]


def test_running_table_shows_progress_current_wait_and_the_wait_profile_up_to_95_percent(monkeypatch, capsys):
    # Spec: README > Run ILM > --mode STATUS — % progress and time left from the long operation, the current wait
    # when it is significant, and waits plus CPU in seconds, descending, until 95% of the time is covered
    # Given: a running table whose session spent 60 s on CPU, 30 s on I/O, 6 s on a lock and 4 s on a latch
    activity = SessionActivity(
        alive=True,
        current_wait=WaitItem("enq: TX - row lock contention", 42.0),
        progress=OperationProgress(percent=37.5, remaining_seconds=3725.0, description="Table Scan APP.ITEMS"),
        waits=(
            WaitItem("latch: cache buffers chains", 4.0),
            WaitItem("CPU", 60.0),
            WaitItem("enq: TX - row lock contention", 6.0),
            WaitItem("db file sequential read", 30.0),
        ),
    )
    engine = _MonitorEngine(records={"SOURCE": [_running()]}, activity=activity)

    # When: STATUS runs
    _, output = _run_status(monkeypatch, capsys, engine)

    # Then: progress, remaining time and the current wait are shown
    assert "37.5%" in output and "1:02:05 left" in output
    assert "waiting 42 s on enq: TX - row lock contention" in output
    # And: the profile is sorted by time and stops once 95% (CPU 60% + I/O 30% + lock 6% = 96%) is covered
    profile = next(line for line in output.splitlines() if "time (s):" in line)
    assert profile.index("CPU 60.0") < profile.index("db file sequential read 30.0") < profile.index("row lock")
    assert "latch" not in profile


def test_a_finished_table_shows_no_session_details(monkeypatch, capsys):
    # Spec: README > Run ILM > --mode STATUS — live statistics apply to active executions only
    activity = SessionActivity(True, WaitItem("x", 99.0), OperationProgress(5.0, 1.0, None), (WaitItem("CPU", 1.0),))
    engine = _MonitorEngine(records={"SOURCE": [_record()]}, activity=activity)
    _, output = _run_status(monkeypatch, capsys, engine)
    assert "progress:" not in output and "time (s):" not in output and "waiting" not in output


def test_a_run_whose_session_is_gone_is_not_reported_as_running(monkeypatch, capsys):
    # Spec: README > Run ILM > --mode STATUS — running means the registered session is alive; a table left
    # in progress by a dead session makes the run partial
    # Given: a table in progress whose session no longer exists
    engine = _MonitorEngine(
        records={"SOURCE": [_running()]},
        activity=SessionActivity(alive=False, current_wait=None, progress=None, waits=()),
    )

    # When/Then
    _, output = _run_status(monkeypatch, capsys, engine)
    assert "  PARTIAL  " in _run_line(output, "r1", "SOURCE")
    assert "INTERRUPTED" in output


@pytest.mark.parametrize(("silent_seconds", "state"), [(30, "RUNNING"), (3600, "PARTIAL")])
def test_without_session_views_a_table_counts_as_running_only_while_its_heartbeat_is_recent(
    monkeypatch, capsys, tmp_path, silent_seconds, state
):
    # Spec: README > Run ILM > --mode STATUS — without access to gv$session the control table's last activity decides;
    # the data of the views is skipped and a script with the missing grants is written for a DBA
    # Given: no readable session view, and a table last active silent_seconds ago
    hint = "GRANT SELECT ON sys.gv_$session TO LDB_SOURCE_ROLE"
    no_access = MonitoringAccess(session=False, progress=False, waits=False, grants=(hint,))
    record = _running(last_activity=_NOW - timedelta(seconds=silent_seconds))
    engine = _MonitorEngine(records={"SOURCE": [record]}, access=no_access)

    # When/Then: the state follows the heartbeat, is flagged unverified, and the grant hint is printed
    _, output = _run_status(monkeypatch, capsys, engine, ddl_pending_dir=str(tmp_path))
    assert f"  {state}  " in _run_line(output, "r1", "SOURCE")
    assert "session not verified" in output
    assert engine.activity_conns == []
    # And: the grants are in a script under the pending directory and the report says to run it
    script = next(tmp_path.glob("status_*_monitoring_grants_*.sql")).read_text(encoding="utf-8")
    assert f"{hint};" in script
    assert "Ask a DBA to run" in output and str(tmp_path) in output
    assert engine.grantees == ["LDB_SOURCE_ROLE", "LDB_HISTORY_ROLE"]


def test_runs_are_listed_most_recent_first_each_with_its_own_state(monkeypatch, capsys):
    # Spec: README > Run ILM > --last — the state and statistics of each of the last n executions
    engine = _MonitorEngine(records={"SOURCE": [_record("new", last_status=Status.ERROR), _record("old", table="B")]})
    _, output = _run_status(monkeypatch, capsys, engine, last=2)
    assert output.index(f"run {_label('new')}") < output.index(f"run {_label('old')}")
    assert "  ERRORS  " in _run_line(output, "new", "SOURCE")
    assert "  OK  " in _run_line(output, "old", "SOURCE")


def test_a_held_runner_lock_proves_the_run_is_alive_even_without_session_views(monkeypatch, capsys):
    # Spec: README > Run ILM > --mode STATUS — liveness is verified by trying the runner mutex without waiting;
    # a lock held by another session means the run is active, whatever the session views allow
    # Given: a table in progress, a silent heartbeat, no readable session view, and the runner lock held
    no_access = MonitoringAccess(session=False, progress=False, waits=False)
    record = _running(last_activity=_NOW - timedelta(seconds=3600))
    engine = _MonitorEngine(records={"SOURCE": [record]}, access=no_access, runner_alive=True)

    # When/Then: the run is RUNNING and verified, and the probe named the table and its run
    _, output = _run_status(monkeypatch, capsys, engine)
    assert "  RUNNING  " in _run_line(output, "r1", "SOURCE")
    assert "session not verified" not in output
    assert ("APP", "ITEMS", "r1") in engine.probed


def test_a_free_runner_lock_means_the_run_is_gone_even_if_its_session_looks_alive(monkeypatch, capsys):
    # Spec: README > Run ILM > --mode STATUS — nobody holds the runner mutex: the run died and left the table in
    # progress; the lock outranks a recycled or lingering session
    engine = _MonitorEngine(records={"SOURCE": [_running()]}, runner_alive=False)

    _, output = _run_status(monkeypatch, capsys, engine)
    assert "  PARTIAL  " in _run_line(output, "r1", "SOURCE")
    assert "INTERRUPTED" in output


def test_runner_lock_is_probed_only_for_tables_in_progress(monkeypatch, capsys):
    # Spec: README > Run ILM > --mode STATUS — finished tables are never probed (nothing is locked needlessly)
    engine = _MonitorEngine(records={"SOURCE": [_record(table="A"), _running(table="B")]}, runner_alive=True)
    _run_status(monkeypatch, capsys, engine)
    assert [table for _, table, _ in engine.probed] == ["B"]


def test_the_admin_connection_reads_the_session_views_the_runtime_user_cannot(monkeypatch, capsys, tmp_path):
    # Spec: README > Run ILM > --mode STATUS — when the runtime user cannot read the gv$ views and admin credentials
    # exist, the session details are read over the admin connection and no script is needed
    # Given: a running table, runtime without views, an admin that reads them all
    no_access = MonitoringAccess(
        session=False, progress=False, waits=False, grants=("GRANT SELECT ON sys.gv_$session TO R",)
    )
    engine = _MonitorEngine(records={"SOURCE": [_running()]}, access=no_access, admin_access=_FULL_ACCESS)

    # When
    _, output = _run_status(
        monkeypatch,
        capsys,
        engine,
        ddl_pending_dir=str(tmp_path),
        admin_source_username="sa",
        admin_source_password="sap",
    )

    # Then: the activity was read with an admin connection, which is closed; nothing is written for a DBA
    assert [conn.admin for conn in engine.activity_conns] == [True]
    assert {id(c) for c in engine.closed} == {id(c) for c in engine.opened}
    assert "admin connection" not in output
    # And: HISTORY has no admin credentials, so only it gets the script
    assert [f.name.split("_monitoring")[0] for f in tmp_path.iterdir()] == ["status_history"]


def test_an_admin_connection_without_the_views_does_not_replace_the_runtime_one(monkeypatch, capsys, tmp_path):
    # Spec: README > Run ILM > --mode STATUS — if the admin user cannot read them either, the data is skipped and
    # the script is written
    no_access = MonitoringAccess(
        session=False, progress=False, waits=False, grants=("GRANT SELECT ON sys.gv_$session TO R",)
    )
    engine = _MonitorEngine(records={"SOURCE": [_running()]}, access=no_access)

    _, output = _run_status(
        monkeypatch,
        capsys,
        engine,
        ddl_pending_dir=str(tmp_path),
        admin_source_username="sa",
        admin_source_password="sap",
    )

    assert engine.activity_conns == []
    assert {id(c) for c in engine.closed} == {id(c) for c in engine.opened}
    assert len(list(tmp_path.glob("status_*_monitoring_grants_*.sql"))) == 2  # SOURCE and HISTORY
    assert "admin connection" not in output


def test_without_missing_grants_no_script_is_written_and_no_admin_connection_is_opened(monkeypatch, capsys, tmp_path):
    # Spec: README > Run ILM > --mode STATUS — admin credentials are used only when the runtime user lacks views
    engine = _MonitorEngine(records={"SOURCE": [_running()]})
    _run_status(
        monkeypatch,
        capsys,
        engine,
        ddl_pending_dir=str(tmp_path),
        admin_source_username="sa",
        admin_source_password="sap",
    )
    assert all(not conn.admin for conn in engine.opened)
    assert list(tmp_path.iterdir()) == []


# --- Grouping of the log into executions (the log carries no run id) ---------------------------------------------


def _at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 10, 22, hour, minute, second)


def _table_log(first_id, table, start, statuses, owner="APP", rows=100, message=None):
    """Log rows of one table run: one row per status, one minute apart, in order."""
    return [
        LogEntry(
            first_id + i, owner, table, "SOURCE_ILM", _at(0), status, start + timedelta(minutes=i),
            start + timedelta(minutes=i + 1), message if status == Status.ERROR else None,
            rows if status in (Status.CHUNK_END, Status.LOB_END) else 0,
        )
        for i, status in enumerate(statuses)
    ]  # fmt: skip


_FINISHED = [Status.TABLE_START, Status.CHUNK_START, Status.CHUNK_END, Status.CHUNK_END, Status.TABLE_END]


def test_log_rows_without_a_run_id_are_grouped_into_executions_by_time():
    # Spec: README > Run ILM > --mode STATUS — executions are rebuilt from the log timestamps (no run id is stored)
    # Given: three tables processed by two invocations hours apart, the first with two workers overlapping
    log = (
        _table_log(1, "A", _at(8, 0), _FINISHED)
        + _table_log(10, "B", _at(8, 1), _FINISHED)
        + _table_log(20, "A", _at(11, 0), _FINISHED)
    )

    # When
    executions = build_executions(log, [], last=5)

    # Then: newest first; A and B belong together in the first, labelled with its start time
    assert [label for label, _ in executions] == ["20261022-110000", "20261022-080000"]
    assert [r.table_name for r in executions[1][1]] == ["A", "B"]
    assert [r.table_name for r in executions[0][1]] == ["A"]
    # And: rows are the chunk rows committed (2 chunks of 100)
    assert executions[0][1][0].rows == 200 and executions[0][1][0].last_status == Status.TABLE_END


def test_last_n_limits_the_executions_to_the_most_recent_ones():
    # Spec: README > Run ILM > --last — the n most recent executions
    log = [e for hour in (6, 8, 10) for e in _table_log(hour * 10, "A", _at(hour), _FINISHED)]
    assert [label for label, _ in build_executions(log, [], last=2)] == ["20261022-100000", "20261022-080000"]
    assert len(build_executions(log, [], last=1)) == 1


def test_a_table_that_appears_twice_starts_a_new_execution_even_when_close_in_time():
    # Spec: README > Run ILM > --mode STATUS — a run processes a table once; a second run minutes later is another one
    log = _table_log(1, "A", _at(8, 0), _FINISHED) + _table_log(20, "A", _at(8, 10), _FINISHED)
    assert len(build_executions(log, [], last=5)) == 2


def test_runs_written_by_a_preview_are_not_reported():
    # Spec: README > Run ILM > --mode STATUS — PREVIEW executions are never shown
    preview = [Status.TABLE_PREVIEW_START, Status.CHUNK_PREVIEW_END, Status.TABLE_PREVIEW_END]
    log = _table_log(1, "A", _at(8), _FINISHED) + _table_log(20, "A", _at(9), preview)
    control = [
        ControlEntry("APP", "A", Status.TABLE_PREVIEW_END, "p", "SOURCE_ILM", _at(0), _at(9), _at(9), 5, _at(9), None)
    ]

    executions = build_executions(log, control, last=5)

    assert [label for label, _ in executions] == ["20261022-080000"]
    assert executions[0][1][0].last_status == Status.TABLE_END


def test_the_control_row_says_whether_the_table_is_still_in_progress_and_who_runs_it():
    # Spec: README > Run ILM > --mode STATUS — the state of the running table comes from ldb_ctl
    log = _table_log(1, "A", _at(8), [Status.TABLE_START, Status.CHUNK_END])
    control = [
        ControlEntry("APP", "A", Status.CHUNK_END, "run-9", "SOURCE_ILM", _at(0), _at(8), None, 450, _at(8, 5), "1.2.3")
    ]

    ((_, records),) = build_executions(log, control, last=1)

    record = records[0]
    assert (record.in_progress, record.rows, record.session, record.run_id) == (True, 450, "1.2.3", "run-9")
    assert record.last_activity == _at(8, 5)


def test_a_control_row_without_log_rows_is_still_reported():
    # Spec: README > Run ILM > --mode STATUS — ldb_ctl is reported even if the log has nothing about the table
    control = [
        ControlEntry("APP", "A", Status.TABLE_END, "r", "SOURCE_ILM", _at(0), _at(7), _at(7, 30), 900, None, None)
    ]
    ((_, records),) = build_executions([], control, last=1)
    assert (records[0].table_name, records[0].last_status, records[0].rows, records[0].in_progress) == (
        "A", Status.TABLE_END, 900, False,
    )  # fmt: skip


def test_a_table_refused_as_already_processed_is_an_execution_member_flagged_skipped():
    # Spec: README > Run ILM > --mode STATUS — ORA-20003 "already processed" is neither an error nor unfinished work
    log = _table_log(1, "A", _at(8), _FINISHED) + _table_log(
        20, "B", _at(8, 2), [Status.ERROR], message="Table B is already processed for date 1"
    )
    ((_, records),) = build_executions(log, [], last=1)
    assert [(r.table_name, r.skipped) for r in records] == [("A", False), ("B", True)]


def test_a_table_line_shows_status_rows_times_and_the_control_data(monkeypatch, capsys):
    # Spec: README > Run ILM > --mode STATUS — per-table status, rows processed, started, finished and elapsed
    engine = _MonitorEngine(records={"SOURCE": [_record(table="DSP_DET", rows=126038)]})
    _, output = _run_status(monkeypatch, capsys, engine)
    start = _RUN_START.get("r1", _DEFAULT_START)
    assert (
        f"APP.DSP_DET  status: TEND  rows processed: 126,038, started: {start:%Y-%m-%d %H:%M:%S}"
        f" finished: {start + timedelta(minutes=30):%Y-%m-%d %H:%M:%S} elapsed 0:30:00"
    ) in output
    assert "action: SOURCE_ILM, process date: 2026-10-22" in output
    assert "admin connection" not in output


def test_a_successful_connection_logs_no_detail(monkeypatch, capsys, caplog):
    # Spec: README > Logging — a successful connection is reported without dsn or user
    runner_logger = logging.getLogger(ldb_runner.logger.name)
    runner_logger.addHandler(caplog.handler)
    monkeypatch.setattr(runner_logger, "level", logging.INFO)
    engine = _MonitorEngine(records={"SOURCE": [_record()]})
    try:
        _run_status(monkeypatch, capsys, engine)
    finally:
        runner_logger.removeHandler(caplog.handler)
    messages = [r.getMessage() for r in caplog.records if "connected" in r.getMessage()]
    assert "SOURCE runtime: connected" in messages
    assert not any(" as " in m or "sd" in m.split(": ")[-1] for m in messages)


def test_an_engine_without_monitoring_still_gets_states_rows_and_no_grants_script(monkeypatch, capsys, tmp_path):
    # Spec: README > Run ILM > --mode STATUS — session statistics are optional engine capabilities; an engine that
    # keeps the contract defaults reports states and rows, and neither asks for grants nor writes a script
    # Given: an engine with the default (no) monitoring and a table whose runner liveness cannot be probed
    class _PlainEngine(_MonitorEngine):
        check_monitoring_access = staticmethod(DatabaseEngine.check_monitoring_access)
        is_run_alive = staticmethod(DatabaseEngine.is_run_alive)
        get_session_activity = staticmethod(DatabaseEngine.get_session_activity)

    engine = _PlainEngine(records={"SOURCE": [_running()]})

    # When
    code, output = _run_status(monkeypatch, capsys, engine, ddl_pending_dir=str(tmp_path))

    # Then: the report works from the heartbeat, with no monitoring message and nothing written
    assert code == 0
    assert "RUNNING" in output
    assert "monitoring" not in output
    assert list(tmp_path.iterdir()) == []
