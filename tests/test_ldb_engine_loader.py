import pytest

from limitsdb.db.ldb_engine_loader import get_db_engine
from limitsdb.db.oracle.ldb_engine_impl import OracleEngine


def test_get_db_engine_returns_oracle_engine():
    engine = get_db_engine("oracle")

    assert engine is OracleEngine


def test_get_db_engine_raises_for_unknown_engine():
    with pytest.raises(ValueError):
        get_db_engine("unknown")
