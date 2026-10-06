# Changelog

## [Unreleased]

- Add isolated Oracle integration tests backed by an ephemeral Oracle Database Free container in GitHub Actions; cover real rollback, recovery after partial DDL, and idempotent object and privilege administration, and characterize runner dependencies and progress-based retry behavior.
- Preserve the documented configuration precedence from system and user files through explicit overlays, environment variables, CLI values, and encrypted secrets; map names such as `LDB_CHUNK_SIZE` to their flat Python keys; add immutable execution, connection, administration, and runtime-context views without changing the public flat configuration; and type normalized ILM rules and derived table state.
- Add chained domain errors for configuration, secrets, validation, database connections, and execution; fail closed on unreadable secret files; and make worker failures recoverable by the coordinator.
- Replace YAPF, Flake8, and Pyright with a unified Ruff formatter/linter profile, retain strict mypy as the type-checking authority, and add reproducible pre-commit and CI quality gates.
- **Breaking:** rename the project and Python package from `TerminusDB` / `terminusdb` to `LimitsDb` / `limitsdb`, and replace the `tdb` / `TDB` prefix with `ldb` / `LDB` across CLI commands, environment variables, local paths, configuration keys, generated columns, and Oracle control objects.
- Dynamic table configuration from `LDB_CONF`
- Use dedicated `LDB_CTL` and `LDB_LOG` tables to track process control metadata and provide end-to-end traceability.
- Support for parallel execution on Oracle.
- Basic CLI module (`ldb-init`, `ldb-crypt`, `ldb-run`).
- Centralized Config definition with defaults, YAML configurations, CLI, and environment variables.
- Improved logger to include process and thread names.

## [0.4.0] - 2025-10-30

- First public open-source release of LimitsDb (Apache 2.0).
