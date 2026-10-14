from types import SimpleNamespace

import oracledb
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


def test_failed_ddl_statement_is_reported_and_remaining_statements_still_run():
    # Spec: README > Run ILM > Admin credentials in ldb-run — DDL applied with runtime
    # credentials continues after a rejected statement and reports each failure with
    # its Oracle error, so the caller can write the failed statements for a DBA.
    executed = []

    class DummyCursor:
        def execute(self, statement: str) -> None:
            executed.append(statement)
            if "BAD" in statement:
                raise oracledb.DatabaseError("ORA-01031: insufficient privileges\nHelp: ...")

        def close(self) -> None:
            pass

    class DummyConnection:
        def cursor(self) -> DummyCursor:
            return DummyCursor()

    # When
    failures = OracleEngine.execute_ddl_statements(
        DummyConnection(),  # type: ignore[arg-type]
        ["ALTER TABLE A ADD (X NUMBER)", "ALTER TABLE BAD ADD (X NUMBER)", "ALTER TABLE C ADD (X NUMBER)"],
    )

    # Then: all three were attempted and only the rejected one is reported
    assert len(executed) == 3
    assert [statement for statement, _ in failures] == ["ALTER TABLE BAD ADD (X NUMBER)"]
    assert "ORA-01031" in failures[0][1]
    assert "\n" not in failures[0][1]


def _history_pk_replacement_script(
    monkeypatch: pytest.MonkeyPatch,
    desired_pk: tuple[str, ...] | None,
    existing_pk_column: str = "OLD_COL",
    extra_index: str | None = None,
) -> list[str]:
    """Generate the structure script for a table whose existing PK is on existing_pk_column."""

    class ScriptedCursor:
        def __init__(self) -> None:
            self._rows: list[tuple] = []

        def execute(self, sql: str, params=None, **kwargs) -> None:
            if "FROM all_tables" in sql:
                self._rows = [(1,)]
            elif "FROM all_constraints" in sql:
                self._rows = [("ITEMS_PK", "ITEMS_PK")]
            elif "FROM all_cons_columns" in sql:
                self._rows = [(existing_pk_column,)]
            elif "FROM all_indexes" in sql:
                self._rows = [("ITEMS_PK", "UNIQUE", existing_pk_column)]
                if extra_index:
                    self._rows.append((extra_index, "NONUNIQUE", "ID"))
            else:
                self._rows = []

        def fetchone(self):
            return self._rows[0] if self._rows else None

        def __iter__(self):
            return iter(self._rows)

        def close(self) -> None:
            pass

    class DummyConnection:
        def cursor(self) -> ScriptedCursor:
            return ScriptedCursor()

    column = ColumnDefinition(name="ID", data_type="integer")
    monkeypatch.setattr(OracleEngine, "get_table_columns", lambda conn, owner, name: (["ID"], {"ID": column}))
    monkeypatch.setattr(OracleEngine, "_column_needs_update", lambda existing, desired: False)
    table = TableDefinition(owner="OWNER", name="ITEMS", columns=(column,), primary_key=desired_pk)
    return OracleEngine.generate_table_structure_script(DummyConnection(), table, {})  # type: ignore[arg-type]


def test_replacing_a_primary_key_in_a_ddl_script_drops_its_index_in_the_same_statement(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: README > Run ILM > Admin credentials in ldb-run — the history DDL left for a DBA
    # must run to the end: the index Oracle created for the old PK is removed together with
    # the constraint, so a separate DROP INDEX would fail with ORA-01418 and stop the script.

    # Given: an existing PK on OLD_COL and a desired PK on ID

    # When: the structure script is generated
    statements = _history_pk_replacement_script(monkeypatch, ("ID",))

    # Then: one statement drops the PK with its index, and no standalone DROP INDEX follows
    assert statements[0] == "ALTER TABLE owner.items DROP PRIMARY KEY DROP INDEX"
    assert statements[1].startswith("ALTER TABLE owner.items ADD CONSTRAINT items_pk PRIMARY KEY")
    assert not any(statement.startswith("DROP INDEX") for statement in statements)


def test_removing_an_unexpected_primary_key_in_a_ddl_script_leaves_no_standalone_drop_index(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: README > Run ILM > Admin credentials in ldb-run — same ORA-01418 hazard when the
    # table must have no primary key at all.

    # Given: an existing PK and a table definition without one

    # When: the structure script is generated
    statements = _history_pk_replacement_script(monkeypatch, None)

    # Then: the PK and its index go in one statement and nothing else is emitted
    assert statements == ["ALTER TABLE owner.items DROP PRIMARY KEY DROP INDEX"]


def test_unchanged_primary_key_index_is_kept_while_unrelated_indexes_are_dropped(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: README > Run ILM > Admin credentials in ldb-run — the index backing a primary
    # key that already matches the definition is not an unmanaged index; indexes unrelated
    # to the definition and to the PK are still dropped.

    # Given: a PK on ID that already matches, its backing index ITEMS_PK, and a stray index
    # When: the structure script is generated
    statements = _history_pk_replacement_script(monkeypatch, ("ID",), existing_pk_column="ID", extra_index="STRAY_IDX")

    # Then: only the stray index is dropped; the PK and its index are untouched
    assert statements == ["DROP INDEX owner.stray_idx"]


@pytest.mark.parametrize(("admin_connection", "prefix"), [(False, "all"), (True, "dba")], ids=["runtime", "admin"])
def test_primary_key_metadata_is_read_from_the_view_set_the_connection_can_access(
    monkeypatch: pytest.MonkeyPatch, admin_connection: bool, prefix: str
):
    # Spec: README > Run ILM > Admin credentials in ldb-run — without admin credentials the
    # history schema is inspected over the runtime connection, which can only read all_* views;
    # admin connections keep using dba_* views, with the same query text.
    # Given: a runtime or an admin connection whose table has no primary key constraint
    executed: list[str] = []

    class RecordingCursor:
        def __init__(self, connection) -> None:
            self.connection = connection

        def execute(self, sql: str, *args, **kwargs) -> None:
            executed.append(sql)

        def fetchone(self):
            return None

        def __iter__(self):
            return iter(())

        def close(self) -> None:
            pass

    class DummyConnection:
        def cursor(self) -> RecordingCursor:
            return RecordingCursor(self)

    connection = DummyConnection()
    if admin_connection:
        monkeypatch.setattr(OracleEngine, "_admin_connections", {id(connection)})

    # When: the primary key columns are requested
    OracleEngine.get_primary_key_columns(connection, "OWNER", "ITEMS", {})  # type: ignore[arg-type]

    # Then: constraint and index metadata came only from the matching view set
    other = "dba" if prefix == "all" else "all"
    assert any(f"{prefix}_constraints" in sql for sql in executed)
    assert any(f"{prefix}_indexes" in sql for sql in executed)
    assert not any(f"{other}_constraints" in sql or f"{other}_indexes" in sql for sql in executed)


def test_structure_script_without_a_connection_creates_every_object(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM > --mode SCRIPT — with history_baseline EMPTY nothing is read from
    # the database: the script creates the table, its primary key and its indexes.
    # Given: a table definition with a primary key and no connection
    column = ColumnDefinition(name="ID", data_type="integer")
    table = TableDefinition(owner="OWNER", name="ITEMS", columns=(column,), primary_key=("ID",))

    # When: the structure script is generated without a connection
    statements = OracleEngine.generate_table_structure_script(None, table, {})

    # Then: the table and its primary key are created
    assert statements[0].startswith("CREATE TABLE owner.items")
    assert statements[1] == "ALTER TABLE owner.items ADD CONSTRAINT items_pk PRIMARY KEY (id)"


def test_structure_script_with_a_reference_reads_the_reference_and_emits_ddl_for_the_table(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: README > Run ILM > --mode SCRIPT — with history_baseline SOURCE the current structure is
    # that of the source table, and the DDL targets the history table.
    # Given: a reference table (SRC.ITEMS) with column ID only, and a history table that adds LDB_PROCESS_DATE
    lookups: list[tuple] = []

    class ReferenceCursor:
        def __init__(self) -> None:
            self._rows: list[tuple] = []

        def execute(self, sql: str, params=None, **kwargs) -> None:
            lookups.append(tuple(params) if params else (kwargs.get("owner"), kwargs.get("table_name")))
            self._rows = [(1,)] if "FROM all_tables" in sql else []

        def fetchone(self):
            return self._rows[0] if self._rows else None

        def __iter__(self):
            return iter(self._rows)

        def close(self) -> None:
            pass

    class DummyConnection:
        def cursor(self) -> ReferenceCursor:
            return ReferenceCursor()

    id_column = ColumnDefinition(name="ID", data_type="integer")
    date_column = ColumnDefinition(name="LDB_PROCESS_DATE", data_type="date")
    monkeypatch.setattr(OracleEngine, "get_table_columns", lambda conn, owner, name: ([], {"ID": id_column}))
    monkeypatch.setattr(OracleEngine, "_column_needs_update", lambda existing, desired: False)
    table = TableDefinition(owner="HIST", name="ITEMS", columns=(id_column, date_column), primary_key=None)

    # When: the structure script is generated against the reference
    statements = OracleEngine.generate_table_structure_script(
        DummyConnection(),  # type: ignore[arg-type]
        table,
        {},
        reference=("SRC", "ITEMS"),
    )

    # Then: every dictionary lookup targeted the reference, and the DDL targets the history table
    assert lookups and all(lookup == ("SRC", "ITEMS") for lookup in lookups)
    assert len(statements) == 1 and statements[0].startswith("ALTER TABLE hist.items ADD (")
