import logging

from terminusdb.core import tdb_logger


def test_logger_configuration_idempotent():
    logger1 = tdb_logger.configure_logger(level="DEBUG", base_name="tdbtest")
    logger2 = tdb_logger.get_logger("tdbtest")
    assert logger1 is logger2
    assert logger1.level == logging.DEBUG
    tdb_logger.reconfigure_logger(level="ERROR")
    assert logger1.level == logging.ERROR
