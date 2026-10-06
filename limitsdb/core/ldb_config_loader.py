# limitsdb/core/ldb_config_loader.py
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import yaml

from limitsdb.core.ldb_crypto import decrypt as _DECRYPT
from limitsdb.core.ldb_crypto import is_encrypted as _IS_ENC
from limitsdb.core.ldb_errors import ConfigurationError, SecretError
from limitsdb.core.ldb_logger import get_logger
from limitsdb.core.ldb_utils import get_config_roots, resolve_schema_file, secret_keys_from_config

logger = get_logger("config_loader")


# --- utils --------------------------------------------------------------------
def _deep_merge(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    res: dict[str, Any] = dict(a)
    for k, v in b.items():
        av = res.get(k)
        if isinstance(av, Mapping) and isinstance(v, Mapping):
            res[k] = _deep_merge(cast(Mapping[str, Any], av), cast(Mapping[str, Any], v))
        else:
            res[k] = v
    return res


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        data: Any = yaml.safe_load(text)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Unable to read YAML configuration at {path}: {exc}") from exc
    if data is None:
        return {}
    if isinstance(data, dict):
        return cast(dict[str, Any], data)
    raise ConfigurationError(f"YAML root must be a mapping (dict) in {path}")


def _load_secrets(path: Path) -> dict[str, Any]:
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SecretError(f"Unable to read secrets file at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SecretError(f"Secrets root must be a mapping (object) in {path}")
    return cast(dict[str, Any], data)


def _read_env_overrides(prefix: str = "LDB_") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in os.environ.items():
        if not k.startswith(prefix):
            continue
        # A single underscore belongs to the public flat key (for example,
        # LDB_CHUNK_SIZE -> chunk_size). A double underscore is reserved for
        # a future/nested key (LDB_GROUP__VALUE -> group.value).
        key = k[len(prefix) :].lower().replace("__", ".")
        # basic typing
        if v.lower() in ("true", "false"):
            out[key] = v.lower() == "true"
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


def _automatic_config_paths(*, schema: str, profile: str | None, explicit_config_dir: str | None) -> list[Path]:
    """Return existing runtime-config layers from lowest to highest priority."""
    filename = f"config.{profile}.yml" if profile else "config.yml"
    if explicit_config_dir:
        roots = [Path(explicit_config_dir)]
    else:
        user_root, system_root = get_config_roots()
        roots = [system_root, user_root]
    return [path for root in roots if (path := root / "schemas" / schema / filename).exists()]


def _apply_dot_set(cfg: dict[str, Any], key: str, value: Any) -> None:
    cur = cfg
    parts = key.split(".")
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def _apply_overrides(cfg: dict[str, Any], kvs: dict[str, Any]) -> dict[str, Any]:
    out = dict(cfg)
    for k, v in kvs.items():
        _apply_dot_set(out, k, v)
    return out


# --- loaders ------------------------------------------------------------------
def load_runtime_config(
    *,
    schema: str,
    profile: str | None,
    cli_sets: dict[str, Any],
    explicit_config_file: str | None = None,
    explicit_config_dir: str | None = None,
    enforce_encrypted_secrets: bool = True,
) -> dict[str, Any]:
    """
    config_file → ENV LDB_* → --set
    Then inject **decrypted** secrets from schemas/<schema>/secrets*.json.
    If enforce_encrypted_secrets=True, plaintext secrets are rejected.
    """
    cfg: dict[str, Any] = {}
    # 1-2) System then user config, or the single explicitly selected root.
    for config_path in _automatic_config_paths(schema=schema, profile=profile, explicit_config_dir=explicit_config_dir):
        cfg = _deep_merge(cfg, _load_yaml(config_path))
    # 3) Explicit config file is an overlay, not a replacement for the
    # automatically discovered layers.
    if explicit_config_file:
        config_file = resolve_schema_file(
            schema=schema,
            profile=profile,
            explicit_config_dir=explicit_config_dir,
            explicit_file=explicit_config_file,
            prefix_name="config",
            extension_name="yml",
            description="configuration file",
        )
        assert config_file is not None
        cfg = _deep_merge(cfg, _load_yaml(Path(config_file)))
    # 4) ENV
    env_over = _read_env_overrides()
    if env_over:
        cfg = _apply_overrides(cfg, env_over)
    # 5) CLI --set
    if cli_sets:
        cfg = _apply_overrides(cfg, cli_sets)
    # 6) Secrets (user > system), strictly enforced
    secrets: dict[str, Any] = {}
    secrets_path = resolve_schema_file(
        schema=schema,
        profile=profile,
        explicit_config_dir=explicit_config_dir,
        explicit_file=None,
        prefix_name="secrets",
        extension_name="json",
        description="secrets file",
    )
    if secrets_path:
        secrets = _load_secrets(Path(secrets_path))
    if secrets:
        for k in secret_keys_from_config():
            val = secrets.get(k)
            if val is None or val == "":
                continue
            if isinstance(val, str) and _IS_ENC(val):
                try:
                    cfg[k] = _DECRYPT(val)
                except Exception as exc:
                    raise SecretError(f"Unable to decrypt secret '{k}' at {secrets_path}") from exc
            else:
                if enforce_encrypted_secrets:
                    where = f" at {secrets_path}" if secrets_path else ""
                    raise SecretError(f"Secret '{k}' must be encrypted (enc:v1:aes256gcm:...){where}")
                cfg[k] = val  # only if you explicitly disabled enforcement
    # minimal defaults
    return cfg


def load_ilm_config(*, schema: str, profile: str | None, explicit_config_dir: str | None = None) -> dict[str, Any]:
    """Load ILM config from schema/profile layers."""
    ilm: dict[str, Any] = {}
    ilm_config_path = resolve_schema_file(
        schema=schema,
        profile=profile,
        explicit_config_dir=explicit_config_dir,
        explicit_file=None,
        prefix_name="ilm",
        extension_name="yml",
        description="ILM configuration file",
    )
    if ilm_config_path:
        ilm = _load_yaml(Path(ilm_config_path))
    return ilm
