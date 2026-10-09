import json
from json import JSONDecodeError
from pathlib import Path

import pytest

from limitsdb.core import ldb_config_loader
from limitsdb.core.ldb_errors import ConfigurationError, SecretError


def test_cli_set_overrides_file_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/configuration-contract.md > overlay precedence — CLI/--set wins
    # over YAML files
    # Given: a config file with foo=1 and a CLI override foo=99
    schema_dir = tmp_path / "user" / "schemas" / "s"
    schema_dir.mkdir(parents=True)
    (schema_dir / "config.yml").write_text("foo: 1\n", encoding="utf-8")
    monkeypatch.setattr(
        ldb_config_loader,
        "resolve_schema_file",
        lambda **k: str(schema_dir / "config.yml") if k["prefix_name"] == "config" else None,
    )

    # When: config is loaded with a --set override
    cfg = ldb_config_loader.load_runtime_config(schema="s", profile=None, cli_sets={"foo": 99})

    # Then: the CLI value wins
    assert cfg["foo"] == 99


def test_dotted_cli_set_creates_nested_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/configuration-contract.md > overlay precedence — --set supports
    # dotted keys for nested overrides
    # Given: a config file with a nested structure
    schema_dir = tmp_path / "user" / "schemas" / "s"
    schema_dir.mkdir(parents=True)
    (schema_dir / "config.yml").write_text("bar:\n  nested: true\n", encoding="utf-8")
    monkeypatch.setattr(
        ldb_config_loader,
        "resolve_schema_file",
        lambda **k: str(schema_dir / "config.yml") if k["prefix_name"] == "config" else None,
    )

    # When: a dotted --set is applied
    cfg = ldb_config_loader.load_runtime_config(schema="s", profile=None, cli_sets={"bar.nested": False})

    # Then: the nested value is overridden
    assert cfg["bar"]["nested"] is False


def test_env_variable_overrides_file_value(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/configuration-contract.md > overlay precedence — env LDB_*
    # overrides YAML but is overridden by CLI
    # Given: LDB_CHUNK_SIZE in environment
    monkeypatch.setenv("LDB_CHUNK_SIZE", "500")
    monkeypatch.setattr(ldb_config_loader, "resolve_schema_file", lambda **k: None)

    # When: config is loaded with that env binding
    cfg = ldb_config_loader.load_runtime_config(
        schema="s",
        profile=None,
        cli_sets={},
        env_bindings={"LDB_CHUNK_SIZE": "chunk_size"},
    )

    # Then: the env value is reflected
    assert cfg["chunk_size"] == 500


def test_precedence_is_system_user_explicit_env_cli_then_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/configuration-contract.md > overlay precedence (full order)
    # Given: overlapping values at every level
    system_root = tmp_path / "system"
    user_root = tmp_path / "user"
    (system_root / "schemas" / "s").mkdir(parents=True)
    (user_root / "schemas" / "s").mkdir(parents=True)
    (system_root / "schemas" / "s" / "config.dev.yml").write_text(
        "winner: system\nsystem_only: true\nnested: {system: true, winner: system}\n",
        encoding="utf-8",
    )
    (user_root / "schemas" / "s" / "config.dev.yml").write_text(
        "winner: user\nuser_only: true\nnested: {user: true, winner: user}\n",
        encoding="utf-8",
    )
    explicit = tmp_path / "extra.yml"
    explicit.write_text("winner: explicit\nnested: {explicit: true, winner: explicit}\n", encoding="utf-8")
    secrets = user_root / "schemas" / "s" / "secrets.dev.json"
    secrets.write_text(json.dumps({"source_password": "enc:v1:aes256gcm:test"}), encoding="utf-8")

    monkeypatch.setattr(ldb_config_loader, "get_config_roots", lambda: (user_root, system_root))
    original_resolver = ldb_config_loader.resolve_schema_file

    def resolve_for_test(**kwargs):
        if kwargs["prefix_name"] == "secrets":
            return str(secrets)
        return original_resolver(**kwargs)

    monkeypatch.setattr(ldb_config_loader, "resolve_schema_file", resolve_for_test)
    monkeypatch.setattr(ldb_config_loader, "secret_keys_from_config", lambda: ["source_password"])
    monkeypatch.setattr(ldb_config_loader, "_IS_ENC", lambda value: True)
    monkeypatch.setattr(ldb_config_loader, "_DECRYPT", lambda value: "secret-wins")
    monkeypatch.setenv("LDB_WINNER", "env")

    # When: config is loaded with all layers active
    cfg = ldb_config_loader.load_runtime_config(
        schema="s",
        profile="dev",
        cli_sets={"winner": "cli", "nested.winner": "cli"},
        env_bindings={"LDB_WINNER": "winner"},
        explicit_config_file=str(explicit),
    )

    # Then: CLI beats env, env beats explicit, explicit beats user, user beats system
    assert cfg["winner"] == "cli"
    assert cfg["system_only"] is True
    assert cfg["user_only"] is True
    assert cfg["nested"] == {"system": True, "user": True, "explicit": True, "winner": "cli"}
    assert cfg["source_password"] == "secret-wins"


def test_plaintext_secret_is_rejected_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Secrets & Encryption — loader rejects plaintext secrets by default
    # Given: a secrets file with an unencrypted password
    schema_dir = tmp_path / "user" / "schemas" / "s"
    schema_dir.mkdir(parents=True)
    secrets_file = schema_dir / "secrets.json"
    secrets_file.write_text(json.dumps({"source_password": "plain"}), encoding="utf-8")
    monkeypatch.setattr(ldb_config_loader, "resolve_schema_file", lambda **k: str(secrets_file))
    monkeypatch.setattr(ldb_config_loader, "secret_keys_from_config", lambda: ["source_password"])

    # When / Then: default load raises — plaintext must be encrypted first
    with pytest.raises(ValueError):
        ldb_config_loader.load_runtime_config(schema="s", profile=None, cli_sets={})


def test_plaintext_secret_is_accepted_when_enforcement_is_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Secrets & Encryption — enforcement can be relaxed (e.g. in tests)
    # Given: a secrets file with an unencrypted password
    schema_dir = tmp_path / "user" / "schemas" / "s"
    schema_dir.mkdir(parents=True)
    secrets_file = schema_dir / "secrets.json"
    secrets_file.write_text(json.dumps({"source_password": "plain"}), encoding="utf-8")
    monkeypatch.setattr(ldb_config_loader, "resolve_schema_file", lambda **k: str(secrets_file))
    monkeypatch.setattr(ldb_config_loader, "secret_keys_from_config", lambda: ["source_password"])

    # When: enforcement is disabled
    cfg = ldb_config_loader.load_runtime_config(schema="s", profile=None, cli_sets={}, enforce_encrypted_secrets=False)

    # Then: the plaintext value is loaded as-is
    assert cfg["source_password"] == "plain"


def test_malformed_config_file_raises_configuration_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/exception-handling.md — ConfigurationError when config cannot be read
    # Given: an explicitly selected config YAML file with a syntax error
    broken = tmp_path / "bad.yml"
    broken.write_text("broken: [", encoding="utf-8")
    monkeypatch.setattr(
        ldb_config_loader,
        "resolve_schema_file",
        lambda **k: str(broken) if k["prefix_name"] == "config" else None,
    )

    # When / Then: the parse error propagates as ConfigurationError
    with pytest.raises(ConfigurationError):
        ldb_config_loader.load_runtime_config(
            schema="s",
            profile=None,
            cli_sets={},
            explicit_config_file=str(broken),
        )


def test_malformed_secrets_file_raises_secret_error_with_chained_cause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/exception-handling.md — SecretError: secret files fail;
    # errors chain the original cause
    # Given: a secrets file that is not valid JSON
    schema_dir = tmp_path / "user" / "schemas" / "s"
    schema_dir.mkdir(parents=True)
    secrets_file = schema_dir / "secrets.json"
    secrets_file.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(
        ldb_config_loader,
        "resolve_schema_file",
        lambda **k: str(secrets_file) if k["prefix_name"] == "secrets" else None,
    )
    monkeypatch.setattr(ldb_config_loader, "secret_keys_from_config", lambda: ["source_password"])

    # When: loading the secrets fails
    with pytest.raises(SecretError) as caught:
        ldb_config_loader.load_runtime_config(schema="s", profile=None, cli_sets={})

    # Then: the parse error is chained as __cause__
    assert isinstance(caught.value.__cause__, JSONDecodeError)


def test_decryption_failure_names_secret_without_exposing_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/exception-handling.md — messages identify the operation but never
    # include secret values
    # Given: an encrypted token that fails to decrypt
    schema_dir = tmp_path / "user" / "schemas" / "s"
    schema_dir.mkdir(parents=True)
    token = "enc:v1:aes256gcm:sensitive-token"
    secrets_file = schema_dir / "secrets.json"
    secrets_file.write_text(json.dumps({"source_password": token}), encoding="utf-8")
    monkeypatch.setattr(
        ldb_config_loader,
        "resolve_schema_file",
        lambda **k: None if k["prefix_name"] == "config" else str(secrets_file),
    )
    monkeypatch.setattr(ldb_config_loader, "secret_keys_from_config", lambda: ["source_password"])
    monkeypatch.setattr(ldb_config_loader, "_IS_ENC", lambda value: True)
    monkeypatch.setattr(
        ldb_config_loader,
        "_DECRYPT",
        lambda value: (_ for _ in ()).throw(RuntimeError("bad tag")),
    )

    # When: the decryption failure is encountered
    with pytest.raises(SecretError) as caught:
        ldb_config_loader.load_runtime_config(schema="s", profile=None, cli_sets={})

    # Then: the field name appears in the message but the token value does not
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert "source_password" in str(caught.value)
    assert token not in str(caught.value)


def test_missing_config_file_is_treated_as_empty_layer(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/configuration-contract.md — absent files contribute nothing;
    # the loader does not fail on missing optional files
    # Given: no config or secrets file can be resolved
    monkeypatch.setattr(ldb_config_loader, "resolve_schema_file", lambda **k: None)

    # When: config is loaded
    cfg = ldb_config_loader.load_runtime_config(schema="s", profile=None, cli_sets={})

    # Then: an empty dict is returned without error
    assert isinstance(cfg, dict)


def test_ilm_config_is_loaded_from_resolved_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM > --ilm-config-file — a specific file bypasses discovery
    # Given: an ILM YAML file
    ilm_file = tmp_path / "ilm.yml"
    ilm_file.write_text("tables: []\n", encoding="utf-8")
    monkeypatch.setattr(
        ldb_config_loader,
        "resolve_schema_file",
        lambda **k: str(ilm_file) if k["prefix_name"] == "ilm" else None,
    )

    # When: ILM config is loaded
    result = ldb_config_loader.load_ilm_config(schema="s", profile=None)

    # Then: the file contents are returned
    assert result == {"tables": []}


def test_ilm_config_returns_empty_when_no_file_is_found(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM > --ilm-config-file — file is optional; absent file
    # falls back to DB discovery (not an error at load time)
    # Given: no ILM file can be resolved
    monkeypatch.setattr(ldb_config_loader, "resolve_schema_file", lambda **k: None)

    # When: ILM config is loaded
    result = ldb_config_loader.load_ilm_config(schema="s", profile=None)

    # Then: empty dict is returned — DB discovery happens later in the runner
    assert result == {}


def test_read_env_overrides_maps_registered_variables_and_ignores_unregistered(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: docs/configuration-contract.md — only variables with explicit Env(...)
    # declarations are valid overrides; unregistered LDB_* variables are ignored
    # Given: a mix of registered and unregistered env vars
    monkeypatch.setenv("LDB_FLAG", "true")
    monkeypatch.setenv("LDB_NUMBER", "42")
    monkeypatch.setenv("LDB_UNREGISTERED", "ignored")

    # When: env overrides are read with a restricted binding map
    env = ldb_config_loader.read_env_overrides({"LDB_FLAG": "flag", "LDB_NUMBER": "number"})

    # Then: only registered variables appear with correct type coercion
    assert env == {"flag": True, "number": 42}
    assert "LDB_UNREGISTERED" not in env
