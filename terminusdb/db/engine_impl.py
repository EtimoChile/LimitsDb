"""Factory helpers that return engine-specific provisioning implementers."""
from __future__ import annotations

from typing import Protocol, Sequence, Tuple

from terminusdb.core.tdb_params_config import Config


class EngineImplementer(Protocol):
    """Protocol for engine-specific provisioning helpers."""

    def provision(
        self,
        source_tables: Sequence[Tuple[str, str]],
        history_tables: Sequence[Tuple[str, str]],
    ) -> None:
        """Create or update database artifacts required by TerminusDB."""


def get_engine_implementer(config: Config) -> EngineImplementer:
    """Return the provisioning implementer for the configured engine."""

    engine = (config.db_engine or "").lower()
    if engine == "oracle":
        from terminusdb.db.oracle.engine_impl import OracleImplementer

        return OracleImplementer(config)
    raise NotImplementedError(f"tdb-impl is not implemented for engine '{config.db_engine}'")
