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
