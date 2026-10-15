# Changelog

## [Unreleased]

## [0.7.0] - 2026-10-15

- **Breaking for custom engines:** `DatabaseEngine` no longer has `generate_table_structure_script`, `execute_ddl_statements`, `generate_table_privilege_script` or `generate_bootstrap_script`. Every `ensure_*` method now takes an optional keyword `ddl_sink`; when given, the engine hands it the DDL instead of executing it (a `None` connection then means nothing is read from the database). `ensure_table_structure` and `ensure_table_privileges` also take `ddl_failures`: with it, statements that fail are collected with their error and the rest still run. `ensure_table_structure` and `ensure_table_privileges` no longer return the generated statements.
- `ldb-run` and `ldb-impl` deliver their DDL through the `ensure_*` methods; their behavior and generated files are unchanged.
- The `ldb-impl` bootstrap script asks once for the runtime user's password and reuses it to reconnect for the database link, instead of asking twice.
- `ldb-impl` now logs which runtime user rejected the configured password (an existing user keeps its password) and exits 1, instead of failing with the generic connection error; the bootstrap script prints the same hint before reconnecting. New `InvalidCredentialsError`, a `DatabaseConnectionError` subclass that `get_connection` raises for ORA-01017.
- The `ldb-impl` bootstrap script now asks for every password at its start, before it changes anything. `DatabaseEngine` has a new abstract method, `script_password_prompts`, which a script calls first.
- Fix `ldb-run` without history admin credentials failing with ORA-00942 while applying history grants: existing table grants are now read from `all_tab_privs` on runtime connections, keeping `dba_tab_privs` on admin connections.

## [0.6.0] - 2026-10-14

- `ldb-run` without history admin credentials now applies pending history DDL with its runtime connection; statements that fail are written to `ddl_pending_dir` (`--ddl-pending-dir`) for a DBA and the run exits 1.
- `ldb-impl` no longer requires admin credentials: when they are absent it writes a rerunnable SQL*Plus bootstrap script per database under `ddl_pending_dir` for a DBA, creating the database link as the runtime user. The script prompts for passwords with hidden input and warns when a user already exists, because its password is left unchanged.
- `HISTORY_ILM` without admin credentials now reaches the runtime-connection path.
- Document that `PREVIEW` applies the same DDL and grants as `EXECUTE` and skips only the data movement, because a run needs the objects in place.
- Replace a primary key with a single `DROP PRIMARY KEY DROP INDEX` statement so the generated DDL no longer fails with ORA-01418.
- Read primary-key and index metadata from `all_*` dictionary views on non-admin connections, keeping `dba_*` on admin connections.
- `--mode SCRIPT` now writes the history table DDL and the table grants to files under `ddl_pending_dir` and applies nothing; the ILM script stays on stdout. Without history admin credentials `ldb-run` also applies the history grants over its runtime connection, and any statement or grant it cannot apply goes to the pending file. The new `history_baseline` option (`--history-baseline`, `LDB_HISTORY_BASELINE`, default `EMPTY`) chooses whether to assume an empty history or one with the same objects as source when history cannot be read.
- Add the `ddl_pending_dir` option (`--ddl-pending-dir`, `LDB_DDL_PENDING_DIR`, default `ldb_ddl_pending`) for the files that `ldb-run` and `ldb-impl` write for a DBA.
- **Breaking for custom engines:** `DatabaseEngine` has four new abstract methods, `generate_table_structure_script`, `execute_ddl_statements`, `generate_table_privilege_script` and `generate_bootstrap_script`.

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
- Encrypt database credentials at rest using AES-256-GCM; `ldb-run` and `ldb-impl` automatically encrypt any plaintext secrets in `secrets.json` at startup before the first ILM run; `ldb-crypt` encrypts them in advance without executing ILM; the per-user key is stored in `~/.limitsdb/ldb.key` (or `LDB_MASTER_KEY`).
- Centralized Config definition with defaults, YAML configurations, CLI, and environment variables.
- Improved logger to include process and thread names.
