"""Command line interface to execute ILM runs."""

from __future__ import annotations

from typing import Any

from limitsdb.core.ldb_crypto import load_or_create_key
from limitsdb.core.ldb_errors import LimitsDbError
from limitsdb.core.ldb_logger import configure_logger, get_logger, reconfigure_logger
from limitsdb.core.ldb_params_config import Config, build_config, parse_args
from limitsdb.core.ldb_runner import ldb_run
from limitsdb.core.ldb_utils import encrypt_secrets_in_place, secret_keys_from_config

# Boot the logger early with a conservative level; we'll reconfigure after loading config.
configure_logger(level="WARNING")


def _mask_secrets(config_dict: dict[str, Any]) -> dict[str, Any]:
    masked = dict(config_dict)
    for key in secret_keys_from_config():
        if masked.get(key):
            masked[key] = "****"
    return masked


def run_cli() -> None:
    """Entry point for the ``ldb-run`` command."""
    logger = get_logger("run")
    try:
        args = parse_args()
        if hasattr(args, "log_level"):
            reconfigure_logger(level=args.log_level)
        # Ensure encryption key exists and auto-encrypt secrets if user left cleartext.
        load_or_create_key()
        encrypt_secrets_in_place(
            schema=args.schema,
            profile=getattr(args, "profile", None),
            config_root=getattr(args, "config_dir", None),
        )
        # Build final config and run
        cfg_dict = build_config({}, args)
        config = Config.from_dict(cfg_dict)
        reconfigure_logger(level=config.log_level)
        logger.debug("Effective config: %s", _mask_secrets(config.to_dict()))
        ldb_run(config)
    except (LimitsDbError, ValueError, KeyError) as exc:
        logger.error("%s", exc)
        logger.error(exc, exc_info=True)
        raise SystemExit(1) from exc


if __name__ == "__main__":  # pragma: no cover
    run_cli()
