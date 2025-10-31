"""CLI entry point that encrypts TerminusDB secret files."""
from __future__ import annotations

import argparse

from terminusdb.core.tdb_crypto import load_or_create_key
from terminusdb.core.tdb_logger import configure_logger, get_logger
from terminusdb.core.tdb_utils import encrypt_secrets_in_place

configure_logger(level="WARNING")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Encrypt cleartext secrets in secrets.json")
    parser.add_argument("--schema", required=True, help="Schema name (folder under schemas/)")
    parser.add_argument("--profile", help="Profile name (e.g., dev, prod)")
    parser.add_argument("--config-dir", help="Configuration root (overrides autodiscovery)")
    return parser.parse_args()


def run_cli() -> None:
    """Encrypt cleartext secrets in-place inside `secrets.json`."""
    logger = get_logger("crypt")
    args = _parse_args()

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
