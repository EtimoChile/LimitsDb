# terminusdb/core/tdb_ilm_config.py
from __future__ import annotations
import yaml
from typing import Dict, List, Any, Tuple, Optional, Set, Mapping, ItemsView, cast

from terminusdb.core.tdb_config_loader import load_ilm_config

_id_counter = 0
_loaded_tables: Set[Tuple[str, str]] = set()

_VALID_ROOT_KEYS: Set[str] = {"tables"}

_VALID_TABLE_KEYS: Set[str] = {
    "source_owner", "history_owner", "table_name", "retain_months_source", "retain_months_history", "exec_day", "frecuency", "purge_date_expr",
    "additional_filter_expr", "history_addtl_filter_expr", "history_hint_expr", "orphan_check_column", "referencing_tables", "join_expr", "hint_expr",
    "long_columns", "source_orphan_purge", "has_lob_columns"
}

_VALID_COND_KEYS: Set[str] = _VALID_TABLE_KEYS | {"is_active"}

def _assert_valid_keys(keys: Set[str], *, where: str, allowed: Set[str]) -> None:
    """Raise if any key in `keys` is not present in `allowed`."""
    invalid = sorted(k for k in keys if k not in allowed)
    if invalid:
        allowed_sorted = ", ".join(sorted(allowed))
        invalid_sorted = ", ".join(invalid)
        raise KeyError(f"Invalid key(s) in ILM config at {where}: {invalid_sorted}. Expected only: {allowed_sorted}")

def _normalize_bool(val: Any) -> Any:
    return 'Y' if val else 'N' if val is not None else None

def normalize_table_row(table: Dict[str, Any], cond: Dict[str, Any]) -> Dict[str, Any]:
    global _id_counter
    row: Dict[str, Any] = {}
    # Merge the table definition with the condition (excluding nested "conds").
    for key, value in list(table.items()) + list(cond.items()):
        if key == "conds":
            continue
        row[key] = _normalize_bool(value) if isinstance(value, bool) else value
        row["ctl_status"] = None
    # Ensure default flags are present when omitted.
    for key in ["is_active", "source_orphan_purge", "has_lob_columns"]:
        if key not in row:
            row[key] = "N" if key != "is_active" else "Y"
    for key in ["source_owner", "history_owner", "table_name", "retain_months_source", "retain_months_history", "exec_day", "frecuency",
                "purge_date_expr", "additional_filter_expr", "history_addtl_filter_expr", "history_hint_expr", "orphan_check_column",
                "referencing_tables", "join_expr", "hint_expr", "long_columns"]:
        if key not in row:
            row[key] = None
    row["id"] = _id_counter
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
    _loaded_tables: Set[Tuple[str, str]] = set()
    with open(yaml_path, "r", encoding="utf-8") as f:
        data_any: Any = yaml.safe_load(f)
    data: Dict[str, Any] = _ensure_mapping(data_any, yaml_path)
    _assert_valid_keys(set(data.keys()), where=f"{yaml_path} (root)", allowed=_VALID_ROOT_KEYS)
    rows: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = _ensure_list_of_mappings(data.get("tables"), "tables")
    for idx, table in enumerate(tables):
        table_keys = set(table.keys()) - {"conds"}
        _assert_valid_keys(table_keys, where=f"{yaml_path} tables[{idx}]", allowed=_VALID_TABLE_KEYS)
        owner: str = str(table.get("source_owner", "") or "")
        table_name: str = str(table.get("table_name", "") or "")
        if (owner, table_name) in _loaded_tables:
            raise ValueError(f"Duplicate table in ilm-config-file: {owner}.{table_name}")
        # Normalize nested "conds" entries for the table.
        conds_any = table.get("conds")
        conds: List[Dict[str, Any]] = _ensure_list_of_mappings(conds_any, "conds") if conds_any is not None else []
        if not conds:
            conds = [{"is_active": True}]
        active_conds: List[Dict[str, Any]] = []
        for c_idx, cond in enumerate(conds):
            _assert_valid_keys(set(cond.keys()), where=f"{yaml_path} tables[{idx}].conds[{c_idx}]", allowed=_VALID_COND_KEYS)
            if bool(cond.get("is_active")) is True:
                active_conds.append(cond)
        for cond in active_conds:
            rows.append(normalize_table_row(table, cond))
        if active_conds:
            _loaded_tables.add((owner, table_name))
    return rows

def resolve_and_load_ilm_rows(*, schema: str, profile: Optional[str], config_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """ Load merged ILM for schema/profile and return normalized rows.
    """
    ilm_dict: Dict[str, Any] = load_ilm_config(schema=schema, profile=profile, explicit_config_dir=config_dir)
    rows: List[Dict[str, Any]] = []
    global _loaded_tables, _id_counter
    _loaded_tables = set()
    _id_counter = 0
    _assert_valid_keys(set(ilm_dict.keys()), where=f"ILM config for schema={schema} profile={profile} (root)", allowed=_VALID_ROOT_KEYS)
    tables: List[Dict[str, Any]] = _ensure_list_of_mappings(ilm_dict.get("tables"), "tables")
    for idx, table in enumerate(tables):
        table_keys = set(table.keys()) - {"conds"}
        _assert_valid_keys(table_keys, where=f"ILM config tables[{idx}] (schema={schema} profile={profile})", allowed=_VALID_TABLE_KEYS)
        owner: str = str(table.get("source_owner", "") or "")
        table_name: str = str(table.get("table_name", "") or "")
        if (owner, table_name) in _loaded_tables:
            raise ValueError(f"Duplicate table in ILM config: {owner}.{table_name}")
        conds_any = table.get("conds")
        conds: List[Dict[str, Any]] = _ensure_list_of_mappings(conds_any, "conds") if conds_any is not None else []
        if not conds:
            conds = [{"is_active": True}]
        active_conds: List[Dict[str, Any]] = []
        for c_idx, cond in enumerate(conds):
            _assert_valid_keys(
                set(cond.keys()), where=f"ILM config tables[{idx}].conds[{c_idx}] (schema={schema} profile={profile})", allowed=_VALID_COND_KEYS
            )
            if bool(cond.get("is_active")) is True:
                active_conds.append(cond)
        for cond in active_conds:
            rows.append(normalize_table_row(table, cond))
        if active_conds:
            _loaded_tables.add((owner, table_name))
    return rows
