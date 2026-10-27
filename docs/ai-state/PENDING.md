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
| PEND-035 | OPEN | Oracle/status | `is_run_alive` returns `True` on any ORA-00054 over the table's runner lock without checking that the lock belongs to the requested run, so STATUS shows an old execution as `RUNNING` while a later run holds the table. | `limitsdb/db/oracle/ldb_engine_impl.py` | Compare `rl_run_id` of the table's key row with the requested `run_id`; a different run is not alive. Test a stale run over a table held by a later run. |
| PEND-036 | OPEN | Oracle identifiers | `check_monitoring_access` emits `GRANT SELECT ON sys.<view> TO <grantee>` with the raw role name, so a case-sensitive role such as `LdbHistoryRole` resolves to another role and the monitoring grants script fails or grants the wrong role. | `limitsdb/db/oracle/ldb_engine_impl.py`; `limitsdb/core/ldb_runner.py` | Render the grantee with `_format_identifier`; test a mixed-case role through `ldb-run --mode STATUS`. |
| PEND-037 | OPEN | Oracle identifiers | Runtime-user ownership (`_collect_privilege_plan`) compares `engine.get_identifier_str(runtime_user)` with the table owners, so a user `hist` hides the grants and checks of another schema `HIST` while `hist` tables stay in the plan. | `limitsdb/core/ldb_runner.py` | Compare the configured username with the dictionary owner without normalizing it; cover mixed-case users in PREPARE and VALIDATE through `ldb_run`. |
| PEND-042 | OPEN | Architecture | `generate_script_output` in the core still prints a whole SQL*Plus script (`whenever`, `set echo`, `spool`, `DEFINE`, `COLUMN`, `SELECT ... FROM DUAL`), so the dialect text of the generated ILM script is not in the engine. DEC-045 moved only the envelope of the DDL and bootstrap files. | `limitsdb/core/ldb_runner.py`; `limitsdb/db/ldb_engines.py` | Decide whether the ILM script header and footer also become engine methods, and move them. |
| PEND-039 | OPEN | Tests | The runtime-user ownership test calls the private `_collect_privilege_plan` (R2), passing `runtime_user` by hand, so the wiring from the configuration is untested. Other tests of that helper (around `tests/test_run_ilm.py:967-1018`) may be coupled the same way. | `tests/test_run_ilm.py` | Rewrite them through `ldb_run` with a database double, asserting the grants and checks produced; review the remaining direct calls against `docs/prompts/test-authoring.md`. |
| PEND-040 | OPEN | Release | PyPI publication does not depend on the TestPyPI job. | `.github/workflows/release.yaml` | Restore the documented release job sequence (TestPyPI before PyPI). |
| PEND-041 | OPEN | Quality | `pyproject.toml` contains a non-English comment. | `pyproject.toml` | Translate it to English. |
