from datetime import datetime

import pytest

from limitsdb.core.ldb_errors import DatabaseConnectionError, ExecutionError
from limitsdb.core.ldb_params_config import Config
from limitsdb.db.oracle import ldb_engine_impl
from limitsdb.db.oracle.ldb_engine_impl import OracleEngine


def test_driver_failure_is_wrapped_as_domain_connection_error(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/exception-handling.md > DatabaseConnectionError — connection failures
    # from the Oracle driver are chained as DatabaseConnectionError; the error message
    # must not include the password to avoid credential leakage in logs
    cause = RuntimeError("driver failure")
    monkeypatch.setattr(ldb_engine_impl.oracledb, "connect", lambda **kwargs: (_ for _ in ()).throw(cause))
    config = Config(schema="s", action="SOURCE_ILM", source_username="user", source_password="secret", source_dsn="dsn")

    with pytest.raises(DatabaseConnectionError) as caught:
        OracleEngine.get_connection(config)

    assert caught.value.__cause__ is cause
    assert "secret" not in str(caught.value)


def test_row_count_query_failure_is_chained_as_execution_error():
    # Spec: docs/exception-handling.md > ExecutionError — row count failures must not
    # be silently reported as zero; the caller uses the count to detect partial progress
    cause = RuntimeError("query failure")

    class DummyConnection:
        def cursor(self):
            raise cause

    with pytest.raises(ExecutionError) as caught:
        OracleEngine.get_rows_processed(DummyConnection(), "OWNER", "TABLE", "20261006")  # type: ignore[arg-type]

    assert caught.value.__cause__ is cause


def test_error_status_is_persisted_and_committed_after_table_failure(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/exception-handling.md > ExecutionError — table failures are recorded
    # in the control table via check_save_status before the run continues; the record
    # is committed so partial progress is visible even if the run crashes later
    calls = []

    class DummyCursor:
        closed = False

        def var(self, data_type):
            return f"var:{data_type}"

        def callproc(self, name, arguments):
            calls.append((name, arguments))

        def close(self):
            self.closed = True

    class DummyConnection:
        def __init__(self):
            self.cursor_instance = DummyCursor()
            self.commits = 0

        def cursor(self):
            return self.cursor_instance

        def commit(self):
            self.commits += 1

    connection = DummyConnection()
    process_end = datetime(2026, 10, 7, 12, 5)
    monkeypatch.setattr(OracleEngine, "get_system_date", lambda conn: process_end)
    config = Config(
        schema="s",
        mode="EXECUTE",
        source_dsn="dsn",
        source_username="user",
        source_password="password",
    )

    OracleEngine.save_error_status(
        connection,  # type: ignore[arg-type]
        config,
        "OWNER",
        "ITEMS",
        "20261007",
        datetime(2026, 10, 7, 12, 0),
        "controlled failure",
        "begin fail; end;",
    )

    assert calls[0][0] == "check_save_status"
    assert calls[0][1][0:5] == ["OWNER", "ITEMS", datetime(2026, 10, 7).date(), "SOURCE_ILM", "ERROR"]
    assert calls[0][1][7:11] == [process_end, "controlled failure", 0, "begin fail; end;"]
    assert connection.commits == 1
    assert connection.cursor_instance.closed is True


def test_error_status_persistence_failure_is_chained_as_execution_error(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/exception-handling.md > ExecutionError — if the status-save procedure
    # itself fails, the error is chained so root cause is preserved; cursor is closed
    cause = RuntimeError("procedure failed")

    class DummyCursor:
        def var(self, data_type):
            return object()

        def callproc(self, name, arguments):
            raise cause

        def close(self):
            self.closed = True

    class DummyConnection:
        def __init__(self):
            self.cursor_instance = DummyCursor()

        def cursor(self):
            return self.cursor_instance

    connection = DummyConnection()
    monkeypatch.setattr(OracleEngine, "get_system_date", lambda conn: datetime(2026, 10, 7, 12, 5))
    config = Config(
        schema="s",
        mode="EXECUTE",
        source_dsn="dsn",
        source_username="user",
        source_password="password",
    )

    with pytest.raises(ExecutionError) as caught:
        OracleEngine.save_error_status(
            connection,  # type: ignore[arg-type]
            config,
            "OWNER",
            "ITEMS",
            "20261007",
            datetime(2026, 10, 7, 12, 0),
            "controlled failure",
            "begin fail; end;",
        )

    assert caught.value.__cause__ is cause
    assert connection.cursor_instance.closed is True
