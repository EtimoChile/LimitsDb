"""Interfaces for engine-specific provisioning helpers used by tdb-impl."""
from __future__ import annotations

from typing import Protocol, Sequence, Tuple

from terminusdb.core.tdb_params_config import Config


class DatabaseEngineImplementer(Protocol):
    """Protocol for database-engine-specific provisioning helpers."""

    def provision(
        self,
        source_tables: Sequence[Tuple[str, str]],
        history_tables: Sequence[Tuple[str, str]],
    ) -> None:
        """Create or update database artifacts required by TerminusDB."""


def get_engine_implementer(config: Config) -> DatabaseEngineImplementer:
    """Return the provisioning implementer for the configured engine."""

    from terminusdb.db.tdb_engine_loader import get_db_engine_impl

    engine_name = (config.db_engine or "").lower()
    return get_db_engine_impl(engine_name)(config)
