import json
from dataclasses import FrozenInstanceError
from json import JSONDecodeError
from pathlib import Path
from types import SimpleNamespace

import pytest

from limitsdb.core import ldb_config_loader, ldb_params_config, ldb_utils
from limitsdb.core.ldb_errors import ConfigurationError, SecretError

# ---------------------------------------------------------------------------
# Overlay precedence — load_runtime_config
# ---------------------------------------------------------------------------


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


def test_empty_config_file_is_treated_as_empty_layer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/configuration-contract.md — an empty YAML file contributes nothing;
    # the layer is silently skipped as if the file were absent
    # Given: an empty config file resolved as the explicit config
    empty = tmp_path / "empty.yml"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        ldb_config_loader,
        "resolve_schema_file",
        lambda **k: str(empty) if k["prefix_name"] == "config" else None,
    )

    # When: config is loaded with that explicit file
    cfg = ldb_config_loader.load_runtime_config(schema="s", profile=None, cli_sets={}, explicit_config_file=str(empty))

    # Then: result is an empty dict — no keys injected from the empty file
    assert isinstance(cfg, dict)
    assert "foo" not in cfg


def test_config_file_with_list_root_raises_configuration_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/exception-handling.md — ConfigurationError when config root is not a
    # mapping; YAML list roots are invalid and must be rejected on load
    # Given: a config file whose root is a YAML list
    list_file = tmp_path / "list.yml"
    list_file.write_text("- item1\n- item2\n", encoding="utf-8")
    monkeypatch.setattr(ldb_config_loader, "resolve_schema_file", lambda **k: str(list_file))

    # When / Then: ConfigurationError is raised — list root is not a valid config
    with pytest.raises(ConfigurationError):
        ldb_config_loader.load_runtime_config(
            schema="s", profile=None, cli_sets={}, explicit_config_file=str(list_file)
        )


def test_secrets_file_with_list_root_raises_secret_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/exception-handling.md — SecretError when secrets file root is not a
    # mapping; a JSON array at root indicates a malformed secrets file
    # Given: a secrets file whose JSON root is an array
    schema_dir = tmp_path / "user" / "schemas" / "s"
    schema_dir.mkdir(parents=True)
    secrets_file = schema_dir / "secrets.json"
    secrets_file.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setattr(
        ldb_config_loader,
        "resolve_schema_file",
        lambda **k: str(secrets_file) if k["prefix_name"] == "secrets" else None,
    )
    monkeypatch.setattr(ldb_config_loader, "secret_keys_from_config", lambda: ["source_password"])

    # When / Then: SecretError is raised — non-dict secrets root is never accepted
    with pytest.raises(SecretError):
        ldb_config_loader.load_runtime_config(schema="s", profile=None, cli_sets={})


def test_read_env_overrides_coerces_float_values(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/configuration-contract.md — registered env vars are coerced: booleans,
    # integers, floats and strings are inferred from the string representation
    # Given: an env var with a float value
    monkeypatch.setenv("LDB_RATIO", "3.14")

    # When: env overrides are read
    env = ldb_config_loader.read_env_overrides({"LDB_RATIO": "ratio"})

    # Then: the value is a float
    assert env == {"ratio": 3.14}


def test_secret_key_with_null_value_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Secrets & Encryption — a registered secret key that is null or
    # empty in the secrets file is silently skipped; it does not cause an error
    # Given: a secrets file where the registered key has a null value
    schema_dir = tmp_path / "user" / "schemas" / "s"
    schema_dir.mkdir(parents=True)
    import json

    secrets_file = schema_dir / "secrets.json"
    secrets_file.write_text(json.dumps({"source_password": None}), encoding="utf-8")
    monkeypatch.setattr(
        ldb_config_loader,
        "resolve_schema_file",
        lambda **k: str(secrets_file) if k["prefix_name"] == "secrets" else None,
    )
    monkeypatch.setattr(ldb_config_loader, "secret_keys_from_config", lambda: ["source_password"])

    # When: config is loaded
    cfg = ldb_config_loader.load_runtime_config(schema="s", profile=None, cli_sets={}, enforce_encrypted_secrets=False)

    # Then: source_password is absent — null value is skipped, not injected
    assert "source_password" not in cfg


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


# ---------------------------------------------------------------------------
# Config schema — Config type contract
# ---------------------------------------------------------------------------


def test_dry_run_mode_is_normalized_to_preview():
    # Spec: docs/configuration-contract.md — DRY_RUN is a deprecated alias for PREVIEW
    # Given: a config built with the legacy DRY_RUN mode name
    # When: the Config is constructed
    cfg = ldb_params_config.Config.from_dict(
        {
            "schema": "s",
            "mode": "DRY_RUN",
            "source_dsn": "dsn",
            "source_username": "user",
            "source_password": "secret",
        }
    )

    # Then: mode is silently normalised to PREVIEW
    assert cfg.mode == "PREVIEW"


def test_unknown_config_keys_fail_closed():
    # Spec: docs/configuration-contract.md — unknown keys are rejected to catch typos early
    # Given: a dict with an unrecognised key
    # When / Then: from_dict raises ValueError naming the unknown key
    with pytest.raises(ValueError, match="Unknown configuration keys"):
        ldb_params_config.Config.from_dict({"schema": "s", "unexpected": True})


def test_to_dict_stays_flat_and_excludes_typed_views():
    # Spec: docs/configuration-contract.md — public configuration is flat; typed views
    # (execution, connections, administration, context) do not appear in the exported dict
    # Given: a config with operational values
    cfg = ldb_params_config.Config.from_dict(
        {
            "schema": "s",
            "mode": "DRY_RUN",
            "source_dsn": "dsn",
            "source_username": "user",
            "source_password": "secret",
            "admin_source_username": "admin",
        }
    )

    # When: the config is exported
    exported = cfg.to_dict()

    # Then: the dict is flat and contains only raw keys
    assert exported["admin_source_username"] == "admin"
    assert "execution" not in exported
    assert "connections" not in exported


def test_administration_and_context_views_are_excluded_from_dict():
    # Spec: docs/configuration-contract.md — typed views (administration, context)
    # are excluded from the public flat dict to avoid serialising internal structure
    # Given: a PLAN config with admin credentials
    cfg = ldb_params_config.Config(
        schema="s",
        profile="prod",
        mode="PLAN",
        ilm_config_file="ilm.prod.yml",
        admin_source_username="source_admin",
        admin_source_password="source_secret",
        source_default_tablespace="SOURCE_DATA",
        source_role_name="SOURCE_ROLE",
    )

    # Then: administration and context views are accessible but absent from to_dict
    assert cfg.administration.source == ldb_params_config.AdministrativeCredentials(
        username="source_admin", password="source_secret"
    )
    assert cfg.context == ldb_params_config.RuntimeContext(schema="s", profile="prod", ilm_config_file="ilm.prod.yml")
    assert "administration" not in cfg.to_dict()
    assert "context" not in cfg.to_dict()


def test_cli_set_args_are_parsed_and_passed_to_config_loader(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/configuration-contract.md > overlay precedence — --set values are
    # coerced: int, bool, and str are inferred from the string representation
    # Given: a parser-produced args object with typed --set pairs
    args = ldb_params_config.build_argparser_from_config().parse_args(["--schema", "myschema"])
    args.set = ["a=1", "b=true", "c=text"]
    captured: dict = {}

    def fake_loader(**kwargs):
        captured.update(kwargs.get("cli_sets", {}))
        return {}

    monkeypatch.setattr(ldb_params_config, "load_runtime_config", fake_loader)
    monkeypatch.setattr(ldb_params_config, "env_bindings_from_config", lambda: {})
    monkeypatch.setattr(ldb_params_config, "read_env_overrides", lambda bindings: {})

    # When: build_config processes the --set pairs
    ldb_params_config.build_config({"base": 1}, args)

    # Then: integer, boolean and string types are inferred correctly
    assert captured["a"] == 1
    assert captured["b"] is True
    assert captured["c"] == "text"


def test_env_metadata_is_authoritative_and_ilm_name_is_normalized(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/configuration-contract.md — only fields with Env(...) are exposed;
    # LDB_ILM_CONFIG_FILE is the canonical name; use_added_columns and add_ldb_columns
    # are excluded from env overrides (must be set in persistent YAML only)
    # Given: the binding map from the live Config schema
    bindings = ldb_params_config.env_bindings_from_config()

    # Then: canonical names are present and excluded names are absent
    assert bindings["LDB_CHUNK_SIZE"] == "chunk_size"
    assert bindings["LDB_ILM_CONFIG_FILE"] == "ilm_config_file"
    assert "ILM_CONFIG_FILE" not in bindings
    assert "LDB_USE_ADDED_COLUMNS" not in bindings
    assert "LDB_ADD_LDB_COLUMNS" not in bindings

    # When: registered and unregistered env vars coexist
    monkeypatch.setenv("LDB_CHUNK_SIZE", "250")
    monkeypatch.setenv("LDB_ILM_CONFIG_FILE", "current.yml")
    monkeypatch.setenv("ILM_CONFIG_FILE", "legacy.yml")
    monkeypatch.setenv("LDB_USE_ADDED_COLUMNS", "false")

    # Then: only registered vars are returned with correct types; unregistered are ignored
    assert ldb_params_config.read_env_overrides(bindings) == {
        "chunk_size": 250,
        "ilm_config_file": "current.yml",
    }


def test_persistent_column_settings_cannot_be_overridden_via_cli_set(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/configuration-contract.md (DEC-013) — use_added_columns and
    # add_ldb_columns are set in persistent YAML only; --set must reject them to
    # prevent historical processing instability
    # Given: --set args attempting to override column settings
    monkeypatch.setattr(ldb_params_config, "env_bindings_from_config", lambda: {})
    monkeypatch.setattr(ldb_params_config, "read_env_overrides", lambda bindings: {})

    for key in ("use_added_columns", "add_ldb_columns"):
        args = SimpleNamespace(schema="s", profile=None, set=[f"{key}=false"])

        # When / Then: build_config rejects the override with a message naming the restriction
        with pytest.raises(ValueError, match="persistent YAML"):
            ldb_params_config.build_config({}, args)


def test_env_bindings_raises_for_duplicate_ldb_var(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/configuration-contract.md — each LDB_* variable must map to exactly
    # one config key; duplicate mappings indicate a Config schema authoring error
    # Given: the real type hints augmented with a duplicate Env mapping
    from typing import Annotated, get_args

    from limitsdb.core.ldb_params_config import Config, Env

    real_hints = ldb_params_config.get_type_hints(Config, include_extras=True)
    duplicate_env = next(m.name for ann in real_hints.values() for m in get_args(ann)[1:] if isinstance(m, Env))
    fake_hints = {**real_hints, "_dup_field": Annotated[str, Env(duplicate_env)]}
    monkeypatch.setattr(ldb_params_config, "get_type_hints", lambda cls, **kw: fake_hints)

    # When / Then: building the binding map raises RuntimeError naming the duplicate
    with pytest.raises(RuntimeError, match="Duplicate"):
        ldb_params_config.env_bindings_from_config()


def test_build_config_raises_when_schema_is_missing(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM — schema is required; missing schema is caught before
    # any file resolution
    # Given: args with no schema value
    monkeypatch.setattr(ldb_params_config, "env_bindings_from_config", lambda: {})
    monkeypatch.setattr(ldb_params_config, "read_env_overrides", lambda bindings: {})
    args = SimpleNamespace(schema=None, profile=None, set=[])

    # When / Then: ValueError names the missing schema
    with pytest.raises(ValueError, match="schema is required"):
        ldb_params_config.build_config({}, args)


def test_build_config_passes_profile_to_config_loader(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/configuration-contract.md — profile selects the config file variant
    # (e.g. config.dev.yml vs config.prod.yml); it is forwarded to load_runtime_config
    # Given: args with a non-null profile
    monkeypatch.setattr(ldb_params_config, "env_bindings_from_config", lambda: {})
    monkeypatch.setattr(ldb_params_config, "read_env_overrides", lambda bindings: {})
    monkeypatch.setattr(
        ldb_params_config,
        "load_runtime_config",
        lambda **kw: {"schema": "s", "profile": kw.get("profile")},
    )
    args = SimpleNamespace(schema="s", profile="prod", set=[])

    # When: build_config is called
    result = ldb_params_config.build_config({}, args)

    # Then: the profile is present in the resulting config
    assert result["profile"] == "prod"


# ---------------------------------------------------------------------------
# Schema file resolution
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Config typed views
# ---------------------------------------------------------------------------


def test_execution_view_is_frozen_and_reflects_config():
    # Spec: docs/configuration-contract.md — ExecutionConfig is an immutable typed
    # view over the flat config; callers cannot modify it
    # Given: a SCRIPT config
    cfg = ldb_params_config.Config(
        schema="s",
        mode="SCRIPT",
        chunk_size=250,
        source_dsn="dsn",
        source_username="user",
        source_password="secret",
    )

    # When: the execution view is read
    exec_view = cfg.execution

    # Then: values match the flat config and mutation raises
    assert exec_view == ldb_params_config.ExecutionConfig(
        action="SOURCE_ILM",
        mode="SCRIPT",
        chunk_size=250,
        use_added_columns=True,
        add_ldb_columns=True,
        generate_script=True,
        parallel_max=10,
        log_level="INFO",
    )
    assert cfg.to_dict()["chunk_size"] == 250
    with pytest.raises(FrozenInstanceError):
        cfg.execution.chunk_size = 500  # type: ignore[misc]


def test_connection_view_groups_endpoint_per_environment():
    # Spec: docs/configuration-contract.md — DatabaseEndpoint groups dsn, username,
    # password per environment; accessible via connections.source / connections.history
    # Given: a config with explicit source and history endpoints
    cfg = ldb_params_config.Config(
        schema="s",
        mode="SCRIPT",
        source_dsn="dsn",
        source_username="user",
        source_password="secret",
    )

    # Then: source endpoint matches the flat fields
    assert cfg.connections.source == ldb_params_config.DatabaseEndpoint(dsn="dsn", username="user", password="secret")
