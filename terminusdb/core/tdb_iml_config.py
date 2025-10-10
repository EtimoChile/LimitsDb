# terminusdb/core/tdb_iml_config.py
import yaml
from typing import Dict, List, Any, Tuple, Optional
from terminusdb.core.tdb_config_loader import load_ilm_config

id = 0
loaded_tables: set[Tuple[str, str]] = set()

def normalize_table_row(table: Dict[str, Any], cond: Dict[str, Any]) -> Dict[str, Any]:
    global id
    row: Dict[str, Any] = {}
    def norm_bool(val: Any) -> Any:
        return 'Y' if val else 'N' if val is not None else None
    for key, value in list(table.items()) + list(cond.items()):
        if key == "conds": continue
        row[f"cnf_{key}"] = norm_bool(value) if isinstance(value, bool) else value
        row["ctl_status"] = None
    for key in ["is_active", "prod_orphan_purge", "has_lob"]:
        if "cnf_" + key not in row: row["cnf_" + key] = "N" if key != "is_active" else "Y"
    for key in ["prod_owner", "hist_owner", "table_name", "months_keep_prod", "months_keep_hist", "exec_day", "frecuency", "purge_limit_date_expr",
                "additional_expr", "additional_hist_expr", "orphan_chk_column", "ref_tables", "join_expr", "hint_expr", "long_cols"]:
        if "cnf_" + key not in row: row["cnf_" + key] = None
    row["cnf_id"] = id
    id += 1
    return row

def load_rows_from_yaml(yaml_path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    global loaded_tables
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    rows = []
    for table in data.get("tables", []):
        owner, table_name = table.get("prod_owner", ""), table.get("table_name", "")
        if (owner, table_name) in loaded_tables:
            raise ValueError(f"Duplicate table found in tdb-config-file: {owner}.{table_name}")
        if not table.get("conds"):
            table["conds"] = [{"is_active": True}]
        conds: List[Dict[str, Any]] = [cd for cd in table.get("conds", []) if cd.get("is_active") is True]
        for cond in conds:
            rows.append(normalize_table_row(table, cond))
        if conds:
            loaded_tables.add((owner, table_name))
    return rows

def resolve_and_load_ilm_rows(*, schema: str, profile: Optional[str], config_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Localiza el ILM consolidado para schema/profile y retorna filas normalizadas.
    Si el ILM está particionado en múltiples archivos (overlays), load_ilm_config ya los fusiona.
    """
    ilm_dict = load_ilm_config(schema=schema, profile=profile, explicit_config_dir=config_dir)
    # Guardamos a un YAML temporal en memoria/archivo para reusar tu pipeline actual (si quieres seguir por path)
    # O podemos construir filas directo sin archivo auxiliar:
    rows: List[Dict[str, Any]] = []
    global loaded_tables, id
    loaded_tables = set()
    id = 0

    for table in ilm_dict.get("tables", []):
        owner, table_name = table.get("prod_owner", ""), table.get("table_name", "")
        if (owner, table_name) in loaded_tables:
            raise ValueError(f"Duplicate table found in ILM config: {owner}.{table_name}")
        if not table.get("conds"):
            table["conds"] = [{"is_active": True}]
        conds = [cd for cd in table.get("conds", []) if cd.get("is_active") is True]
        for cond in conds:
            rows.append(normalize_table_row(table, cond))
        if conds:
            loaded_tables.add((owner, table_name))
    return rows
