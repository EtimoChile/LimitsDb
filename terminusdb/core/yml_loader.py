import yaml
from typing import Dict, List, Any

def normalize_table_row(table: Dict[str, Any], cond: Dict[str, Any]) -> Dict[str, Any]:
    """Builds a single normalized row by merging table and condition fields.
    Args:
        table: Table-level configuration.
        cond: Condition-level configuration.
    Returns:
        A dictionary with 'cnf_'-prefixed keys and boolean values as 'Y'/'N'."""
    row: Dict[str, Any] = {}
    def norm_bool(val: Any) -> Any:
        return 'Y' if val else 'N' if val is not None else None
    for key, value in list(table.items()) + list(cond.items()):
        if key == "conds":
            continue
        row[f"cnf_{key}"] = norm_bool(value) if isinstance(value, bool) else value
        row["ctl_status"] = None
    return row

def load_rows_from_yaml(yaml_path: str) -> List[Dict[str, Any]]:
    """Loads and normalizes table rows from a YAML file.
    Args:
        yaml_path: Path to the YAML configuration file.
    Returns:
        A list of normalized rows as dictionaries."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    rows: List[Dict[str, Any]] = []
    for table in data.get("tables", []):
        for cond in table.get("conds", []):
            rows.append(normalize_table_row(table, cond))
    return rows
