from __future__ import annotations

import datetime
from typing import Any, Dict, List, Sequence, Tuple

import pytest

from terminusdb.core import tdb_runner
from terminusdb.core.tdb_params_config import Config
from terminusdb.db.tdb_engines import ColumnDefinition


class StubEngine:
    def __init__(self, conf_rows: List[Dict[str, Any]], metadata: Dict[Tuple[str, str], Dict[str, ColumnDefinition]]) -> None:
        self._conf_rows = conf_rows
        self._metadata = metadata
        self.metadata_calls: List[Tuple[str, str, Sequence[str]]] = []

    def load_config(self, conn: Any) -> List[Dict[str, Any]]:
        return self._conf_rows

    def get_status(self, conn: Any, process_date: str) -> List[Dict[str, Any]]:
        return []

    def get_table_columns(self, conn: Any, owner: str, table_name: str) -> List[str]:
        columns = self._metadata[(owner.upper(), table_name.upper())]
        return [definition.name for definition in columns.values()]

    def get_columns_metadata(
        self,
        conn: Any,
        owner: str,
        table_name: str,
        columns: Sequence[str],
    ) -> Dict[str, ColumnDefinition]:
        self.metadata_calls.append((owner.upper(), table_name.upper(), list(columns)))
        available = self._metadata[(owner.upper(), table_name.upper())]
        result: Dict[str, ColumnDefinition] = {}
        for column in columns:
            stripped = column.strip()
            if stripped.startswith('"') and stripped.endswith('"') and len(stripped) > 1:
                normalized = stripped[1:-1]
            else:
                normalized = stripped.upper()
            key = normalized.lower()
            if normalized in available:
                result[key] = available[normalized]
        return result

    def get_primary_key_columns(self, conn: Any, owner: str, table_name: str) -> Tuple[str, ...]:
        return ("inv_id",)

    def get_date_cond(self, date_expr: str, months_keep_src: int) -> str:
        return f"COND({date_expr},{months_keep_src})"

    def generate_sql_block(self, config: Config, table_cnf: Dict[str, Any], process_date: str) -> str:
        return "SQL"

    def get_system_date(self, conn: Any) -> datetime.datetime:
        return datetime.datetime(2025, 1, 1)

    def get_connection(self, config: Config, *, admin: bool = False, env: Any = None) -> object:
        return object()

    def close_connection(self, conn: Any) -> None:
        return None


def _build_config_rows(filter_expression: str) -> List[Dict[str, Any]]:
    return [
        {
            "cnf_source_owner": "GL",
            "cnf_history_owner": "GLHST",
            "cnf_table_name": "INV_HEAD",
            "cnf_retain_months_source": 3,
            "cnf_retain_months_history": 11,
            "cnf_purge_date_expr": "INV_DATE",
            "cnf_additional_filter_expr": filter_expression,
            "cnf_history_addtl_filter_expr": None,
            "cnf_source_orphan_purge": "N",
            "cnf_long_columns": None,
            "cnf_referencing_tables": None,
            "cnf_join_expr": None,
            "cnf_orphan_check_column": None,
            "cnf_has_lob_columns": "N",
        },
        {
            "cnf_source_owner": "GL",
            "cnf_history_owner": "GLHST",
            "cnf_table_name": "INV_DET",
            "cnf_retain_months_source": None,
            "cnf_retain_months_history": None,
            "cnf_purge_date_expr": None,
            "cnf_additional_filter_expr": None,
            "cnf_history_addtl_filter_expr": None,
            "cnf_source_orphan_purge": "N",
            "cnf_long_columns": None,
            "cnf_referencing_tables": "GL.INV_HEAD B",
            "cnf_join_expr": "JOIN GL.INV_HEAD B ON B.INV_ID = A.INV_ID",
            "cnf_orphan_check_column": None,
            "cnf_has_lob_columns": "N",
        },
    ]


def _build_metadata() -> Dict[Tuple[str, str], Dict[str, ColumnDefinition]]:
    return {
        ("GL", "INV_HEAD"): {
            "INV_ID": ColumnDefinition(name="inv_id", data_type="number", nullable=False),
            "INV_STATUS": ColumnDefinition(name="inv_status", data_type="varchar2", length=1, nullable=False),
            "QuotedCol": ColumnDefinition(name='"QuotedCol"', data_type="varchar2", length=10, nullable=True),
        },
        ("GL", "INV_DET"): {
            "INV_ID": ColumnDefinition(name="inv_id", data_type="number", nullable=False),
            "LINE_NO": ColumnDefinition(name="line_no", data_type="number", nullable=False),
        },
    }


def _build_config() -> Config:
    cfg = Config(schema="demo", source_dsn="dsn", source_username="user", source_password="pwd")
    cfg.action = "HISTORY_ILM"
    cfg.use_added_columns = True
    cfg.generate_script = False
    return cfg


@pytest.mark.parametrize(
    "filter_expr, expected_lookup, expected_expr",
    [
        ("@INV_STATUS = 'A'", "INV_STATUS", "INV_STATUS"),
        ("@B.INV_STATUS = 'A'", "INV_STATUS", "INV_STATUS"),
    ],
)
def test_derived_columns_use_correct_alias(filter_expr: str, expected_lookup: str, expected_expr: str) -> None:
    engine = StubEngine(_build_config_rows(filter_expr), _build_metadata())
    config = _build_config()
    tables = tdb_runner.process_table_cnf(object(), config, engine, "20250101")
    inv_det = tables[("GL", "INV_DET")]
    derived = {col["name"]: col for col in inv_det["derived_columns"]}
    assert "INV_STATUS" in derived
    assert derived["INV_STATUS"]["source_alias"] == "B"
    assert derived["INV_STATUS"]["lookup"] == expected_lookup
    assert derived["INV_STATUS"]["lookup_expr"] == expected_expr
    assert f"B.{expected_expr}" in inv_det["other_cols_exprs"]


def test_alias_mismatch_is_ignored() -> None:
    engine = StubEngine(_build_config_rows("@A.INV_STATUS = 'A'"), _build_metadata())
    config = _build_config()
    tables = tdb_runner.process_table_cnf(object(), config, engine, "20250101")
    inv_det = tables[("GL", "INV_DET")]
    aliases = {col["name"] for col in inv_det["derived_columns"]}
    assert "INV_STATUS" not in aliases


def test_quoted_columns_preserve_lookup_expression_and_case() -> None:
    engine = StubEngine(_build_config_rows('@B."QuotedCol" = \'Y\''), _build_metadata())
    config = _build_config()
    tables = tdb_runner.process_table_cnf(object(), config, engine, "20250101")
    inv_det = tables[("GL", "INV_DET")]
    derived = {col["name"]: col for col in inv_det["derived_columns"]}
    quoted = derived["QuotedCol"]
    assert quoted["source_alias"] == "B"
    assert quoted["lookup"] == "QuotedCol"
    assert quoted["lookup_expr"] == '"QuotedCol"'

    table_def = tdb_runner._build_history_table_definition(config, engine, object(), inv_det)
    names = [column.name for column in table_def.columns]
    assert "QuotedCol" in names
    assert any('"QuotedCol"' in call[2] for call in engine.metadata_calls if call[1] == "INV_HEAD")
