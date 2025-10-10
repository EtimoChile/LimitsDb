# terminusdb/core/config.py
from terminusdb.core.tdb_params_config import DefaultConfigType

DEFAULT_CONFIG: DefaultConfigType = {
    "parallel_max": 10,
    "db_engine": "oracle",
    "prod_credentials": "tdb/etm1tdb@leon.etimo.cl:1521/alpha",
    "hist_credentials": "tdbhst/etm1tdb@leon.etimo.cl:1521/alpha",
    "prod_admin_credentials": "system/etm1alpha@leon.etimo.cl:1521/alpha",
    "hist_admin_credentials": "system/etm1alpha@leon.etimo.cl:1521/alpha",
    "action": "MANT_PROD",
    "mode": "ALL",
    "chunk_size": 100000,
    "use_added_cols": True,
    "add_tdb_columns": True,
    "print_process": False,
    "log_level": "DEBUG"
}
