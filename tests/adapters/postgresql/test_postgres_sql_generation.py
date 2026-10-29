import pytest

from limitsdb.core.ldb_params_config import Config
from limitsdb.db.ldb_engines import ColumnDefinition
from limitsdb.db.postgresql.ldb_engine_impl import (
    PostgresEngine,
    control,
    format_identifier,
    parse_program,
    supporting_functions,
)


def _table_config(**overrides):
    table_config = {
        "conds": [
            {"source_owner": "source", "history_owner": "history", "table_name": "items"},
        ],
        "other_columns": [
            {"name": "ldb_process_date", "expr": "l_process_date", "metadata": None},
            {"name": "ldb_insert_date", "expr": "localtimestamp(0)", "metadata": None},
        ],
        "referencing_tables": [("source", "orders")],
        "query_expr": "from source.items A\nwhere A.created_at < l_process_date",
        "table_columns": ["id", "created_at"],
        "months_keep_history_max": 3,
    }
    table_config.update(overrides)
    return table_config


def _config(action="SOURCE_ILM", **kwargs):
    return Config(
        schema="s",
        action=action,
        mode="EXECUTE",
        db_engine="postgresql",
        source_dsn="host/db",
        source_username="source",
        source_password="secret",
        **kwargs,
    )


def test_date_predicate_subtracts_an_interval_from_the_process_date():
    # Spec: README > Run ILM — retention is a date cut-off; PostgreSQL renders it as an interval on l_process_date
    assert (
        PostgresEngine.get_date_condition("date_trunc('day', process_date)", 6)
        == "(date_trunc('day', process_date) < (l_process_date - interval '6 months'))"
    )


def test_identifiers_are_bare_only_when_postgresql_would_fold_them_to_themselves():
    # Spec: docs/configuration-contract.md — catalog names are kept exact: bare when lower case and not reserved
    assert format_identifier("items") == "items"
    assert format_identifier("Items") == '"Items"'
    assert format_identifier("order") == '"order"'
    assert format_identifier('we"ird') == '"we""ird"'


@pytest.mark.parametrize("name", ["LDB_DATE_B", "LDB_IS_ORPHAN", "LDB_PROCESS_DATE", "LDB_INSERT_DATE"])
def test_runner_derived_columns_are_folded_to_lower_case(name):
    # Spec: docs/configuration-contract.md — derived columns are written unquoted by the runner, so they fold in PostgreSQL
    assert format_identifier(name) == name.lower()
    assert format_identifier("Ldb_Date_B") == '"Ldb_Date_B"'


def test_filter_columns_of_referencing_tables_are_folded_with_their_alias_suffix():
    # Spec: docs/configuration-contract.md — the runner appends the upper case alias (history_state_B); the column is
    # created and read in the default case
    assert format_identifier("history_state_B") == "history_state_b"
    assert format_identifier("MixedCase") == '"MixedCase"'


def test_identifier_string_is_the_catalog_form_of_the_written_name():
    # Spec: README > Run ILM — quoted names keep their case, bare names are folded to lower case
    assert PostgresEngine.get_identifier_str('"MixedCase"') == "MixedCase"
    assert PostgresEngine.get_identifier_str("Already_Lower") == "already_lower"


def test_control_object_names_are_quoted_upper_case():
    # Spec: docs/ai-state/DECISIONS.md (DEC-048) — control objects are created upper case and always quoted
    assert control("SELECT ctl_status FROM ldb_ctl") == 'SELECT "CTL_STATUS" FROM "LDB_CTL"'
    assert control("SELECT ldb_rows_total FROM t") == "SELECT ldb_rows_total FROM t"


def test_ldb_control_columns_use_the_process_date_variable_and_the_local_timestamp():
    # Spec: README > Run ILM — LDB_PROCESS_DATE and LDB_INSERT_DATE are added to each archived row
    assert PostgresEngine.get_ldb_columns_expressions() == ("l_process_date", "localtimestamp(0)")


def test_expression_identifiers_are_found_and_rewritten_with_the_qualifier():
    # Spec: docs/configuration-contract.md — @column references in expressions are resolved per table
    expression = "@Amount + @fee"
    assert PostgresEngine.get_identifiers_from_expression(expression) == {"amount", "fee"}
    assert (
        PostgresEngine.rewrite_expression_identifiers(expression, {"amount": "Amount", "fee": "fee"}, qualifier="A")
        == 'A."Amount" + A.fee'
    )


def test_table_owners_are_rewritten_for_the_tables_that_move():
    # Spec: docs/configuration-contract.md — history expressions point to the history owner of each table
    rewritten = PostgresEngine.rewrite_table_owners(
        "select 1 from source.items where x in (select y from source.other)", {("source", "items"): "history"}
    )
    assert rewritten == "select 1 from history.items where x in (select y from source.other)"


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        (ColumnDefinition(name="a", data_type="string", length=20), "varchar(20)"),
        (ColumnDefinition(name="a", data_type="number", precision=12, scale=2), "numeric(12,2)"),
        (ColumnDefinition(name="a", data_type="integer"), "numeric(10)"),
        (ColumnDefinition(name="a", data_type="date"), "timestamp(0)"),
        (ColumnDefinition(name="a", data_type="clob"), "text"),
        (ColumnDefinition(name="a", data_type="timestamptz"), "timestamptz"),
    ],
)
def test_generic_column_types_map_to_postgresql_types(column, expected):
    # Spec: docs/configuration-contract.md — the configured generic types map to engine types
    assert PostgresEngine.get_column_type(column) == expected


def test_unknown_column_type_is_rejected():
    # Spec: docs/configuration-contract.md — unsupported types fail before any DDL is built
    with pytest.raises(ValueError, match="Unsupported column data type"):
        PostgresEngine.get_column_type(ColumnDefinition(name="a", data_type="geometry"))


def test_source_block_program_copies_through_dblink_and_deletes_by_ctid():
    # Spec: docs/ai-state/DECISIONS.md (DEC-048) — rows move source -> history through dblink, never via the runner
    block = PostgresEngine.generate_sql_block(_config(), _table_config(), "20261028")

    program = parse_program(block)
    chunk = program.sections["chunk"]
    assert program.header["copy"] is True and program.header["refs"] == ["source.orders"]
    assert "timestamp '2026-10-28 00:00:00'" in chunk
    assert "LIMIT" in chunk and "ctid" in chunk
    assert "dblink_exec('ldb_hist'" in chunk
    assert "INSERT INTO history.items (id, created_at, ldb_process_date, ldb_insert_date) VALUES " in chunk
    assert "DELETE FROM source.items WHERE ctid = ANY(l_ids)" in chunk
    assert "dblink_connect" in program.sections["connect"]
    assert "secret" not in block


def test_history_block_program_deletes_from_the_history_owner_without_copying():
    # Spec: README > Run ILM > HISTORY_ILM — rows are deleted from the history owner; nothing is copied
    config = Config(
        schema="s",
        action="HISTORY_ILM",
        mode="EXECUTE",
        db_engine="postgresql",
        history_dsn="host/db",
        history_username="history",
        history_password="secret",
    )
    block = PostgresEngine.generate_sql_block(config, _table_config(query_expr="from history.items A"), "20261028")

    program = parse_program(block)
    assert program.header["copy"] is False and "connect" not in program.sections
    assert "dblink_exec" not in program.sections["chunk"]
    assert "DELETE FROM history.items WHERE ctid = ANY(l_ids)" in program.sections["chunk"]


def test_preview_program_has_a_count_section_without_deleting():
    # Spec: README > Run ILM > PREVIEW — preview counts the candidate rows and changes nothing
    program = parse_program(PostgresEngine.generate_sql_block(_config(), _table_config(), "20261028"))

    assert "count(*)" in program.sections["count"]
    assert "DELETE" not in program.sections["count"] and "dblink_exec" not in program.sections["count"]


def test_script_block_is_one_do_block_driven_by_session_settings():
    # Spec: README > Generate scripts — a SCRIPT run is a psql file; mode and date come from session settings
    block = PostgresEngine.generate_sql_block(_config(generate_script=True), _table_config(), "20261028")

    assert block.startswith("DO $ldb$") and block.rstrip().endswith("$ldb$;")
    assert "current_setting('ldb.mode')" in block and "current_setting('ldb.process_date')" in block
    assert "check_save_status(" in block and "check_referencing_tables(" in block
    assert "dblink_disconnect('ldb_hist')" in block


def test_supporting_functions_are_created_in_the_owner_schema():
    # Spec: docs/ai-state/DECISIONS.md (DEC-048) — check_save_status and check_referencing_tables live in the owner
    statements = supporting_functions("source")

    assert len(statements) == 2
    assert all(
        "CREATE OR REPLACE FUNCTION" in statement and "SET search_path TO source" in statement
        for statement in statements
    )
