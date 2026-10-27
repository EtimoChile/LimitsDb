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
| PEND-038 | OPEN | Architecture | SQL*Plus envelopes (`whenever`, `set echo`, `prompt`, `exit`) are written by the engine-independent core in `write_ddl_file` and the bootstrap renderer. | `limitsdb/core/ldb_ddl.py`; `limitsdb/db/ldb_engines.py`; `limitsdb/db/oracle/ldb_engine_impl.py` | Expose script rendering in `DatabaseEngine`, implement it in the Oracle adapter, and keep the core free of dialect text. |
| PEND-039 | OPEN | Tests | The privilege-plan tests now run through `ldb_run`. Other tests in `tests/test_run_ilm.py` still call private helpers (`_plan_mode`, `_load_offline_rows`, `_validate_environment`, `_validate_privileges`, `_build_dependency_graph`, `_compute_plan_layers`), against R2. | `tests/test_run_ilm.py` | Rewrite them through `ldb_run` (or the public entry point) with a database double, one helper at a time, per `docs/prompts/test-authoring.md`. |
| PEND-040 | OPEN | Release | PyPI publication does not depend on the TestPyPI job. | `.github/workflows/release.yaml` | Restore the documented release job sequence (TestPyPI before PyPI). |
| PEND-041 | OPEN | Quality | `pyproject.toml` contains a non-English comment. | `pyproject.toml` | Translate it to English. |
