import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from terminusdb.core import tdb_utils

class DummyConfig(SimpleNamespace):
  action: str = "SOURCE_ILM"
  source_username: str = "src"
  source_password: str = "pw1"
  source_dsn: str = "dsn1"
  history_username: str = "hist"
  history_password: str = "pw2"
  history_dsn: str = "dsn2"
  admin_source_username: str = "admin_src"
  admin_source_password: str = "apw1"
  admin_history_username: str = "admin_hist"
  admin_history_password: str = "apw2"

def test_nvl_and_max_ignore_none():
  assert tdb_utils.nvl("val", "default") == "val"
  assert tdb_utils.nvl(None, "fallback") == "fallback"
  assert tdb_utils.max_ignore_none([None, 3, 2, None, 5]) == 5
  assert tdb_utils.max_ignore_none([None, None]) is None

def test_indent_and_wrap():
  text = "line1\nline2\nline3"
  assert tdb_utils.indent_lines(text, 2) == "line1\n  line2\n  line3"
  wrapped = tdb_utils.join_wrapped(",", ["a", "b", "long_word"], 4)
  assert wrapped.splitlines() == ["a,b", ",long_word"]

def test_get_effective_credentials_variations():
  cfg = DummyConfig()
  assert tdb_utils.get_effective_credentials(cfg) == ("src", "pw1", "dsn1")
  cfg.action = "HISTORY_ILM"
  assert tdb_utils.get_effective_credentials(cfg) == ("hist", "pw2", "dsn2")
  assert tdb_utils.get_effective_credentials(cfg, admin=True) == ("admin_hist", "apw2", "dsn2")
  cfg.history_password = ""
  with pytest.raises(ValueError):
    tdb_utils.get_effective_credentials(cfg)

def test_resolve_and_write_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
  monkeypatch.setattr(tdb_utils, "get_config_roots", lambda appname=tdb_utils.APPNAME: (tmp_path / "user", tmp_path / "sys"))
  for root in (tmp_path / "user", tmp_path / "sys"):
    (root / "schemas" / "demo").mkdir(parents=True, exist_ok=True)
  config_path = tmp_path / "user" / "schemas" / "demo" / "config.yml"
  config_path.write_text("key: value\n", encoding="utf-8")
  resolved = tdb_utils.resolve_schema_file(
      schema="demo", profile=None, explicit_config_dir=None, explicit_file=None, prefix_name="config", extension_name="yml", description="cfg"
  )
  assert resolved == str(config_path.resolve())

  secret_file = tdb_utils.write_or_update_secrets("demo", None, str(tmp_path))
  data = json.loads(secret_file.read_text(encoding="utf-8"))
  assert isinstance(data, dict)

  ilm_example = tdb_utils.write_ilm_example("demo", None, str(tmp_path), overwrite=True)
  assert ilm_example.exists()
  config_written = tdb_utils.write_config_yaml("demo", None, str(tmp_path), overwrite=True)
  assert "config" in config_written.name

def test_init_schema_encrypts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
  calls = {}
  monkeypatch.setattr(tdb_utils, "load_or_create_key", lambda: calls.setdefault("key", True))
  monkeypatch.setattr(tdb_utils, "encrypt_secrets_in_place", lambda *a, **k: calls.setdefault("enc", True))
  cfg, ilm, sec, ex = tdb_utils.init_schema(schema="s", profile=None, config_root=str(tmp_path), overwrite=True)
  assert cfg.exists() and ilm.exists() and sec.exists()
  assert calls == {"key": True, "enc": True}
  assert ex.exists()
