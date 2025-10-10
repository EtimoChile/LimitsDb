import logging
from typing import Optional

_logger: Optional[logging.Logger] = None

def configure_logger(name: str = "terminusdb", level: str = "WARNING") -> logging.Logger:
    """Configures a global logger with a given name and logging level.
    Args:
        name: The name of the logger.
        level: Logging level as a string (e.g., "INFO", "DEBUG").
    Returns:
        The configured logger instance."""
    global _logger
    if _logger is None:
        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, level.upper(), logging.WARNING))
        if not logger.handlers:
            console_handler = logging.StreamHandler()
            formatter = logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] %(processName)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
        _logger = logger
    return _logger


def get_logger(name: str = "terminusdb") -> logging.Logger:
    """Returns the global logger, creating it if it doesn't exist.
    Args:
        name: The name of the logger.
    Returns:
        The global logger instance."""
    global _logger
    if _logger is None:
        return configure_logger(name)
    return _logger


def reconfigure_logger(level: str = "INFO") -> None:
    """Reconfigures the log level of the already configured global logger.
    Args:
        level: The new logging level."""
    global _logger
    if _logger is not None:
        _logger.setLevel(getattr(logging, level.upper(), logging.INFO))