import json
from pathlib import Path

import pytest

from limitsdb.core import ldb_config_loader

def test_deep_merge_and_overrides():
  base = {"a": 1, "b": {"c": 2}}
  over = {"b": {"d": 3}, "e": 4}
  merged = ldb_config_loader._deep_merge(base, over)
  assert merged == {"a": 1, "b": {"c": 2, "d": 3}, "e": 4}

  cfg = {"a": {"b": 1}}
  overrides = ldb_config_loader._apply_overrides(cfg, {"a.c": 2, "d": 3})
  assert overrides["a"]["c"] == 2
  assert overrides["d"] == 3

def test_read_env_overrides(monkeypatch: pytest.MonkeyPatch):
  monkeypatch.setenv("LDB_FLAG", "true")
  monkeypatch.setenv("LDB_NUMBER", "42")
  monkeypatch.setenv("LDB_FLOAT", "3.14")
  monkeypatch.setenv("OTHER", "ignored")
  env = ldb_config_loader._read_env_overrides()
  assert env == {"flag": True, "number": 42, "float": 3.14}

def test_load_runtime_config_with_overlays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
  schema_dir = tmp_path / "user" / "schemas" / "s"
  schema_dir.mkdir(parents=True)
  config_file = schema_dir / "config.yml"
  config_file.write_text("foo: 1\nbar: {nested: true}\n", encoding="utf-8")
  secrets_file = schema_dir / "secrets.json"
  secrets_file.write_text(json.dumps({"source_password": "enc:v1:aes256gcm:aa:bb"}), encoding="utf-8")

  monkeypatch.setattr(ldb_config_loader, "resolve_schema_file", lambda **k: str(config_file) if k["prefix_name"] == "config" else str(secrets_file))
  monkeypatch.setattr(ldb_config_loader, "secret_keys_from_config", lambda: ["source_password"])
  monkeypatch.setattr(ldb_config_loader, "_IS_ENC", lambda v: True)
  monkeypatch.setenv("LDB_EXTRA", "value")

  monkeypatch.setattr(ldb_config_loader, "_DECRYPT", lambda v: "pw")
  cfg = ldb_config_loader.load_runtime_config(schema="s", profile=None, cli_sets={"bar.nested": False}, explicit_config_dir=None)
  assert cfg["foo"] == 1
  assert cfg["bar"]["nested"] is False
  assert cfg["extra"] == "value"
  assert cfg["source_password"] == "pw"

def test_load_runtime_config_plaintext_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
  schema_dir = tmp_path / "user" / "schemas" / "s"
  schema_dir.mkdir(parents=True)
  secrets_file = schema_dir / "secrets.json"
  secrets_file.write_text(json.dumps({"source_password": "plain"}), encoding="utf-8")
  monkeypatch.setattr(ldb_config_loader, "resolve_schema_file", lambda **k: str(secrets_file))
  monkeypatch.setattr(ldb_config_loader, "secret_keys_from_config", lambda: ["source_password"])
  cfg = ldb_config_loader.load_runtime_config(schema="s", profile=None, cli_sets={}, explicit_config_dir=None, enforce_encrypted_secrets=False)
  assert cfg["source_password"] == "plain"
  with pytest.raises(ValueError):
    ldb_config_loader.load_runtime_config(schema="s", profile=None, cli_sets={}, explicit_config_dir=None)
