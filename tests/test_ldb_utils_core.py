import json
from pathlib import Path

import pytest

from limitsdb.core import ldb_utils
from limitsdb.core.ldb_errors import ConfigurationError, SecretError
from limitsdb.core.ldb_params_config import Config


def test_nvl_and_max_ignore_none():
    assert ldb_utils.nvl("val", "default") == "val"
    assert ldb_utils.nvl(None, "fallback") == "fallback"
    assert ldb_utils.max_ignore_none([None, 3, 2, None, 5]) == 5
    assert ldb_utils.max_ignore_none([None, None]) is None


def test_indent_and_wrap():
    text = "line1\nline2\nline3"
    assert ldb_utils.indent_lines(text, 2) == "line1\n  line2\n  line3"
    wrapped = ldb_utils.join_wrapped(",", ["a", "b", "long_word"], 4)
    assert wrapped.splitlines() == ["a,b", ",long_word"]


def test_get_effective_credentials_variations():
    cfg = Config(
        schema="s",
        mode="PLAN",
        source_username="src",
        source_password="pw1",
        source_dsn="dsn1",
        history_username="hist",
        history_password="pw2",
        history_dsn="dsn2",
        admin_source_username="admin_src",
        admin_source_password="apw1",
        admin_history_username="admin_hist",
        admin_history_password="apw2",
    )
    assert ldb_utils.get_effective_credentials(cfg) == ("src", "pw1", "dsn1")
    cfg.action = "HISTORY_ILM"
    assert ldb_utils.get_effective_credentials(cfg) == ("hist", "pw2", "dsn2")
    assert ldb_utils.get_effective_credentials(cfg, admin=True) == ("admin_hist", "apw2", "dsn2")
    cfg.history_password = ""
    with pytest.raises(ValueError):
        ldb_utils.get_effective_credentials(cfg)


def test_resolve_and_write_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        ldb_utils, "get_config_roots", lambda appname=ldb_utils.APPNAME: (tmp_path / "user", tmp_path / "sys")
    )
    for root in (tmp_path / "user", tmp_path / "sys"):
        (root / "schemas" / "demo").mkdir(parents=True, exist_ok=True)
    config_path = tmp_path / "user" / "schemas" / "demo" / "config.yml"
    config_path.write_text("key: value\n", encoding="utf-8")
    resolved = ldb_utils.resolve_schema_file(
        schema="demo",
        profile=None,
        explicit_config_dir=None,
        explicit_file=None,
        prefix_name="config",
        extension_name="yml",
        description="cfg",
    )
    assert resolved == str(config_path.resolve())

    secret_file = ldb_utils.write_or_update_secrets("demo", None, str(tmp_path))
    data = json.loads(secret_file.read_text(encoding="utf-8"))
    assert isinstance(data, dict)

    ilm_example = ldb_utils.write_ilm_example("demo", None, str(tmp_path), overwrite=True)
    assert ilm_example.exists()
    config_written = ldb_utils.write_config_yaml("demo", None, str(tmp_path), overwrite=True)
    assert "config" in config_written.name


def test_init_schema_encrypts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    calls = {}
    monkeypatch.setattr(ldb_utils, "load_or_create_key", lambda: calls.setdefault("key", True))
    monkeypatch.setattr(ldb_utils, "encrypt_secrets_in_place", lambda *a, **k: calls.setdefault("enc", True))
    cfg, ilm, sec, ex = ldb_utils.init_schema(schema="s", profile=None, config_root=str(tmp_path), overwrite=True)
    assert cfg.exists() and ilm.exists() and sec.exists()
    assert calls == {"key": True, "enc": True}
    assert ex.exists()


def test_max_ignore_none_raises_for_incomparable_values():
    with pytest.raises(TypeError):
        ldb_utils.max_ignore_none(["string", 1])


def test_resolve_schema_file_explicit_file_not_found_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        ldb_utils, "get_config_roots", lambda appname=ldb_utils.APPNAME: (tmp_path / "user", tmp_path / "sys")
    )
    for root in (tmp_path / "user", tmp_path / "sys"):
        (root / "schemas" / "s").mkdir(parents=True, exist_ok=True)
    with pytest.raises((ConfigurationError, FileNotFoundError, ValueError)):
        ldb_utils.resolve_schema_file(
            schema="s",
            profile=None,
            explicit_config_dir=None,
            explicit_file="nonexistent.yml",
            prefix_name="config",
            extension_name="yml",
            description="cfg",
        )


def test_write_or_update_secrets_raises_for_valid_json_non_dict_root(tmp_path: Path):
    secrets_path = tmp_path / "schemas" / "s" / "secrets.json"
    secrets_path.parent.mkdir(parents=True)
    secrets_path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(SecretError):
        ldb_utils.write_or_update_secrets("s", None, str(tmp_path))
    assert secrets_path.read_text(encoding="utf-8") == "[1, 2, 3]"


def test_encrypt_secrets_in_place_raises_for_valid_json_non_dict_root(tmp_path: Path):
    secrets_path = tmp_path / "schemas" / "s" / "secrets.json"
    secrets_path.parent.mkdir(parents=True)
    secrets_path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(SecretError):
        ldb_utils.encrypt_secrets_in_place("s", None, str(tmp_path))
    assert secrets_path.read_text(encoding="utf-8") == "[1, 2, 3]"


def test_render_config_template_includes_literal_choices():
    output = ldb_utils.render_config_template_with_help()
    assert isinstance(output, str)
    assert len(output) > 0


def test_write_config_yaml_no_overwrite_when_file_exists(tmp_path: Path):
    first = ldb_utils.write_config_yaml("s", None, str(tmp_path), overwrite=True)
    first.write_text("original: true\n", encoding="utf-8")
    second = ldb_utils.write_config_yaml("s", None, str(tmp_path), overwrite=False)
    assert second == first
    assert second.read_text(encoding="utf-8") == "original: true\n"


def test_write_ilm_yaml_no_overwrite_when_file_exists(tmp_path: Path):
    first = ldb_utils.write_ilm_yaml("s", None, str(tmp_path), overwrite=True)
    first.write_text("original: []\n", encoding="utf-8")
    second = ldb_utils.write_ilm_yaml("s", None, str(tmp_path), overwrite=False)
    assert second == first
    assert second.read_text(encoding="utf-8") == "original: []\n"


def test_write_ilm_example_no_overwrite_when_file_exists(tmp_path: Path):
    first = ldb_utils.write_ilm_example("s", None, str(tmp_path), overwrite=True)
    first.write_text("original example\n", encoding="utf-8")
    second = ldb_utils.write_ilm_example("s", None, str(tmp_path), overwrite=False)
    assert second == first
    assert second.read_text(encoding="utf-8") == "original example\n"


def test_init_schema_without_examples(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(ldb_utils, "load_or_create_key", lambda: None)
    monkeypatch.setattr(ldb_utils, "encrypt_secrets_in_place", lambda *a, **k: None)
    cfg, ilm, sec, ex = ldb_utils.init_schema(
        schema="s", profile=None, config_root=str(tmp_path), overwrite=True, with_examples=False
    )
    assert cfg.exists() and ilm.exists() and sec.exists()
    assert ex is None


def test_init_schema_without_auto_encrypt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    calls: dict = {}
    monkeypatch.setattr(ldb_utils, "load_or_create_key", lambda: None)
    monkeypatch.setattr(ldb_utils, "encrypt_secrets_in_place", lambda *a, **k: calls.setdefault("enc", True))
    ldb_utils.init_schema(schema="s", profile=None, config_root=str(tmp_path), overwrite=True, auto_encrypt=False)
    assert "enc" not in calls


def test_invalid_existing_secrets_are_not_overwritten(tmp_path: Path):
    secrets_path = tmp_path / "schemas" / "s" / "secrets.json"
    secrets_path.parent.mkdir(parents=True)
    secrets_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(SecretError):
        ldb_utils.write_or_update_secrets("s", None, str(tmp_path))
    with pytest.raises(SecretError):
        ldb_utils.encrypt_secrets_in_place("s", None, str(tmp_path))

    assert secrets_path.read_text(encoding="utf-8") == "{broken"
