# terminusdb/core/config.py

DEFAULT_CONFIG = {
    "parallel_max": 10,
    "dsn": "leon.etimo.cl:1521/alpha",
    "user": "tdb",
    "password": "etm1tdb",
    "action": "MANT_PROD",
    "mode": "ALL",
    "chunk_size": 100000,
    "use_added_cols": False,
    "print_process": False,
    "log_level": "DEBUG"
}

class Config:
    """Wrapper to access configuration with attributes instead of dictionary keys."""
    def __init__(self, config_dict):
        for key, value in config_dict.items():
            setattr(self, key, value)

    def as_dict(self):
        """Optional: get a dict version back if needed."""
        return self.__dict__
