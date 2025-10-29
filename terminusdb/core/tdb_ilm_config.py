# terminusdb/core/tdb_ilm_config.py
from __future__ import annotations
import yaml
from typing import Dict, List, Any, Tuple, Optional, Set, Mapping, ItemsView, cast

from terminusdb.core.tdb_config_loader import load_ilm_config

_id_counter = 0
_loaded_tables: Set[Tuple[str, str]] = set()

def _normalize_bool(val: Any) -> Any:
    return 'Y' if val else 'N' if val is not None else None

def normalize_table_row(table: Dict[str, Any], cond: Dict[str, Any]) -> Dict[str, Any]:
    global _id_counter
    row: Dict[str, Any] = {}

    # Merge the table definition with the condition (excluding nested "conds").
    for key, value in list(table.items()) + list(cond.items()):
        if key == "conds":
            continue
        row[f"cnf_{key}"] = _normalize_bool(value) if isinstance(value, bool) else value
        row["ctl_status"] = None

    # Ensure default flags are present when omitted.
    for key in ["is_active", "source_orphan_purge", "has_lob_columns"]:
        if "cnf_" + key not in row:
            row["cnf_" + key] = "N" if key != "is_active" else "Y"

    for key in [
        "source_owner", "history_owner", "table_name", "retain_months_source", "retain_months_history",
        "exec_day", "frecuency", "purge_date_expr", "additional_filter_expr",
        "history_addtl_filter_expr", "orphan_check_column", "referencing_tables", "join_expr",
        "hint_expr", "long_columns",
    ]:
        if "cnf_" + key not in row:
            row["cnf_" + key] = None

    row["cnf_id"] = _id_counter
    _id_counter += 1
    return row

def _ensure_mapping(obj: Any, where: str) -> Dict[str, Any]:
    """Ensure obj is a mapping[str, Any] and return a plain dict[str, Any]."""
    if obj is None:
        return {}
    if not isinstance(obj, Mapping):
        raise TypeError(f"YAML root at {where} must be a mapping (dict). Got {type(obj).__name__}")
    out: Dict[str, Any] = {}
    # Explicitly type the ItemsView iterator for mypy.
    obj_items: ItemsView[Any, Any] = cast(Mapping[Any, Any], obj).items()
    for k_any, v_any in obj_items:
        key: str = str(k_any)
        val: Any = v_any
        out[key] = val
    return out

def _ensure_list_of_mappings(obj: Any, where: str) -> List[Dict[str, Any]]:
    """Ensure obj is a list of mapping-like items and normalize each to dict[str, Any]."""
    if obj is None:
        return []
    if not isinstance(obj, list):
        raise TypeError(f"'{where}' must be a list. Got {type(obj).__name__}")
    out: List[Dict[str, Any]] = []
    # Cast the heterogeneous list so we can iterate with type checking.
    obj_list: List[Any] = cast(List[Any], obj)
    for i, el_any in enumerate(obj_list):
        if not isinstance(el_any, Mapping):
            raise TypeError(f"'{where}[{i}]' must be a mapping (dict). Got {type(el_any).__name__}")
        el_map: Mapping[Any, Any] = cast(Mapping[Any, Any], el_any)
        d: Dict[str, Any] = {}
        el_items: ItemsView[Any, Any] = el_map.items()
        for k_any, v_any in el_items:
            key: str = str(k_any)
            val: Any = v_any
            d[key] = val
        out.append(d)
    return out

def load_rows_from_yaml(yaml_path: str) -> List[Dict[str, Any]]:
    global _loaded_tables
    with open(yaml_path, "r", encoding="utf-8") as f:
        data_any: Any = yaml.safe_load(f)
    data: Dict[str, Any] = _ensure_mapping(data_any, yaml_path)

    rows: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = _ensure_list_of_mappings(data.get("tables"), "tables")

    for table in tables:
        owner: str = str(table.get("source_owner", "") or "")
        table_name: str = str(table.get("table_name", "") or "")
        if (owner, table_name) in _loaded_tables:
            raise ValueError(f"Duplicate table in ilm-config-file: {owner}.{table_name}")

        # Normalize nested "conds" entries for the table.
        conds_any = table.get("conds")
        conds: List[Dict[str, Any]] = _ensure_list_of_mappings(conds_any, "conds") if conds_any is not None else []
        if not conds:
            conds = [{"is_active": True}]

        active_conds: List[Dict[str, Any]] = [cd for cd in conds if bool(cd.get("is_active")) is True]
        for cond in active_conds:
            rows.append(normalize_table_row(table, cond))

        if active_conds:
            _loaded_tables.add((owner, table_name))

    return rows

def resolve_and_load_ilm_rows(*, schema: str, profile: Optional[str], config_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Load merged ILM for schema/profile and return normalized rows.
    """
    ilm_dict: Dict[str, Any] = load_ilm_config(schema=schema, profile=profile, explicit_config_dir=config_dir)
    rows: List[Dict[str, Any]] = []
    global _loaded_tables, _id_counter
    _loaded_tables = set()
    _id_counter = 0
    tables: List[Dict[str, Any]] = _ensure_list_of_mappings(ilm_dict.get("tables"), "tables")
    for table in tables:
        owner: str = str(table.get("source_owner", "") or "")
        table_name: str = str(table.get("table_name", "") or "")
        if (owner, table_name) in _loaded_tables:
            raise ValueError(f"Duplicate table in ILM config: {owner}.{table_name}")
        conds_any = table.get("conds")
        conds: List[Dict[str, Any]] = _ensure_list_of_mappings(conds_any, "conds") if conds_any is not None else []
        if not conds:
            conds = [{"is_active": True}]
        active_conds: List[Dict[str, Any]] = [cd for cd in conds if bool(cd.get("is_active")) is True]
        for cond in active_conds:
            rows.append(normalize_table_row(table, cond))
        if active_conds:
            _loaded_tables.add((owner, table_name))
    return rows
