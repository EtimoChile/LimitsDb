import logging
import os
import argparse
import yaml

def nvl(value, default):
    return value if value is not None else default

def load_yaml_config(config_file):
    if config_file and os.path.exists(config_file):
        with open(config_file, 'r') as f:
            return yaml.safe_load(f) or {}
    return {}

def parse_args():
    parser = argparse.ArgumentParser(description="TerminusDB CLI")
    parser.add_argument("--config-file", type=str, help="Path to external YAML config file")
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

def merge_configs(defaults, file_config, cli_args):
    cfg = defaults.copy()
    cfg.update(file_config)
    cli_args_dict = vars(cli_args)
    for key, value in cli_args_dict.items():
        if key != "config_file" and value is not None:
            cfg[key] = value  # Always overwrite if specified in CLI
    return cfg

_logger = None

def configure_logger(name="terminusdb", level="WARNING"):
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

def reconfigure_logger(level="INFO"):
    global _logger
    if _logger is not None:
        _logger.setLevel(getattr(logging, level.upper(), logging.INFO))

def get_logger(name="terminusdb"):
    global _logger
    if _logger is None:
        return configure_logger(name)
    return _logger