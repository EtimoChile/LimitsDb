# terminusdb/core/tdb_config_loader.py
from __future__ import annotations
import os, json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, cast
import yaml
from terminusdb.core.tdb_crypto import (is_encrypted as _IS_ENC, decrypt as _DECRYPT)
from terminusdb.core.tdb_logger import get_logger
from terminusdb.core.tdb_utils import resolve_schema_file, secret_keys_from_config

logger = get_logger("config_loader")

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
    data: Any = yaml.safe_load(text)
    if data is None:
        return {}
    if isinstance(data, dict):
        return cast(Dict[str, Any], data)
    raise TypeError(f"YAML root must be a mapping (dict) in {path}")

def _read_env_overrides(prefix: str = "TDB_") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in os.environ.items():
        if not k.startswith(prefix):
            continue
        key = k[len(prefix):].lower().replace("__", ".").replace("_", ".")
        # basic typing
        if v.lower() in ("true", "false"):
            out[key] = (v.lower() == "true")
            continue
        try:
            out[key] = int(v)
            continue
        except ValueError:
            pass
        try:
            out[key] = float(v)
            continue
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

# --- loaders ------------------------------------------------------------------
def load_runtime_config(
    *, schema: str, profile: Optional[str], cli_sets: Dict[str, Any], explicit_config_file: Optional[str] = None,
    explicit_config_dir: Optional[str] = None, enforce_encrypted_secrets: bool = True
) -> Dict[str, Any]:
    """
    config_file → ENV TDB_* → --set
    Then inject **decrypted** secrets from schemas/<schema>/secrets*.json.
    If enforce_encrypted_secrets=True, plaintext secrets are rejected.
    """
    cfg: Dict[str, Any] = {}
    # 1) system/user dirs (or explicit)
    config_file = resolve_schema_file(
        schema=schema, profile=profile, explicit_config_dir=explicit_config_dir, explicit_file=explicit_config_file, prefix_name="config",
        extension_name="yml", description="configuration file"
    )
    if config_file:
        cfg = _deep_merge(cfg, _load_yaml(Path(config_file)))
    # 5) ENV
    env_over = _read_env_overrides()
    if env_over:
        cfg = _apply_overrides(cfg, env_over)
    # 6) CLI --set
    if cli_sets:
        cfg = _apply_overrides(cfg, cli_sets)
    # 7) Secrets (user > system), strictly enforced
    secrets: Dict[str, Any] = {}
    secrets_path = resolve_schema_file(
        schema=schema, profile=profile, explicit_config_dir=explicit_config_dir, explicit_file=None, prefix_name="secrets", extension_name="json",
        description="secrets file"
    )
    if secrets_path:
        secrets = json.loads(Path(secrets_path).read_text(encoding="utf-8"))
    if secrets:
        for k in secret_keys_from_config():
            val = secrets.get(k)
            if val is None or val == "":
                continue
            if isinstance(val, str) and _IS_ENC(val):
                cfg[k] = _DECRYPT(val)
            else:
                if enforce_encrypted_secrets:
                    where = f" at {secrets_path}" if secrets_path else ""
                    raise ValueError(f"Secret '{k}' must be encrypted (enc:v1:aes256gcm:...){where}")
                cfg[k] = val  # only if you explicitly disabled enforcement
    # minimal defaults
    return cfg

def load_ilm_config(*, schema: str, profile: Optional[str], explicit_config_dir: Optional[str] = None) -> Dict[str, Any]:
    """Load ILM config from schema/profile layers."""
    ilm: Dict[str, Any] = {}
    ilm_config_path = resolve_schema_file(
        schema=schema, profile=profile, explicit_config_dir=explicit_config_dir, explicit_file=None, prefix_name="ilm", extension_name="yml",
        description="ILM configuration file"
    )
    if ilm_config_path:
        ilm = _load_yaml(Path(ilm_config_path))
    return ilm
