# limitsdb/core/ldb_params_config.py
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Literal, Annotated, Tuple, Set, get_args, get_origin, get_type_hints

from limitsdb.core.ldb_config_loader import load_runtime_config
from limitsdb.core.ldb_logger import get_logger
from limitsdb.core.ldb_meta import Help, Cli, Secret, Env, CliOnly
from limitsdb.core.ldb_utils import resolve_schema_file  # markers for Annotated metadata

logger = get_logger("params_config")

VALID_MODES: Tuple[str, ...] = ("VALIDATE", "PLAN", "PREVIEW", "SCRIPT", "EXECUTE")
MODE_ALIASES: Dict[str, str] = {"DRY_RUN": "PREVIEW"}
MODES_REQUIRING_CONNECTIONS: Set[str] = {"VALIDATE", "PREVIEW", "SCRIPT", "EXECUTE"}

def _normalize_mode(value: str) -> str:
    normalized = (value or "").upper()
    return MODE_ALIASES.get(normalized, normalized)

# ------------------------------------------------------------------------------
# Single source of truth: Config + Annotated metadata
# ------------------------------------------------------------------------------
@dataclass
class Config:
    #Execution mode parameters
    # yapf: disable
    action: Annotated[Literal["SOURCE_ILM", "HISTORY_ILM"], Help("ILM target: months_keep_history_max (SOURCE_ILM) or history (HISTORY_ILM)"), Cli("--action"), Env("LDB_ACTION")] = "SOURCE_ILM"
    mode: Annotated[Literal["VALIDATE", "PLAN", "PREVIEW", "SCRIPT", "EXECUTE", "DRY_RUN"], Help("Runtime mode: VALIDATE config/credentials, PLAN dependency order, PREVIEW simulate without changes, SCRIPT generate SQL scripts, EXECUTE apply changes"), Cli("--mode"), Env("LDB_MODE")] = "PREVIEW"
    chunk_size: Annotated[int, Help("Rows per chunk when processing large tables"), Cli("--chunk-size"), Env("LDB_CHUNK_SIZE")] = 100000
    use_added_columns: Annotated[bool, Help("Populate derived columns in history tables")] = True
    add_ldb_columns: Annotated[bool, Help("Add LimitsDb execution-date columns in history tables")] = True
    generate_script: Annotated[bool, Help("Generate SQL script without executing (auto-enabled in SCRIPT mode)")] = False
    parallel_max: Annotated[int, Help("Maximum number of parallel processes"), Cli("--parallel-max"), Env("LDB_PARALLEL_MAX")] = 10
    db_engine: Annotated[Literal["oracle", "postgres"], Help("Database engine")] = "oracle"
    log_level: Annotated[Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], Help("Logging level"), Cli("--log-level"), Env("LDB_LOG_LEVEL")] = "INFO"
    # schema/profile + optional tables override file
    schema: Annotated[str, Help("Schema name (folder under schemas/)"), Cli("--schema"), Env("LDB_SCHEMA"), CliOnly()] = ""
    profile: Annotated[Optional[str], Help("Profile name (e.g., dev, prod)"), Cli("--profile"), Env("LDB_PROFILE"), CliOnly()] = None
    ilm_config_file: Annotated[Optional[str], Help("YAML file with tables (bypass DB discovery)"), Cli("--ilm-config-file"), Env("ILM_CONFIG_FILE"), CliOnly()] = None
    # Database connection parameters
    source_dsn: Annotated[Optional[str], Help("DSN / connection descriptor (engine-specific). Examples — Oracle: host:port/service (EZCONNECT) or TNS alias (e.g., ORCL). Postgres: host:port/dbname.")] = ""
    source_username: Annotated[Optional[str], Help("Username")] = ""
    source_password: Annotated[Optional[str], Help("Password"), Secret()] = ""
    history_dsn: Annotated[Optional[str], Help("DSN / connection descriptor (engine-specific). Examples — Oracle: host:port/service (EZCONNECT) or TNS alias (e.g., ORCL). Postgres: host:port/dbname.")] = ""
    history_username: Annotated[Optional[str], Help("Username")] = ""
    history_password: Annotated[Optional[str], Help("Password"), Secret()] = ""
    admin_source_username: Annotated[Optional[str], Help("Admin username")] = ""
    admin_source_password: Annotated[Optional[str], Help("Admin Source password"), Secret()] = ""
    admin_history_username: Annotated[Optional[str], Help("Admin History username")] = ""
    admin_history_password: Annotated[Optional[str], Help("Admin History password"), Secret()] = ""
    source_default_tablespace: Annotated[Optional[str], Help("Default tablespace for the source user")] = None
    history_default_tablespace: Annotated[Optional[str], Help("Default tablespace for the history user")] = None
    source_to_history_dblink_name: Annotated[str, Help("Database link name in source environment that connects to history")] = "HIST"
    history_to_source_dblink_name: Annotated[str, Help("Database link name in history environment that connects to source")] = "SRC"
    source_role_name: Annotated[str, Help("Role name to create in the source environment")] = "LDB_SOURCE_ROLE"
    history_role_name: Annotated[str, Help("Role name to create in the history environment")] = "LDB_HISTORY_ROLE"
    # yapf: enable

    def __post_init__(self) -> None:
        normalized_mode = _normalize_mode(self.mode)
        if normalized_mode not in VALID_MODES:
            raise ValueError(f"invalid mode: {self.mode}")
        self.mode = normalized_mode  # type: ignore[assignment]
        if self.mode == "SCRIPT":
            self.generate_script = True
        requires_connections = self.mode in MODES_REQUIRING_CONNECTIONS
        if not self.schema:
            raise ValueError("schema is required")
        if self.db_engine not in ("oracle", "postgres"):
            raise ValueError(f"invalid db_engine: {self.db_engine}")
        if requires_connections and self.action == "SOURCE_ILM" and (not self.source_dsn or not self.source_username or not self.source_password):
            raise ValueError("source_dsn, source_username and source_password are required for SOURCE_ILM action")
        if requires_connections and self.action == "HISTORY_ILM" and (not self.history_dsn or not self.history_username or not self.history_password):
            raise ValueError("history_dsn, history_username and history_password are required for HISTORY_ILM action")

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Config":
        # Strict field validation
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"Unknown configuration keys: {sorted(unknown)}")
        return cls(**d)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

# ------------------------------------------------------------------------------
# CLI builder from Config metadata (Annotated)
# ------------------------------------------------------------------------------
def _arg_type_from_default(default: Any):
    if isinstance(default, bool):
        return bool
    if isinstance(default, int):
        return int
    if isinstance(default, float):
        return float
    return str

def build_argparser_from_config() -> argparse.ArgumentParser:
    """
    Build an argparse.ArgumentParser from Config's Annotated metadata.
    Also adds builder-only flags: --config-dir, --config-file, and --set.
    CLI defaults are suppressed so they don't override values from YAML or ENV.
    """
    parser = argparse.ArgumentParser(description="LimitsDb CLI", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
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
        arg_kwargs: Dict[str, Any] = {"help": help_text or "", "default": argparse.SUPPRESS}
        # infer choices from Literal if present
        origin = get_origin(annotated)
        if origin is Literal:
            arg_kwargs["choices"] = tuple(get_args(annotated))
        # Make --schema required (empty-string default is just for template rendering)
        if name == "schema":
            arg_kwargs["required"] = True
        # Booleans: use BooleanOptionalAction for --flag / --no-flag
        if isinstance(default, bool):
            parser.add_argument(cli, action=argparse.BooleanOptionalAction, **arg_kwargs)
        else:
            parser.add_argument(cli, type=_arg_type_from_default(default), **arg_kwargs)
    # Builder-only flags (not part of Config dataclass)
    parser.add_argument("--config-dir", help="Configuration root (overrides autodiscovery of ~/.config/LimitsDb and /etc/limitsdb)")
    parser.add_argument("--config-file", help="Additional YAML overlay (highest priority)")
    parser.add_argument("--set", action="append", default=[], help="Overrides like key=value; supports dotted keys for nesting")
    return parser

def parse_args() -> argparse.Namespace:
    return build_argparser_from_config().parse_args()

# ------------------------------------------------------------------------------
# CLI --set parser (k=v, dotted keys)
# ------------------------------------------------------------------------------
def _parse_cli_sets(pairs: Sequence[str]) -> Dict[str, Any]:
    """
    Parse a list of key=value strings into a dict, with simple auto-typing."""
    out: Dict[str, Any] = {}
    for p in pairs or []:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        v = v.strip()
        # simple auto-typing
        if v.isdigit():
            v_typed: Any = int(v)
        elif v.lower() in ("true", "false"):
            v_typed = (v.lower() == "true")
        else:
            # try float
            try:
                v_typed = float(v)
            except ValueError:
                v_typed = v
        out[k.strip()] = v_typed
    return out

# ------------------------------------------------------------------------------
# Build final config dict using loader + CLI overrides
# ------------------------------------------------------------------------------
def build_config(defaults: Mapping[str, Any], cli_args: argparse.Namespace) -> Dict[str, Any]:
    """
    Produce the final configuration dictionary using the new loader (overlays + secrets).
    Environment and CLI overrides are handled inside the loader (ENV LDB_*), and here we map
    explicit flags to --set so they win with the highest priority.
    """
    # Map CLI flags (derived from Config metadata) into --set key=value overrides
    cli_sets: Dict[str, Any] = _parse_cli_sets(getattr(cli_args, "set", []))
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
        schema=cli_args.schema, profile=getattr(cli_args, "profile", None), cli_sets=cli_sets,
        explicit_config_file=getattr(cli_args, "config_file", None), explicit_config_dir=getattr(cli_args, "config_dir", None),
    )
    # Merge with embedded defaults (if you still keep some minimal defaults in code)
    final = dict(defaults)
    final.update(cfg)
    # Ensure schema/profile land in the object (the loader also uses them but they are not part of the dict)
    final["schema"] = cli_args.schema
    final["profile"] = getattr(cli_args, "profile", None)
    final["ilm_config_file"] = resolve_schema_file(
        schema=cli_args.schema, profile=getattr(cli_args, "profile", None), explicit_config_dir=getattr(cli_args, "config_dir", None),
        explicit_file=getattr(cli_args, "ilm_config_file", None), prefix_name="ilm", extension_name="yml", description="ilm configuration file",
    )
    return final
