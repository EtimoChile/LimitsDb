# limitsdb/core/ldb_meta.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Help:
    text: str


@dataclass(frozen=True)
class Cli:
    flag: str  # e.g. "--parallel-max"


@dataclass(frozen=True)
class Secret:
    enabled: bool = True  # default marks the field as secret


@dataclass(frozen=True)
class Env:
    name: str  # e.g. "LDB_PARALLEL_MAX"


@dataclass(frozen=True)
class CliOnly:
    """Marker for fields that are used only through CLI (never stored in config.yml)."""

    enabled: bool = True
