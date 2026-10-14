---
status: active
authority: operational-state
scope: development/maintenance/release
last-reviewed: 2026-10-14
last-updated: 2026-10-14
---

# Open pending items

Compact queue. Each pending item must identify an owner artifact and a next
action. When resolved, remove it from this table and record any durable
decision that resulted in `DECISIONS.md`.

Allowed states: `OPEN`, `IN_PROGRESS`, `DEFERRED`, and `FUTURE`.

| ID | Status | Scope | Pending | Owner artifact | Next action |
|---|---|---|---|---|---|
| `PEND-008` | `OPEN` | Distribution | Define the supported channels for a container and eventual standalone executable without assuming a binary prevents copying or replaces license obligations; the wheel already has automated clean installation. | `pyproject.toml`; `README.md`; `.github/workflows/quality.yaml`; future packaging and release configuration | Evaluate container, PyInstaller, or Nuitka with the Oracle driver and document the chosen matrix before offering binaries. |
| `PEND-020` | `FUTURE` | Diagnostics | Distinguish an ORA-01017 caused by a missing user from one caused by a password that does not match the existing user (for example right after `ldb-impl` created or verified the user), instead of the generic "Failed to connect to Oracle database". Left out of 0.6.0. | `limitsdb/db/oracle/ldb_engine_impl.py` (`get_connection`); `limitsdb/cli/ldb_impl.py`; `limitsdb/core/ldb_runner.py` | With admin credentials, query `dba_users` when a connection fails with ORA-01017 and report which case applies. |
| `PEND-021` | `FUTURE` | Architecture | Shrink `DatabaseEngine` back to one method per object: since 0.6.0 each `ensure_*` that applies DDL has a generate-plus-execute twin (`generate_table_structure_script`, `generate_table_privilege_script`, `execute_ddl_statements`, `generate_bootstrap_script`) only so the runner can choose a destination. Give the `ensure_*` methods an optional `ddl_sink` that receives the statements instead of executing them, and pass `deliver_ddl` (`DEC-026`) as that sink, so the engine still generates the DDL and keeps its own logic (such as skipping grants that already exist) and no method is added. `ldb-impl` would use the same path with a file sink, which makes `generate_bootstrap_script` unnecessary except for the dialect block that tolerates "already exists". Retiring the four 0.6.0 methods is breaking for engines that implemented them. | `limitsdb/db/ldb_engines.py`; `limitsdb/db/oracle/ldb_engine_impl.py`; `limitsdb/core/ldb_runner.py`; `limitsdb/core/ldb_ddl.py`; `limitsdb/cli/ldb_impl.py`; `tests/integration/oracle/test_oracle_ilm_lifecycle.py` | Decide the target version (0.7.0), add the optional sink to `ensure_table_structure`, `ensure_table_privileges` and the `ldb-impl` ensure methods, migrate `ldb-run` and `ldb-impl`, then remove the four methods and update tests, README and CHANGELOG. |
