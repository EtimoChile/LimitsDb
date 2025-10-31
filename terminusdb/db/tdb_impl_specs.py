"""Engine-agnostic metadata for provisioning TerminusDB auxiliary objects."""
from __future__ import annotations

from typing import Dict, List, Optional, TypedDict


class ColumnSpec(TypedDict, total=False):
    name: str
    types: Dict[str, str]
    nullable: bool
    default: Optional[str]


class IndexSpec(TypedDict, total=False):
    name: str
    columns: List[str]
    unique: bool


class TableSpec(TypedDict, total=False):
    columns: List[ColumnSpec]
    primary_key: IndexSpec
    indexes: List[IndexSpec]


TABLE_SPECS: Dict[str, TableSpec] = {
    "tdb_ctl": {
        "columns": [
            {"name": "ctl_owner", "types": {"oracle": "VARCHAR2(50)", "postgres": "VARCHAR(50)"}, "nullable": False},
            {"name": "ctl_table_name", "types": {"oracle": "VARCHAR2(50)", "postgres": "VARCHAR(50)"}, "nullable": False},
            {"name": "ctl_process_date", "types": {"oracle": "DATE", "postgres": "TIMESTAMP"}, "nullable": False},
            {"name": "ctl_action", "types": {"oracle": "VARCHAR2(10)", "postgres": "VARCHAR(10)"}, "nullable": False},
            {"name": "ctl_status", "types": {"oracle": "VARCHAR2(10)", "postgres": "VARCHAR(10)"}, "nullable": False},
            {"name": "ctl_process_start", "types": {"oracle": "DATE", "postgres": "TIMESTAMP"}},
            {"name": "ctl_process_end", "types": {"oracle": "DATE", "postgres": "TIMESTAMP"}},
            {"name": "ctl_rows_processed", "types": {"oracle": "NUMBER", "postgres": "BIGINT"}, "default": "0"},
            {"name": "ctl_plsql", "types": {"oracle": "CLOB", "postgres": "TEXT"}},
        ],
        "primary_key": {
            "name": "tdb_ctl_pk",
            "columns": ["ctl_owner", "ctl_table_name"],
            "unique": True,
        },
        "indexes": [
            {
                "name": "tdb_ctl_i1",
                "columns": ["ctl_process_date"],
            }
        ],
    },
    "tdb_log": {
        "columns": [
            {"name": "log_id", "types": {"oracle": "NUMBER", "postgres": "BIGINT"}, "nullable": False},
            {"name": "log_owner", "types": {"oracle": "VARCHAR2(50)", "postgres": "VARCHAR(50)"}},
            {"name": "log_table_name", "types": {"oracle": "VARCHAR2(50)", "postgres": "VARCHAR(50)"}},
            {"name": "log_process_date", "types": {"oracle": "DATE", "postgres": "TIMESTAMP"}},
            {"name": "log_action", "types": {"oracle": "VARCHAR2(10)", "postgres": "VARCHAR(10)"}},
            {"name": "log_status", "types": {"oracle": "VARCHAR2(10)", "postgres": "VARCHAR(10)"}},
            {"name": "log_process_start", "types": {"oracle": "DATE", "postgres": "TIMESTAMP"}},
            {"name": "log_process_end", "types": {"oracle": "DATE", "postgres": "TIMESTAMP"}},
            {"name": "log_message", "types": {"oracle": "VARCHAR2(200)", "postgres": "VARCHAR(200)"}},
            {"name": "log_rows_processed", "types": {"oracle": "NUMBER", "postgres": "BIGINT"}},
            {"name": "log_plsql", "types": {"oracle": "CLOB", "postgres": "TEXT"}},
        ],
        "primary_key": {
            "name": "tdb_log_pk",
            "columns": ["log_id"],
            "unique": True,
        },
        "indexes": [
            {
                "name": "tdb_log_i1",
                "columns": ["log_process_date"],
            }
        ],
    },
    "tdb_cnf": {
        "columns": [
            {"name": "cnf_id", "types": {"oracle": "NUMBER", "postgres": "BIGINT"}, "nullable": False},
            {"name": "cnf_source_owner", "types": {"oracle": "VARCHAR2(50)", "postgres": "VARCHAR(50)"}},
            {"name": "cnf_history_owner", "types": {"oracle": "VARCHAR2(50)", "postgres": "VARCHAR(50)"}},
            {"name": "cnf_table_name", "types": {"oracle": "VARCHAR2(50)", "postgres": "VARCHAR(50)"}},
            {"name": "cnf_retain_months_source", "types": {"oracle": "NUMBER", "postgres": "INTEGER"}},
            {"name": "cnf_retain_months_history", "types": {"oracle": "NUMBER", "postgres": "INTEGER"}},
            {"name": "cnf_exec_day", "types": {"oracle": "VARCHAR2(10)", "postgres": "VARCHAR(10)"}},
            {"name": "cnf_frecuency", "types": {"oracle": "VARCHAR2(10)", "postgres": "VARCHAR(10)"}},
            {"name": "cnf_is_active", "types": {"oracle": "CHAR(1)", "postgres": "CHAR(1)"}, "default": "'Y'"},
            {"name": "cnf_purge_date_expr", "types": {"oracle": "VARCHAR2(100)", "postgres": "VARCHAR(100)"}},
            {"name": "cnf_additional_filter_expr", "types": {"oracle": "VARCHAR2(4000)", "postgres": "TEXT"}},
            {"name": "cnf_history_addtl_filter_expr", "types": {"oracle": "VARCHAR2(4000)", "postgres": "TEXT"}},
            {"name": "cnf_source_orphan_purge", "types": {"oracle": "CHAR(1)", "postgres": "CHAR(1)"}, "default": "'N'"},
            {"name": "cnf_orphan_check_column", "types": {"oracle": "VARCHAR2(4000)", "postgres": "TEXT"}},
            {"name": "cnf_has_lob_columns", "types": {"oracle": "CHAR(1)", "postgres": "CHAR(1)"}, "default": "'N'"},
            {"name": "cnf_referencing_tables", "types": {"oracle": "VARCHAR2(400)", "postgres": "TEXT"}},
            {"name": "cnf_join_expr", "types": {"oracle": "VARCHAR2(4000)", "postgres": "TEXT"}},
            {"name": "cnf_hint_expr", "types": {"oracle": "VARCHAR2(4000)", "postgres": "TEXT"}},
            {"name": "cnf_long_columns", "types": {"oracle": "VARCHAR2(4000)", "postgres": "TEXT"}},
        ],
        "primary_key": {
            "name": "tdb_cnf_pk",
            "columns": ["cnf_id"],
            "unique": True,
        },
        "indexes": [
            {
                "name": "tdb_cnf_i1",
                "columns": ["cnf_source_owner", "cnf_table_name"],
                "unique": True,
            }
        ],
    },
}

SEQUENCE_SPECS: Dict[str, Dict[str, Optional[str]]] = {
    "tdb_log_id": {"start": "1", "increment": "1"},
    "tdb_cnf_id": {"start": "1", "increment": "1"},
}
