from typing import Callable, cast

from terminusdb.core.tdb_params_config import Config
from terminusdb.db.tdb_engines import DatabaseEngine
from terminusdb.db.tdb_engine_impl import DatabaseEngineImplementer

ImplementerFactory = Callable[[Config], DatabaseEngineImplementer]


def get_db_engine(engine_name: str) -> DatabaseEngine:
    """Retrieves the database engine class based on the provided engine name."""
    engine_key = (engine_name or "").lower()
    if engine_key == "oracle":
        from terminusdb.db.oracle.tdb_engine import OracleEngine

        return cast(DatabaseEngine, OracleEngine)
    raise ValueError(f"Unknown engine: {engine_name}")


def get_db_engine_impl(engine_name: str) -> ImplementerFactory:
    """Return a factory that builds the provisioning implementer for the engine."""
    engine_key = (engine_name or "").lower()
    if engine_key == "oracle":
        from terminusdb.db.oracle.tdb_engine_impl import OracleEngineImplementer

        return cast(ImplementerFactory, OracleEngineImplementer)
    raise ValueError(f"Unknown engine implementer for: {engine_name}")
