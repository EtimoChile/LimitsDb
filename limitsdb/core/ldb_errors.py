"""Domain errors exposed by LimitsDb boundaries.

All errors retain their original exception through ``raise ... from exc`` at
the boundary where low-level parsing, cryptography, database, or worker errors
are translated.
"""


class LimitsDbError(Exception):
    """Base class for recoverable LimitsDb failures."""


class ConfigurationError(LimitsDbError, ValueError):
    """Configuration could not be read or interpreted."""


class SecretError(ConfigurationError):
    """Secret storage, key management, or decryption failed."""


class DatabaseConnectionError(LimitsDbError, ConnectionError):
    """A database connection could not be established or checked."""


class ValidationError(LimitsDbError, ValueError):
    """Configuration or runtime state failed domain validation."""


class ExecutionError(LimitsDbError, RuntimeError):
    """An ILM or administrative operation failed."""
