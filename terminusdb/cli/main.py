# main.py
from __future__ import annotations
from typing import Any, Dict

from terminusdb.core.tdb_params_config import parse_args, build_config, Config
from terminusdb.core.tdb_default_config import DEFAULT_CONFIG
from terminusdb.core.tdb_logger import configure_logger, get_logger, reconfigure_logger
from terminusdb.core.tdb_runner import tdb_run

# Boot logger early with a conservative level; we’ll reconfigure after loading config
configure_logger(level="WARNING")

_SECRET_KEYS = {
    "prod_credentials",
    "hist_credentials",
    "prod_admin_credentials",
    "hist_admin_credentials",
}

def _mask_secrets(d: Dict[str, Any]) -> Dict[str, Any]:
    masked = dict(d)
    for k in _SECRET_KEYS:
        if k in masked and masked[k]:
            masked[k] = "****"
    return masked

def run_cli() -> None:
    logger = get_logger()
    args = parse_args()
    cfg_dict = build_config(DEFAULT_CONFIG, args)
    config = Config.from_dict(cfg_dict)
    reconfigure_logger(level=config.log_level)
    logger.debug("Effective config: %s", _mask_secrets(config.as_dict()))
    tdb_run(config)

if __name__ == "__main__":
    run_cli()
