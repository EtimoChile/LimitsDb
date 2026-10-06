from __future__ import annotations
from typing import Any, Dict, List, Optional, Iterable, Tuple, Sequence, TypeVar, Literal, get_args, get_origin, get_type_hints, TYPE_CHECKING
import json
from pathlib import Path
from platformdirs import PlatformDirs  # requerido
from importlib.resources import files as ir_files

if TYPE_CHECKING:
    from limitsdb.core.ldb_params_config import Config
from limitsdb.core.ldb_logger import get_logger
from limitsdb.core.ldb_meta import Help, Secret, CliOnly
from limitsdb.core.ldb_crypto import load_or_create_key, encrypt, is_encrypted

logger = get_logger("utils")

def nvl(value: Optional[Any], default: Any) -> Any:
    """Returns the value if it is not None, otherwise returns the default value.
    Args:
        value: The value to evaluate.
        default: The value to return if `value` is None.
    Returns:
        The original value if not None, else the default."""
    return value if value is not None else default

T = TypeVar('T')

def max_ignore_none(values: Sequence[Optional[T]]) -> Optional[T]:
    """
    Returns the maximum value from a sequence, ignoring None values.
    
    Raises:
        ValueError: If no non-None values are found.
        TypeError: If values are not mutually comparable.

    :param values: Sequence of optional values.
    :return: The maximum value or None if all values are None.
    """
    filtered = [v for v in values if v is not None]
    if not filtered:
        return None
    try:
        return max(filtered)  # type: ignore
    except TypeError as e:
        raise TypeError("Values are not mutually comparable.") from e

def indent_lines(text: str, spaces: int) -> str:
    """
    Returns the input string with all lines after the first indented by the given number of spaces.

    :param text: Multiline string to process.
    :param spaces: Number of spaces to prepend to each line after the first.
    :return: Modified string with indentation applied.
    """
    return ("\n" + (" " * spaces)).join(text.splitlines())

def join_wrapped(connector: str, items: Iterable[str], max_line_length: int) -> str:
    """
    Joins items into a comma-separated string, wrapping lines when the max_line_length is exceeded.
    Lines after the first begin with ',' to indicate continuation.

    :param connector: String to use as a separator (e.g., ',').
    :param items: Iterable of elements to join (converted to strings).
    :param max_line_length: Max allowed characters per line before wrapping.
    :return: String with comma-separated items and continuation lines starting with ','.
    """
    lines: List[str] = []
    current_line = ""
    for item in map(str, items):
        candidate = (connector if current_line else "") + item
        if len(current_line) + len(candidate) > max_line_length:
            lines.append(current_line)
            current_line = connector + item
        else:
            current_line += candidate
    if current_line:
        lines.append(current_line)
    return "\n".join(lines)

def get_effective_credentials(cfg: Config, *, admin: bool = False, env: Optional[Literal["SOURCE", "HISTORY"]] = None):
    """Return (username, password, dsn) tuple according to action and admin flag."""
    is_source = cfg.action == "SOURCE_ILM" if env is None else (env == "SOURCE")
    env_part = "source" if is_source else "history"
    role = "admin_" if admin else ""
    user_key = f"{role}{env_part}_username"
    pwd_key = f"{role}{env_part}_password"
    dsn_key = f"{env_part}_dsn"
    user: Optional[str] = getattr(cfg, user_key, None)
    pwd: Optional[str] = getattr(cfg, pwd_key, None)
    dsn: Optional[str] = getattr(cfg, dsn_key, None)
    missing = [key for key, value in ((user_key, user), (pwd_key, pwd), (dsn_key, dsn)) if not value]
    if missing:
        raise ValueError(f"Missing {'/'.join(missing)} for action={cfg.action}")
    return user, pwd, dsn

APPNAME = "LimitsDb"

def _schema_dir(schema: str, profile: Optional[str], config_root: Optional[str]) -> Path:
    base = Path(config_root) if config_root else Path(get_config_roots(APPNAME)[0])
    d = base / "schemas" / schema
    d.mkdir(parents=True, exist_ok=True)
    return d

def resolve_schema_file(
    *, schema: str, profile: Optional[str], explicit_config_dir: Optional[str], explicit_file: Optional[str],
    prefix_name: Literal["config", "ilm", "secrets"], extension_name: Literal["yml", "json"] = "yml", description: str,
) -> Optional[str]:
    """
    Resolve the file path for a schema-level file (config.yml, ilm.yml, secrets.json).

    Rules:
      1. Always start from --config-dir if provided; otherwise use default roots.
      2. If explicit_file is given:
         - If absolute, return as-is.
         - If relative, resolve under <root>/schemas/<schema>/.
      3. If not given:
         - Use <prefix>.<profile>.yml if profile is set, else <prefix>.yml.
    """
    user_root, sys_root = get_config_roots()
    roots = [Path(explicit_config_dir)] if explicit_config_dir else [user_root, sys_root]
    # Case 1: explicit path
    if explicit_file:
        p = Path(explicit_file)
        if not p.is_absolute():
            for root in roots:
                cand = root / "schemas" / schema / p
                if cand.exists():
                    return str(cand.resolve())
        if p.exists():
            return str(p.resolve())
        raise FileNotFoundError(f"Specified {description} not found: {explicit_file}")
    # Case 2: automatic candidate
    filename = f"{prefix_name}.{profile}.{extension_name}" if profile else f"{prefix_name}.{extension_name}"
    for root in roots:
        cand = root / "schemas" / schema / filename
        if cand.exists():
            return str(cand.resolve())
    return None

def _hints():
    from limitsdb.core.ldb_params_config import Config  # lazy import to avoid circulars
    return get_type_hints(Config, include_extras=True)

def secret_keys_from_config() -> List[str]:
    keys: List[str] = []
    for name, annotated in _hints().items():
        metas = get_args(annotated)
        if any(isinstance(m, Secret) and m.enabled for m in metas):
            keys.append(name)
    return keys

def render_config_template_with_help() -> str:
    from limitsdb.core.ldb_params_config import Config  # lazy import to avoid circulars
    lines: List[str] = ["# LimitsDb config template", "# All keys are commented; defaults are shown on the right; help after the hash.", "", ]
    for name, annotated in _hints().items():
        metas = get_args(annotated)
        # skip secrets from config.yml (they go in secrets.json)
        if any(isinstance(m, Secret) and m.enabled for m in metas) or any(isinstance(m, CliOnly) and m.enabled for m in metas):
            continue
        help_text = next((m.text for m in metas if isinstance(m, Help)), "")
        default = getattr(Config, name)
        # show Literal choices nicely if default is a Literal member
        if get_origin(annotated) is Literal:
            choices = ", ".join(repr(x) for x in get_args(annotated))
            if help_text:
                help_text = f"{help_text} (choices: {choices})"
            else:
                help_text = f"(choices: {choices})"
        dv = f'"{default}"' if isinstance(default, str) else default
        lines.append(f"# {name}: {dv}  # {help_text}".rstrip())
    lines.append("")
    return "\n".join(lines)

def write_or_update_secrets(schema: str, profile: Optional[str], config_root: Optional[str], overwrite: bool = False) -> Path:
    sd = _schema_dir(schema, profile, config_root)
    p = sd / ("secrets.json" if not profile else f"secrets.{profile}.json")
    current: Dict[str, Any] = {}
    if p.exists():
        try:
            current = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    keys = secret_keys_from_config()
    if overwrite:
        current = {k: "" for k in keys}
    else:
        for k in keys:
            current.setdefault(k, "")
    p.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p

def encrypt_secrets_in_place(schema: str, profile: Optional[str], config_root: Optional[str]) -> Optional[Path]:
    load_or_create_key()
    sd = _schema_dir(schema, profile, config_root)
    p = sd / ("secrets.json" if not profile else f"secrets.{profile}.json")
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    updated = False
    for k in secret_keys_from_config():
        v = data.get(k)
        if isinstance(v, str) and v != "" and not is_encrypted(v):
            data[k] = encrypt(v)
            updated = True
    if updated:
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return p
    return None

def write_config_yaml(schema: str, profile: Optional[str], config_root: Optional[str], overwrite: bool = False) -> Path:
    sd = _schema_dir(schema, profile, config_root)
    p = sd / ("config.yml" if not profile else f"config.{profile}.yml")
    if overwrite or not p.exists():
        p.write_text(render_config_template_with_help(), encoding="utf-8")
    return p

def write_ilm_yaml(schema: str, profile: Optional[str], config_root: Optional[str], overwrite: bool = False) -> Path:
    sd = _schema_dir(schema, profile, config_root)
    p = sd / ("ilm.yml" if not profile else f"ilm.{profile}.yml")
    if overwrite or not p.exists():
        p.write_text("tables: []\n", encoding="utf-8")
    return p

def _read_ilm_example_text() -> str:
    # Will work whether installed as a wheel or running from source
    logger.debug("Reading ILM example template from package resources %s", (ir_files("limitsdb.resources") / "ilm.example.yml"))
    return (ir_files("limitsdb.resources") / "ilm.example.yml").read_text(encoding="utf-8")

def write_ilm_example(schema: str, profile: str | None, config_root: str | None, overwrite: bool = False) -> Path:
    logger.debug("Writing ILM example template for schema=%s, profile=%s, config_root=%s, overwrite=%s", schema, profile, config_root, overwrite)
    sd = _schema_dir(schema, profile, config_root)
    out = sd / ("ilm.example.yml" if not profile else f"ilm.{profile}.example.yml")
    logger.debug("ILM example path: %s", out)
    if overwrite or not out.exists():
        logger.debug("Creating ILM example file")
        example_text = _read_ilm_example_text()
        out.write_text(example_text, encoding="utf-8")
    return out

def get_config_roots(appname: str = APPNAME) -> Tuple[Path, Path]:
    d = PlatformDirs(appname, appauthor=False)
    return Path(d.user_config_dir), Path("/etc/limitsdb")

def init_schema(
    *, schema: str, profile: Optional[str] = None, config_root: Optional[str] = None, overwrite: bool = False, with_examples: bool = True,
    auto_encrypt: bool = True
) -> Tuple[Path, Path, Path, Path | None]:
    """ Initialize schema/profile configuration structure.  """
    # ensure key exists (idempotent)
    load_or_create_key()
    logger.debug("Initializing schema/profile structure with_examples=%s, auto_encrypt=%s, overwrite=%s", with_examples, auto_encrypt, overwrite)
    cfg_p = write_config_yaml(schema, profile, config_root, overwrite)
    ilm_p = write_ilm_yaml(schema, profile, config_root, overwrite)
    sec_p = write_or_update_secrets(schema, profile, config_root, overwrite)
    ilm_exple_p = None
    if with_examples:
        ilm_exple_p = write_ilm_example(schema, profile, config_root, overwrite)
    if auto_encrypt:
        encrypt_secrets_in_place(schema, profile, config_root)
    return cfg_p, ilm_p, sec_p, ilm_exple_p
