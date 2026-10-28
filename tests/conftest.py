"""Fixtures shared by the runner tests."""

import pytest
import yaml

_TABLE_KEYS = ("source_owner", "history_owner", "table_name")
_NOT_CONFIGURED = {"id", "ctl_status", "is_active", "cond_expr"}


@pytest.fixture
def ilm_file(tmp_path):
    """Write an ILM file from flat rule dicts and return its path.

    The runner reads its rules from a file, so tests give it one instead of replacing the loader. Each dict is one
    rule: its owner and table name key the table and the rest are the keys of one active condition, so several
    rules of the same table become several conditions of it.
    """

    def write(rules: list[dict]) -> str:
        tables: dict[tuple, dict] = {}
        for rule in rules:
            key = tuple(rule.get(name) for name in _TABLE_KEYS)
            table = tables.setdefault(key, {name: rule[name] for name in _TABLE_KEYS if rule.get(name) is not None})
            cond = {
                k: v for k, v in rule.items() if k not in _TABLE_KEYS and k not in _NOT_CONFIGURED and v is not None
            }
            table.setdefault("conds", []).append({"is_active": True, **cond})
        path = tmp_path / "ilm.yml"
        path.write_text(yaml.safe_dump({"tables": list(tables.values())}, sort_keys=False), encoding="utf-8")
        return str(path)

    return write
