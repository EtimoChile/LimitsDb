from datetime import datetime

import oracledb
import pytest

from limitsdb.core.ldb_errors import ExecutionError
from limitsdb.db.ldb_engines import MonitoringAccess
from limitsdb.db.oracle.ldb_engine_impl import CHECK_SAVE_STATUS_PROC, OracleEngine

_SESSION = "2.117.5551234"
_FULL = MonitoringAccess(session=True, progress=True, waits=True)


def _unreadable(view: str) -> oracledb.DatabaseError:
    return oracledb.DatabaseError(type("E", (), {"code": 942, "message": f"ORA-00942: {view} does not exist"})())


class _Cursor:
    """Answers each statement with the first canned result whose key occurs in its text."""

    def __init__(self, conn):
        self.conn = conn
        self.rows: list = []

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        self.conn.log.append((text, params))
        for view in self.conn.denied:
            if f"FROM {view}" in text:
                raise _unreadable(view)
        self.rows = []
        for key, rows in self.conn.results.items():
            if key in text:
                self.rows = list(rows)
                break

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class _Conn:
    def __init__(self, results=None, denied=()):
        self.results = results or {}
        self.denied = denied
        self.log: list = []

    def cursor(self):
        return _Cursor(self)


def test_the_run_registers_its_session_in_the_control_table():
    # Spec: README > Run ILM > --mode STATUS — the session is recorded when the table is registered (one
    # engine-specific id; Oracle: instance.sid.audsid, RAC aware)
    proc = " ".join(CHECK_SAVE_STATUS_PROC.split())

    composed = (
        "sys_context('USERENV', 'INSTANCE') || '.' || sys_context('USERENV', 'SID') "
        "|| '.' || sys_context('USERENV', 'SESSIONID')"
    )

    # Then: a new control row and a restarted one both store the one composed id (instance.sid.audsid)
    assert proc.count(composed) == 2
    # And: the session is overwritten only when a table starts, never by chunk or end rows
    update = proc.split("ctl_db_session_id = CASE WHEN p_status IN ('TSTART', 'PTSTART')", 1)[1]
    assert update.startswith(f" THEN {composed} ELSE ctl_db_session_id END")
    # And: the log keeps no run id (executions are grouped by time) and the control row keeps the runner's
    assert "log_run_id" not in proc
    assert "ctl_run_id" in proc


def test_log_entries_are_read_newest_first_up_to_the_limit():
    # Spec: README > Run ILM > --mode STATUS — the log is read as raw rows; executions are grouped by the core
    # Given: two log rows
    start, end = datetime(2026, 10, 22, 11, 0), datetime(2026, 10, 22, 11, 30)
    rows = [
        (12, "APP", "ITEMS", "SOURCE_ILM", start, "CEND", start, end, None, 300),
        (11, "APP", "ITEMS", "SOURCE_ILM", start, "TSTART", start, None, None, 0),
    ]
    conn = _Conn(results={"FROM ldb_log": rows})

    # When
    entries = OracleEngine.get_log_entries(conn, 500)  # type: ignore[arg-type]

    # Then: the query asks for the newest rows first, limited, without the PL/SQL column
    sql, params = conn.log[0]
    assert "ORDER BY log_id DESC FETCH FIRST :max_rows ROWS ONLY" in sql and "log_plsql" not in sql
    assert params == {"max_rows": 500}
    assert [(e.log_id, e.status, e.rows, e.ended) for e in entries] == [(12, "CEND", 300, end), (11, "TSTART", 0, None)]


def test_control_entries_carry_the_run_the_heartbeat_and_the_registered_session():
    # Spec: README > Run ILM > --mode STATUS — ldb_ctl gives the current state of each table and its session
    start, beat = datetime(2026, 10, 22, 11, 0), datetime(2026, 10, 22, 11, 20)
    row = ("APP", "BUSY", "CEND", "run-1", "SOURCE_ILM", start, start, None, 450, beat, _SESSION)
    conn = _Conn(results={"FROM ldb_ctl": [row]})

    (entry,) = OracleEngine.get_control_entries(conn)  # type: ignore[arg-type]

    assert (entry.owner, entry.table_name, entry.status, entry.run_id) == ("APP", "BUSY", "CEND", "run-1")
    assert (entry.rows, entry.last_activity, entry.session, entry.ended) == (450, beat, _SESSION, None)


@pytest.mark.parametrize("read", [lambda c: OracleEngine.get_log_entries(c, 10), OracleEngine.get_control_entries])
def test_a_failed_read_of_the_control_tables_is_an_execution_error_that_points_to_ldb_impl(read):
    # Spec: docs/exception-handling.md — database failures cross the adapter as ExecutionError; control tables
    # created before STATUS lack CTL_DB_SESSION_ID (ORA-00904) and are fixed by rerunning ldb-impl
    class _Broken(_Conn):
        def cursor(self):
            cur = super().cursor()

            def fail(sql, params=None):
                raise oracledb.DatabaseError(type("E", (), {"code": 904, "message": "ORA-00904: invalid identifier"})())

            cur.execute = fail  # type: ignore[method-assign]
            return cur

    with pytest.raises(ExecutionError, match="Could not read"):
        read(_Broken())  # type: ignore[arg-type]


def test_a_missing_session_column_names_ldb_impl_as_the_fix():
    class _Old(_Conn):
        def cursor(self):
            cur = super().cursor()

            def fail(sql, params=None):
                raise oracledb.DatabaseError(type("E", (), {"code": 904, "message": "ORA-00904"})())

            cur.execute = fail  # type: ignore[method-assign]
            return cur

    with pytest.raises(ExecutionError, match="ldb-impl"):
        OracleEngine.get_control_entries(_Old())  # type: ignore[arg-type]


def test_monitoring_access_names_the_grant_for_each_unreadable_view():
    # Spec: README > Run ILM > --mode STATUS — access to the session views is verified and the missing grants named
    # Given: the longops view is not readable
    conn = _Conn(denied=("gv$session_longops",))

    # When
    access = OracleEngine.check_monitoring_access(conn, "LDB_SOURCE_ROLE")  # type: ignore[arg-type]

    # Then: only progress is lost, and the hint grants the SYS wrapper of the view to the role
    assert (access.session, access.progress, access.waits) == (True, False, True)
    assert access.grants == ("GRANT SELECT ON sys.gv_$session_longops TO LDB_SOURCE_ROLE",)


def test_monitoring_access_is_complete_when_every_view_is_readable_and_empty_when_none_is():
    # Spec: README > Run ILM > --mode STATUS — the four views are probed independently of each other
    full = OracleEngine.check_monitoring_access(_Conn(), "R")  # type: ignore[arg-type]
    assert (full.session, full.progress, full.waits, full.grants) == (True, True, True, ())

    views = ("gv$session", "gv$session_longops", "gv$session_event", "gv$sess_time_model")
    none = OracleEngine.check_monitoring_access(_Conn(denied=views), "R")  # type: ignore[arg-type]
    assert (none.session, none.progress, none.waits) == (False, False, False)
    assert len(none.grants) == 4


def test_session_activity_reads_the_session_by_instance_sid_and_audsid_of_its_id():
    # Spec: README > Run ILM > --mode STATUS — Oracle RAC: the session is found by instance + sid + audsid, which
    # also protects against a recycled sid; percent and time left come from the long operation
    # Given: a live session waiting 12 s in an operation that is 25% done with 90 s left
    conn = _Conn(
        results={
            "FROM gv$session WHERE": [(8123, "WAITING", "db file sequential read", 12, "User I/O")],
            "FROM gv$session_longops": [(25, 100, 90, "Table Scan", "APP.ITEMS")],
            "FROM gv$session_event": [("db file sequential read", 30.5), ("log file sync", 2.0)],
            "FROM gv$sess_time_model": [(61.25,)],
        }
    )

    # When
    activity = OracleEngine.get_session_activity(conn, _SESSION, _FULL, 5.0)  # type: ignore[arg-type]

    # Then
    assert activity.alive is True
    assert activity.current_wait is not None and activity.current_wait.name == "db file sequential read"
    assert activity.progress is not None
    assert (activity.progress.percent, activity.progress.remaining_seconds) == (25.0, 90.0)
    assert activity.progress.description == "Table Scan APP.ITEMS"
    assert {(w.name, w.seconds) for w in activity.waits} == {
        ("db file sequential read", 30.5),
        ("log file sync", 2.0),
        ("CPU", 61.25),
    }
    session_sql, session_params = conn.log[0]
    assert "inst_id = :inst AND sid = :sid AND audsid = :aud" in session_sql
    assert session_params == {"inst": 2, "sid": 117, "aud": 5551234}
    longops_params = next(params for sql, params in conn.log if "gv$session_longops" in sql)
    assert longops_params == {"inst": 2, "sid": 117, "ser": 8123}
    # And: idle waits are never part of the profile
    assert "wait_class <> 'Idle'" in next(sql for sql, _ in conn.log if "gv$session_event" in sql)


@pytest.mark.parametrize(
    ("state", "wait_class", "seconds"),
    [("WAITING", "User I/O", 2), ("WAITING", "Idle", 900), ("WORKING", "User I/O", 900)],
)
def test_a_wait_is_not_reported_unless_it_is_long_and_not_idle(state, wait_class, seconds):
    # Spec: README > Run ILM > --mode STATUS — the current event is shown only when the session has been waiting
    # a significant time; idle waits and a session that is on CPU are never a wait
    conn = _Conn(results={"FROM gv$session WHERE": [(1, state, "some event", seconds, wait_class)]})
    activity = OracleEngine.get_session_activity(conn, _SESSION, _FULL, 5.0)  # type: ignore[arg-type]
    assert activity.current_wait is None


def test_a_session_that_no_longer_exists_is_reported_dead_and_not_profiled():
    # Spec: README > Run ILM > --mode STATUS — a session missing from gv$session is not running
    conn = _Conn(results={"FROM gv$session WHERE": []})
    activity = OracleEngine.get_session_activity(conn, _SESSION, _FULL, 5.0)  # type: ignore[arg-type]
    assert activity.alive is False
    assert activity.waits == () and activity.progress is None
    assert not [sql for sql, _ in conn.log if "gv$session_event" in sql or "gv$session_longops" in sql]


def test_session_activity_skips_the_views_the_user_cannot_read():
    # Spec: README > Run ILM > --mode STATUS — without access to the views the state is unknown, not an error
    # Given: no readable view
    conn = _Conn()
    access = MonitoringAccess(session=False, progress=False, waits=False)

    # When
    activity = OracleEngine.get_session_activity(conn, _SESSION, access, 5.0)  # type: ignore[arg-type]

    # Then: nothing was queried and liveness is unknown
    assert conn.log == []
    assert activity.alive is None and activity.waits == ()


def test_a_held_runner_lock_is_reported_active_and_the_probe_never_waits():
    # Spec: README > Run ILM > --mode STATUS — liveness tries the runner lock with NOWAIT; ORA-00054 = a live runner
    # Given: the lock is held by another session
    class _Held(_Conn):
        def cursor(self):
            cur = super().cursor()

            def busy(sql, params=None):
                self.log.append((" ".join(sql.split()), params))
                raise oracledb.DatabaseError(type("E", (), {"code": 54, "message": "ORA-00054: busy"})())

            cur.execute = busy  # type: ignore[method-assign]
            return cur

    conn = _Held()
    conn.rollback = lambda: conn.log.append(("ROLLBACK", None))  # type: ignore[attr-defined]

    # When/Then
    assert OracleEngine.is_run_alive(conn, "APP", "ITEMS", "r1") is True  # type: ignore[arg-type]
    sql, params = conn.log[0]
    assert "FROM ldb_run_lock" in sql and sql.endswith("FOR UPDATE NOWAIT") and "rl_kind = 'L'" in sql
    assert params == ["APP", "ITEMS"]
    # And: nothing stays locked
    assert conn.log[-1][0] == "ROLLBACK"


def test_an_obtained_runner_lock_means_no_live_runner_and_is_released_at_once():
    # Spec: README > Run ILM > --mode STATUS — the probe never holds a lock: it rolls back right after taking it
    conn = _Conn(results={"FROM ldb_run_lock": [(1,)]})
    conn.rollback = lambda: conn.log.append(("ROLLBACK", None))  # type: ignore[attr-defined]

    assert OracleEngine.is_run_alive(conn, "APP", "ITEMS", "r1") is False  # type: ignore[arg-type]
    assert [text for text, _ in conn.log][-1] == "ROLLBACK"


@pytest.mark.parametrize(("run_id", "rows"), [("r1", []), ("S-20261022", [(1,)])])
def test_liveness_is_unknown_without_a_runner_lock_row_or_for_a_script_run(run_id, rows):
    # Spec: docs/concurrency-control.md — SCRIPT runs (id starting with S) take no lock; a missing lock row
    # gives nothing to try; both leave the decision to the session views
    conn = _Conn(results={"FROM ldb_run_lock": rows})
    conn.rollback = lambda: conn.log.append(("ROLLBACK", None))  # type: ignore[attr-defined]

    assert OracleEngine.is_run_alive(conn, "APP", "ITEMS", run_id) is None  # type: ignore[arg-type]
    if run_id.startswith("S"):
        assert [text for text, _ in conn.log] == []  # not even probed


def test_a_session_id_that_is_not_instance_sid_audsid_is_unknown_not_an_error():
    # Spec: README > Run ILM > --mode STATUS — the id is opaque to the core; an id this engine cannot read is
    # reported as unverifiable and nothing is queried
    conn = _Conn()
    activity = OracleEngine.get_session_activity(conn, "garbage", _FULL, 5.0)  # type: ignore[arg-type]
    assert activity.alive is None and conn.log == []
