"""Command line interface to execute ILM runs."""
from __future__ import annotations

from typing import Any, Dict

from terminusdb.core.tdb_params_config import parse_args, build_config, Config
from terminusdb.core.tdb_logger import configure_logger, get_logger, reconfigure_logger
from terminusdb.core.tdb_runner import tdb_run
from terminusdb.core.tdb_crypto import load_or_create_key
from terminusdb.core.tdb_utils import secret_keys_from_config, encrypt_secrets_in_place


# Boot the logger early with a conservative level; we'll reconfigure after loading config.
configure_logger(level="WARNING")


def _mask_secrets(config_dict: Dict[str, Any]) -> Dict[str, Any]:
    masked = dict(config_dict)
    for key in secret_keys_from_config():
        if key in masked and masked[key]:
            masked[key] = "****"
    return masked


def run_cli() -> None:
    """Entry point for the ``tdb-run`` command."""

    logger = get_logger("run")
    try:
        args = parse_args()
        if hasattr(args, "log_level"):
            reconfigure_logger(level=args.log_level)

        # Ensure encryption key exists and auto-encrypt secrets if user left cleartext.
        load_or_create_key()
        try:
            encrypt_secrets_in_place(
                schema=args.schema,
                profile=getattr(args, "profile", None),
                config_root=getattr(args, "config_dir", None),
            )
        except Exception:  # pragma: no cover - log and continue
            logger.warning(
                "Auto-encrypt failed; continuing. Loader will enforce encrypted secrets.",
                exc_info=True,
            )

        # Build final config and run
        cfg_dict = build_config({}, args)
        config = Config.from_dict(cfg_dict)
        reconfigure_logger(level=config.log_level)
        logger.debug("Effective config: %s", _mask_secrets(config.to_dict()))
        tdb_run(config) # type: ignore
    except (ValueError, KeyError) as exc:
        logger.error("%s", exc)
        logger.error(exc, exc_info=True)
        raise SystemExit(1)


if __name__ == "__main__":  # pragma: no cover
    run_cli()
