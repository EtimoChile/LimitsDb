from datetime import datetime

import oracledb
import pytest

from limitsdb.core.ldb_errors import ExecutionError, RunnerBusyError
from limitsdb.core.ldb_params_config import Config
from limitsdb.db.oracle import ldb_engine_impl
from limitsdb.db.oracle.ldb_engine_impl import CHECK_SAVE_STATUS_PROC, OracleEngine


class _Cursor:
    def __init__(self, owner, row=None, fail_on=None):
        self.owner, self.row, self.fail_on = owner, row, fail_on
        self.closed = False

    def execute(self, sql, params=None):
        self.owner.log.append((self.owner.name, " ".join(sql.split()), params))
        if self.fail_on and self.fail_on(sql, params):
            raise self.owner.failure

    def fetchone(self):
        return self.row

    def close(self):
        self.closed = True


class _Conn:
    def __init__(self, name, row=None, fail_on=None, failure=None, log=None):
        self.name, self.row, self.fail_on, self.failure = name, row, fail_on, failure
        self.log = log if log is not None else []

    def cursor(self):
        return _Cursor(self, self.row, self.fail_on)

    def commit(self):
        self.log.append((self.name, "COMMIT", None))

    def rollback(self):
        self.log.append((self.name, "ROLLBACK", None))


def _busy_error() -> oracledb.DatabaseError:
    return oracledb.DatabaseError(type("E", (), {"code": 54, "message": "ORA-00054: resource busy"})())


def test_runner_locks_tables_in_sorted_order_then_publishes_its_keys():
    # Spec: docs/concurrency-control.md > Layer 1 — anchors are created and committed by the key connection, locks
    # are taken NOWAIT in a fixed order on the lock connection, and only then are the keys published
    # Given: two connections writing to one log, tables given out of order
    log: list = []
    lock_conn, key_conn = _Conn("lock", row=(1,), log=log), _Conn("key", log=log)

    # When: the runner acquires its locks
    OracleEngine.acquire_run_locks(
        lock_conn,  # type: ignore[arg-type]
        key_conn,  # type: ignore[arg-type]
        [("A", "T2"), ("A", "T1")],
        "run-1",
        "EXECUTE",
        "host",
        42,
    )

    # Then: locks follow T1 then T2, nothing is ever committed on the lock connection, and keys carry the run
    locks = [params for who, sql, params in log if who == "lock" and "FOR UPDATE NOWAIT" in sql]
    assert locks == [["A", "T1"], ["A", "T2"]]
    assert not [entry for entry in log if entry[0] == "lock" and entry[1] == "COMMIT"]
    keys = [params for who, sql, params in log if sql.startswith("UPDATE ldb_run_lock")]
    assert keys == [["run-1", "EXECUTE", "host", 42, "A", "T1"], ["run-1", "EXECUTE", "host", 42, "A", "T2"]]
    first_lock = next(i for i, entry in enumerate(log) if "FOR UPDATE NOWAIT" in entry[1])
    first_key = next(i for i, entry in enumerate(log) if entry[1].startswith("UPDATE ldb_run_lock"))
    assert first_lock < first_key


def test_a_table_held_by_another_runner_raises_runner_busy_and_leaves_nothing_locked():
    # Spec: docs/concurrency-control.md > Layer 1 — ORA-00054 on a lock row means a live runner owns the table;
    # the attempt is rolled back so no earlier table stays locked
    # Given: the second table cannot be locked
    log: list = []
    key_conn = _Conn("key", log=log)
    lock_conn = _Conn(
        "lock", row=(1,), log=log, fail_on=lambda sql, params: params == ["A", "T2"], failure=_busy_error()
    )

    # When/Then: the run is refused with RunnerBusyError naming the table
    with pytest.raises(RunnerBusyError, match=r"A.T2"):
        OracleEngine.acquire_run_locks(
            lock_conn,  # type: ignore[arg-type]
            key_conn,  # type: ignore[arg-type]
            [("A", "T1"), ("A", "T2")],
            "run-1",
            "EXECUTE",
            "host",
            42,
        )
    assert ("lock", "ROLLBACK", None) in log
    assert not [entry for entry in log if entry[1].startswith("UPDATE ldb_run_lock")]


def test_the_busy_message_names_the_runner_that_holds_the_table():
    # Spec: docs/concurrency-control.md > Layer 1 — the key row of the table says which runner to wait for
    # Given: the table is busy and its key row holds the other runner's data
    holder = ("run-9", "EXECUTE", "otherhost", 77, datetime(2026, 10, 22, 15, 31, 5))
    key_conn = _Conn("key", row=holder)
    lock_conn = _Conn("lock", fail_on=lambda sql, params: params == ["A", "T1"], failure=_busy_error())

    # When/Then: the refusal carries its run id, mode, host, pid and start
    with pytest.raises(RunnerBusyError) as caught:
        OracleEngine.acquire_run_locks(
            lock_conn,  # type: ignore[arg-type]
            key_conn,  # type: ignore[arg-type]
            [("A", "T1")],
            "run-1",
            "EXECUTE",
            "host",
            42,
        )
    assert "(runner run-9, EXECUTE, host otherhost, pid 77, since 2026-10-22 15:31:05)" in str(caught.value)


def test_a_lost_anchor_race_does_not_undo_the_other_anchors():
    # Spec: docs/concurrency-control.md > Layer 1 — each anchor is committed on its own, so the IntegrityError of
    # a race on one table rolls back only that insert and never the anchors created before it
    # Given: the insert of the second table's 'L' anchor loses a race
    log: list = []
    lost = oracledb.IntegrityError(type("E", (), {"code": 1, "message": "ORA-00001"})())
    key_conn = _Conn(
        "key",
        log=log,
        fail_on=lambda sql, params: (
            sql.lstrip().startswith("INSERT") and params["table_name"] == "T2" and params["kind"] == "L"
        ),
        failure=lost,
    )

    # When: the runner acquires its locks
    OracleEngine.acquire_run_locks(
        _Conn("lock", row=(1,), log=log),  # type: ignore[arg-type]
        key_conn,  # type: ignore[arg-type]
        [("A", "T1"), ("A", "T2")],
        "run-1",
        "EXECUTE",
        "host",
        42,
    )

    # Then: T1's anchors were committed before T2's insert failed, and only the failed insert was rolled back
    insert_t2 = next(
        i for i, (who, sql, params) in enumerate(log) if who == "key" and params and params.get("table_name") == "T2"
    )
    assert ("key", "COMMIT", None) in log[:insert_t2]
    assert [entry for entry in log if entry == ("key", "ROLLBACK", None)] == [("key", "ROLLBACK", None)]


def test_a_missing_anchor_stops_the_run_instead_of_running_unprotected():
    # Spec: docs/concurrency-control.md > Layer 1 — a lock that selects no row protects nothing
    # Given: the lock query returns no row
    lock_conn = _Conn("lock", row=None)

    # When/Then: the run is refused
    with pytest.raises(ExecutionError, match=r"A.T1"):
        OracleEngine.acquire_run_locks(
            lock_conn,  # type: ignore[arg-type]
            _Conn("key"),  # type: ignore[arg-type]
            [("A", "T1")],
            "run-1",
            "EXECUTE",
            "host",
            42,
        )


def test_any_other_lock_failure_is_an_execution_error_not_a_busy_runner():
    # Spec: docs/exception-handling.md — only ORA-00054 means "busy"; a different failure must not be blamed on
    # another runner
    # Given: a lock failure with another ORA code
    other = oracledb.DatabaseError(type("E", (), {"code": 942, "message": "ORA-00942"})())
    lock_conn = _Conn("lock", fail_on=lambda sql, params: True, failure=other)

    # When/Then
    with pytest.raises(ExecutionError) as caught:
        OracleEngine.acquire_run_locks(
            lock_conn,  # type: ignore[arg-type]
            _Conn("key"),  # type: ignore[arg-type]
            [("A", "T1")],
            "run-1",
            "EXECUTE",
            "host",
            42,
        )
    assert not isinstance(caught.value, RunnerBusyError)


def test_release_rolls_back_the_lock_connection_without_committing():
    # Spec: docs/concurrency-control.md > Layer 1 — lock rows are never committed; a rollback frees them
    log: list = []
    OracleEngine.release_run_locks(_Conn("lock", log=log))  # type: ignore[arg-type]
    assert log == [("lock", "ROLLBACK", None)]


def test_a_cut_lock_connection_is_reported_as_execution_error():
    # Spec: docs/concurrency-control.md > Failure scenarios — the liveness ping turns a dead lock session into an error
    ok = _Conn("lock")
    OracleEngine.check_run_locks(ok)  # type: ignore[arg-type]
    assert ok.log[-1][1] == "SELECT 1 FROM dual"

    dead = _Conn("lock", fail_on=lambda sql, params: True, failure=RuntimeError("DPY-4011: connection closed"))
    with pytest.raises(ExecutionError):
        OracleEngine.check_run_locks(dead)  # type: ignore[arg-type]


@pytest.mark.parametrize(("row", "expected"), [((7,), 7), (None, 0)])
def test_resumed_rows_are_those_of_an_incomplete_execute_or_zero(row, expected):
    # Spec: docs/concurrency-control.md > Process date — only an incomplete EXECUTE status is continued
    # Given/When: the lookup
    conn = _Conn("db", row=row)
    result = OracleEngine.get_resumed_rows(conn, "A", "T1", "20261007")  # type: ignore[arg-type]

    # Then: the count comes back, the query is limited to EXECUTE in-progress statuses (never P-statuses)
    assert result == expected
    sql = conn.log[0][1]
    assert "'TSTART', 'CSTART', 'CEND', 'LSTART', 'LEND', 'ERROR'" in sql
    assert "'P" not in sql


@pytest.mark.parametrize(("row", "expected"), [((1,), True), (None, False)])
def test_error_recorded_since_looks_for_error_or_perror(row, expected):
    # Spec: docs/concurrency-control.md > Layer 2 — Python writes ERROR only when the block did not
    conn = _Conn("db", row=row)
    result = OracleEngine.error_recorded_since(conn, "A", "T1", datetime(2026, 10, 7))  # type: ignore[arg-type]
    assert result is expected
    assert "'ERROR', 'PERROR'" in conn.log[0][1]


def test_preview_failures_are_saved_as_perror_with_the_run_id(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/concurrency-control.md > Status — a PREVIEW failure never overwrites an EXECUTE status
    calls = []

    class _Proc:
        def var(self, data_type):
            return type("V", (), {"getvalue": lambda self: None})()

        def callproc(self, name, arguments):
            calls.append((name, arguments))

        def close(self):
            pass

    class _C:
        def cursor(self):
            return _Proc()

        def commit(self):
            pass

    monkeypatch.setattr(OracleEngine, "get_system_date", lambda conn: datetime(2026, 10, 7, 12, 5))
    config = Config(schema="s", mode="PREVIEW", source_dsn="dsn", source_username="u", source_password="p")

    OracleEngine.save_error_status(
        _C(),  # type: ignore[arg-type]
        config,
        "OWNER",
        "ITEMS",
        "20261007",
        datetime(2026, 10, 7, 12, 0),
        "boom",
        "begin null; end;",
        0,
        "run-7",
    )

    args = calls[0][1]
    assert args[4] == "PERROR"
    assert "run-7" in args


def test_check_save_status_text_enforces_ownership_and_preview_rules():
    # Spec: docs/concurrency-control.md > Status — ownership (-20005), PREVIEW refused over EXECUTE (-20006),
    # a closure row for abandoned runs, and the row lock never waits
    proc = CHECK_SAVE_STATUS_PROC
    assert "-20005" in proc and "-20006" in proc
    assert "ldb_run_lock" in proc
    assert "FOR UPDATE NOWAIT" in proc.upper()
    assert "Incomplete process closed by a later process" in proc
    assert "TPEND" not in proc
    assert ldb_engine_impl.Status.TABLE_PREVIEW_END == "PTEND"


def test_check_save_status_text_identifies_script_runs_and_uses_the_chunk_end_as_heartbeat():
    # Spec: docs/concurrency-control.md > Time guard — a lock-less run is identified by ldb_ctl.ctl_run_id, and the
    # 5 s guard counts from the last chunk end, only for in-progress statuses (PEND-026)
    proc = " ".join(CHECK_SAVE_STATUS_PROC.split())

    # Then: SCRIPT ids (prefix S) skip the lock table, the row keeps the id, and the guard uses the heartbeat
    assert "p_run_id NOT LIKE 'S%'" in proc
    assert "ctl_run_id = CASE WHEN p_status IN ('TSTART', 'PTSTART') THEN p_run_id ELSE ctl_run_id END" in proc
    assert "l_ctl_run_id <> p_run_id" in proc
    assert "GREATEST(l_process_start, NVL(l_process_end, l_process_start))" in proc
    # an ERROR left by a failed run can be retried at once: it is not an in-progress status
    guard = proc.split("-- The heartbeat", 1)[1].split("THEN", 1)[0]
    assert "'ERROR'" not in guard


def test_anchor_insert_binds_each_value_once_by_name():
    # Spec: docs/concurrency-control.md > Layer 1 — the anchor insert repeats its values in the NOT EXISTS
    # subquery; positional binds would then need twice the values (DPY-4009 on a real driver)
    # Given/When: the runner acquires locks for one table
    log: list = []
    OracleEngine.acquire_run_locks(
        _Conn("lock", row=(1,), log=log),  # type: ignore[arg-type]
        _Conn("key", log=log),  # type: ignore[arg-type]
        [("A", "T1")],
        "run-1",
        "EXECUTE",
        "host",
        42,
    )

    # Then: every anchor insert passes a mapping, never a list
    inserts = [params for _, sql, params in log if sql.startswith("INSERT INTO ldb_run_lock")]
    assert len(inserts) == 2
    assert all(isinstance(params, dict) for params in inserts)


def test_plsql_text_is_never_mixed_with_the_clob_column_in_nvl():
    # Spec: docs/concurrency-control.md > Layer 2 — ctl_plsql is a CLOB; NVL(varchar2, clob) converts it back to
    # VARCHAR2 and fails (ORA-22835) once the stored block passes 4000 characters
    proc = " ".join(CHECK_SAVE_STATUS_PROC.split())
    assert "NVL(p_plsql, ctl_plsql)" not in proc
    assert "IF p_plsql IS NOT NULL THEN UPDATE ldb_ctl SET ctl_plsql = p_plsql" in proc


@pytest.mark.parametrize(
    ("expression", "owners", "expected"),
    [
        # unquoted references match by upper case and keep the table as written
        ("@ JOIN gl . parent p ON p.id = a.pid", {("GL", "PARENT"): "HIST"}, "@ JOIN hist.parent p ON p.id = a.pid"),
        # a quoted source name matches exactly
        ('@ JOIN "Gl"."Parent" p ON 1=1', {("Gl", "Parent"): "HIST"}, '@ JOIN hist."Parent" p ON 1=1'),
        ('@ JOIN GL."Parent" p ON 1=1', {("GL", "PARENT"): "HIST"}, '@ JOIN GL."Parent" p ON 1=1'),
        # the owner is rendered like every other generated identifier: DD upper case unquoted, anything else quoted
        ("@ JOIN GL.PARENT p ON 1=1", {("GL", "PARENT"): "hist"}, '@ JOIN "hist".PARENT p ON 1=1'),
        # other schemas, aliases and longer names are left alone
        ("@ JOIN OTHER.PARENT p ON p.GL = 1", {("GL", "PARENT"): "HIST"}, "@ JOIN OTHER.PARENT p ON p.GL = 1"),
        ("@ JOIN GL.PARENT_X p ON 1=1", {("GL", "PARENT"): "HIST"}, "@ JOIN GL.PARENT_X p ON 1=1"),
        # a reference that already points at the owner is not touched
        ("@ JOIN HIST.PARENT p ON 1=1", {("HIST", "PARENT"): "HIST"}, "@ JOIN HIST.PARENT p ON 1=1"),
    ],
)
def test_table_owners_are_rewritten_with_oracle_identifier_rules(expression, owners, expected):
    # Spec: DEC-039 — history joins read the history owner; quoting and case follow the engine, not the core
    assert OracleEngine.rewrite_table_owners(expression, owners) == expected
