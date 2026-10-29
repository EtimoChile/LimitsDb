import pytest

from limitsdb.core import ldb_params_config
from limitsdb.db.ldb_engine_loader import get_db_engine
from limitsdb.db.oracle.ldb_engine_impl import OracleEngine
from limitsdb.db.postgresql.ldb_engine_impl import PostgresEngine


def test_oracle_engine_name_resolves_to_the_oracle_implementation():
    # Spec: docs/configuration-contract.md — "oracle" must resolve to OracleEngine
    # Given: the registered engine name "oracle"
    # When: the engine class is resolved
    engine = get_db_engine("oracle")

    # Then: OracleEngine is returned
    assert engine is OracleEngine


def test_postgresql_engine_name_resolves_to_the_postgresql_implementation():
    # Spec: docs/configuration-contract.md — "postgresql" must resolve to PostgresEngine
    # Given: the registered engine name "postgresql"
    # When: the engine class is resolved
    engine = get_db_engine("postgresql")

    # Then: PostgresEngine is returned
    assert engine is PostgresEngine


@pytest.mark.parametrize("engine_name", ["postgres", "unknown"])
def test_unsupported_engine_name_raises_with_supported_engines_listed(engine_name: str):
    # Spec: docs/configuration-contract.md (DEC-014) — unsupported engines are rejected
    # before any connection attempt; the error message names the requested engine and
    # lists supported ones so the user can correct the config
    # Given: an unsupported engine name
    # When / Then: ValueError names both the bad engine and the supported list
    with pytest.raises(
        ValueError,
        match=rf"Unsupported database engine: {engine_name}; supported engines: oracle, postgresql",
    ):
        get_db_engine(engine_name)


def test_postgresql_engine_is_accepted_in_config_and_other_names_are_rejected():
    # Spec: docs/configuration-contract.md — oracle and postgresql are the supported engines
    # Given: a config requesting the postgresql engine
    # When / Then: it is accepted
    assert ldb_params_config.Config(schema="s", mode="PLAN", db_engine="postgresql").db_engine == "postgresql"

    # Given: a config requesting an unsupported engine name
    # When / Then: ValueError names the unsupported engine and lists supported ones
    with pytest.raises(ValueError, match="unsupported db_engine: postgres; supported engines: oracle, postgresql"):
        ldb_params_config.Config(schema="s", mode="PLAN", db_engine="postgres")  # type: ignore[arg-type]
