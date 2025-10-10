# terminusdb/core/tdb_config_loader.py
from __future__ import annotations
import os, json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Iterable, cast

from platformdirs import PlatformDirs            # requerido
import yaml                                      # requerido
from terminusdb.core.tdb_crypto import (         # requerido
    is_encrypted as _IS_ENC,
    decrypt as _DECRYPT,
)

SECRET_KEYS = [
    "prod_credentials",
    "hist_credentials",
    "prod_admin_credentials",
    "hist_admin_credentials",
]

# --- utils --------------------------------------------------------------------
def _deep_merge(a: Mapping[str, Any], b: Mapping[str, Any]) -> Dict[str, Any]:
    res: Dict[str, Any] = dict(a)
    for k, v in b.items():
        av = res.get(k)
        if isinstance(av, Mapping) and isinstance(v, Mapping):
            res[k] = _deep_merge(cast(Mapping[str, Any], av), cast(Mapping[str, Any], v))
        else:
            res[k] = v
    return res

def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    data: Any = yaml.safe_load(text)  # <- data es Any (explícito)
    if data is None:
        return {}
    if isinstance(data, dict):
        return cast(Dict[str, Any], data)  # <- fijamos tipo para Pylance
    # Si el YAML no tiene un mapeo en la raíz, consideramos que es inválido para config
    raise TypeError(f"YAML root must be a mapping (dict) in {path}")


def _layer_files_exist(paths: Iterable[Optional[Path]]) -> List[Path]:
    return [p for p in paths if p and p.exists()]

def _appdirs(appname: str = "TerminusDB") -> Tuple[Path, Path]:
    d = PlatformDirs(appname, appauthor=False)
    return Path(d.user_config_dir), Path("/etc/terminusdb")

def _assets_dir() -> Path:
    return (Path(__file__).resolve().parent / ".." / "assets" / "config").resolve()

def _read_env_overrides(prefix: str = "TDB_") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in os.environ.items():
        if not k.startswith(prefix):
            continue
        key = k[len(prefix):].lower().replace("__", ".").replace("_", ".")
        # basic typing
        if v.lower() in ("true", "false"):
            out[key] = (v.lower() == "true"); continue
        try:
            out[key] = int(v); continue
        except ValueError:
            pass
        try:
            out[key] = float(v); continue
        except ValueError:
            pass
        out[key] = v
    return out

def _apply_dot_set(cfg: Dict[str, Any], key: str, value: Any) -> None:
    cur = cfg
    parts = key.split(".")
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value

def _apply_overrides(cfg: Dict[str, Any], kvs: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(cfg)
    for k, v in kvs.items():
        _apply_dot_set(out, k, v)
    return out

# --- path resolvers -----------------------------------------------------------
def _schema_layer_paths(schema: str, profile: Optional[str], base: Path) -> List[Path]:
    schema_dir = base / "schemas" / schema
    files: List[Path] = [schema_dir / "config.yml"]
    if profile:
        files.append(schema_dir / f"config.{profile}.yml")
    return _layer_files_exist(files)

def _schema_ilm_paths(schema: str, profile: Optional[str], base: Path) -> List[Path]:
    schema_dir = base / "schemas" / schema
    files: List[Path] = [schema_dir / "ilm.yml"]
    if profile:
        files.append(schema_dir / f"ilm.{profile}.yml")
    return _layer_files_exist(files)

def _schema_secrets_paths(schema: str, profile: Optional[str], base: Path) -> List[Path]:
    schema_dir = base / "schemas" / schema
    files: List[Path] = []
    if profile:
        files.append(schema_dir / f"secrets.{profile}.json")  # most specific first
    files.append(schema_dir / "secrets.json")
    return _layer_files_exist(files)

def _global_paths(base: Path) -> List[Path]:
    return _layer_files_exist([base / "global" / "config.yml"])

def _assets_paths(schema: str, profile: Optional[str]) -> Tuple[List[Path], List[Path]]:
    base = _assets_dir()
    global_files = _layer_files_exist([base / "global.default.yml"])
    schema_files = _layer_files_exist([
        base / "schemas" / schema / "config.default.yml",
        base / "schemas" / schema / f"config.{profile}.default.yml" if profile else None,
    ])
    return global_files, schema_files

# --- loaders ------------------------------------------------------------------
def load_runtime_config(
    *,
    schema: str,
    profile: Optional[str],
    cli_sets: Dict[str, Any],
    explicit_config_file: Optional[str] = None,
    explicit_config_dir: Optional[str] = None,
    enforce_encrypted_secrets: bool = True,
) -> Dict[str, Any]:
    """
    Overlay order (low → high):
      package assets → /etc → ~/.config → --config-file → ENV TDB_* → --set
    Then inject **decrypted** secrets from schemas/<schema>/secrets*.json.
    If enforce_encrypted_secrets=True, plaintext secrets are rejected.
    """
    cfg: Dict[str, Any] = {}

    # 1) package assets
    assets_global, assets_schema = _assets_paths(schema, profile)
    for f in assets_global + assets_schema:
        cfg = _deep_merge(cfg, _load_yaml(f))

    # 2/3) system/user dirs (or explicit)
    if explicit_config_dir:
        user_dir = system_dir = Path(explicit_config_dir)
    else:
        user_dir, system_dir = _appdirs("TerminusDB")

    for f in _global_paths(system_dir) + _schema_layer_paths(schema, profile, system_dir):
        cfg = _deep_merge(cfg, _load_yaml(f))
    for f in _global_paths(user_dir) + _schema_layer_paths(schema, profile, user_dir):
        cfg = _deep_merge(cfg, _load_yaml(f))

    # 4) explicit file
    if explicit_config_file:
        p = Path(explicit_config_file)
        if not p.exists():
            raise FileNotFoundError(f"--config-file not found: {p}")
        cfg = _deep_merge(cfg, _load_yaml(p))

    # 5) ENV
    env_over = _read_env_overrides()
    if env_over:
        cfg = _apply_overrides(cfg, env_over)

    # 6) CLI --set
    if cli_sets:
        cfg = _apply_overrides(cfg, cli_sets)

    # 7) Secrets (user > system), strictly enforced
    secrets: Dict[str, Any] = {}
    secrets_path: Optional[Path] = None
    for path in _schema_secrets_paths(schema, profile, user_dir) + _schema_secrets_paths(schema, profile, system_dir):
        try:
            secrets = json.loads(Path(path).read_text(encoding="utf-8"))
            secrets_path = Path(path)
            break
        except Exception:
            continue

    if secrets:
        for k in SECRET_KEYS:
            val = secrets.get(k)
            if val is None:
                continue
            if isinstance(val, str) and _IS_ENC(val):
                cfg[k] = _DECRYPT(val)
            else:
                if enforce_encrypted_secrets:
                    where = f" at {secrets_path}" if secrets_path else ""
                    raise ValueError(f"Secret '{k}' must be encrypted (enc:v1:aes256gcm:...){where}")
                cfg[k] = val  # only if you explicitly disabled enforcement

    # minimal defaults
    cfg.setdefault("db_engine", "oracle")
    cfg.setdefault("parallel_max", 10)
    cfg.setdefault("log_level", "INFO")
    return cfg

def load_ilm_config(
    *,
    schema: str,
    profile: Optional[str],
    explicit_config_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Overlay order for ILM: package assets → /etc → ~/.config."""
    ilm: Dict[str, Any] = {}

    base_assets = _assets_dir() / "schemas" / schema
    assets_files = _layer_files_exist([
        base_assets / "ilm.default.yml",
        base_assets / f"ilm.{profile}.default.yml" if profile else None,
    ])
    for f in assets_files:
        ilm = _deep_merge(ilm, _load_yaml(f))

    if explicit_config_dir:
        user_dir = system_dir = Path(explicit_config_dir)
    else:
        user_dir, system_dir = _appdirs("TerminusDB")

    for f in _schema_ilm_paths(schema, profile, system_dir):
        ilm = _deep_merge(ilm, _load_yaml(f))
    for f in _schema_ilm_paths(schema, profile, user_dir):
        ilm = _deep_merge(ilm, _load_yaml(f))

    return ilm
