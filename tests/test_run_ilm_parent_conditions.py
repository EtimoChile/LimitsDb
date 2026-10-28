from limitsdb.core import ldb_runner
from limitsdb.core.ldb_params_config import Config
from limitsdb.db.ldb_engines import ColumnDefinition
from limitsdb.db.oracle.ldb_engine_impl import OracleEngine


class _Engine:
    """Engine double for process_tables_cnf: the SQL block is the planned query."""

    get_identifier_str = staticmethod(OracleEngine.get_identifier_str)
    format_identifier = staticmethod(OracleEngine.format_identifier)
    get_date_condition = staticmethod(OracleEngine.get_date_condition)
    get_identifiers_from_expression = staticmethod(OracleEngine.get_identifiers_from_expression)
    rewrite_expression_identifiers = staticmethod(OracleEngine.rewrite_expression_identifiers)
    get_fallback_expression = staticmethod(OracleEngine.get_fallback_expression)

    @staticmethod
    def get_ldb_columns_expressions():
        return "l_process_date", "sysdate"

    @staticmethod
    def get_table_columns(connection, owner, table_name):
        names = ["ID", "STS", "STS_DATE"]
        return names, {
            name: ColumnDefinition(name=name, data_type="varchar2", id=index, length=20)
            for index, name in enumerate(names, start=1)
        }

    @staticmethod
    def generate_sql_block(config, table_cnf, process_date):
        return table_cnf["query_expr"]


def _rule(table_name: str, rule_id: int, **overrides) -> dict:
    return {
        "id": rule_id,
        "table_name": table_name,
        "source_owner": "GL",
        "history_owner": "GLH",
        "retain_months_source": None,
        "retain_months_history": None,
        "exec_day": None,
        "frecuency": "D",
        "is_active": "Y",
        "purge_date_expr": None,
        "additional_filter_expr": None,
        "history_addtl_filter_expr": None,
        "history_hint_expr": None,
        "source_orphan_purge": "N",
        "orphan_check_column": None,
        "has_lob_columns": "N",
        "referencing_tables": None,
        "join_expr": None,
        "hint_expr": None,
        "long_columns": None,
        "ctl_status": None,
        **overrides,
    }


def _status_rule(table_name: str, rule_id: int, status: str, months: int) -> dict:
    return _rule(
        table_name,
        rule_id,
        retain_months_source=months,
        retain_months_history=months,
        purge_date_expr="@STS_DATE",
        additional_filter_expr=f"@STS='{status}'",
    )


def _child_query(rules: list[dict], ilm_file) -> str:
    ilm_path = ilm_file(rules)
    config = Config(
        schema="s",
        ilm_config_file=ilm_path,
        action="SOURCE_ILM",
        mode="SCRIPT",
        source_dsn="dsn",
        source_username="user",
        source_password="password",
        history_dsn="dsn",
        history_username="user",
        history_password="password",
    )
    tables = ldb_runner.process_tables_cnf(object(), config, _Engine(), "20261007")
    return tables[("GL", "CHILD")]["query_expr"]


def test_child_is_purged_only_when_every_parent_allows_it(ilm_file):
    # Spec: README > ILM Rule Semantics > Referencing tables — conditions of one table are alternatives (or);
    # a child inherits those of every referencing table and a row is purged only when all of them are met.
    # Given: a child referencing two parents, each with two alternative conditions
    rules = [
        _status_rule("P1", 1, "X", 1),
        _status_rule("P1", 2, "Y", 2),
        _status_rule("P2", 3, "X", 3),
        _status_rule("P2", 4, "Y", 4),
        _rule(
            "CHILD",
            5,
            referencing_tables="GL.P1 B, GL.P2 C",
            join_expr="@ JOIN GL.P1 B ON B.ID=A.ID @ JOIN GL.P2 C ON C.ID=A.ID",
        ),
    ]

    # When: the child query is planned
    query = _child_query(rules, ilm_file)

    # Then: the alternatives of each parent are or-ed and the parents are and-ed
    where = " ".join(query.split("where", 1)[1].split())
    assert where == (
        "(((B.STS='X' and (B.STS_DATE < add_months(l_process_date,-1))) "
        "or (B.STS='Y' and (B.STS_DATE < add_months(l_process_date,-2)))) "
        "and ((C.STS='X' and (C.STS_DATE < add_months(l_process_date,-3))) "
        "or (C.STS='Y' and (C.STS_DATE < add_months(l_process_date,-4)))))"
    )
