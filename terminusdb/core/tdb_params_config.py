# terminusdb/core/tdb_params_config.py
import argparse
from dataclasses import dataclass
from typing import Any, Dict, Literal, Mapping, Optional, TypedDict, Sequence

from terminusdb.core.tdb_config_loader import load_runtime_config

class DefaultConfigType(TypedDict, total=False):
    parallel_max: int
    db_engine: Literal["oracle", "postgres"]
    # Estos 4 vendrán de secrets.json normalmente (pueden no estar en defaults):
    prod_credentials: str
    hist_credentials: str
    prod_admin_credentials: str
    hist_admin_credentials: str
    action: Literal["MANT_PROD", "MANT_HIST"]
    mode: Literal["ALL", "QUERY_ONLY"]
    chunk_size: int
    use_added_cols: bool
    add_tdb_columns: bool
    print_process: bool
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

@dataclass
class Config:
    action: Literal["MANT_PROD","MANT_HIST"] = "MANT_PROD"
    mode: Literal["ALL","QUERY_ONLY"] = "ALL"
    chunk_size: int = 100000
    use_added_cols: bool = True
    add_tdb_columns: bool = True
    print_process: bool = False
    parallel_max: int = 10
    db_engine: Literal["oracle","postgres"] = "oracle"
    prod_credentials: Optional[str] = None
    hist_credentials: Optional[str] = None
    prod_admin_credentials: Optional[str] = None
    hist_admin_credentials: Optional[str] = None
    log_level: Literal["DEBUG","INFO","WARNING","ERROR","CRITICAL"] = "INFO"
    schema: str = ""         # requerido (validar no vacío en __post_init__)
    profile: Optional[str] = None
    tdb_config_file: Optional[str] = None

    def __post_init__(self):
        if not self.schema:
            raise ValueError("schema es requerido")
        if self.db_engine not in ("oracle", "postgres"):
            raise ValueError(f"db_engine inválido: {self.db_engine}")

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Config":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"Claves desconocidas: {sorted(unknown)}")
        return cls(**d)
    
    def as_dict(self) -> Dict[str, Any]:
        return self.__dict__

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TerminusDB CLI")

    # Nuevo modelo
    parser.add_argument("--schema", required=True, help="Nombre del esquema/target (carpeta en schemas/)")
    parser.add_argument("--profile", help="Perfil de configuración (p.ej. dev, prod)")
    parser.add_argument("--config-dir", help="Raíz de configuración (sobrescribe autodetección ~/.config/TerminusDB y /etc/terminusdb)")
    parser.add_argument("--config-file", help="Archivo YAML adicional a superponer (overlay de alta prioridad)")

    # Overrides clásicos (también pueden venir por --set)
    parser.add_argument("--tdb-config-file", type=str, help="YAML con tablas (bypass DB)")
    parser.add_argument("--parallel-max", type=int, help="Maximum number of parallel processes")
    parser.add_argument("--action", type=str, choices=["MANT_PROD", "MANT_HIST"], help="Action to perform")
    parser.add_argument("--mode", type=str, choices=["ALL", "QUERY_ONLY"], help="Mode of operation")
    parser.add_argument("--chunk-size", type=int, help="Rows per chunk")
    parser.add_argument("--use-added-cols", action=argparse.BooleanOptionalAction, help="Add derived columns in history tables")
    parser.add_argument("--add-tdb-columns", action=argparse.BooleanOptionalAction, help="Add TerminusDB execution date columns in history tables")
    parser.add_argument("--print-process", action=argparse.BooleanOptionalAction, help="Dry-run to generate SQL*Plus script")
    parser.add_argument("--log-level", type=str, choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Logging level")

    # Overrides genéricos k=v (admite clave.anidada)
    parser.add_argument("--set", action="append", default=[], help="Overrides tipo key=value; admite claves anidadas con punto")

    return parser.parse_args()

def _parse_cli_sets(pairs: Sequence[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for p in pairs or []:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        v = v.strip()
        # auto-typing simple
        if v.isdigit():
            v = int(v)
        elif v.lower() in ("true","false"):
            v = v.lower() == "true"
        out[k.strip()] = v
    return out

def build_config(defaults: Mapping[str, Any], cli_args: argparse.Namespace) -> Dict[str, Any]:
    """
    Produce el diccionario de configuración final usando el nuevo loader (overlays + secrets).
    CLI y ENV se aplican dentro del loader (ENV TDB_*) y aquí mapeamos flags directas a --set.
    """
    # Transforma flags individuales en sets (para que ganen prioridad)
    cli_sets: Dict[str, Any] = _parse_cli_sets(getattr(cli_args, "set", []))
    mapping: Dict[str, Any] = {
        "parallel_max": cli_args.parallel_max,
        "action": cli_args.action,
        "mode": cli_args.mode,
        "chunk_size": cli_args.chunk_size,
        "use_added_cols": cli_args.use_added_cols,
        "add_tdb_columns": getattr(cli_args, "add_tdb_columns", None),
        "print_process": cli_args.print_process,
        "log_level": cli_args.log_level,
        "tdb_config_file": cli_args.tdb_config_file,
    }
    mapping = {k: v for k, v in mapping.items() if v is not None}

    for k, v in mapping.items():
        if v is not None:
            cli_sets[k] = v

    cfg = load_runtime_config(
        schema=cli_args.schema,
        profile=cli_args.profile,
        cli_sets=cli_sets,
        explicit_config_file=cli_args.config_file,
        explicit_config_dir=cli_args.config_dir,
    )
    # Mezcla con defaults embebidos (solo para llaves que falten)
    final = dict(defaults)
    final.update(cfg)
    # fija schema/profile para que queden en el objeto Config
    final["schema"] = cli_args.schema
    final["profile"] = cli_args.profile
    final["tdb_config_file"] = cli_args.tdb_config_file
    return final
