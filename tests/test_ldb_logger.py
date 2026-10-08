import logging

import pytest

from limitsdb.core import ldb_logger


def test_reconfigure_logger_no_op_when_not_configured(monkeypatch: pytest.MonkeyPatch):
    import limitsdb.core.ldb_logger as mod

    monkeypatch.setattr(mod, "_configured", False)
    mod.reconfigure_logger(level="WARNING")
    assert not mod._configured


def test_logger_configuration_idempotent():
    logger1 = ldb_logger.configure_logger(level="DEBUG", base_name="ldbtest")
    logger2 = ldb_logger.get_logger("ldbtest")
    assert logger1 is logger2
    assert logger1.level == logging.DEBUG
    ldb_logger.reconfigure_logger(level="ERROR")
    assert logger1.level == logging.ERROR
