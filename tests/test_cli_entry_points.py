from types import SimpleNamespace

import pytest

from limitsdb.cli import ldb_crypt, ldb_impl, ldb_init, ldb_run
from limitsdb.core import ldb_params_config
from limitsdb.core.ldb_errors import InvalidCredentialsError, LimitsDbError
from limitsdb.core.ldb_params_config import Config

from .script_envelope import script_envelope_methods

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


# ---------------------------------------------------------------------------
# Argparser contract
# ---------------------------------------------------------------------------


def test_argparser_requires_schema_and_rejects_unsupported_engine(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM — --schema is required; --db-engine accepts only oracle
    # Given: a parser built from the live Config schema
    monkeypatch.delenv("LDB_SCHEMA", raising=False)
    parser = ldb_params_config.build_argparser_from_config()

    # When: no --schema is given
    # Then: parser exits
    with pytest.raises(SystemExit):
        parser.parse_args([])

    # When: the removed DRY_RUN is passed as --mode
    # Then: parser exits
    with pytest.raises(SystemExit):
        parser.parse_args(["--schema", "s", "--mode", "DRY_RUN"])

    # When: oracle is given as --db-engine
    # Then: it is accepted
    assert parser.parse_args(["--schema", "s", "--db-engine", "oracle"]).db_engine == "oracle"

    # When: postgres is given as --db-engine
    # Then: parser exits — not yet supported
    with pytest.raises(SystemExit):
        parser.parse_args(["--schema", "s", "--db-engine", "postgres"])


def test_status_mode_and_last_option_are_accepted_by_ldb_run(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM — --mode STATUS reports the executions; --last n selects how many (integer)
    monkeypatch.delenv("LDB_SCHEMA", raising=False)
    parser = ldb_params_config.build_argparser_from_config()

    # When/Then: STATUS with --last parses; a non-integer count is refused
    parsed = parser.parse_args(["--schema", "s", "--mode", "STATUS", "--last", "5"])
    assert (parsed.mode, parsed.last) == ("STATUS", 5)
    with pytest.raises(SystemExit):
        parser.parse_args(["--schema", "s", "--mode", "STATUS", "--last", "many"])


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


# ---------------------------------------------------------------------------
# ldb-impl
# ---------------------------------------------------------------------------


def test_ldb_impl_control_tables_have_correct_shape():
    # Spec: README > ldb-impl — LDB_CTL, LDB_LOG and LDB_RUN_LOCK are the control tables;
    # CTL_ACTION and LOG_ACTION have length 11 to accommodate all action codes
    # Given: an owner name
    # When: control tables are built
    tables = ldb_impl._build_control_tables("OWNER")

    # Then: both tables exist with correct names and primary key / column constraints
    assert {t.name for t in tables} == {"LDB_CTL", "LDB_LOG", "LDB_RUN_LOCK"}
    run_lock = next(t for t in tables if t.name == "LDB_RUN_LOCK")
    assert run_lock.primary_key == ("RL_OWNER", "RL_TABLE_NAME", "RL_KIND")
    ctl = next(t for t in tables if t.name == "LDB_CTL")
    log = next(t for t in tables if t.name == "LDB_LOG")
    assert ctl.primary_key == ("CTL_OWNER", "CTL_TABLE_NAME")
    assert next(c for c in ctl.columns if c.name == "CTL_ACTION").length == 11
    assert next(c for c in log.columns if c.name == "LOG_ACTION").length == 11
    # And: STATUS can find the session of each table (engine-specific id); the log carries no run id (executions are grouped by time)
    assert "CTL_DB_SESSION_ID" in {c.name for c in ctl.columns}
    assert "LOG_RUN_ID" not in {c.name for c in log.columns}


def test_ldb_impl_run_cli_creates_database_objects(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > ldb-impl — provisions roles, users, tables, sequences, database
    # links and supporting objects using admin credentials
    # Given: all CLI args resolved and a DummyEngine that records calls
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
        script_password_prompts=lambda *a, **k: None,
        **script_envelope_methods(),
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

    # When / Then: run_cli completes without raising
    ldb_impl.run_cli()


@pytest.mark.parametrize(
    "empty_key",
    [
        "source_username",
        "history_username",
        "history_dsn",
        "source_dsn",
    ],
)
def test_ldb_impl_run_cli_exits_with_error_when_credential_is_missing(monkeypatch: pytest.MonkeyPatch, empty_key: str):
    # Spec: README > ldb-impl — runtime credentials and DSNs are required;
    # missing any of them exits with code 1 before touching the database
    # Given: a config dict with one required credential empty
    cfg = {**_FULL_IMPL_DICT, empty_key: ""}
    monkeypatch.setattr(
        ldb_impl,
        "_parse_args",
        lambda: SimpleNamespace(schema="s", profile=None, config_dir=None, config_file=None, set=[], log_level="INFO"),
    )
    monkeypatch.setattr(ldb_impl, "load_or_create_key", lambda: None)
    monkeypatch.setattr(ldb_impl, "encrypt_secrets_in_place", lambda **k: None)
    monkeypatch.setattr(ldb_impl, "build_config", lambda defaults, args: dict(cfg))

    # When / Then: SystemExit with code 1
    with pytest.raises(SystemExit) as caught:
        ldb_impl.run_cli()
    assert caught.value.code == 1


def _run_impl_with_admin_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path, absent_envs: set[str], *, runtime_login_error=None, **extra
):
    """Run ldb-impl with a recording engine double; admin credentials are removed for absent_envs."""
    cfg = {**_FULL_IMPL_DICT, "ddl_pending_dir": str(tmp_path / "pending"), **extra}
    if "SOURCE" in absent_envs:
        cfg.update(admin_source_username="", admin_source_password="")
    if "HISTORY" in absent_envs:
        cfg.update(admin_history_username="", admin_history_password="")
    calls: list[tuple[str, object]] = []
    scripts: list[dict] = []

    def record(name, result=None):
        def method(conn, *args, ddl_sink=None, **kwargs):
            if ddl_sink is not None:
                # Script mode: nothing is read or executed, the engine only hands over script fragments.
                assert conn is None
                scripts.append({"step": name, "args": args, **kwargs})
                ddl_sink(f"-- fragment {name}")
                return []
            calls.append((name, conn.env))
            return result or []

        return method

    def connect(config, *, admin=False, env=None):
        if runtime_login_error is not None and not admin:
            raise runtime_login_error
        return SimpleNamespace(env=env, admin=admin)

    engine = SimpleNamespace(
        REQUIRED_SYSTEM_PRIVILEGES=("CREATE SESSION",),
        script_password_prompts=lambda *a, **k: None,
        **script_envelope_methods(),
        ensure_roles=record("roles", ["role"]),
        ensure_users=record("users", ["user"]),
        ensure_tables=record("tables", ["table"]),
        ensure_sequences=record("sequences", ["seq"]),
        ensure_supporting_objects=record("supporting", ["obj"]),
        ensure_database_links=record("links", ["dblink"]),
        get_connection=connect,
        close_connection=lambda conn: None,
    )
    monkeypatch.setattr(
        ldb_impl,
        "_parse_args",
        lambda: SimpleNamespace(schema="s", profile=None, config_dir=None, config_file=None, set=[], log_level="INFO"),
    )
    monkeypatch.setattr(ldb_impl, "load_or_create_key", lambda: None)
    monkeypatch.setattr(ldb_impl, "encrypt_secrets_in_place", lambda **k: None)
    monkeypatch.setattr(ldb_impl, "build_config", lambda defaults, args: dict(cfg))
    monkeypatch.setattr(ldb_impl, "get_db_engine", lambda name: engine)
    ldb_impl.run_cli()
    return calls, scripts


def test_ldb_impl_without_admin_credentials_writes_one_script_per_database_and_creates_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # Spec: README > ldb-impl — when admin credentials for a database are absent, ldb-impl creates
    # nothing in it and writes a bootstrap script for that database (one file per database)
    # under ddl_pending_dir for a DBA to run; the run does not fail
    # When: both admin credentials are absent
    calls, scripts = _run_impl_with_admin_absent(monkeypatch, tmp_path, {"SOURCE", "HISTORY"})

    # Then: nothing was created and two scripts were written, one per database
    assert calls == []
    files = sorted(path.name for path in (tmp_path / "pending").glob("*.sql"))
    assert len(files) == 2
    assert files[0].startswith("bootstrap_s_history_") and files[1].startswith("bootstrap_s_source_")
    assert {step["connect_as"][0] for step in scripts if step["step"] == "links"} == {
        _FULL_IMPL_DICT["source_username"],
        _FULL_IMPL_DICT["history_username"],
    }


def test_ldb_impl_names_the_user_whose_existing_password_differs_from_the_configured_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # Spec: README > ldb-impl — when the runtime user already existed with another password, the
    # reconnection is rejected (ORA-01017); ldb-impl says which user rejected the configured password
    # and exits 1, instead of the generic connection failure
    errors: list[str] = []
    monkeypatch.setattr(
        ldb_impl,
        "get_logger",
        lambda name=None: SimpleNamespace(
            info=lambda *a, **k: None,
            debug=lambda *a, **k: None,
            error=lambda msg, *a, **k: errors.append(msg % a),
        ),
    )
    with pytest.raises(SystemExit) as caught:
        _run_impl_with_admin_absent(
            monkeypatch, tmp_path, set(), runtime_login_error=InvalidCredentialsError("Failed to connect")
        )

    assert caught.value.code == 1
    logged = " ".join(errors)
    assert f"runtime user {_FULL_IMPL_DICT['source_username']} exists in SOURCE" in logged
    assert "does not change the password of an existing user" in logged


def test_ldb_impl_only_writes_a_script_for_the_database_whose_admin_credentials_are_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # Spec: README > ldb-impl — the script is written per database: a database with admin
    # credentials is provisioned directly, the other one gets a script
    # When: only the history admin credentials are absent
    calls, scripts = _run_impl_with_admin_absent(monkeypatch, tmp_path, {"HISTORY"})

    # Then: SOURCE was provisioned directly (including its database link) and HISTORY got a script
    assert {env for _, env in calls} == {"SOURCE"}
    assert {"roles", "users", "tables", "sequences", "supporting", "links"} == {name for name, _ in calls}
    (written,) = (tmp_path / "pending").glob("*.sql")
    assert written.name.startswith("bootstrap_s_history_")
    assert {step["step"] for step in scripts} == {"roles", "users", "tables", "sequences", "supporting", "links"}
    assert [step["connect_as"][0] for step in scripts if step["step"] == "links"] == [
        _FULL_IMPL_DICT["history_username"]
    ]


def test_ldb_impl_exits_1_when_the_script_cannot_be_written(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # Spec: README > ldb-impl — if the script cannot be written, ldb-impl logs the error and exits with code 1
    # Given: ddl_pending_dir points below a regular file
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")

    # When / Then
    with pytest.raises(SystemExit) as caught:
        _run_impl_with_admin_absent(monkeypatch, tmp_path, {"SOURCE", "HISTORY"}, ddl_pending_dir=str(blocker / "sub"))
    assert caught.value.code == 1


@pytest.mark.parametrize("empty_key", ["source_username", "source_dsn"])
def test_ldb_impl_run_cli_exits_with_error_for_source_credential_missing_in_plan_mode(
    monkeypatch: pytest.MonkeyPatch, empty_key: str
):
    # Spec: README > ldb-impl — PLAN mode bypasses Config credential validation;
    # ldb_impl.py checks source credentials explicitly before proceeding
    # Given: PLAN mode config with an empty source credential
    cfg = {**_FULL_IMPL_DICT, "mode": "PLAN", empty_key: ""}
    monkeypatch.setattr(
        ldb_impl,
        "_parse_args",
        lambda: SimpleNamespace(schema="s", profile=None, config_dir=None, config_file=None, set=[], log_level="INFO"),
    )
    monkeypatch.setattr(ldb_impl, "load_or_create_key", lambda: None)
    monkeypatch.setattr(ldb_impl, "encrypt_secrets_in_place", lambda **k: None)
    monkeypatch.setattr(ldb_impl, "build_config", lambda defaults, args: dict(cfg))

    # When / Then: SystemExit with code 1
    with pytest.raises(SystemExit) as caught:
        ldb_impl.run_cli()
    assert caught.value.code == 1


def test_ldb_impl_run_cli_logs_when_all_objects_already_exist(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > ldb-impl — when all required objects are already present,
    # ldb-impl logs a confirmation instead of silently exiting
    # Given: an engine where all ensure_* return empty lists (nothing to create)
    monkeypatch.setattr(
        ldb_impl,
        "_parse_args",
        lambda: SimpleNamespace(schema="s", profile=None, config_dir=None, config_file=None, set=[], log_level="INFO"),
    )
    engine = SimpleNamespace(
        REQUIRED_SYSTEM_PRIVILEGES=(),
        script_password_prompts=lambda *a, **k: None,
        **script_envelope_methods(),
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

    # When: run_cli runs with everything already in place
    ldb_impl.run_cli()

    # Then: a message indicating objects were already present is logged
    assert any("already present" in m for m in logged)


# ---------------------------------------------------------------------------
# ldb-crypt
# ---------------------------------------------------------------------------


def test_ldb_crypt_run_cli_encrypts_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # Spec: README > ldb-crypt — encrypts plaintext secrets in the secrets file and
    # reports the output path; does not raise when file is already encrypted (None return)
    # Given: CLI args and a mock encrypt function
    args = SimpleNamespace(schema="s", profile=None, config_dir=str(tmp_path))
    monkeypatch.setattr(ldb_crypt, "_parse_args", lambda: args)
    monkeypatch.setattr(ldb_crypt, "load_or_create_key", lambda: None)
    monkeypatch.setattr(ldb_crypt, "encrypt_secrets_in_place", lambda **k: tmp_path / "out.json")

    # When: run_cli executes with an output path
    # Then: no error raised
    ldb_crypt.run_cli()

    # When: encrypt returns None (all already encrypted)
    monkeypatch.setattr(ldb_crypt, "encrypt_secrets_in_place", lambda **k: None)

    # Then: no error raised
    ldb_crypt.run_cli()


def test_ldb_crypt_run_cli_exits_with_error_code_on_failure(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/exception-handling.md — SecretError in ldb-crypt is reported and
    # the process exits with code 1
    # Given: encrypt raises
    args = SimpleNamespace(schema="s", profile=None, config_dir=None)
    monkeypatch.setattr(ldb_crypt, "_parse_args", lambda: args)
    monkeypatch.setattr(ldb_crypt, "load_or_create_key", lambda: None)
    monkeypatch.setattr(
        ldb_crypt,
        "encrypt_secrets_in_place",
        lambda **k: (_ for _ in ()).throw(ValueError("bad secrets")),
    )

    # When / Then: SystemExit with code 1
    with pytest.raises(SystemExit) as caught:
        ldb_crypt.run_cli()
    assert caught.value.code == 1


# ---------------------------------------------------------------------------
# ldb-run
# ---------------------------------------------------------------------------


def test_ldb_run_run_cli_executes_successfully(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM — ldb-run resolves config, constructs Config and
    # dispatches to the runner; returns 0 on success
    # Given: all dependencies mocked
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
            debug=lambda *a, **k: None,
            error=lambda *a, **k: None,
            warning=lambda *a, **k: None,
        ),
    )

    # When / Then: run_cli completes without raising
    ldb_run.run_cli()


def test_ldb_run_run_cli_exits_with_error_code_on_failure(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/exception-handling.md — LimitsDbError propagates from the runner
    # to the CLI as exit code 1
    # Given: ldb_run raises LimitsDbError
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

    # When / Then: SystemExit with code 1
    with pytest.raises(SystemExit) as caught:
        ldb_run.run_cli()
    assert caught.value.code == 1


# ---------------------------------------------------------------------------
# ldb-init
# ---------------------------------------------------------------------------


def test_ldb_init_run_cli_creates_all_files_and_encrypts(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # Spec: README > ldb-init — creates config.yml, ilm.yml, secrets.json and
    # ilm.example.yml; encrypts secrets; respects --overwrite and --profile
    # Given: fully specified CLI args
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
    monkeypatch.setattr(
        ldb_init,
        "reconfigure_logger",
        lambda **kwargs: calls.setdefault("log_level", kwargs["level"]),
    )

    def initialize(**kwargs):
        calls["init"] = kwargs
        return tuple(tmp_path / name for name in ("config.yml", "ilm.yml", "secrets.json", "ilm.example.yml"))

    monkeypatch.setattr(ldb_init, "init_schema", initialize)

    # When: run_cli executes
    ldb_init.run_cli()

    # Then: key was loaded, log level was set, init was called with correct args
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


def test_ldb_init_run_cli_exits_with_error_code_on_invalid_request(
    monkeypatch: pytest.MonkeyPatch,
):
    # Spec: docs/exception-handling.md — ConfigurationError or ValueError from
    # init_schema exits ldb-init with code 1
    # Given: init_schema raises ValueError
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
    monkeypatch.setattr(
        ldb_init,
        "init_schema",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("invalid root")),
    )

    # When / Then: SystemExit with code 1
    with pytest.raises(SystemExit) as caught:
        ldb_init.run_cli()
    assert caught.value.code == 1


def test_ldb_init_run_cli_tolerates_missing_example_file(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # Spec: README > ldb-init > --no-examples — when init_schema returns None for
    # the example path, run_cli must not attempt to log it and must not raise
    # Given: init_schema returns None for the example path
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
        lambda **kwargs: (
            tmp_path / "config.yml",
            tmp_path / "ilm.yml",
            tmp_path / "secrets.json",
            None,
        ),
    )

    # When / Then: run_cli completes without raising
    ldb_init.run_cli()


def test_ldb_impl_accepts_ddl_pending_dir_flag(monkeypatch: pytest.MonkeyPatch):
    # Spec: README § Bootstrap Database Objects > Without admin credentials — the script directory is
    # ddl_pending_dir, set with the same --ddl-pending-dir flag as ldb-run
    monkeypatch.setattr("sys.argv", ["ldb-impl", "--schema", "s", "--ddl-pending-dir", "out"])

    assert ldb_impl._parse_args().ddl_pending_dir == "out"
