# terminusdb/core/config.py
from terminusdb.core.config import DefaultConfigType

DEFAULT_CONFIG: DefaultConfigType = {
    "parallel_max": 10,
    "db_engine": "oracle",
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
