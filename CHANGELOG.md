# Changelog

## [Unreleased]

## [0.5.0] - 2026-10-10

- Archive source orphans selected by `left outer` joins and `orphan_check_column IS NULL`, snapshotting `LDB_IS_ORPHAN` and the process date whenever a related retention-date expression is null so historical retention remains evaluable.
- Reject PostgreSQL as a configured database engine until its adapter exists; Oracle is the sole accepted engine in configuration, CLI help, and engine loading.
- Make `Env(...)` metadata authoritative for environment overrides, normalize the ILM file variable to `LDB_ILM_CONFIG_FILE`, and keep history-column shape settings file-only by rejecting environment and `--set` overrides.
- Preserve related-table columns used only by historical filters and rewrite those predicates to their stored `<column>_<alias>` snapshots during `HISTORY_ILM`.
- Generate source-to-history inserts with one explicitly aliased value for each `LDB_PROCESS_DATE` and `LDB_INSERT_DATE` audit column.
- Avoid double-counting processed rows when chunk totals have already been recorded before the table reaches `TEND`.
- Route `HISTORY_ILM` reads and deletes through the configured history owner, and size control/log action fields for the full `HISTORY_ILM` value.
- Use stored relationship-derived columns during `HISTORY_ILM` instead of joining history cleanup queries back to source tables.
- Restore completed-run status from `LDB_CTL` when policies come from YAML, so same-day reruns remain idempotent and launch no workers.
- Add isolated Oracle adapter integration tests backed by an ephemeral Oracle Database Free container in GitHub Actions with generated, masked per-job credentials; cover recovery after partial DDL, idempotent object and privilege administration, and an end-to-end source/archive/history ILM happy path; characterize runner dependencies and progress-based retry behavior.
- Preserve the documented configuration precedence from system and user files through explicit overlays, environment variables, CLI values, and encrypted secrets; map names such as `LDB_CHUNK_SIZE` to their flat Python keys; add immutable execution, connection, administration, and runtime-context views without changing the public flat configuration; and type normalized ILM rules and derived table state.
- Add chained domain errors for configuration, secrets, validation, database connections, and execution; fail closed on unreadable secret files; and make worker failures recoverable by the coordinator.
- Replace YAPF, Flake8, and Pyright with a unified Ruff formatter/linter profile, retain strict mypy as the type-checking authority, and add reproducible pre-commit and CI quality gates.
- **Breaking:** rename the project and Python package from `TerminusDB` / `terminusdb` to `LimitsDb` / `limitsdb`, and replace the `tdb` / `TDB` prefix with `ldb` / `LDB` across CLI commands, environment variables, local paths, configuration keys, generated columns, and Oracle control objects.
- **Breaking:** retire `LDB_CNF` and `LDB_CNF_ID`; ILM policies are now loaded exclusively from configuration files (`LDB_ILM_CONFIG_FILE` or the default path).
- Use dedicated `LDB_CTL` and `LDB_LOG` tables to track process control metadata and provide end-to-end traceability.
- Process independent tables in parallel using a configurable worker pool (`--parallel-max` / `LDB_PARALLEL_MAX`, default 10); tables with dependencies are sequenced automatically across execution stages.
- Basic CLI module (`ldb-init`, `ldb-crypt`, `ldb-run`, `ldb-impl`).
- Centralized Config definition with defaults, YAML configurations, CLI, and environment variables.
- Improved logger to include process and thread names.
