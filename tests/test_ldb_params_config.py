from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from limitsdb.core import ldb_params_config


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


def test_schema_and_dsn_are_required_for_non_plan_modes():
    # Spec: README > Run ILM — schema, source_dsn and credentials are required
    # Given: configs missing required fields
    # When: Config is constructed with an empty schema
    # Then: ValueError is raised
    with pytest.raises(ValueError):
        ldb_params_config.Config(schema="", source_dsn="d", source_username="u", source_password="p")

    # When: Config is constructed with an empty source DSN
    # Then: ValueError is raised
    with pytest.raises(ValueError):
        ldb_params_config.Config(schema="s", source_dsn="", source_username="u", source_password="p")


def test_postgres_engine_is_rejected_because_no_adapter_exists():
    # Spec: docs/configuration-contract.md — only oracle is supported until
    # a second adapter is contributed
    # Given: a config requesting the postgres engine
    # When / Then: ValueError names the unsupported engine and lists supported ones
    with pytest.raises(ValueError, match="unsupported db_engine: postgres; supported engines: oracle"):
        ldb_params_config.Config(schema="s", mode="PLAN", db_engine="postgres")  # type: ignore[arg-type]


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


def test_plan_mode_requires_no_credentials():
    # Spec: README > Run ILM > --mode PLAN — PLAN evaluates deps without a DB connection
    # Given: a PLAN config with no credentials
    plan = ldb_params_config.Config(schema="s", mode="PLAN")

    # Then: DSN is empty — no connection is attempted in PLAN mode
    assert plan.source_dsn == ""


def test_script_mode_enables_generation_flag():
    # Spec: README > Run ILM > --mode SCRIPT — SCRIPT generates SQL blocks for review
    # Given: a SCRIPT config with minimal credentials
    script = ldb_params_config.Config(
        schema="s",
        mode="SCRIPT",
        source_dsn="dsn",
        source_username="user",
        source_password="secret",
    )

    # Then: generate_script is set to true
    assert script.generate_script is True


def test_history_ilm_action_requires_history_connection():
    # Spec: README > Run ILM > --action HISTORY_ILM — history credentials are required
    # Given: a HISTORY_ILM config with history credentials
    cfg = ldb_params_config.Config(
        schema="s",
        action="HISTORY_ILM",
        history_dsn="dsn",
        history_username="user",
        history_password="secret",
    )

    # Then: action is accepted
    assert cfg.action == "HISTORY_ILM"

    # When: HISTORY_ILM is requested without history_dsn
    # Then: ValueError names the missing field
    with pytest.raises(ValueError, match="history_dsn"):
        ldb_params_config.Config(schema="s", action="HISTORY_ILM")


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


def test_argparser_requires_schema_and_rejects_unsupported_engine(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM — --schema is required; --db-engine accepts only oracle
    # Given: a parser built from the live Config schema
    monkeypatch.delenv("LDB_SCHEMA", raising=False)
    parser = ldb_params_config.build_argparser_from_config()

    # When: no --schema is given
    # Then: parser exits
    with pytest.raises(SystemExit):
        parser.parse_args([])

    # When: DRY_RUN is passed as --mode
    parsed = parser.parse_args(["--schema", "s", "--mode", "DRY_RUN"])
    # Then: it is accepted as a valid choice
    assert parsed.mode == "DRY_RUN"

    # When: oracle is given as --db-engine
    # Then: it is accepted
    assert parser.parse_args(["--schema", "s", "--db-engine", "oracle"]).db_engine == "oracle"

    # When: postgres is given as --db-engine
    # Then: parser exits — not yet supported
    with pytest.raises(SystemExit):
        parser.parse_args(["--schema", "s", "--db-engine", "postgres"])


def test_invalid_mode_raises_validation_error():
    # Spec: README > Run ILM > --mode — only PLAN, VALIDATE, PREVIEW, SCRIPT, EXECUTE
    # are accepted mode names
    # Given: an unrecognised mode string
    # When / Then: ValueError names the invalid mode
    with pytest.raises(ValueError, match="invalid mode"):
        ldb_params_config.Config(schema="s", mode="BADMODE")  # type: ignore[arg-type]


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


def test_schema_can_be_bootstrapped_from_environment_variable(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/configuration-contract.md — LDB_SCHEMA is the env var for schema;
    # when present, --schema is not required on the CLI
    # Given: LDB_SCHEMA is set in the environment
    monkeypatch.setenv("LDB_SCHEMA", "environment-schema")
    parser = ldb_params_config.build_argparser_from_config()

    # When: no --schema is given
    parsed = parser.parse_args([])

    # Then: schema is not in parsed args (it is resolved from the env during build_config)
    assert not hasattr(parsed, "schema")
