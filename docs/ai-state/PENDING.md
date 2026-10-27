---
status: active
authority: operational-state
scope: development/maintenance/release
last-reviewed: 2026-10-26
last-updated: 2026-10-26
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
| PEND-038 | OPEN | Architecture | SQL*Plus envelopes (`whenever`, `set echo`, `prompt`, `exit`) are written by the engine-independent core in `write_ddl_file` and the bootstrap renderer. | `limitsdb/core/ldb_ddl.py`; `limitsdb/db/ldb_engines.py`; `limitsdb/db/oracle/ldb_engine_impl.py` | Expose script rendering in `DatabaseEngine`, implement it in the Oracle adapter, and keep the core free of dialect text. |
| PEND-039 | OPEN | Tests | The runtime-user ownership test calls the private `_collect_privilege_plan` (R2), passing `runtime_user` by hand, so the wiring from the configuration is untested. Other tests of that helper (around `tests/test_run_ilm.py:967-1018`) may be coupled the same way. | `tests/test_run_ilm.py` | Rewrite them through `ldb_run` with a database double, asserting the grants and checks produced; review the remaining direct calls against `docs/prompts/test-authoring.md`. |
| PEND-040 | OPEN | Release | PyPI publication does not depend on the TestPyPI job. | `.github/workflows/release.yaml` | Restore the documented release job sequence (TestPyPI before PyPI). |
| PEND-041 | OPEN | Quality | `pyproject.toml` contains a non-English comment. | `pyproject.toml` | Translate it to English. |
