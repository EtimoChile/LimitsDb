import argparse
from dataclasses import dataclass
import os
from typing import Any, Dict, Literal, Mapping, Optional, TypedDict

import yaml


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


@dataclass
class Config:
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
    tdb_config_file: Optional[str] = None
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