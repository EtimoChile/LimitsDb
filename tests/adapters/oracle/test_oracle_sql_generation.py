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


def _status_block(mode: str) -> str:
    config = Config(
        schema="s",
        action="SOURCE_ILM",
        mode=mode,  # type: ignore[arg-type]
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
        "other_columns": [],
        "referencing_tables": [],
        "query_expr": "from source.items A\nwhere A.created_at < l_process_date",
        "table_columns": ["ID"],
        "months_keep_history_max": 3,
    }
    return OracleEngine.generate_sql_block(config, table_config, "20261007")


def _lob_block(*, script: bool = False) -> str:
    config = Config(
        schema="s",
        action="SOURCE_ILM",
        mode="SCRIPT" if script else "EXECUTE",  # type: ignore[arg-type]
        source_dsn="dsn",
        source_username="source",
        source_password="secret",
    )
    table_config = {
        "conds": [
            {
                "source_owner": "SOURCE",
                "history_owner": "HISTORY",
                "table_name": "DOCS",
                "history_hint_expr": None,
                "hint_expr": "",
                "has_lob_columns": "Y",
            }
        ],
        "other_columns": [],
        "referencing_tables": [],
        "query_expr": "from source.docs A\nwhere A.created_at < l_process_date",
        "table_columns": ["ID"],
        "months_keep_history_max": 3,
    }
    return OracleEngine.generate_sql_block(config, table_config, "20261007")


def _script_block() -> str:
    config = Config(
        schema="s",
        action="SOURCE_ILM",
        mode="SCRIPT",
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
        "other_columns": [],
        "referencing_tables": [],
        "query_expr": "from source.items A\nwhere A.created_at < l_process_date",
        "table_columns": ["ID"],
        "months_keep_history_max": 3,
    }
    return OracleEngine.generate_sql_block(config, table_config, "20261007")


def test_block_text_does_not_depend_on_the_run_mode():
    # Spec: docs/concurrency-control.md > Layer 2 — the mode reaches the block at run time (PEND-027), so the
    # block generated for a PREVIEW run is the one generated for an EXECUTE run
    # Given/When: the blocks of a PREVIEW and an EXECUTE run
    preview, execute = _status_block("PREVIEW"), _status_block("EXECUTE")

    # Then: they are identical
    assert preview == execute


def test_runner_block_receives_mode_and_run_id_as_binds():
    # Spec: docs/concurrency-control.md > Layer 2 — the runner hands the mode and its run_id to the block as binds
    # Given/When: the block of a runner execution
    block = _status_block("EXECUTE")

    # Then: mode and run_id are binds; no SQL*Plus substitution variable is left
    assert "l_mode varchar2(10) := :ldb_mode;" in block
    assert "l_run_id varchar2(36) := :ldb_run_id;" in block
    assert "&" not in block


def test_script_block_takes_mode_and_process_date_from_sqlplus_and_generates_its_own_run_id():
    # Spec: docs/concurrency-control.md > Modalities — a SCRIPT is lock-less: its run_id is generated by the block
    # (prefix S, no lock row), it has no binds, and its parameters are the ldb_-prefixed SQL*Plus variables
    # Given/When: the block generated for a SCRIPT
    block = _script_block()

    # Then: mode and date are SQL*Plus variables, the old name is gone and no bind is used
    assert "l_mode varchar2(10) := upper('&ldb_mode');" in block
    assert "to_date('&ldb_process_date', 'YYYYMMDD')" in block
    assert "&process_date" not in block
    assert "l_run_id varchar2(36) := 'S' || rawtohex(sys_guid());" in block
    assert ":ldb_" not in block and ":plsql_code" not in block


def test_statuses_take_the_preview_prefix_at_run_time():
    # Spec: docs/concurrency-control.md > Status — PREVIEW statuses are the EXECUTE ones prefixed with P
    # Given/When: any block
    block = _status_block("EXECUTE")

    # Then: every status is built from l_pfx, and the parent check allows PTEND only in PREVIEW
    for status in ("TSTART", "CSTART", "CEND", "TEND", "ERROR"):
        assert f"l_pfx || '{status}'" in block
    assert "l_pfx varchar2(1) := case when l_mode = 'PREVIEW' then 'P' else null end;" in block
    assert (
        "check_referencing_tables(l_referencing_tables, l_process_date, case when l_mode = 'PREVIEW' then 1 else 0 end)"
        in block
    )
    assert "TPEND" not in block


def test_an_invalid_mode_is_rejected_before_anything_is_written():
    # Spec: docs/concurrency-control.md > Layer 2 — the block accepts EXECUTE or PREVIEW only; a wrong
    # value stops it before the control row is touched
    # Given/When: any block
    block = _script_block()

    # Then: the guard precedes the first check_save_status call and the exception handler
    guard = block.index("-20007")
    assert guard < block.index("check_save_status(")
    assert guard < block.index("exception")
    assert "l_mode not in ('EXECUTE', 'PREVIEW')" in block


def test_a_worker_that_loses_the_table_stops_at_every_status_call():
    # Spec: docs/concurrency-control.md > Layer 2 — CSTART and CEND raise like TSTART, otherwise two workers
    # could process the same table and duplicate history
    # Given/When: the chunked block
    lines = _status_block("EXECUTE").splitlines()

    # Then: each non-error check_save_status call is followed by the raise on l_sqlcode
    calls = [i for i, line in enumerate(lines) if "check_save_status(" in line and "'ERROR'" not in line]
    assert len(calls) == 4  # TSTART, CSTART, CEND, TEND
    for i in calls:
        assert "raise_application_error(l_sqlcode, l_out_message)" in lines[i + 1]


def test_the_dml_runs_only_in_execute_mode():
    # Spec: docs/concurrency-control.md > Layer 2 — a PREVIEW reads and counts but deletes nothing
    # Given/When: the chunked block
    block = _status_block("EXECUTE")

    # Then: the history insert and the delete sit inside the EXECUTE branch, the counter outside it
    guard = block.index("if l_mode = 'EXECUTE' then")
    assert guard < block.index("insert into") < block.index("delete from") < block.index("end if;", guard)
    assert block.index("end if;", guard) < block.index("l_record_count := l_record_count + r_rec.count;")


def test_lob_block_holds_the_control_row_between_lstart_and_lend_without_committing():
    # Spec: docs/concurrency-control.md > Layer 2 — the LOB path has no chunks: LSTART locks the control row
    # before the long statement and LEND/TEND release it, with no commit in between
    # Given/When: the block of a SOURCE_ILM table with LOB columns
    block = _lob_block()

    # Then: LSTART and LEND are checked, the statement sits between them and the only commit follows TEND
    lstart, lend = block.index("l_pfx || 'LSTART'"), block.index("l_pfx || 'LEND'")
    assert lstart < block.index("if l_mode = 'EXECUTE' then") < lend < block.index("l_pfx || 'TEND'")
    assert "commit" not in block[lstart:lend]
    assert "raise_application_error(l_sqlcode, l_out_message)" in block[lstart:].splitlines()[1]
    assert "l_pfx || 'TEND', l_process_start, null, sysdate, l_message, 0," in block
    assert "l_pfx || 'LEND', null, l_chunk_start, sysdate, l_message, l_record_count," in block


def test_error_handler_reports_failure_without_committing_tstart_early():
    # Spec: README > Run ILM — a failed table is reported as ERROR with the rows already moved
    # Given/When: the block of an EXECUTE run
    block = _status_block("EXECUTE")
    handler = block.split("exception", 1)[1]

    # Then: ERROR carries the plsql, every error but "already processed" is re-raised,
    # and the TSTART lock is released only by the existing first commit (no extra commit after TSTART)
    assert "l_pfx || 'ERROR', l_process_start, null, sysdate, sqlerrm, l_record_count, l_plsql, l_run_id" in handler
    assert "if nvl(l_sqlcode, 0) <> -20003 then" in handler
    head = block.split("check_referencing_tables", 1)[0]
    assert "commit" not in head.split("'TSTART'", 1)[1]
