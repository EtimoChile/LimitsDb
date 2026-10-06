from typing import cast
from limitsdb.db.ldb_engines import DatabaseEngine

def get_db_engine(engine_name: str) -> DatabaseEngine:
    """Retrieves the database engine class based on the provided engine name.
    Args:
        engine_name (str): The name of the database engine.
    Returns:
        type: DatabaseEngine interface"""
    if engine_name == "oracle":
        from limitsdb.db.oracle.ldb_engine_impl import OracleEngine
        return cast(DatabaseEngine, OracleEngine)
    raise ValueError(f"Unknown engine: {engine_name}")
