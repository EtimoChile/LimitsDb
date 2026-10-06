# ldb_logger.py
import logging
import threading

_base_name = "ldb"
_configured = False
_lock = threading.RLock()

# Un único formato legible (sin JSON, sin ambientes)
_FORMAT = logging.Formatter(fmt="%(asctime)s [%(levelname)s] %(processName)s-%(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S", )

def configure_logger(level: str = "INFO", base_name: str = _base_name) -> logging.Logger:
    """Configura el logger base una sola vez (handlers/format)."""
    global _configured, _base_name
    with _lock:
        _base_name = base_name
        base = logging.getLogger(_base_name)
        lvl = getattr(logging, level.upper(), logging.INFO)

        if not _configured:
            base.setLevel(lvl)
            base.propagate = False  # evita duplicar en root
            ch = logging.StreamHandler()
            ch.setLevel(lvl)
            ch.setFormatter(_FORMAT)
            base.addHandler(ch)
            _configured = True
        else:
            base.setLevel(lvl)
            for h in base.handlers:
                h.setLevel(lvl)
                h.setFormatter(_FORMAT)
        return base

def get_logger(name: str = _base_name) -> logging.Logger:
    """Devuelve el logger base o un hijo ('limitsdb.<name>')."""
    if not _configured:
        configure_logger()
    if name == _base_name or name == "":
        return logging.getLogger(_base_name)
    return logging.getLogger(f"{_base_name}.{name}")

def reconfigure_logger(level: str = "INFO") -> None:
    """Ajusta el nivel del logger base y sus handlers."""
    if not _configured:
        return
    lvl = getattr(logging, level.upper(), logging.INFO)
    base = logging.getLogger(_base_name)
    base.setLevel(lvl)
    for h in base.handlers:
        h.setLevel(lvl)
