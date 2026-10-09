import logging

import pytest

from limitsdb.core import ldb_logger


def test_log_level_is_applied_on_initial_configuration():
    # Spec: README > Run ILM > --log-level DEBUG|INFO|WARNING|ERROR|CRITICAL
    # Given: no prior configuration at this level
    # When: logger is configured at DEBUG
    logger = ldb_logger.configure_logger(level="DEBUG", base_name="ldbtest")

    # Then: its effective level is DEBUG
    assert logger.level == logging.DEBUG


def test_logger_retrieved_by_name_is_the_same_instance():
    # Spec: README > Run ILM > --log-level — logger identity is stable within a run
    # Given: logger is configured
    ldb_logger.configure_logger(level="INFO", base_name="ldbtest")

    # Then: repeated calls for the same name return the same object
    assert ldb_logger.get_logger("ldbtest") is ldb_logger.get_logger("ldbtest")


def test_log_level_change_applies_immediately_to_existing_logger():
    # Spec: README > Run ILM > --log-level — level can be adjusted at runtime
    # Given: logger configured at DEBUG
    logger = ldb_logger.configure_logger(level="DEBUG", base_name="ldbtest")

    # When: level is changed to ERROR
    ldb_logger.reconfigure_logger(level="ERROR")

    # Then: the existing logger now filters at ERROR
    assert logger.level == logging.ERROR


def test_reconfigure_before_initial_configuration_is_ignored(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Run ILM > --log-level — reconfigure only affects an active session
    # NOTE: accesses _configured to simulate an unconfigured state — no public API resets
    # module state between tests, so this is the only viable approach.
    import limitsdb.core.ldb_logger as mod

    # Given: logger was never configured in this session
    monkeypatch.setattr(mod, "_configured", False)

    # When: reconfigure is called without prior setup
    mod.reconfigure_logger(level="WARNING")

    # Then: the module remains unconfigured — the call is silently ignored
    assert not mod._configured
