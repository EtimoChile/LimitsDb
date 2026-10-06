from types import SimpleNamespace

import pytest

from limitsdb.core import ldb_params_config

class DummyArgs(SimpleNamespace):
  schema: str
  profile: str | None
  config_file: str | None = None
  config_dir: str | None = None
  set: list[str]
  mode: str | None = None

def test_normalize_and_config_validation():
  cfg = ldb_params_config.Config(schema="s", source_dsn="d", source_username="u", source_password="p")
  assert cfg.mode == "PREVIEW"
  with pytest.raises(ValueError):
    ldb_params_config.Config(schema="", source_dsn="d", source_username="u", source_password="p")
  with pytest.raises(ValueError):
    ldb_params_config.Config(schema="s", source_dsn="", source_username="u", source_password="p")

def test_parse_cli_sets_and_build_config(monkeypatch: pytest.MonkeyPatch):
  parser = ldb_params_config.build_argparser_from_config()
  args = parser.parse_args(["--schema", "myschema"])
  cli_sets = ldb_params_config._parse_cli_sets(["a=1", "b=true", "c=text"])
  assert cli_sets == {"a": 1, "b": True, "c": "text"}

  defaults = {"base": 1}
  dummy_cfg = {"merged": True}

  def fake_loader(**kwargs):
    assert kwargs["cli_sets"]["schema"] == "myschema"
    return dummy_cfg

  monkeypatch.setattr(ldb_params_config, "load_runtime_config", fake_loader)
  merged = ldb_params_config.build_config(defaults, args)
  assert merged["base"] == 1
  assert merged["merged"] is True
  assert merged["ilm_config_file"] is None

def test_argparser_choices_requirements():
  parser = ldb_params_config.build_argparser_from_config()
  with pytest.raises(SystemExit):
    parser.parse_args([])
  parsed = parser.parse_args(["--schema", "s", "--mode", "DRY_RUN"])
  assert parsed.mode == "DRY_RUN"
