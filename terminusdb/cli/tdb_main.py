"""Backward compatible aggregating module for CLI entry points."""
from __future__ import annotations

import argparse
from typing import Any, Dict

from terminusdb.core.tdb_params_config import parse_args, build_config, Config
from terminusdb.core.tdb_logger import configure_logger, get_logger, reconfigure_logger
from terminusdb.core.tdb_runner import tdb_run
from terminusdb.core.tdb_crypto import load_or_create_key
from terminusdb.core.tdb_utils import secret_keys_from_config, init_schema,  encrypt_secrets_in_place

# Boot the logger early with a conservative level; we’ll reconfigure after loading config.
configure_logger(level="WARNING")

def _mask_secrets(d: Dict[str, Any]) -> Dict[str, Any]:
    masked = dict(d)
    for k in secret_keys_from_config():
        if k in masked and masked[k]:
            masked[k] = "****"
    return masked

# ------------------------------------------------------------------------------
# Main runner
# ------------------------------------------------------------------------------
def run_cli() -> None:
    """
    Main entry point:
      1) Parse CLI (auto-built from Config Annotated metadata)
      2) Ensure key exists and auto-encrypt cleartext secrets (idempotent)
      3) Build merged config (overlays + env + --set)
      4) Reconfigure logger level
      5) Run ILM
    """
    logger = get_logger("run")
    args = parse_args()
    if hasattr(args,"log_level"):
        reconfigure_logger(level=args.log_level)
    # Ensure encryption key exists and auto-encrypt secrets if user left cleartext
    load_or_create_key()
    try:
        encrypt_secrets_in_place(schema=args.schema, profile=getattr(args, "profile", None), config_root=getattr(args, "config_dir", None))
    except Exception:
        logger.warning("Auto-encrypt failed; continuing. Loader will enforce encrypted secrets.",exc_info=True)
    # Build final config and run
    cfg_dict = build_config({}, args)
    config = Config.from_dict(cfg_dict)
    reconfigure_logger(level=config.log_level)
    logger.debug("Effective config: %s", _mask_secrets(config.to_dict()))
    tdb_run(config)

# ------------------------------------------------------------------------------
# Scaffolding utilities (INIT / CRYPT) in the same module
# ------------------------------------------------------------------------------

def _parse_init_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Initialize schema/profile config files")
    p.add_argument("--schema", required=True, help="Schema name (folder under schemas/)")
    p.add_argument("--profile", help="Profile name (e.g., dev, prod)")
    p.add_argument("--config-dir", help="Configuration root (overrides autodiscovery)")
    p.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    p.add_argument("--no-examples", action="store_true", help="Do not copy example ILM templates")
    p.add_argument("--log-level", default="INFO", help="Logging level (e.g., DEBUG, INFO, WARNING)", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    return p.parse_args()

def main_init() -> None:
    """
    Create schema/profile structure with:
      - config.yml (commented defaults + help, derived from Config Annotated metadata)
      - ilm.yml (empty)
      - ilm.example.yml (copied from package resources, unless --no-examples)
      - secrets.json (secret keys = ""; then auto-encrypted if values are present)
    """
    logger = get_logger("init")
    args = _parse_init_args()
    reconfigure_logger(level=args.log_level)
    # Ensure key exists (idempotent); secrets will be auto-encrypted if present
    load_or_create_key()
    logger.info("Creating configuration files with defaults and help")
    cfg_p, ilm_p, sec_p, ilm_exple_p = init_schema(
        schema=args.schema,
        profile=getattr(args, "profile", None),
        config_root=getattr(args, "config_dir", None),
        overwrite=bool(args.overwrite),
        with_examples=not bool(args.no_examples),
        auto_encrypt=True,
    )
    logger.info("Initialized: %s", cfg_p)
    logger.info("Initialized: %s", ilm_p)
    logger.info("Initialized: %s", sec_p)
    if ilm_exple_p:
        logger.info("Initialized: %s", ilm_exple_p)

def _parse_crypt_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Encrypt cleartext secrets in secrets.json")
    p.add_argument("--schema", required=True, help="Schema name (folder under schemas/)")
    p.add_argument("--profile", help="Profile name (e.g., dev, prod)")
    p.add_argument("--config-dir", help="Configuration root (overrides autodiscovery)")
    return p.parse_args()


def main_crypt() -> None:
    """
    Find secrets.json under schema/profile and encrypt any cleartext values in-place.
    """
    logger = get_logger("crypt")
    args = _parse_crypt_args()

    load_or_create_key()
    updated = encrypt_secrets_in_place(
        schema=args.schema,
        profile=getattr(args, "profile", None),
        config_root=getattr(args, "config_dir", None),
    )
    if updated:
        logger.info("Secrets updated: %s", updated)
    else:
        logger.info("No secrets to update (file missing or already encrypted).")


if __name__ == "__main__":
    run_cli()
