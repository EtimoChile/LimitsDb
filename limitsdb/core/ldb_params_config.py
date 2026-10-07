# limitsdb/core/ldb_params_config.py
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from typing import (
    Annotated,
    Any,
    Literal,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from limitsdb.core.ldb_config_loader import load_runtime_config, read_env_overrides
from limitsdb.core.ldb_errors import ValidationError
from limitsdb.core.ldb_logger import get_logger
from limitsdb.core.ldb_meta import Cli, CliOnly, Env, Help, Secret
from limitsdb.core.ldb_utils import resolve_schema_file  # markers for Annotated metadata

logger = get_logger("params_config")

VALID_MODES: tuple[str, ...] = ("VALIDATE", "PLAN", "PREVIEW", "SCRIPT", "EXECUTE")
MODE_ALIASES: dict[str, str] = {"DRY_RUN": "PREVIEW"}
MODES_REQUIRING_CONNECTIONS: set[str] = {"VALIDATE", "PREVIEW", "SCRIPT", "EXECUTE"}
FILE_ONLY_CONFIG_KEYS: frozenset[str] = frozenset({"use_added_columns", "add_ldb_columns"})

IlmAction = Literal["SOURCE_ILM", "HISTORY_ILM"]
RuntimeMode = Literal["VALIDATE", "PLAN", "PREVIEW", "SCRIPT", "EXECUTE"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
DatabaseEngineName = Literal["oracle"]
SUPPORTED_DATABASE_ENGINES: tuple[DatabaseEngineName, ...] = ("oracle",)


def _normalize_mode(value: str) -> str:
    normalized = (value or "").upper()
    return MODE_ALIASES.get(normalized, normalized)


@dataclass(frozen=True)
class ExecutionConfig:
    """Typed, immutable execution settings derived from the public flat config."""

    action: IlmAction
    mode: RuntimeMode
    chunk_size: int
    use_added_columns: bool
    add_ldb_columns: bool
    generate_script: bool
    parallel_max: int
    log_level: LogLevel


@dataclass(frozen=True)
class DatabaseEndpoint:
    """Application connection values for one database environment."""

    dsn: str | None
    username: str | None
    password: str | None


@dataclass(frozen=True)
class ConnectionConfig:
    """Typed source/history connection settings."""

    db_engine: DatabaseEngineName
    source: DatabaseEndpoint
    history: DatabaseEndpoint


@dataclass(frozen=True)
class AdministrativeCredentials:
    """Administrative credentials for one database environment."""

    username: str | None
    password: str | None


@dataclass(frozen=True)
class AdministrationConfig:
    """Typed settings used to provision users, roles, and database links."""

    source: AdministrativeCredentials
    history: AdministrativeCredentials
    source_default_tablespace: str | None
    history_default_tablespace: str | None
    source_to_history_dblink_name: str
    history_to_source_dblink_name: str
    source_role_name: str
    history_role_name: str


@dataclass(frozen=True)
class RuntimeContext:
    """Identity and optional file override for one invocation."""

    schema: str
    profile: str | None
    ilm_config_file: str | None


# ------------------------------------------------------------------------------
# Single source of truth: Config + Annotated metadata
# ------------------------------------------------------------------------------
@dataclass
class Config:
    # Execution mode parameters
    action: Annotated[
        Literal["SOURCE_ILM", "HISTORY_ILM"],
        Help("ILM target: months_keep_history_max (SOURCE_ILM) or history (HISTORY_ILM)"),
        Cli("--action"),
        Env("LDB_ACTION"),
    ] = "SOURCE_ILM"
    mode: Annotated[
        Literal["VALIDATE", "PLAN", "PREVIEW", "SCRIPT", "EXECUTE", "DRY_RUN"],
        Help(
            "Runtime mode: VALIDATE config/credentials, PLAN dependency order, PREVIEW simulate without changes, SCRIPT generate SQL scripts, EXECUTE apply changes"
        ),
        Cli("--mode"),
        Env("LDB_MODE"),
    ] = "PREVIEW"
    chunk_size: Annotated[
        int, Help("Rows per chunk when processing large tables"), Cli("--chunk-size"), Env("LDB_CHUNK_SIZE")
    ] = 100000
    use_added_columns: Annotated[bool, Help("Populate derived columns in history tables")] = True
    add_ldb_columns: Annotated[bool, Help("Add LimitsDb execution-date columns in history tables")] = True
    generate_script: Annotated[bool, Help("Generate SQL script without executing (auto-enabled in SCRIPT mode)")] = (
        False
    )
    parallel_max: Annotated[
        int, Help("Maximum number of parallel processes"), Cli("--parallel-max"), Env("LDB_PARALLEL_MAX")
    ] = 10
    db_engine: Annotated[
        DatabaseEngineName,
        Help("Database engine (Oracle only)"),
        Cli("--db-engine"),
        Env("LDB_DB_ENGINE"),
    ] = "oracle"
    log_level: Annotated[
        Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        Help("Logging level"),
        Cli("--log-level"),
        Env("LDB_LOG_LEVEL"),
    ] = "INFO"
    # schema/profile + optional tables override file
    schema: Annotated[
        str, Help("Schema name (folder under schemas/)"), Cli("--schema"), Env("LDB_SCHEMA"), CliOnly()
    ] = ""
    profile: Annotated[
        str | None, Help("Profile name (e.g., dev, prod)"), Cli("--profile"), Env("LDB_PROFILE"), CliOnly()
    ] = None
    ilm_config_file: Annotated[
        str | None,
        Help("YAML file with tables (bypass DB discovery)"),
        Cli("--ilm-config-file"),
        Env("LDB_ILM_CONFIG_FILE"),
        CliOnly(),
    ] = None
    # Database connection parameters
    source_dsn: Annotated[
        str | None,
        Help("Oracle DSN / connection descriptor: host:port/service (EZCONNECT) or TNS alias (e.g., ORCL)."),
        Env("LDB_SOURCE_DSN"),
    ] = ""
    source_username: Annotated[str | None, Help("Username"), Env("LDB_SOURCE_USERNAME")] = ""
    source_password: Annotated[str | None, Help("Password"), Secret()] = ""
    history_dsn: Annotated[
        str | None,
        Help("Oracle DSN / connection descriptor: host:port/service (EZCONNECT) or TNS alias (e.g., ORCL)."),
        Env("LDB_HISTORY_DSN"),
    ] = ""
    history_username: Annotated[str | None, Help("Username"), Env("LDB_HISTORY_USERNAME")] = ""
    history_password: Annotated[str | None, Help("Password"), Secret()] = ""
    admin_source_username: Annotated[str | None, Help("Admin username"), Env("LDB_ADMIN_SOURCE_USERNAME")] = ""
    admin_source_password: Annotated[str | None, Help("Admin Source password"), Secret()] = ""
    admin_history_username: Annotated[str | None, Help("Admin History username"), Env("LDB_ADMIN_HISTORY_USERNAME")] = (
        ""
    )
    admin_history_password: Annotated[str | None, Help("Admin History password"), Secret()] = ""
    source_default_tablespace: Annotated[str | None, Help("Default tablespace for the source user")] = None
    history_default_tablespace: Annotated[str | None, Help("Default tablespace for the history user")] = None
    source_to_history_dblink_name: Annotated[
        str, Help("Database link name in source environment that connects to history")
    ] = "HIST"
    history_to_source_dblink_name: Annotated[
        str, Help("Database link name in history environment that connects to source")
    ] = "SRC"
    source_role_name: Annotated[str, Help("Role name to create in the source environment")] = "LDB_SOURCE_ROLE"
    history_role_name: Annotated[str, Help("Role name to create in the history environment")] = "LDB_HISTORY_ROLE"

    def __post_init__(self) -> None:
        normalized_mode = _normalize_mode(self.mode)
        if normalized_mode not in VALID_MODES:
            raise ValidationError(f"invalid mode: {self.mode}")
        self.mode = normalized_mode  # type: ignore[assignment]
        if self.mode == "SCRIPT":
            self.generate_script = True
        requires_connections = self.mode in MODES_REQUIRING_CONNECTIONS
        if not self.schema:
            raise ValidationError("schema is required")
        if self.db_engine not in SUPPORTED_DATABASE_ENGINES:
            raise ValidationError(
                f"unsupported db_engine: {self.db_engine}; supported engines: {', '.join(SUPPORTED_DATABASE_ENGINES)}"
            )
        if (
            requires_connections
            and self.action == "SOURCE_ILM"
            and (not self.source_dsn or not self.source_username or not self.source_password)
        ):
            raise ValidationError("source_dsn, source_username and source_password are required for SOURCE_ILM action")
        if (
            requires_connections
            and self.action == "HISTORY_ILM"
            and (not self.history_dsn or not self.history_username or not self.history_password)
        ):
            raise ValidationError(
                "history_dsn, history_username and history_password are required for HISTORY_ILM action"
            )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Config:
        # Strict field validation
        known = {field.name for field in fields(cls)}
        unknown = set(d) - known
        if unknown:
            raise ValidationError(f"Unknown configuration keys: {sorted(unknown)}")
        return cls(**d)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def execution(self) -> ExecutionConfig:
        """Return the typed execution view without changing the public flat model."""
        return ExecutionConfig(
            action=self.action,
            mode=cast(RuntimeMode, self.mode),
            chunk_size=self.chunk_size,
            use_added_columns=self.use_added_columns,
            add_ldb_columns=self.add_ldb_columns,
            generate_script=self.generate_script,
            parallel_max=self.parallel_max,
            log_level=self.log_level,
        )

    @property
    def connections(self) -> ConnectionConfig:
        """Return typed source/history endpoints without copying public keys."""
        return ConnectionConfig(
            db_engine=self.db_engine,
            source=DatabaseEndpoint(
                dsn=self.source_dsn,
                username=self.source_username,
                password=self.source_password,
            ),
            history=DatabaseEndpoint(
                dsn=self.history_dsn,
                username=self.history_username,
                password=self.history_password,
            ),
        )

    @property
    def administration(self) -> AdministrationConfig:
        """Return typed administrative settings without changing public keys."""
        return AdministrationConfig(
            source=AdministrativeCredentials(
                username=self.admin_source_username,
                password=self.admin_source_password,
            ),
            history=AdministrativeCredentials(
                username=self.admin_history_username,
                password=self.admin_history_password,
            ),
            source_default_tablespace=self.source_default_tablespace,
            history_default_tablespace=self.history_default_tablespace,
            source_to_history_dblink_name=self.source_to_history_dblink_name,
            history_to_source_dblink_name=self.history_to_source_dblink_name,
            source_role_name=self.source_role_name,
            history_role_name=self.history_role_name,
        )

    @property
    def context(self) -> RuntimeContext:
        """Return the typed invocation context without changing public keys."""
        return RuntimeContext(schema=self.schema, profile=self.profile, ilm_config_file=self.ilm_config_file)


# ------------------------------------------------------------------------------
# CLI builder from Config metadata (Annotated)
# ------------------------------------------------------------------------------
def _arg_type_from_default(default: Any) -> type[bool] | type[int] | type[float] | type[str]:
    if isinstance(default, bool):
        return bool
    if isinstance(default, int):
        return int
    if isinstance(default, float):
        return float
    return str


def env_bindings_from_config() -> dict[str, str]:
    """Return exact environment-name to Config-field bindings from Env metadata."""
    bindings: dict[str, str] = {}
    hints = get_type_hints(Config, include_extras=True)
    for field_name, annotated in hints.items():
        env = next((meta for meta in get_args(annotated)[1:] if isinstance(meta, Env)), None)
        if env is None:
            continue
        if env.name in bindings:
            raise RuntimeError(f"Duplicate environment binding: {env.name}")
        bindings[env.name] = field_name
    return bindings


def build_argparser_from_config() -> argparse.ArgumentParser:
    """
    Build an argparse.ArgumentParser from Config's Annotated metadata.
    Also adds builder-only flags: --config-dir, --config-file, and --set.
    CLI defaults are suppressed so they don't override values from YAML or ENV.
    """
    parser = argparse.ArgumentParser(description="LimitsDb CLI", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    env_values = read_env_overrides(env_bindings_from_config())
    hints = get_type_hints(Config, include_extras=True)
    for name, annotated in hints.items():
        metas = get_args(annotated)
        cli = next((m.flag for m in metas if isinstance(m, Cli)), None)
        is_secret = any(getattr(m, "enabled", False) for m in metas if isinstance(m, Secret))
        if not cli or is_secret:
            continue  # Only fields explicitly marked with Cli(...) become CLI flags; Secret fields are not exposed via CLI flags
        help_text = next((m.text for m in metas if isinstance(m, Help)), None)
        default = getattr(Config, name)
        # Suppress defaults so argparse only sets values explicitly provided in CLI
        arg_kwargs: dict[str, Any] = {"help": help_text or "", "default": argparse.SUPPRESS}
        # infer choices from Literal if present
        parameter_type = metas[0]
        origin = get_origin(parameter_type)
        if origin is Literal:
            arg_kwargs["choices"] = tuple(get_args(parameter_type))
        # Schema can be supplied by its explicitly registered environment variable.
        if name == "schema" and "schema" not in env_values:
            arg_kwargs["required"] = True
        # Booleans: use BooleanOptionalAction for --flag / --no-flag
        if isinstance(default, bool):
            parser.add_argument(cli, action=argparse.BooleanOptionalAction, **arg_kwargs)
        else:
            parser.add_argument(cli, type=_arg_type_from_default(default), **arg_kwargs)
    # Builder-only flags (not part of Config dataclass)
    parser.add_argument(
        "--config-dir", help="Configuration root (overrides autodiscovery of ~/.config/LimitsDb and /etc/limitsdb)"
    )
    parser.add_argument("--config-file", help="Additional YAML overlay (highest priority)")
    parser.add_argument(
        "--set", action="append", default=[], help="Overrides like key=value; supports dotted keys for nesting"
    )
    return parser


def parse_args() -> argparse.Namespace:
    return build_argparser_from_config().parse_args()


# ------------------------------------------------------------------------------
# CLI --set parser (k=v, dotted keys)
# ------------------------------------------------------------------------------
def _parse_cli_sets(pairs: Sequence[str]) -> dict[str, Any]:
    """
    Parse a list of key=value strings into a dict, with simple auto-typing."""
    out: dict[str, Any] = {}
    for p in pairs or []:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        key = k.strip()
        if key.split(".", 1)[0] in FILE_ONLY_CONFIG_KEYS:
            raise ValidationError(f"'{key}' can only be set in the persistent YAML configuration")
        v = v.strip()
        # simple auto-typing
        if v.isdigit():
            v_typed: Any = int(v)
        elif v.lower() in ("true", "false"):
            v_typed = v.lower() == "true"
        else:
            # try float
            try:
                v_typed = float(v)
            except ValueError:
                v_typed = v
        out[key] = v_typed
    return out


# ------------------------------------------------------------------------------
# Build final config dict using loader + CLI overrides
# ------------------------------------------------------------------------------
def build_config(defaults: Mapping[str, Any], cli_args: argparse.Namespace) -> dict[str, Any]:
    """
    Produce the final configuration dictionary using the new loader (overlays + secrets).
    Environment and CLI overrides are handled inside the loader (ENV LDB_*), and here we map
    explicit flags to --set so they win with the highest priority.
    """
    env_bindings = env_bindings_from_config()
    env_values = read_env_overrides(env_bindings)
    schema = getattr(cli_args, "schema", None) or env_values.get("schema")
    if not schema:
        raise ValidationError("schema is required through --schema or LDB_SCHEMA")
    profile = getattr(cli_args, "profile", None)
    if profile is None:
        profile = env_values.get("profile")

    # Map CLI flags (derived from Config metadata) into --set key=value overrides
    cli_sets: dict[str, Any] = _parse_cli_sets(getattr(cli_args, "set", []))
    # Pull values for every CLI-exposed field from args and push into cli_sets if not None
    hints = get_type_hints(Config, include_extras=True)
    for name, annotated in hints.items():
        metas = get_args(annotated)
        cli = next((m.flag for m in metas if isinstance(m, Cli)), None)
        if not cli:
            continue
        # skip secrets even if they had Cli (we didn't add them)
        is_secret = any(getattr(m, "enabled", False) for m in metas if isinstance(m, Secret))
        if is_secret:
            continue
        # argparse stores flags as dest = field name with dashes replaced by underscores,
        # but since we used the field name to derive flags, we can read by attribute name.
        if hasattr(cli_args, name):
            val = getattr(cli_args, name)
            # Only propagate explicit values (argparse always populates defaults; let loader/defaults handle true defaults)
            # Here we still push values to ensure CLI has top priority.
            if val is not None:
                cli_sets[name] = val
    # Call the loader with overlay sources
    cfg = load_runtime_config(
        schema=str(schema),
        profile=cast(str | None, profile),
        cli_sets=cli_sets,
        env_bindings=env_bindings,
        explicit_config_file=getattr(cli_args, "config_file", None),
        explicit_config_dir=getattr(cli_args, "config_dir", None),
    )
    # Merge with embedded defaults (if you still keep some minimal defaults in code)
    final = dict(defaults)
    final.update(cfg)
    # Ensure schema/profile land in the object (the loader also uses them but they are not part of the dict)
    final["schema"] = schema
    final["profile"] = profile
    final["ilm_config_file"] = resolve_schema_file(
        schema=str(schema),
        profile=cast(str | None, profile),
        explicit_config_dir=getattr(cli_args, "config_dir", None),
        explicit_file=cast(str | None, final.get("ilm_config_file")),
        prefix_name="ilm",
        extension_name="yml",
        description="ilm configuration file",
    )
    return final
