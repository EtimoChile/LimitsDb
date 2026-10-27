---
status: active
authority: operational-state
scope: development/maintenance/release
last-reviewed: 2026-10-27
last-updated: 2026-10-27
---

# Open pending items

Compact queue. Each pending item must identify an owner artifact and a next
action. When resolved, remove it from this table and record any durable
decision that resulted in `DECISIONS.md`.

Allowed states: `OPEN`, `IN_PROGRESS`, `DEFERRED`, and `FUTURE`.

No concurrency/state pending item remains (PEND-022 to PEND-027 are implemented). The design is
described in `docs/concurrency-control.md`, which must be kept in sync with the implementation.

| ID | Status | Scope | Pending | Owner artifact | Next action |
|---|---|---|---|---|---|
| PEND-028 | FUTURE | ILM/history | By design history tables carry only their primary key (extra indexes slow the ILM and history is not queried like production), so source indexes are not propagated. A setting for special history indexes may be added: per-table index list, and how the runtime user creates them (`INDEX` privilege creates the index in the grantee's schema; otherwise admin credentials or the DBA file). The drop of indexes outside the primary key is controlled by `history_drop_unmanaged_indexes` (default `true`). | `limitsdb/core/ldb_runner.py`; `limitsdb/db/oracle/ldb_engine_impl.py` | Design the setting when a use case appears. |
| PEND-042 | OPEN | Architecture | `generate_script_output` in the core still prints a whole SQL*Plus script (`whenever`, `set echo`, `spool`, `DEFINE`, `COLUMN`, `SELECT ... FROM DUAL`), so the dialect text of the generated ILM script is not in the engine. DEC-045 moved only the envelope of the DDL and bootstrap files. | `limitsdb/core/ldb_runner.py`; `limitsdb/db/ldb_engines.py` | Decide whether the ILM script header and footer also become engine methods, and move them. |
| PEND-039 | OPEN | Tests | The privilege-plan tests now run through `ldb_run`. Other tests in `tests/test_run_ilm.py` still call private helpers (`_plan_mode`, `_load_offline_rows`, `_validate_environment`, `_validate_privileges`, `_build_dependency_graph`, `_compute_plan_layers`), against R2. | `tests/test_run_ilm.py` | Rewrite them through `ldb_run` (or the public entry point) with a database double, one helper at a time, per `docs/prompts/test-authoring.md`. |
