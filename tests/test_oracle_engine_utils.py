import pytest

from limitsdb.core.ldb_errors import DatabaseConnectionError, ExecutionError
from limitsdb.core.ldb_params_config import Config
from limitsdb.db.ldb_engines import ColumnDefinition, IndexDefinition, TableDefinition
from limitsdb.db.oracle import ldb_engine_impl
from limitsdb.db.oracle.ldb_engine_impl import OracleEngine


def test_connection_failure_is_chained_domain_error(monkeypatch: pytest.MonkeyPatch):
    cause = RuntimeError("driver failure")
    monkeypatch.setattr(ldb_engine_impl.oracledb, "connect", lambda **kwargs: (_ for _ in ()).throw(cause))
    config = Config(schema="s", action="SOURCE_ILM", source_username="user", source_password="secret", source_dsn="dsn")

    with pytest.raises(DatabaseConnectionError) as caught:
        OracleEngine.get_connection(config)

    assert caught.value.__cause__ is cause
    assert "secret" not in str(caught.value)


def test_rows_processed_failure_is_not_reported_as_zero():
    cause = RuntimeError("query failure")

    class DummyConnection:
        def cursor(self):
            raise cause

    with pytest.raises(ExecutionError) as caught:
        OracleEngine.get_rows_processed(DummyConnection(), "OWNER", "TABLE", "20261006")  # type: ignore[arg-type]

    assert caught.value.__cause__ is cause


def test_get_date_condition_renders_expected_predicate():
    assert (
        OracleEngine.get_date_condition("TRUNC(process_date)", 6)
        == "(TRUNC(process_date) < add_months(l_process_date,-6))"
    )


def test_identifier_helpers_strip_quotes_and_uppercase():
    assert OracleEngine.get_identifier_str('"MixedCase"') == "MixedCase"
    assert OracleEngine.get_identifier_str("already_upper") == "ALREADY_UPPER"


def test_identifier_formatting_and_quoting():
    assert OracleEngine._format_identifier("SIMPLE") == "simple"
    assert OracleEngine._format_identifier("With Space") == '"With Space"'
    assert OracleEngine._quote('Needs"Quote') == '"Needs""Quote"'
    assert OracleEngine._quote_literal("Mc'Dowell") == "'Mc''Dowell'"


def test_ldb_columns_expressions_return_expected_defaults():
    assert OracleEngine.get_ldb_columns_expressions() == ("l_process_date", "sysdate")


def test_generated_source_insert_does_not_duplicate_limitsdb_columns():
    config = Config(
        schema="s",
        action="SOURCE_ILM",
        mode="EXECUTE",
        source_dsn="dsn",
        source_username="source",
        source_password="secret",
    )
    table_config = {
        "conds": [
            {
                "source_owner": "SOURCE",
                "history_owner": "HISTORY",
                "table_name": "ITEMS",
                "history_hint_expr": None,
                "hint_expr": "",
                "has_lob_columns": "N",
            }
        ],
        "other_columns": [
            {
                "name": "LDB_PROCESS_DATE",
                "expr": "l_process_date",
                "metadata": ColumnDefinition(name="LDB_PROCESS_DATE", data_type="date"),
            },
            {
                "name": "LDB_INSERT_DATE",
                "expr": "sysdate",
                "metadata": ColumnDefinition(name="LDB_INSERT_DATE", data_type="date"),
            },
        ],
        "referencing_tables": [],
        "query_expr": "from source.items A\nwhere A.created_at < l_process_date",
        "table_columns": ["ID", "CREATED_AT"],
        "months_keep_history_max": 3,
    }

    block = OracleEngine.generate_sql_block(config, table_config, "20261007")

    assert "A.*, l_process_date AS ldb_process_date, sysdate AS ldb_insert_date" in block
    assert "(ID, CREATED_AT, LDB_PROCESS_DATE, LDB_INSERT_DATE)" in block
    assert "values (r_rec(i).ID, r_rec(i).CREATED_AT, r_rec(i).LDB_PROCESS_DATE, r_rec(i).LDB_INSERT_DATE)" in block
    assert "LDB_PROCESS_DATE, LDB_INSERT_DATE, LDB_PROCESS_DATE" not in block


def test_register_and_fetch_connection_env_round_trip():
    class DummyConnection:
        pass

    conn = DummyConnection()
    OracleEngine._register_connection_env(conn, "SRC")

    try:
        assert OracleEngine._get_connection_env(conn) == "SRC"
        OracleEngine._register_connection_env(conn, None)
        assert OracleEngine._get_connection_env(conn) is None
    finally:
        OracleEngine._connection_envs.pop(id(conn), None)


def test_execute_ddl_strips_private_markers_before_invocation():
    executed = []

    class DummyCursor:
        def __init__(self) -> None:
            self.connection = None  # populated by wrapping below

        def execute(self, statement: str) -> None:  # type: ignore[override]
            executed.append(statement)

    class DummyConnection:
        pass

    conn = DummyConnection()
    cursor = DummyCursor()
    cursor.connection = conn  # type: ignore[assignment]
    OracleEngine._register_connection_env(conn, "SRC")

    try:
        OracleEngine._execute_ddl(conn, cursor, "CREATE USER ##SECRET## IDENTIFIED BY password")
        assert executed == ["CREATE USER SECRET IDENTIFIED BY password"]
    finally:
        OracleEngine._register_connection_env(conn, None)


def test_get_identifiers_from_expression_handles_mixed_cases_and_quotes():
    expression = '@simple + @"Quoted" + @"Escaped""Quote" + @lower_case'
    identifiers = OracleEngine.get_identifiers_from_expression(expression)

    assert identifiers == {
        "SIMPLE",
        "Quoted",
        'Escaped"Quote',
        "LOWER_CASE",
    }


def test_get_column_type_variants_and_errors():
    assert OracleEngine.get_column_type(ColumnDefinition(name="a", data_type="string", length=10)) == "VARCHAR2(10)"
    assert OracleEngine.get_column_type(ColumnDefinition(name="b", data_type="char", length=None)) == "CHAR(1)"
    assert (
        OracleEngine.get_column_type(ColumnDefinition(name="c", data_type="number", precision=8, scale=2))
        == "NUMBER(8,2)"
    )
    assert (
        OracleEngine.get_column_type(ColumnDefinition(name="d", data_type="number", precision=4, scale=None))
        == "NUMBER(4)"
    )
    assert OracleEngine.get_column_type(ColumnDefinition(name="e", data_type="integer")) == "NUMBER(10)"
    assert OracleEngine.get_column_type(ColumnDefinition(name="f", data_type="date")) == "DATE"
    assert OracleEngine.get_column_type(ColumnDefinition(name="g", data_type="clob")) == "CLOB"

    with pytest.raises(ValueError):
        OracleEngine.get_column_type(ColumnDefinition(name="z", data_type="unknown"))


@pytest.mark.parametrize(
    "existing, desired, expected",
    [
        (
            ColumnDefinition(name="col", data_type="varchar2", length=10),
            ColumnDefinition(name="col", data_type="varchar2", length=20),
            True,
        ),
        (
            ColumnDefinition(name="col", data_type="number", precision=5, scale=1),
            ColumnDefinition(name="col", data_type="number", precision=6, scale=2),
            True,
        ),
        (
            ColumnDefinition(name="col", data_type="integer", precision=6),
            ColumnDefinition(name="col", data_type="integer", precision=8),
            True,
        ),
        (
            ColumnDefinition(name="col", data_type="varchar2", length=30),
            ColumnDefinition(name="col", data_type="varchar2", length=20),
            False,
        ),
        (
            ColumnDefinition(name="col", data_type="number", precision=8, scale=3),
            ColumnDefinition(name="col", data_type="number", precision=8, scale=2),
            False,
        ),
    ],
)
def test_column_needs_update(existing: ColumnDefinition, desired: ColumnDefinition, expected: bool):
    assert OracleEngine._column_needs_update(existing, desired) is expected


def test_determine_process_date_column_returns_preferred_name():
    table = TableDefinition(
        owner="TEST",
        name="TABLE",
        columns=(
            ColumnDefinition(name="ID", data_type="number"),
            ColumnDefinition(name="LDB_PROCESS_DATE", data_type="date"),
            ColumnDefinition(name="OTHER", data_type="varchar2"),
        ),
    )

    assert OracleEngine._determine_process_date_column(table) == "LDB_PROCESS_DATE"


def test_prepare_desired_indexes_appends_process_date_when_missing():
    table = TableDefinition(
        owner="TEST",
        name="TABLE",
        columns=(
            ColumnDefinition(name="ID", data_type="number"),
            ColumnDefinition(name="LDB_PROCESS_DATE", data_type="date"),
            ColumnDefinition(name="OTHER", data_type="varchar2"),
        ),
        primary_key=("ID",),
        indexes=(
            IndexDefinition(name="IDX_PK", columns=("ID",)),
            IndexDefinition(name="IDX_OTHER", columns=("OTHER",), unique=True),
        ),
    )

    desired_indexes = OracleEngine._prepare_desired_indexes(table)

    assert desired_indexes == {
        "IDX_PK": (("ID",), False),
        "IDX_OTHER": (("OTHER", "LDB_PROCESS_DATE"), True),
    }


def test_choose_best_index_prioritizes_not_null_unique_and_distinct_keys():
    column_metadata = {
        "A": ColumnDefinition(name="A", data_type="number", nullable=False),
        "B": ColumnDefinition(name="B", data_type="number", nullable=False),
        "C": ColumnDefinition(name="C", data_type="number", nullable=True),
        "D": ColumnDefinition(name="D", data_type="number", nullable=False),
    }
    indexes = {
        "IDX_UNIQUE_NOT_NULL": {"columns": ("A", "B"), "unique": True, "distinct_keys": 50},
        "IDX_UNIQUE_NULLABLE": {"columns": ("C",), "unique": True, "distinct_keys": 200},
        "IDX_NON_UNIQUE": {"columns": ("D",), "unique": False, "distinct_keys": 300},
    }

    assert OracleEngine._choose_best_index_for_primary_key(indexes, column_metadata) == ("A", "B")


def test_ensure_tables_commits_changes_and_is_idempotent(monkeypatch: pytest.MonkeyPatch):
    class DummyCursor:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class DummyConnection:
        def __init__(self) -> None:
            self.cursor_instance = DummyCursor()
            self.commits = 0
            self.rollbacks = 0

        def cursor(self):
            return self.cursor_instance

        def commit(self) -> None:
            self.commits += 1

        def rollback(self) -> None:
            self.rollbacks += 1

    conn = DummyConnection()
    exists = iter((False, True))
    executed = []
    monkeypatch.setattr(OracleEngine, "_table_exists", lambda cursor, owner, table_name: next(exists))
    monkeypatch.setattr(OracleEngine, "_execute_ddl", lambda connection, cursor, statement: executed.append(statement))
    table = TableDefinition(owner="OWNER", name="ITEMS", columns=(ColumnDefinition(name="ID", data_type="integer"),))

    first = OracleEngine.ensure_tables(conn, (table,))  # type: ignore[arg-type]
    second = OracleEngine.ensure_tables(conn, (table,))  # type: ignore[arg-type]

    assert first == ["owner.items"]
    assert second == []
    assert executed == ["CREATE TABLE owner.items (\n        id NUMBER(10)\n    )"]
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert conn.cursor_instance.closed is True


def test_ensure_tables_rolls_back_and_chains_ddl_failure(monkeypatch: pytest.MonkeyPatch):
    cause = RuntimeError("DDL failed")

    class DummyCursor:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class DummyConnection:
        def __init__(self) -> None:
            self.cursor_instance = DummyCursor()
            self.commits = 0
            self.rollbacks = 0

        def cursor(self):
            return self.cursor_instance

        def commit(self) -> None:
            self.commits += 1

        def rollback(self) -> None:
            self.rollbacks += 1

    conn = DummyConnection()
    monkeypatch.setattr(OracleEngine, "_table_exists", lambda cursor, owner, table_name: False)
    monkeypatch.setattr(
        OracleEngine, "_execute_ddl", lambda connection, cursor, statement: (_ for _ in ()).throw(cause)
    )
    table = TableDefinition(owner="OWNER", name="ITEMS", columns=(ColumnDefinition(name="ID", data_type="integer"),))

    with pytest.raises(ExecutionError) as caught:
        OracleEngine.ensure_tables(conn, (table,))  # type: ignore[arg-type]

    assert caught.value.__cause__ is cause
    assert conn.commits == 0
    assert conn.rollbacks == 1
    assert conn.cursor_instance.closed is True
