"""Provisioning CLI that creates TerminusDB control objects and DB links."""
from __future__ import annotations

import argparse
from typing import Dict, Sequence, Tuple

from terminusdb.core.tdb_crypto import load_or_create_key
from terminusdb.core.tdb_logger import configure_logger, get_logger, reconfigure_logger
from terminusdb.core.tdb_params_config import Config, build_config, parse_args
from terminusdb.core.tdb_utils import secret_keys_from_config
from terminusdb.core.tdb_ilm_config import resolve_and_load_ilm_rows
from terminusdb.db.engine_impl import get_engine_implementer

configure_logger(level="WARNING")


def _mask_secrets(cfg: Dict[str, object]) -> Dict[str, object]:
    masked = dict(cfg)
    for key in secret_keys_from_config():
        if key in masked and masked[key]:
            masked[key] = "****"
    return masked


def _parse_cli_args() -> argparse.Namespace:
    return parse_args()


def run_cli() -> None:
    logger = get_logger("impl")
    args = _parse_cli_args()
    if getattr(args, "log_level", None):
        reconfigure_logger(level=args.log_level)
    load_or_create_key()
    cfg_dict = build_config({}, args)
    config = Config.from_dict(cfg_dict)
    reconfigure_logger(level=config.log_level)
    logger.debug("Effective config: %s", _mask_secrets(config.to_dict()))

    ilm_rows = resolve_and_load_ilm_rows(
        schema=config.schema,
        profile=config.profile,
        config_dir=getattr(args, "config_dir", None),
    )
    source_tables: Sequence[Tuple[str, str]] = sorted(
        {
            (row["cnf_source_owner"], row["cnf_table_name"])
            for row in ilm_rows
            if row.get("cnf_source_owner") and row.get("cnf_table_name")
        }
    )
    history_tables: Sequence[Tuple[str, str]] = sorted(
        {
            (row["cnf_history_owner"], row["cnf_table_name"])
            for row in ilm_rows
            if row.get("cnf_history_owner") and row.get("cnf_table_name")
        }
    )

    implementer = get_engine_implementer(config)
    implementer.provision(source_tables, history_tables)


if __name__ == "__main__":
    run_cli()
