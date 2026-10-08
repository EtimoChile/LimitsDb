import pytest
import yaml

from limitsdb.core import ldb_ilm_config


def test_normalize_and_duplicate_detection(tmp_path):
    content = {
        "tables": [
            {
                "source_owner": "SRC",
                "table_name": "T1",
                "retain_months_source": 1,
                "retain_months_history": 2,
                "conds": [{"is_active": True}],
            },
            {"source_owner": "SRC", "table_name": "T2", "conds": []},
        ]
    }
    path = tmp_path / "ilm.yml"
    path.write_text(yaml.safe_dump(content), encoding="utf-8")
    rows = ldb_ilm_config.load_rows_from_yaml(str(path))
    assert len(rows) == 2
    assert rows[0]["id"] == 0
    assert rows[0]["retain_months_history"] == 2
    assert rows[1]["is_active"] == "Y"
    normalized_keys = set(ldb_ilm_config.IlmRule.__annotations__) - {"cond_expr"}
    assert normalized_keys <= rows[0].keys()
    assert "cond_expr" not in rows[0]

    dup_content = {"tables": [{"source_owner": "SRC", "table_name": "T1"}, {"source_owner": "SRC", "table_name": "T1"}]}
    path.write_text(yaml.safe_dump(dup_content), encoding="utf-8")
    with pytest.raises(ValueError):
        ldb_ilm_config.load_rows_from_yaml(str(path))


def test_resolve_and_load_ilm_rows(monkeypatch, tmp_path):
    config = {
        "tables": [{"source_owner": "SRC", "table_name": "T1", "retain_months_source": 1, "retain_months_history": 2}]
    }
    monkeypatch.setattr(ldb_ilm_config, "load_ilm_config", lambda **_: config)
    rows = ldb_ilm_config.resolve_and_load_ilm_rows(schema="s", profile=None, config_dir=None)
    assert rows[0]["retain_months_history"] == 2
    assert rows[0]["source_owner"] == "SRC"


def test_invalid_keys_and_types(tmp_path):
    path = tmp_path / "ilm.yml"
    path.write_text("tables: 123", encoding="utf-8")
    with pytest.raises(TypeError):
        ldb_ilm_config.load_rows_from_yaml(str(path))

    bad_key = {"wrong": []}
    path.write_text(yaml.safe_dump(bad_key), encoding="utf-8")
    with pytest.raises(KeyError):
        ldb_ilm_config.load_rows_from_yaml(str(path))


def test_ensure_mapping_none_returns_empty_dict():
    result = ldb_ilm_config._ensure_mapping(None, "test_field")
    assert result == {}


def test_ensure_mapping_non_mapping_raises_type_error():
    with pytest.raises(TypeError):
        ldb_ilm_config._ensure_mapping("not_a_mapping", "test_field")


def test_ensure_list_of_mappings_none_returns_empty_list():
    result = ldb_ilm_config._ensure_list_of_mappings(None, "test_field")
    assert result == []


def test_ensure_list_of_mappings_non_mapping_element_raises_type_error():
    with pytest.raises(TypeError):
        ldb_ilm_config._ensure_list_of_mappings([{"valid": True}, "not_a_mapping"], "test_field")


def test_load_rows_from_yaml_all_inactive_conds_excluded(tmp_path):
    content = {
        "tables": [
            {
                "source_owner": "SRC",
                "table_name": "T1",
                "conds": [{"is_active": False}],
            }
        ]
    }
    path = tmp_path / "ilm.yml"
    path.write_text(yaml.safe_dump(content), encoding="utf-8")
    rows = ldb_ilm_config.load_rows_from_yaml(str(path))
    # Table T1 is excluded entirely because all its conds are inactive
    assert len(rows) == 0
