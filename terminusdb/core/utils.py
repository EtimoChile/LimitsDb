import logging
import os
import argparse
import yaml
from typing import Any, Dict, Mapping, Optional, TypedDict, Literal

def nvl(value: Optional[Any], default: Any) -> Any:
    """Returns the value if it is not None, otherwise returns the default value.
    Args:
        value: The value to evaluate.
        default: The value to return if `value` is None.
    Returns:
        The original value if not None, else the default."""
    return value if value is not None else default

class DefaultConfigType(TypedDict):
    parallel_max: int
    db_engine: Literal["oracle", "postgres"]
    dsn: str
    user: str
    password: str
    action: Literal["MANT_PROD", "MANT_HIST"]
    mode: Literal["ALL", "QUERY_ONLY"]
    chunk_size: int
    use_added_cols: bool
    print_process: bool
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

class Config:
    tdb_config_file: str
    action: Literal["MANT_PROD", "MANT_HIST"]
    mode: Literal["ALL", "QUERY_ONLY"]
    chunk_size: int
    use_added_cols: bool
    print_process: bool
    parallel_max: int
    db_engine: Literal["oracle", "postgres"]
    dsn: str
    user: str
    password: str
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    """Wrapper to access configuration with attributes instead of dictionary keys."""

    def __init__(self, config_dict: Dict[str, Any]):
        for key, value in config_dict.items():
            setattr(self, key, value)

    def as_dict(self):
        """Optional: get a dict version back if needed."""
        return self.__dict__

def load_yaml_config(config_file: Optional[str]) -> Dict[str, Any]:
    """Loads a YAML configuration file and returns its contents as a dictionary.
    Args:
        config_file: Path to the YAML file.
    Returns:
        A dictionary with the loaded configuration, or an empty dictionary if the file doesn't exist."""
    if config_file and os.path.exists(config_file):
        with open(config_file, 'r') as f:
            return yaml.safe_load(f) or {}
    return {}

def parse_args() -> argparse.Namespace:
    """Parses command-line arguments for TerminusDB CLI.
    Returns:
        A namespace containing all parsed arguments."""
    parser = argparse.ArgumentParser(description="TerminusDB CLI")
    parser.add_argument("--config-file", type=str, help="Path to external YAML config file")
    parser.add_argument("--tdb-config-file", type=str, help="YAML file with table configuration (bypasses DB loading)")
    parser.add_argument("--parallel-max", type=int)
    parser.add_argument("--dsn", type=str)
    parser.add_argument("--user", type=str)
    parser.add_argument("--password", type=str)
    parser.add_argument("--action", type=str, choices=["MANT_PROD", "MANT_HIST"])
    parser.add_argument("--mode", type=str, choices=["ALL", "QUERY_ONLY"])
    parser.add_argument("--chunk-size", type=int)
    parser.add_argument("--use-added-cols", action=argparse.BooleanOptionalAction)
    parser.add_argument("--print-process", action=argparse.BooleanOptionalAction)
    parser.add_argument("--log-level", type=str, choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    return parser.parse_args()

def merge_configs(defaults: Mapping[str, Any], file_config: Mapping[str, Any], cli_args: argparse.Namespace) -> Dict[str, Any]:
    """Merges default values, configuration from file, and command-line arguments into a single configuration.
    Args:
        defaults: Default configuration values.
        file_config: Configuration values loaded from file.
        cli_args: Arguments parsed from the command line.
    Returns:
        A merged configuration dictionary with CLI arguments taking precedence."""
    cfg = dict(defaults)
    cfg.update(file_config)
    cli_args_dict = vars(cli_args)
    for key, value in cli_args_dict.items():
        if key != "config_file" and value is not None:
            cfg[key] = value
    return cfg


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

def reconfigure_logger(level: str = "INFO") -> None:
    """Reconfigures the log level of the already configured global logger.
    Args:
        level: The new logging level."""
    global _logger
    if _logger is not None:
        _logger.setLevel(getattr(logging, level.upper(), logging.INFO))

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
