"""CLI entry point that runs TerminusDB ILM workflows."""
from __future__ import annotations

from typing import Any, Dict

from terminusdb.core.tdb_crypto import load_or_create_key
from terminusdb.core.tdb_logger import configure_logger, get_logger, reconfigure_logger
from terminusdb.core.tdb_params_config import Config, build_config, parse_args
from terminusdb.core.tdb_runner import tdb_run
from terminusdb.core.tdb_utils import encrypt_secrets_in_place, secret_keys_from_config

configure_logger(level="WARNING")


def _mask_secrets(data: Dict[str, Any]) -> Dict[str, Any]:
    masked = dict(data)
    for key in secret_keys_from_config():
        if key in masked and masked[key]:
            masked[key] = "****"
    return masked


def run_cli() -> None:
    """Main entry point for the `tdb-run` command."""
    logger = get_logger("run")
    args = parse_args()
    if hasattr(args, "log_level"):
        reconfigure_logger(level=args.log_level)

    load_or_create_key()
    try:
        encrypt_secrets_in_place(
            schema=args.schema,
            profile=getattr(args, "profile", None),
            config_root=getattr(args, "config_dir", None),
        )
    except Exception:
        logger.warning(
            "Auto-encrypt failed; continuing. Loader will enforce encrypted secrets.",
            exc_info=True,
        )

    cfg_dict = build_config({}, args)
    config = Config.from_dict(cfg_dict)
    reconfigure_logger(level=config.log_level)
    logger.debug("Effective config: %s", _mask_secrets(config.to_dict()))
    tdb_run(config)


if __name__ == "__main__":
    run_cli()
