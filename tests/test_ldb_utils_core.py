import json
from pathlib import Path

import pytest

from limitsdb.core import ldb_utils
from limitsdb.core.ldb_errors import ConfigurationError, SecretError
from limitsdb.core.ldb_params_config import Config


def test_nvl_returns_value_when_present_and_fallback_when_none():
    # Spec: README > utility — nvl mirrors Oracle NVL: return the first non-None value
    # Given: a non-None value and a None value
    # When / Then: the non-None value wins; the fallback is used for None
    assert ldb_utils.nvl("val", "default") == "val"
    assert ldb_utils.nvl(None, "fallback") == "fallback"


def test_max_ignore_none_returns_maximum_skipping_none_values():
    # Spec: README > utility — max_ignore_none skips None entries in the sequence
    # Given: a sequence mixing None and integers
    # When / Then: the maximum non-None value is returned; all-None gives None
    assert ldb_utils.max_ignore_none([None, 3, 2, None, 5]) == 5
    assert ldb_utils.max_ignore_none([None, None]) is None


def test_max_ignore_none_raises_for_incomparable_mixed_types():
    # Spec: README > utility — max_ignore_none propagates TypeError for incomparable values
    # Given: a sequence of string and integer (incomparable)
    # When / Then: TypeError propagates unchanged
    with pytest.raises(TypeError):
        ldb_utils.max_ignore_none(["string", 1])


def test_indent_lines_indents_continuation_lines_only():
    # Spec: README > utility — indent_lines adds prefix spaces to all lines except
    # the first, preserving the first line's position
    # Given: a multi-line text
    text = "line1\nline2\nline3"

    # When: indented by 2
    result = ldb_utils.indent_lines(text, 2)

    # Then: only continuation lines are indented
    assert result == "line1\n  line2\n  line3"


def test_join_wrapped_splits_at_max_width_on_separator():
    # Spec: README > utility — join_wrapped emits the separator at the start of a
    # wrapped continuation to preserve SQL readability
    # Given: items that exceed the max-width when joined
    # When: wrapped with width 4
    wrapped = ldb_utils.join_wrapped(",", ["a", "b", "long_word"], 4)

    # Then: the long word starts a new line prefixed by the separator
    assert wrapped.splitlines() == ["a,b", ",long_word"]


def test_get_effective_credentials_returns_source_by_default():
    # Spec: README > Run ILM — source credentials are used for SOURCE_ILM and as default
    # Given: a config with both source and history credentials
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

    # When: credentials are resolved for the default (source) action
    # Then: source credentials are returned
    assert ldb_utils.get_effective_credentials(cfg) == ("src", "pw1", "dsn1")


def test_get_effective_credentials_returns_history_for_history_ilm():
    # Spec: README > Run ILM > --action HISTORY_ILM — history credentials are used
    # Given: a config with both environments populated
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
    cfg.action = "HISTORY_ILM"

    # When: credentials are resolved for HISTORY_ILM
    # Then: history credentials are returned
    assert ldb_utils.get_effective_credentials(cfg) == ("hist", "pw2", "dsn2")


def test_get_effective_credentials_uses_admin_when_requested():
    # Spec: README > Run ILM > ldb-impl — admin credentials used for DDL operations
    # Given: a config with admin credentials for history
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
    cfg.action = "HISTORY_ILM"

    # When: admin credentials are requested
    # Then: admin history credentials are returned
    assert ldb_utils.get_effective_credentials(cfg, admin=True) == ("admin_hist", "apw2", "dsn2")


def test_get_effective_credentials_raises_when_password_is_empty():
    # Spec: docs/exception-handling.md — missing credentials raise before any connection
    # Given: a config with empty history password
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
    cfg.action = "HISTORY_ILM"
    cfg.history_password = ""

    # When / Then: ValueError is raised because credentials are incomplete
    with pytest.raises(ValueError):
        ldb_utils.get_effective_credentials(cfg)


def test_resolve_schema_file_finds_user_level_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/configuration-contract.md — schema files are resolved from user
    # then system roots; user-level files take precedence
    # Given: a user-level config file at the expected path
    monkeypatch.setattr(
        ldb_utils,
        "get_config_roots",
        lambda appname=ldb_utils.APPNAME: (tmp_path / "user", tmp_path / "sys"),
    )
    (tmp_path / "user" / "schemas" / "demo").mkdir(parents=True)
    (tmp_path / "sys" / "schemas" / "demo").mkdir(parents=True)
    config_path = tmp_path / "user" / "schemas" / "demo" / "config.yml"
    config_path.write_text("key: value\n", encoding="utf-8")

    # When: the config file for schema "demo" is resolved
    resolved = ldb_utils.resolve_schema_file(
        schema="demo",
        profile=None,
        explicit_config_dir=None,
        explicit_file=None,
        prefix_name="config",
        extension_name="yml",
        description="cfg",
    )

    # Then: the user-level path is returned
    assert resolved == str(config_path.resolve())


def test_resolve_schema_file_with_explicit_nonexistent_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/exception-handling.md — ConfigurationError when an explicitly named
    # file does not exist
    # Given: no file at the given explicit path
    monkeypatch.setattr(
        ldb_utils,
        "get_config_roots",
        lambda appname=ldb_utils.APPNAME: (tmp_path / "user", tmp_path / "sys"),
    )
    (tmp_path / "user" / "schemas" / "s").mkdir(parents=True)
    (tmp_path / "sys" / "schemas" / "s").mkdir(parents=True)

    # When / Then: resolver raises for a missing explicit file
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


def test_write_or_update_secrets_creates_initial_file(tmp_path: Path):
    # Spec: README > ldb-init — secrets file is created with an empty dict when absent
    # Given: no existing secrets file
    # When: write_or_update_secrets is called
    secret_file = ldb_utils.write_or_update_secrets("demo", None, str(tmp_path))

    # Then: file exists and contains a dict
    data = json.loads(secret_file.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


def test_write_or_update_secrets_does_not_overwrite_valid_json_array(tmp_path: Path):
    # Spec: docs/exception-handling.md — SecretError when the secrets file root
    # is not a dict; the original file must be preserved unmodified
    # Given: an existing secrets file with a JSON array at root
    secrets_path = tmp_path / "schemas" / "s" / "secrets.json"
    secrets_path.parent.mkdir(parents=True)
    secrets_path.write_text("[1, 2, 3]", encoding="utf-8")

    # When / Then: SecretError is raised AND the original file is untouched
    with pytest.raises(SecretError):
        ldb_utils.write_or_update_secrets("s", None, str(tmp_path))
    assert secrets_path.read_text(encoding="utf-8") == "[1, 2, 3]"


def test_encrypt_secrets_in_place_does_not_overwrite_valid_json_array(tmp_path: Path):
    # Spec: docs/exception-handling.md — SecretError when secrets file has non-dict
    # root; must not modify the file
    # Given: a secrets file with a JSON array
    secrets_path = tmp_path / "schemas" / "s" / "secrets.json"
    secrets_path.parent.mkdir(parents=True)
    secrets_path.write_text("[1, 2, 3]", encoding="utf-8")

    # When / Then: SecretError is raised and file is unchanged
    with pytest.raises(SecretError):
        ldb_utils.encrypt_secrets_in_place("s", None, str(tmp_path))
    assert secrets_path.read_text(encoding="utf-8") == "[1, 2, 3]"


def test_invalid_existing_secrets_are_not_overwritten(tmp_path: Path):
    # Spec: docs/exception-handling.md — SecretError: existing secrets files with
    # invalid JSON are rejected; neither operation may modify the file
    # Given: a broken secrets file
    secrets_path = tmp_path / "schemas" / "s" / "secrets.json"
    secrets_path.parent.mkdir(parents=True)
    secrets_path.write_text("{broken", encoding="utf-8")

    # When / Then: both operations raise SecretError and the file is unchanged
    with pytest.raises(SecretError):
        ldb_utils.write_or_update_secrets("s", None, str(tmp_path))
    with pytest.raises(SecretError):
        ldb_utils.encrypt_secrets_in_place("s", None, str(tmp_path))
    assert secrets_path.read_text(encoding="utf-8") == "{broken"


def test_init_schema_creates_config_ilm_and_secrets_and_encrypts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Spec: README > ldb-init — creates config.yml, ilm.yml, secrets.json and
    # ilm.example.yml; encrypts any plaintext secrets
    # Given: a clean config root with encryption mocked
    calls = {}
    monkeypatch.setattr(ldb_utils, "load_or_create_key", lambda: calls.setdefault("key", True))
    monkeypatch.setattr(ldb_utils, "encrypt_secrets_in_place", lambda *a, **k: calls.setdefault("enc", True))

    # When: init_schema is called
    cfg, ilm, sec, ex = ldb_utils.init_schema(schema="s", profile=None, config_root=str(tmp_path), overwrite=True)

    # Then: all four files exist and encryption was performed
    assert cfg.exists() and ilm.exists() and sec.exists() and ex.exists()
    assert calls == {"key": True, "enc": True}


def test_init_schema_without_examples_returns_none_for_example_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Spec: README > ldb-init > --no-examples — skips the ilm.example.yml file
    # Given: with_examples=False
    monkeypatch.setattr(ldb_utils, "load_or_create_key", lambda: None)
    monkeypatch.setattr(ldb_utils, "encrypt_secrets_in_place", lambda *a, **k: None)

    # When: init_schema is called without examples
    cfg, ilm, sec, ex = ldb_utils.init_schema(
        schema="s", profile=None, config_root=str(tmp_path), overwrite=True, with_examples=False
    )

    # Then: main files exist but example path is None
    assert cfg.exists() and ilm.exists() and sec.exists()
    assert ex is None


def test_init_schema_without_auto_encrypt_skips_encryption(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Spec: README > ldb-init — auto_encrypt=False skips the encryption step so
    # secrets can be filled in manually before encrypting separately
    # Given: auto_encrypt disabled
    calls: dict = {}
    monkeypatch.setattr(ldb_utils, "load_or_create_key", lambda: None)
    monkeypatch.setattr(ldb_utils, "encrypt_secrets_in_place", lambda *a, **k: calls.setdefault("enc", True))

    # When: init_schema is called with auto_encrypt=False
    ldb_utils.init_schema(schema="s", profile=None, config_root=str(tmp_path), overwrite=True, auto_encrypt=False)

    # Then: encryption was not called
    assert "enc" not in calls


def test_render_config_template_returns_non_empty_string():
    # Spec: README > ldb-init — generated config.yml includes inline help for
    # all supported options
    # Given: no specific prerequisites
    # When: template is rendered
    output = ldb_utils.render_config_template_with_help()

    # Then: output is a non-empty string
    assert isinstance(output, str)
    assert len(output) > 0


def test_write_config_yaml_no_overwrite_when_file_exists(tmp_path: Path):
    # Spec: README > ldb-init > --overwrite — without overwrite flag an existing file
    # is preserved unchanged
    # Given: an existing config file with custom content
    first = ldb_utils.write_config_yaml("s", None, str(tmp_path), overwrite=True)
    first.write_text("original: true\n", encoding="utf-8")

    # When: write_config_yaml is called again without overwrite
    second = ldb_utils.write_config_yaml("s", None, str(tmp_path), overwrite=False)

    # Then: the file is unchanged
    assert second == first
    assert second.read_text(encoding="utf-8") == "original: true\n"


def test_write_ilm_yaml_no_overwrite_when_file_exists(tmp_path: Path):
    # Spec: README > ldb-init > --overwrite — without overwrite the ILM file is
    # preserved unchanged
    # Given: an existing ilm.yml with custom content
    first = ldb_utils.write_ilm_yaml("s", None, str(tmp_path), overwrite=True)
    first.write_text("original: []\n", encoding="utf-8")

    # When: write_ilm_yaml is called again without overwrite
    second = ldb_utils.write_ilm_yaml("s", None, str(tmp_path), overwrite=False)

    # Then: the file is unchanged
    assert second == first
    assert second.read_text(encoding="utf-8") == "original: []\n"


def test_write_ilm_example_no_overwrite_when_file_exists(tmp_path: Path):
    # Spec: README > ldb-init > --overwrite — without overwrite the example file is
    # preserved unchanged
    # Given: an existing example file with custom content
    first = ldb_utils.write_ilm_example("s", None, str(tmp_path), overwrite=True)
    first.write_text("original example\n", encoding="utf-8")

    # When: write_ilm_example is called again without overwrite
    second = ldb_utils.write_ilm_example("s", None, str(tmp_path), overwrite=False)

    # Then: the file is unchanged
    assert second == first
    assert second.read_text(encoding="utf-8") == "original example\n"
