import yaml
import pytest

from limitsdb.core import ldb_ilm_config

def test_normalize_and_duplicate_detection(tmp_path):
  content = {
      "tables": [{
          "source_owner": "SRC",
          "table_name": "T1",
          "retain_months_source": 1,
          "retain_months_history": 2,
          "conds": [{
              "is_active": True
          }]
      }, {
          "source_owner": "SRC",
          "table_name": "T2",
          "conds": []
      }, ]
  }
  path = tmp_path / "ilm.yml"
  path.write_text(yaml.safe_dump(content), encoding="utf-8")
  rows = ldb_ilm_config.load_rows_from_yaml(str(path))
  assert len(rows) == 2
  assert rows[0]["id"] == 0
  assert rows[0]["retain_months_history"] == 2
  assert rows[1]["is_active"] == "Y"

  dup_content = {"tables": [{"source_owner": "SRC", "table_name": "T1"}, {"source_owner": "SRC", "table_name": "T1"}]}
  path.write_text(yaml.safe_dump(dup_content), encoding="utf-8")
  with pytest.raises(ValueError):
    ldb_ilm_config.load_rows_from_yaml(str(path))

def test_resolve_and_load_ilm_rows(monkeypatch, tmp_path):
  config = {"tables": [{"source_owner": "SRC", "table_name": "T1", "retain_months_source": 1, "retain_months_history": 2}]}
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
