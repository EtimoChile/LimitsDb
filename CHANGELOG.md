# Changelog

## [Unreleased]

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
