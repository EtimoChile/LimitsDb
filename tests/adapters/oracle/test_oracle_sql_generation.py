from limitsdb.core.ldb_params_config import Config
from limitsdb.db.ldb_engines import ColumnDefinition
from limitsdb.db.oracle.ldb_engine_impl import OracleEngine


def test_date_predicate_uses_add_months_with_bind_variable():
    # Spec: README > Run ILM — retention is applied as a date cut-off; the Oracle
    # adapter renders it as add_months(l_process_date,-N) for bind-variable safety
    assert (
        OracleEngine.get_date_condition("TRUNC(process_date)", 6)
        == "(TRUNC(process_date) < add_months(l_process_date,-6))"
    )


def test_quoted_identifier_is_returned_verbatim_without_case_change():
    # Spec: README > Run ILM — quoted Oracle identifiers preserve mixed case;
    # unquoted identifiers are uppercased to match Oracle's default collation
    assert OracleEngine.get_identifier_str('"MixedCase"') == "MixedCase"
    assert OracleEngine.get_identifier_str("already_upper") == "ALREADY_UPPER"


def test_ldb_control_columns_use_bind_variable_and_sysdate():
    # Spec: README > Run ILM — LimitsDb adds LDB_PROCESS_DATE (bind variable) and
    # LDB_INSERT_DATE (sysdate) to each archived row for audit and retention purposes
    assert OracleEngine.get_ldb_columns_expressions() == ("l_process_date", "sysdate")


def test_source_insert_block_includes_ldb_columns_exactly_once():
    # Spec: README > Run ILM > SOURCE_ILM — the generated PL/SQL block inserts source
    # rows into the history table; LDB_PROCESS_DATE and LDB_INSERT_DATE appear in the
    # SELECT list and the INSERT column list once each and are never duplicated
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
    assert "'TEND', l_process_start, null, sysdate, l_message, 0, null" in block


def test_history_block_deletes_from_history_owner_not_source():
    # Spec: README > Run ILM > HISTORY_ILM — the generated PL/SQL block deletes rows
    # from the history owner, never from the source; l_action is 'HISTORY_ILM'
    config = Config(
        schema="s",
        action="HISTORY_ILM",
        mode="EXECUTE",
        history_dsn="dsn",
        history_username="history",
        history_password="secret",
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
        "other_columns": [],
        "referencing_tables": [],
        "query_expr": "from history.items A\nwhere A.created_at < l_process_date",
        "table_columns": ["ID", "CREATED_AT"],
        "months_keep_history_max": 3,
    }

    block = OracleEngine.generate_sql_block(config, table_config, "20261007")

    assert "l_action varchar2(11) := 'HISTORY_ILM'" in block
    assert "delete from history.items where rowid = r_rec(i).rowid" in block
    assert "delete from source.items" not in block


def test_expression_identifiers_are_extracted_with_at_prefix_and_quote_handling():
    # Spec: README > Run ILM > ILM Rule Semantics — column references in filter
    # expressions use the @ prefix; quoted identifiers preserve mixed case and
    # embedded double quotes are unescaped
    expression = '@simple + @"Quoted" + @"Escaped""Quote" + @lower_case'
    identifiers = OracleEngine.get_identifiers_from_expression(expression)

    assert identifiers == {
        "SIMPLE",
        "Quoted",
        'Escaped"Quote',
        "LOWER_CASE",
    }


def test_expression_identifiers_are_rewritten_to_snapshotted_names_with_qualifier():
    # Spec: README > Run ILM > HISTORY_ILM — column references in historical filter
    # expressions are rewritten to the snapshotted column names (e.g. STATE_B) with
    # the row alias qualifier; unknown identifiers are preserved unchanged
    expression = "@state = 'PURGE' AND @\"MixedCase\" = 1 AND @untouched = 2"

    rewritten = OracleEngine.rewrite_expression_identifiers(
        expression,
        {"STATE": "STATE_B", "MixedCase": "MixedCase_B"},
        qualifier="A",
    )

    assert rewritten == "A.state_b = 'PURGE' AND A.\"MixedCase_B\" = 1 AND @untouched = 2"
