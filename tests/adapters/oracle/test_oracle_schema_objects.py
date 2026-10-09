from types import SimpleNamespace

import pytest

from limitsdb.core.ldb_errors import ExecutionError
from limitsdb.db.ldb_engines import ColumnDefinition, IndexDefinition, TableDefinition
from limitsdb.db.oracle.ldb_engine_impl import OracleEngine


def test_column_type_maps_string_to_varchar2_with_length():
    # Spec: README > Bootstrap Database Objects — column types in control and history
    # tables follow Oracle DDL conventions: string → VARCHAR2(n), date → DATE, etc.
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


def test_unknown_column_type_raises():
    # Spec: README > Bootstrap Database Objects — unsupported column types are rejected
    # before DDL is emitted so schema errors surface at configuration time
    with pytest.raises(ValueError):
        OracleEngine.get_column_type(ColumnDefinition(name="z", data_type="unknown"))


# R2 exception: _column_needs_update is the sole entry point to the column-alteration
# decision; there is no public method that exposes just this predicate. The observable
# effect (ALTER vs no-op) is tested through ensure_table_structure, but that requires a
# full connection+cursor mock; this targeted test protects the decision logic directly.
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
def test_column_requires_alter_only_when_precision_or_length_increases(
    existing: ColumnDefinition, desired: ColumnDefinition, expected: bool
):
    # Spec: README > Bootstrap Database Objects — columns are only altered when the
    # desired size is larger than the existing one; shrinking a column is never attempted
    assert OracleEngine._column_needs_update(existing, desired) is expected


# R2 exception: _determine_process_date_column is the sole entry to the column-selection
# logic that drives index preparation; no public method exposes just this selection.
def test_process_date_column_is_identified_from_table_definition():
    # Spec: README > Run ILM — LDB_PROCESS_DATE is the preferred audit column name;
    # the adapter must locate it in the table to append it to non-PK indexes
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


# R2 exception: _prepare_desired_indexes is the sole entry to the index-composition rule
# that appends LDB_PROCESS_DATE to non-PK unique indexes; the observable result (indexes
# created with the extra column) requires a real DDL execution path to verify end-to-end.
def test_non_pk_unique_index_gets_process_date_column_appended():
    # Spec: README > Bootstrap Database Objects — unique indexes on history tables include
    # LDB_PROCESS_DATE so rows with the same business key but different process dates are
    # distinct; the PK index is left unchanged
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


# R2 exception: _choose_best_index_for_primary_key is the sole entry to the PK-selection
# algorithm; the selection criteria (not-null columns, uniqueness, cardinality) have no
# public wrapper and are only observable through a full DB metadata query round-trip.
def test_best_index_prefers_not_null_unique_columns_over_nullable_or_non_unique():
    # Spec: README > Run ILM — when a table has no declared PK, the adapter selects the
    # best available unique index: not-nullable columns take priority, then cardinality
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


# R2 exception: get_primary_key_columns calls _get_primary_key_info internally to query
# the Oracle data dictionary; patching it isolates each decision path without a real DB.
def test_existing_primary_key_is_used_as_key_columns(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM — declared PK columns are used as the row identity for
    # idempotent archiving; the adapter prefers them over any index
    cursor = SimpleNamespace(close=lambda: None)
    connection = SimpleNamespace(cursor=lambda: cursor)
    monkeypatch.setattr(OracleEngine, "_get_primary_key_info", lambda *args: ("ITEMS_PK", ("ID",), "ITEMS_PK"))

    assert OracleEngine.get_primary_key_columns(connection, "OWNER", "ITEMS", {}) == ("ID",)  # type: ignore[arg-type]


def test_best_unique_index_is_selected_when_no_primary_key_exists(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM — when no PK is declared, the adapter falls back to the
    # best available unique index using the not-null and cardinality heuristic
    class DummyCursor:
        def __init__(self):
            self.rows = []
            self.closed = False

        def execute(self, statement, **params):
            assert params == {"owner": "OWNER", "table_name": "ITEMS"}
            self.rows = [
                ("IDX_NULLABLE", "UNIQUE", "OPTIONAL_ID", "200"),
                ("IDX_STABLE", "UNIQUE", "ACCOUNT_ID", "100"),
                ("IDX_STABLE", "UNIQUE", "ITEM_ID", "100"),
            ]

        def __iter__(self):
            return iter(self.rows)

        def close(self):
            self.closed = True

    cursor = DummyCursor()
    connection = SimpleNamespace(cursor=lambda: cursor)
    monkeypatch.setattr(OracleEngine, "_get_primary_key_info", lambda *args: (None, (), None))
    columns = {
        "OPTIONAL_ID": ColumnDefinition(name="OPTIONAL_ID", data_type="number", nullable=True),
        "ACCOUNT_ID": ColumnDefinition(name="ACCOUNT_ID", data_type="number", nullable=False),
        "ITEM_ID": ColumnDefinition(name="ITEM_ID", data_type="number", nullable=False),
    }
    table_config = {"columns_metadata": columns, "metadata": object()}

    result = OracleEngine.get_primary_key_columns(
        connection,
        "OWNER",
        "ITEMS",
        table_config,  # type: ignore[arg-type]
    )

    assert result == ("ACCOUNT_ID", "ITEM_ID")
    assert cursor.closed is True


def test_empty_tuple_returned_when_no_index_qualifies(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM — when no suitable key exists the adapter returns an empty
    # tuple; the caller is responsible for handling the no-key case
    class DummyCursor:
        def __init__(self):
            self.closed = False

        def execute(self, statement, **params):
            pass

        def __iter__(self):
            return iter([])

        def close(self):
            self.closed = True

    cursor = DummyCursor()
    connection = SimpleNamespace(cursor=lambda: cursor)
    monkeypatch.setattr(OracleEngine, "_get_primary_key_info", lambda *args: (None, (), None))

    result = OracleEngine.get_primary_key_columns(
        connection,
        "OWNER",
        "ITEMS",
        {"columns_metadata": {}, "metadata": object()},  # type: ignore[arg-type]
    )

    assert result == ()
    assert cursor.closed is True


def test_cursor_is_closed_even_when_metadata_query_fails(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/exception-handling.md — resources are always released; cursor must be
    # closed even when the metadata query raises an unexpected error
    cause = RuntimeError("metadata unavailable")

    class DummyCursor:
        def __init__(self):
            self.closed = False

        def execute(self, statement, **params):
            raise cause

        def close(self):
            self.closed = True

    cursor = DummyCursor()
    connection = SimpleNamespace(cursor=lambda: cursor)
    monkeypatch.setattr(OracleEngine, "_get_primary_key_info", lambda *args: (None, (), None))

    with pytest.raises(RuntimeError) as caught:
        OracleEngine.get_primary_key_columns(
            connection,
            "OWNER",
            "ITEMS",
            {"columns_metadata": {}, "metadata": object()},  # type: ignore[arg-type]
        )

    assert caught.value is cause
    assert cursor.closed is True


def test_ensure_tables_creates_missing_table_and_commits(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Bootstrap Database Objects — ensure_tables creates any table that
    # does not exist yet and commits; it is idempotent: existing tables are skipped
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
    # Spec: docs/exception-handling.md — DDL failures are chained as ExecutionError;
    # the transaction is rolled back and the cursor is always closed
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


# R2 exception: _execute_ddl is the sole entry point to the ##SECRET## marker-stripping
# contract; ensure_tables patches it out and never exercises the sanitization path.
# Stripping is a security invariant: a leaked marker in the executed SQL could expose
# a secret. Cited per README > Secrets & Encryption: credentials must not appear in logs.
def test_ddl_secret_markers_are_stripped_before_statement_is_executed():
    # Spec: README > Secrets & Encryption — ##SECRET## markers in generated DDL are
    # replaced with the plain token before the statement reaches Oracle so that
    # credentials are never visible in database audit logs
    executed = []

    class DummyCursor:
        def __init__(self) -> None:
            self.connection = None

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
