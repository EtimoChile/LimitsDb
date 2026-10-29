import pytest

from limitsdb.core.ldb_errors import ExecutionError, RunnerBusyError
from limitsdb.db.ldb_engines import MonitoringAccess
from limitsdb.db.postgresql.ldb_engine_impl import PostgresEngine
from tests.adapters.postgresql.conftest import NOW, FakeConn


def test_runner_locks_tables_in_sorted_order_then_publishes_its_keys():
    # Spec: docs/concurrency-control.md > Layer 1 — anchors are created by the key connection, advisory locks are
    # taken in a fixed order on the lock connection, and only then are the keys published
    # Given: two connections, tables given out of order
    lock_conn = FakeConn(lambda c, sql, params: (True,))
    key_conn = FakeConn()

    # When: the runner acquires its locks
    PostgresEngine.acquire_run_locks(lock_conn, key_conn, [("a", "t2"), ("a", "t1")], "run-1", "EXECUTE", "host", 42)

    # Then: locks follow the sorted order and the keys are updated after the anchors
    lock_params = [params for sql, params in lock_conn.log if "pg_try_advisory_lock" in sql]
    assert lock_params == [
        list(PostgresEngine._lock_key("a", "t1")),
        list(PostgresEngine._lock_key("a", "t2")),
    ]
    statements = key_conn.statements()
    assert statements[0].startswith('INSERT INTO "LDB_RUN_LOCK"') and "ON CONFLICT DO NOTHING" in statements[0]
    assert any(sql.startswith('UPDATE "LDB_RUN_LOCK"') for sql in statements)
    assert statements.index("COMMIT") < statements.index(next(s for s in statements if s.startswith("UPDATE")))


def test_a_held_lock_releases_everything_and_reports_the_holder():
    # Spec: docs/concurrency-control.md — a busy table stops the run before anything starts, naming the holder
    def lock_respond(conn: FakeConn, sql: str, params):
        return (False,) if "pg_try_advisory_lock" in sql else (1,)

    def key_respond(conn: FakeConn, sql: str, params):
        if sql.startswith("SELECT"):
            return ("other-run", "EXECUTE", "otherhost", 7, NOW)
        return (1,)

    lock_conn, key_conn = FakeConn(lock_respond), FakeConn(key_respond)

    with pytest.raises(RunnerBusyError, match="other-run, EXECUTE, host otherhost, pid 7"):
        PostgresEngine.acquire_run_locks(lock_conn, key_conn, [("a", "t1")], "run-1", "EXECUTE", "host", 42)

    assert any("pg_advisory_unlock_all" in sql for sql in lock_conn.statements())


def test_lock_keys_are_signed_32_bit_integers():
    # Spec: docs/concurrency-control.md — pg_try_advisory_lock(int4, int4) needs keys inside the int4 range
    for key in PostgresEngine._lock_key("some_owner", "some_table"):
        assert -(2**31) <= key < 2**31


def test_lost_lock_connection_is_reported():
    # Spec: docs/concurrency-control.md — the runner stops when its lock connection is gone
    def broken(conn: FakeConn, sql: str, params):
        raise ConnectionError("gone")

    with pytest.raises(ExecutionError):
        PostgresEngine.check_run_locks(FakeConn(broken))


def test_release_unlocks_all_advisory_locks():
    # Spec: docs/concurrency-control.md — releasing the runner locks drops every advisory lock of the session
    conn = FakeConn()

    PostgresEngine.release_run_locks(conn)

    assert "SELECT pg_advisory_unlock_all()" in conn.statements()


def test_run_liveness_follows_the_advisory_lock_and_the_key_row():
    # Spec: docs/concurrency-control.md — a run is dead when its table lock is free, alive when held under its run id
    def free(conn: FakeConn, sql: str, params):
        return (True,)

    def held(conn: FakeConn, sql: str, params):
        return (False,) if "pg_try_advisory_lock" in sql else ("run-1",)

    assert PostgresEngine.is_run_alive(FakeConn(free), "a", "t", "run-1") is False
    assert PostgresEngine.is_run_alive(FakeConn(held), "a", "t", "run-1") is True
    assert PostgresEngine.is_run_alive(FakeConn(held), "a", "t", "run-2") is False
    assert PostgresEngine.is_run_alive(FakeConn(held), "a", "t", "Sscript") is None


def test_monitoring_offers_the_session_view_only():
    # Spec: docs/ai-state/DECISIONS.md (DEC-048) — pg_stat_activity gives liveness; no progress or wait history in 9.5
    assert PostgresEngine.check_monitoring_access(FakeConn(), "grantee") == MonitoringAccess(
        session=True, progress=False, waits=False
    )


def test_session_activity_reports_a_live_session_waiting_on_a_lock():
    # Spec: docs/ai-state/DECISIONS.md (DEC-048) — the waiting flag of pg_stat_activity is the current wait
    conn = FakeConn(lambda c, sql, params: (True, 30.0))
    access = MonitoringAccess(session=True, progress=False, waits=False)

    activity = PostgresEngine.get_session_activity(conn, "123.1700000000", access, 5.0)

    assert activity.alive is True and activity.current_wait is not None


def test_session_activity_reports_a_vanished_session_as_dead():
    # Spec: docs/ai-state/DECISIONS.md (DEC-048) — no row in pg_stat_activity means the session ended
    conn = FakeConn(lambda c, sql, params: None)
    access = MonitoringAccess(session=True, progress=False, waits=False)

    assert PostgresEngine.get_session_activity(conn, "123.1700000000", access, 5.0).alive is False
    assert PostgresEngine.get_session_activity(conn, "not-an-id", access, 5.0).alive is None
