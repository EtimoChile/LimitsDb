from dataclasses import FrozenInstanceError
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


def test_postgres_is_rejected_until_an_adapter_exists():
    with pytest.raises(ValueError, match="unsupported db_engine: postgres; supported engines: oracle"):
        ldb_params_config.Config(schema="s", mode="PLAN", db_engine="postgres")  # type: ignore[arg-type]


def test_flat_config_contract_and_compatibility_behaviour():
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

    assert cfg.mode == "PREVIEW"
    assert cfg.to_dict()["admin_source_username"] == "admin"
    assert "execution" not in cfg.to_dict()
    with pytest.raises(ValueError, match="Unknown configuration keys"):
        ldb_params_config.Config.from_dict({"schema": "s", "unexpected": True})


def test_plan_is_offline_and_script_enables_generation():
    plan = ldb_params_config.Config(schema="s", mode="PLAN")
    script = ldb_params_config.Config(
        schema="s", mode="SCRIPT", source_dsn="dsn", source_username="user", source_password="secret"
    )

    assert plan.source_dsn == ""
    assert script.generate_script is True


def test_history_action_validates_only_history_connection():
    cfg = ldb_params_config.Config(
        schema="s",
        action="HISTORY_ILM",
        history_dsn="dsn",
        history_username="user",
        history_password="secret",
    )
    assert cfg.action == "HISTORY_ILM"

    with pytest.raises(ValueError, match="history_dsn"):
        ldb_params_config.Config(schema="s", action="HISTORY_ILM")


def test_typed_execution_and_connection_views_preserve_flat_contract():
    cfg = ldb_params_config.Config(
        schema="s",
        mode="SCRIPT",
        chunk_size=250,
        source_dsn="dsn",
        source_username="user",
        source_password="secret",
    )

    assert cfg.execution == ldb_params_config.ExecutionConfig(
        action="SOURCE_ILM",
        mode="SCRIPT",
        chunk_size=250,
        use_added_columns=True,
        add_ldb_columns=True,
        generate_script=True,
        parallel_max=10,
        log_level="INFO",
    )
    assert cfg.connections.source == ldb_params_config.DatabaseEndpoint(dsn="dsn", username="user", password="secret")
    assert cfg.to_dict()["chunk_size"] == 250
    assert "execution" not in cfg.to_dict()

    with pytest.raises(FrozenInstanceError):
        cfg.execution.chunk_size = 500  # type: ignore[misc]


def test_typed_administration_and_context_views_preserve_flat_contract():
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

    assert cfg.administration.source == ldb_params_config.AdministrativeCredentials(
        username="source_admin", password="source_secret"
    )
    assert cfg.administration.source_default_tablespace == "SOURCE_DATA"
    assert cfg.administration.source_role_name == "SOURCE_ROLE"
    assert cfg.context == ldb_params_config.RuntimeContext(schema="s", profile="prod", ilm_config_file="ilm.prod.yml")
    assert "administration" not in cfg.to_dict()
    assert "context" not in cfg.to_dict()


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


def test_env_metadata_is_authoritative_and_ilm_name_is_normalized(monkeypatch: pytest.MonkeyPatch):
    bindings = ldb_params_config.env_bindings_from_config()

    assert bindings["LDB_CHUNK_SIZE"] == "chunk_size"
    assert bindings["LDB_ILM_CONFIG_FILE"] == "ilm_config_file"
    assert "ILM_CONFIG_FILE" not in bindings
    assert "LDB_USE_ADDED_COLUMNS" not in bindings
    assert "LDB_ADD_LDB_COLUMNS" not in bindings

    monkeypatch.setenv("LDB_CHUNK_SIZE", "250")
    monkeypatch.setenv("LDB_ILM_CONFIG_FILE", "current.yml")
    monkeypatch.setenv("ILM_CONFIG_FILE", "legacy.yml")
    monkeypatch.setenv("LDB_USE_ADDED_COLUMNS", "false")
    assert ldb_params_config.read_env_overrides(bindings) == {
        "chunk_size": 250,
        "ilm_config_file": "current.yml",
    }


@pytest.mark.parametrize("key", ["use_added_columns", "add_ldb_columns"])
def test_history_column_settings_reject_cli_set(key: str):
    with pytest.raises(ValueError, match="persistent YAML"):
        ldb_params_config._parse_cli_sets([f"{key}=false"])


def test_argparser_choices_requirements(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LDB_SCHEMA", raising=False)
    parser = ldb_params_config.build_argparser_from_config()
    with pytest.raises(SystemExit):
        parser.parse_args([])
    parsed = parser.parse_args(["--schema", "s", "--mode", "DRY_RUN"])
    assert parsed.mode == "DRY_RUN"
    assert parser.parse_args(["--schema", "s", "--db-engine", "oracle"]).db_engine == "oracle"
    with pytest.raises(SystemExit):
        parser.parse_args(["--schema", "s", "--db-engine", "postgres"])


def test_schema_can_be_bootstrapped_from_registered_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LDB_SCHEMA", "environment-schema")
    parser = ldb_params_config.build_argparser_from_config()

    parsed = parser.parse_args([])
    assert not hasattr(parsed, "schema")
