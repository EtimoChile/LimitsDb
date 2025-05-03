from terminusdb.core.config import Config, load_yaml_config, merge_configs, parse_args
from terminusdb.core.default_config import DEFAULT_CONFIG
from terminusdb.core.logger import configure_logger, get_logger
from terminusdb.core.tdb_runner import tdb_run
from terminusdb.core.logger import reconfigure_logger

configure_logger(level="WARNING")

def run_cli() -> None:
    logger = get_logger()
    args = parse_args()
    file_config = load_yaml_config(args.config_file)
    raw_config = merge_configs(DEFAULT_CONFIG, file_config, args)
    config = Config(raw_config)
    reconfigure_logger(level=config.log_level)
    logger.debug("Config:\n%s", config)
    tdb_run(config)

if __name__ == "__main__":
    run_cli()