import pytest

from limitsdb.db.ldb_engine_loader import get_db_engine
from limitsdb.db.oracle.ldb_engine_impl import OracleEngine


def test_get_db_engine_returns_oracle_engine():
    engine = get_db_engine("oracle")

    assert engine is OracleEngine


@pytest.mark.parametrize("engine_name", ["postgres", "unknown"])
def test_get_db_engine_raises_for_unsupported_engine(engine_name: str):
    with pytest.raises(ValueError, match=rf"Unsupported database engine: {engine_name}; supported engines: oracle"):
        get_db_engine(engine_name)
