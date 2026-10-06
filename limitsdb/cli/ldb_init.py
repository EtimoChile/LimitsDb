"""Command line interface for bootstrapping schema/profile configurations."""

from __future__ import annotations

import argparse

from limitsdb.core.ldb_crypto import load_or_create_key
from limitsdb.core.ldb_logger import configure_logger, get_logger, reconfigure_logger
from limitsdb.core.ldb_utils import init_schema

configure_logger(level="WARNING")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialize schema/profile config files",
    )
    parser.add_argument("--schema", required=True, help="Schema name (folder under schemas/)")
    parser.add_argument("--profile", help="Profile name (e.g., dev, prod)")
    parser.add_argument("--config-dir", help="Configuration root (overrides autodiscovery)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    parser.add_argument("--no-examples", action="store_true", help="Do not copy example ILM templates")
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (e.g., DEBUG, INFO, WARNING)",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )
    return parser.parse_args()


def run_cli() -> None:
    """Entry point for the ``ldb-init`` command."""
    logger = get_logger("init")
    try:
        args = _parse_args()
        reconfigure_logger(level=args.log_level)
        # Ensure key exists (idempotent); secrets will be auto-encrypted if present.
        load_or_create_key()
        logger.info("Creating configuration files with defaults and help")
        cfg_path, ilm_path, secrets_path, ilm_examples_path = init_schema(
            schema=args.schema,
            profile=getattr(args, "profile", None),
            config_root=getattr(args, "config_dir", None),
            overwrite=bool(args.overwrite),
            with_examples=not bool(args.no_examples),
            auto_encrypt=True,
        )
        logger.info("Initialized: %s", cfg_path)
        logger.info("Initialized: %s", ilm_path)
        logger.info("Initialized: %s", secrets_path)
        if ilm_examples_path:
            logger.info("Initialized: %s", ilm_examples_path)
    except ValueError as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":  # pragma: no cover
    run_cli()
