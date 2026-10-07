from types import SimpleNamespace

import pytest

from limitsdb.cli import ldb_crypt, ldb_impl, ldb_init, ldb_run
from limitsdb.core.ldb_errors import LimitsDbError
from limitsdb.core.ldb_params_config import Config


def test_mask_secrets():
    cfg = {"source_password": "secret", "other": 1}
    masked = ldb_run._mask_secrets(cfg)
    assert masked["source_password"] == "****"
    assert masked["other"] == 1


def test_build_control_tables_shapes():
    tables = ldb_impl._build_control_tables("OWNER")
    assert {t.name for t in tables} == {"LDB_CTL", "LDB_LOG", "LDB_CNF"}
    ctl = next(t for t in tables if t.name == "LDB_CTL")
    log = next(t for t in tables if t.name == "LDB_LOG")
    assert ctl.primary_key == ("CTL_OWNER", "CTL_TABLE_NAME")
    assert next(column for column in ctl.columns if column.name == "CTL_ACTION").length == 11
    assert next(column for column in log.columns if column.name == "LOG_ACTION").length == 11


def test_ldb_impl_run_cli(monkeypatch: pytest.MonkeyPatch):
    args = SimpleNamespace(
        schema="s",
        profile=None,
        config_dir=None,
        config_file=None,
        set=[],
        log_level="INFO",
        source_username="src",
        source_password="pw",
        history_username="hist",
        history_password="pw2",
        admin_source_username="asrc",
        admin_source_password="apw",
        admin_history_username="ah",
        admin_history_password="ahpw",
        source_dsn="dsn1",
        history_dsn="dsn2",
        db_engine="oracle",
        source_default_tablespace=None,
        history_default_tablespace=None,
        source_to_history_dblink_name="h",
        history_to_source_dblink_name="s",
        source_role_name="SR",
        history_role_name="HR",
    )
    monkeypatch.setattr(ldb_impl, "_parse_args", lambda: args)
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
    monkeypatch.setattr(ldb_impl, "get_db_engine", lambda eng: engine)
    monkeypatch.setattr(
        ldb_impl,
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
    monkeypatch.setattr(ldb_impl, "encrypt_secrets_in_place", lambda **k: None)
    monkeypatch.setattr(ldb_impl, "load_or_create_key", lambda: None)
    ldb_impl.run_cli()


def test_ldb_crypt_cli(monkeypatch: pytest.MonkeyPatch, tmp_path):
    args = SimpleNamespace(schema="s", profile=None, config_dir=str(tmp_path))
    monkeypatch.setattr(ldb_crypt, "_parse_args", lambda: args)
    monkeypatch.setattr(ldb_crypt, "load_or_create_key", lambda: None)
    monkeypatch.setattr(ldb_crypt, "encrypt_secrets_in_place", lambda **k: tmp_path / "out.json")
    ldb_crypt.run_cli()

    monkeypatch.setattr(ldb_crypt, "encrypt_secrets_in_place", lambda **k: None)
    ldb_crypt.run_cli()


def test_ldb_run_cli(monkeypatch: pytest.MonkeyPatch):
    args = SimpleNamespace(schema="s", profile=None, config_dir=None, config_file=None, set=[], log_level="INFO")
    monkeypatch.setattr(ldb_run, "parse_args", lambda: args)
    monkeypatch.setattr(ldb_run, "encrypt_secrets_in_place", lambda **k: None)
    monkeypatch.setattr(ldb_run, "load_or_create_key", lambda: None)
    monkeypatch.setattr(
        ldb_run,
        "build_config",
        lambda defaults, cli_args: {
            "schema": "s",
            "profile": None,
            "log_level": "INFO",
            "db_engine": "oracle",
            "action": "SOURCE_ILM",
            "source_dsn": "d",
            "source_username": "u",
            "source_password": "p",
            "history_dsn": "d2",
            "history_username": "h",
            "history_password": "p2",
        },
    )
    monkeypatch.setattr(ldb_run, "Config", Config)
    monkeypatch.setattr(ldb_run, "ldb_run", lambda cfg: None)
    monkeypatch.setattr(
        ldb_run,
        "get_logger",
        lambda name=None: SimpleNamespace(
            debug=lambda *a, **k: None, error=lambda *a, **k: None, warning=lambda *a, **k: None
        ),
    )
    ldb_run.run_cli()


def test_ldb_init_run_cli_creates_requested_files(monkeypatch: pytest.MonkeyPatch, tmp_path):
    args = SimpleNamespace(
        schema="billing",
        profile="dev",
        config_dir=str(tmp_path),
        overwrite=True,
        no_examples=False,
        log_level="DEBUG",
    )
    calls = {}
    monkeypatch.setattr(ldb_init, "_parse_args", lambda: args)
    monkeypatch.setattr(ldb_init, "load_or_create_key", lambda: calls.setdefault("key", True))
    monkeypatch.setattr(ldb_init, "reconfigure_logger", lambda **kwargs: calls.setdefault("log_level", kwargs["level"]))

    def initialize(**kwargs):
        calls["init"] = kwargs
        return tuple(tmp_path / name for name in ("config.yml", "ilm.yml", "secrets.json", "ilm.example.yml"))

    monkeypatch.setattr(ldb_init, "init_schema", initialize)

    ldb_init.run_cli()

    assert calls == {
        "key": True,
        "log_level": "DEBUG",
        "init": {
            "schema": "billing",
            "profile": "dev",
            "config_root": str(tmp_path),
            "overwrite": True,
            "with_examples": True,
            "auto_encrypt": True,
        },
    }


def test_ldb_init_run_cli_reports_invalid_request(monkeypatch: pytest.MonkeyPatch):
    args = SimpleNamespace(
        schema="billing",
        profile=None,
        config_dir=None,
        overwrite=False,
        no_examples=True,
        log_level="INFO",
    )
    monkeypatch.setattr(ldb_init, "_parse_args", lambda: args)
    monkeypatch.setattr(ldb_init, "load_or_create_key", lambda: None)
    monkeypatch.setattr(ldb_init, "init_schema", lambda **kwargs: (_ for _ in ()).throw(ValueError("invalid root")))

    with pytest.raises(SystemExit) as caught:
        ldb_init.run_cli()

    assert caught.value.code == 1


def test_ldb_init_run_cli_skips_examples_log_when_no_examples_path(monkeypatch: pytest.MonkeyPatch, tmp_path):
    args = SimpleNamespace(
        schema="billing",
        profile=None,
        config_dir=str(tmp_path),
        overwrite=False,
        no_examples=True,
        log_level="INFO",
    )
    monkeypatch.setattr(ldb_init, "_parse_args", lambda: args)
    monkeypatch.setattr(ldb_init, "load_or_create_key", lambda: None)
    monkeypatch.setattr(
        ldb_init,
        "init_schema",
        lambda **kwargs: (tmp_path / "config.yml", tmp_path / "ilm.yml", tmp_path / "secrets.json", None),
    )
    ldb_init.run_cli()  # must not raise; no fourth logger.info call for examples


def test_ldb_crypt_cli_reports_error(monkeypatch: pytest.MonkeyPatch):
    args = SimpleNamespace(schema="s", profile=None, config_dir=None)
    monkeypatch.setattr(ldb_crypt, "_parse_args", lambda: args)
    monkeypatch.setattr(ldb_crypt, "load_or_create_key", lambda: None)
    monkeypatch.setattr(
        ldb_crypt,
        "encrypt_secrets_in_place",
        lambda **k: (_ for _ in ()).throw(ValueError("bad secrets")),
    )

    with pytest.raises(SystemExit) as caught:
        ldb_crypt.run_cli()

    assert caught.value.code == 1


def test_ldb_run_cli_reports_error(monkeypatch: pytest.MonkeyPatch):
    args = SimpleNamespace(schema="s", profile=None, config_dir=None, config_file=None, set=[], log_level="INFO")
    monkeypatch.setattr(ldb_run, "parse_args", lambda: args)
    monkeypatch.setattr(ldb_run, "load_or_create_key", lambda: None)
    monkeypatch.setattr(ldb_run, "encrypt_secrets_in_place", lambda **k: None)
    monkeypatch.setattr(
        ldb_run,
        "build_config",
        lambda defaults, cli_args: {
            "schema": "s",
            "profile": None,
            "log_level": "INFO",
            "db_engine": "oracle",
            "action": "SOURCE_ILM",
            "source_dsn": "d",
            "source_username": "u",
            "source_password": "p",
            "history_dsn": "d2",
            "history_username": "h",
            "history_password": "p2",
        },
    )
    monkeypatch.setattr(ldb_run, "Config", Config)
    monkeypatch.setattr(
        ldb_run,
        "ldb_run",
        lambda cfg: (_ for _ in ()).throw(LimitsDbError("connection refused")),
    )
    monkeypatch.setattr(
        ldb_run,
        "get_logger",
        lambda name=None: SimpleNamespace(debug=lambda *a, **k: None, error=lambda *a, **k: None),
    )

    with pytest.raises(SystemExit) as caught:
        ldb_run.run_cli()

    assert caught.value.code == 1


_FULL_IMPL_DICT = {
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
}


@pytest.mark.parametrize(
    "empty_key",
    [
        "admin_source_username",
        "admin_history_username",
        "source_username",
        "history_username",
        "history_dsn",
        "source_dsn",
    ],
)
def test_ldb_impl_run_cli_fails_when_credential_is_missing(monkeypatch: pytest.MonkeyPatch, empty_key: str):
    cfg = {**_FULL_IMPL_DICT, empty_key: ""}
    monkeypatch.setattr(
        ldb_impl,
        "_parse_args",
        lambda: SimpleNamespace(schema="s", profile=None, config_dir=None, config_file=None, set=[], log_level="INFO"),
    )
    monkeypatch.setattr(ldb_impl, "load_or_create_key", lambda: None)
    monkeypatch.setattr(ldb_impl, "encrypt_secrets_in_place", lambda **k: None)
    monkeypatch.setattr(ldb_impl, "build_config", lambda defaults, args: dict(cfg))

    with pytest.raises(SystemExit) as caught:
        ldb_impl.run_cli()

    assert caught.value.code == 1


def test_ldb_impl_run_cli_logs_when_nothing_created(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        ldb_impl,
        "_parse_args",
        lambda: SimpleNamespace(schema="s", profile=None, config_dir=None, config_file=None, set=[], log_level="INFO"),
    )
    engine = SimpleNamespace(
        REQUIRED_SYSTEM_PRIVILEGES=(),
        ensure_roles=lambda *a, **k: [],
        ensure_users=lambda *a, **k: [],
        ensure_tables=lambda *a, **k: [],
        ensure_sequences=lambda *a, **k: [],
        ensure_supporting_objects=lambda *a, **k: [],
        ensure_database_links=lambda *a, **k: [],
        get_connection=lambda *a, **k: SimpleNamespace(),
        close_connection=lambda *a, **k: None,
    )
    monkeypatch.setattr(ldb_impl, "get_db_engine", lambda eng: engine)
    monkeypatch.setattr(ldb_impl, "build_config", lambda defaults, args: dict(_FULL_IMPL_DICT))
    monkeypatch.setattr(ldb_impl, "encrypt_secrets_in_place", lambda **k: None)
    monkeypatch.setattr(ldb_impl, "load_or_create_key", lambda: None)
    logged: list[str] = []
    monkeypatch.setattr(
        ldb_impl,
        "get_logger",
        lambda name=None: SimpleNamespace(
            info=lambda msg, *a, **k: logged.append(msg),
            error=lambda *a, **k: None,
            debug=lambda *a, **k: None,
        ),
    )
    ldb_impl.run_cli()

    assert any("already present" in m for m in logged)
