from types import SimpleNamespace

import pytest

from terminusdb.cli import tdb_run, tdb_impl, tdb_crypt
from terminusdb.core.tdb_params_config import Config


def test_mask_secrets():
    cfg = {"source_password": "secret", "other": 1}
    masked = tdb_run._mask_secrets(cfg)
    assert masked["source_password"] == "****"
    assert masked["other"] == 1


def test_build_control_tables_shapes():
    tables = tdb_impl._build_control_tables("OWNER")
    assert {t.name for t in tables} == {"TDB_CTL", "TDB_LOG", "TDB_CNF"}
    ctl = next(t for t in tables if t.name == "TDB_CTL")
    assert ctl.primary_key == ("CTL_OWNER", "CTL_TABLE_NAME")


def test_tdb_impl_run_cli(monkeypatch: pytest.MonkeyPatch):
    args = SimpleNamespace(
        schema="s", profile=None, config_dir=None, config_file=None, set=[], log_level="INFO",
        source_username="src", source_password="pw", history_username="hist", history_password="pw2",
        admin_source_username="asrc", admin_source_password="apw", admin_history_username="ah", admin_history_password="ahpw",
        source_dsn="dsn1", history_dsn="dsn2", db_engine="oracle", source_default_tablespace=None, history_default_tablespace=None,
        source_to_history_dblink_name="h", history_to_source_dblink_name="s", source_role_name="SR", history_role_name="HR",
    )
    monkeypatch.setattr(tdb_impl, "_parse_args", lambda: args)
    engine = SimpleNamespace(
        REQUIRED_SYSTEM_PRIVILEGES=("CREATE SESSION",),
        ensure_roles=lambda *a, **k: ["role"],
        ensure_users=lambda *a, **k: ["user"],
        ensure_tables=lambda *a, **k: ["table"],
        ensure_sequences=lambda *a, **k: ["seq"],
        ensure_supporting_objects=lambda *a, **k: ["obj"],
        ensure_database_links=lambda *a, **k: ["dblink"],
        get_connection=lambda *a, **k: SimpleNamespace(close=lambda: None),
        close_connection=lambda *a, **k: None,
    )
    monkeypatch.setattr(tdb_impl, "get_db_engine", lambda eng: engine)
    monkeypatch.setattr(
        tdb_impl,
        "build_config",
        lambda defaults, cli_args: {
            "schema": "s",
            "source_dsn": "d",
            "source_username": "u",
            "source_password": "p",
            "history_dsn": "d2",
            "history_username": "u2",
            "history_password": "p2",
            "admin_source_username": "a1",
            "admin_source_password": "a2",
            "admin_history_username": "a3",
            "admin_history_password": "a4",
            "db_engine": "oracle",
        },
    )
    monkeypatch.setattr(tdb_impl, "encrypt_secrets_in_place", lambda **k: None)
    monkeypatch.setattr(tdb_impl, "load_or_create_key", lambda: None)
    tdb_impl.run_cli()


def test_tdb_crypt_cli(monkeypatch: pytest.MonkeyPatch, tmp_path):
    args = SimpleNamespace(schema="s", profile=None, config_dir=str(tmp_path))
    monkeypatch.setattr(tdb_crypt, "_parse_args", lambda: args)
    monkeypatch.setattr(tdb_crypt, "load_or_create_key", lambda: None)
    monkeypatch.setattr(tdb_crypt, "encrypt_secrets_in_place", lambda **k: tmp_path / "out.json")
    tdb_crypt.run_cli()

    monkeypatch.setattr(tdb_crypt, "encrypt_secrets_in_place", lambda **k: None)
    tdb_crypt.run_cli()


def test_tdb_run_cli(monkeypatch: pytest.MonkeyPatch):
    args = SimpleNamespace(schema="s", profile=None, config_dir=None, config_file=None, set=[], log_level="INFO")
    monkeypatch.setattr(tdb_run, "parse_args", lambda: args)
    monkeypatch.setattr(tdb_run, "encrypt_secrets_in_place", lambda **k: None)
    monkeypatch.setattr(tdb_run, "load_or_create_key", lambda: None)
    monkeypatch.setattr(tdb_run, "build_config", lambda defaults, cli_args: {"schema": "s", "profile": None, "log_level": "INFO", "db_engine": "oracle", "action": "SOURCE_ILM", "source_dsn": "d", "source_username": "u", "source_password": "p", "history_dsn": "d2", "history_username": "h", "history_password": "p2"})
    monkeypatch.setattr(tdb_run, "Config", Config)
    monkeypatch.setattr(tdb_run, "tdb_run", lambda cfg: None)
    monkeypatch.setattr(tdb_run, "get_logger", lambda name=None: SimpleNamespace(debug=lambda *a, **k: None, error=lambda *a, **k: None, warning=lambda *a, **k: None))
    tdb_run.run_cli()
