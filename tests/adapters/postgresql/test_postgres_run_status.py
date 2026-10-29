import pytest

from limitsdb.core.ldb_params_config import Config
from limitsdb.db.postgresql.ldb_engine_impl import BlockAborted, PostgresEngine, build_program
from tests.adapters.postgresql.conftest import NOW, FakeConn, pg_error


def _program(copy: bool = True, check_refs: bool = True) -> str:
    header = {
        "v": 1,
        "owner": "source",
        "table": "items",
        "action": "SOURCE_ILM",
        "date": "20261028",
        "refs": ["source.orders"],
        "check_refs": check_refs,
        "copy": copy,
        "dblink": "ldb_hist",
    }
    sections = {"chunk": "DO $ldb$ chunk $ldb$", "count": "DO $ldb$ count $ldb$"}
    if copy:
        sections["connect"] = "DO $ldb$ connect $ldb$"
    return build_program(header, sections)


def _responder(chunks: list[int], abort_on: dict[str, tuple[int, str]] | None = None, fail_on: str | None = None):
    pending = list(chunks)
    abort_on = abort_on or {}

    def respond(conn: FakeConn, sql: str, params):
        if "localtimestamp" in sql:
            return (NOW,)
        if "check_save_status" in sql:
            status = params[4]
            if status in abort_on and params[12] is None:
                return abort_on[status]
            return (None, None)
        if fail_on and fail_on in sql:
            raise pg_error("22012", "division by zero")
        if sql.startswith("DO") and ("chunk" in sql or "count" in sql):
            conn.notices.append({b"M": f"ldb_rows={pending.pop(0)}".encode()})
        return (1,)

    return respond


def _statuses(conn: FakeConn) -> list[str]:
    return [params[4] for sql, params in conn.log if "check_save_status" in sql]


def test_execute_runs_chunks_until_empty_and_commits_each_phase():
    # Spec: docs/ai-state/DECISIONS.md (DEC-048) — the client drives the phases and commits between them
    conn = FakeConn(_responder([5, 3, 0]))

    PostgresEngine.sql_block_run(conn, _program(), "EXECUTE", "run-1")

    assert _statuses(conn) == ["TSTART", "CSTART", "CEND", "CSTART", "CEND", "CSTART", "TEND"]
    ends = [params for sql, params in conn.log if "check_save_status" in sql and params[4] == "CEND"]
    assert [params[9] for params in ends] == [5, 3]
    assert conn.statements().count("COMMIT") >= 3
    assert any("dblink_disconnect" in sql for sql in conn.statements())


def test_notice_bytes_are_decoded_and_cleared_between_chunks():
    # Spec: docs/ai-state/DECISIONS.md (DEC-048) — pg8000 delivers notices as bytes; a stale notice must not count twice
    conn = FakeConn(_responder([2, 0]))
    conn.notices.append({b"M": b"ldb_rows=99"})

    PostgresEngine.sql_block_run(conn, _program(copy=False), "EXECUTE", "run-1")

    ends = [params for sql, params in conn.log if "check_save_status" in sql and params[4] == "CEND"]
    assert [params[9] for params in ends] == [2]


def test_preview_counts_once_and_prefixes_the_statuses():
    # Spec: README > Run ILM > PREVIEW — preview records P-prefixed statuses and never moves rows
    conn = FakeConn(_responder([7]))

    PostgresEngine.sql_block_run(conn, _program(), "PREVIEW", "run-1")

    assert _statuses(conn) == ["PTSTART", "PCSTART", "PCEND", "PTEND"]
    assert not any("dblink" in sql for sql in conn.statements())


def test_preview_of_an_empty_table_ends_without_a_chunk_end():
    # Spec: README > Run ILM — a table with nothing to do records the table start and end only
    conn = FakeConn(_responder([0]))

    PostgresEngine.sql_block_run(conn, _program(), "PREVIEW", "run-1")

    assert _statuses(conn) == ["PTSTART", "PCSTART", "PTEND"]


def test_status_refusal_rolls_back_logs_the_error_and_raises_block_aborted():
    # Spec: docs/concurrency-control.md — a table owned by another run is refused with its code and left untouched
    conn = FakeConn(_responder([1], abort_on={"CSTART": (20001, "already running")}))

    with pytest.raises(BlockAborted) as caught:
        PostgresEngine.sql_block_run(conn, _program(), "EXECUTE", "run-1")

    detail = caught.value.args[0]
    assert detail.code == 20001 and detail.message == "already running"
    assert "ROLLBACK" in conn.statements()
    error_call = [params for sql, params in conn.log if "check_save_status" in sql and params[4] == "ERROR"]
    assert error_call and error_call[0][12] == 20001


def test_already_processed_table_is_swallowed_like_the_oracle_adapter():
    # Spec: README > Run ILM — a table already finished for the process date is skipped without failing the run
    conn = FakeConn(_responder([1], abort_on={"TSTART": (20003, "already processed")}))

    PostgresEngine.sql_block_run(conn, _program(), "EXECUTE", "run-1")

    assert "TEND" not in _statuses(conn)


def test_unexpected_error_is_recorded_and_re_raised():
    # Spec: README > Run ILM — a failing block rolls back, records ERROR and surfaces the original error
    conn = FakeConn(_responder([1], fail_on="chunk"))

    with pytest.raises(Exception, match="division by zero"):
        PostgresEngine.sql_block_run(conn, _program(copy=False), "EXECUTE", "run-1")

    assert "ROLLBACK" in conn.statements()
    assert "ERROR" in _statuses(conn)


def test_custom_sqlstate_from_the_database_becomes_block_aborted_with_its_code():
    # Spec: docs/ai-state/DECISIONS.md (DEC-048) — SQLSTATE LDnnn maps to code 20000+nnn, as the runner expects
    def respond(conn: FakeConn, sql: str, params):
        if sql.startswith("SELECT check_referencing_tables"):
            raise pg_error("LD004", "referencing table not processed")
        return _responder([0])(conn, sql, params)

    conn = FakeConn(respond)

    with pytest.raises(BlockAborted) as caught:
        PostgresEngine.sql_block_run(conn, _program(), "EXECUTE", "run-1")

    assert caught.value.args[0].code == 20004


def test_invalid_mode_is_refused_before_touching_the_database():
    # Spec: README > Run ILM — only EXECUTE and PREVIEW run a block
    conn = FakeConn()

    with pytest.raises(BlockAborted):
        PostgresEngine.sql_block_run(conn, _program(), "PLAN", "run-1")

    assert conn.log == []


def test_save_error_status_writes_an_error_row_and_commits():
    # Spec: README > Run ILM — failures outside the block are recorded as ERROR for the table
    conn = FakeConn(_responder([]))
    config = Config(
        schema="s",
        action="SOURCE_ILM",
        mode="EXECUTE",
        db_engine="postgresql",
        source_dsn="host/db",
        source_username="source",
        source_password="secret",
    )

    PostgresEngine.save_error_status(conn, config, "source", "items", "20261028", NOW, "boom", "block", 4, "run-1")

    assert _statuses(conn) == ["ERROR"]
    assert conn.statements()[-1] == "COMMIT"


def test_control_queries_quote_the_control_objects():
    # Spec: docs/ai-state/DECISIONS.md (DEC-048) — control tables are quoted upper case
    conn = FakeConn(lambda c, sql, params: [])

    PostgresEngine.get_status(conn, "20261028")

    assert 'FROM "LDB_CTL"' in conn.statements()[0] and '"CTL_STATUS"' in conn.statements()[0]
