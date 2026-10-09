import pytest

from limitsdb.db.ldb_engine_loader import get_db_engine
from limitsdb.db.oracle.ldb_engine_impl import OracleEngine


def test_oracle_engine_name_resolves_to_the_oracle_implementation():
    # Spec: docs/configuration-contract.md (DEC-014) — "oracle" is the only accepted
    # db_engine value; it must resolve to OracleEngine
    # Given: the registered engine name "oracle"
    # When: the engine class is resolved
    engine = get_db_engine("oracle")

    # Then: OracleEngine is returned
    assert engine is OracleEngine


@pytest.mark.parametrize("engine_name", ["postgres", "unknown"])
def test_unsupported_engine_name_raises_with_supported_engines_listed(engine_name: str):
    # Spec: docs/configuration-contract.md (DEC-014) — unsupported engines are rejected
    # before any connection attempt; the error message names the requested engine and
    # lists supported ones so the user can correct the config
    # Given: an unsupported engine name
    # When / Then: ValueError names both the bad engine and the supported list
    with pytest.raises(
        ValueError,
        match=rf"Unsupported database engine: {engine_name}; supported engines: oracle",
    ):
        get_db_engine(engine_name)
