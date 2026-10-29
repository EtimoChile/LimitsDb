---
status: active
authority: operational-state
scope: development/maintenance/release
last-reviewed: 2026-10-28
last-updated: 2026-10-28
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
| `PEND-043` | `OPEN` | Tests/PostgreSQL | The PostgreSQL E2E suite (`tests/integration/postgresql/`, marker `postgres_integration`, job `postgres-integration` in `.github/workflows/quality.yaml` on `postgres:9.5`) is written but has not run yet: `dblink` copy, notices, bootstrap and locks are proven by a fake connection only. | `tests/integration/postgresql/`; `.github/workflows/quality.yaml` | Push the branch, run the workflow with `workflow_dispatch`, fix what it finds, then close this item. Only the SOURCE/HISTORY lifecycle is covered; scripts, failure recovery and orphan purge are not. |
