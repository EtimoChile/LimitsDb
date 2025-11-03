"""Backward compatible aggregating module for CLI entry points."""
from __future__ import annotations

from .tdb_run import run_cli
from .tdb_init import main_init
from .tdb_crypt import main_crypt

__all__ = ["run_cli", "main_init", "main_crypt"]
