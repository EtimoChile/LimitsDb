---
status: active
authority: operational-state
scope: development/maintenance/release
last-reviewed: 2026-10-13
last-updated: 2026-10-13
---

# Open pending items

Compact queue. Each pending item must identify an owner artifact and a next
action. When resolved, remove it from this table and record any durable
decision that resulted in `DECISIONS.md`.

Allowed states: `OPEN`, `IN_PROGRESS`, `DEFERRED`, and `FUTURE`.

| ID | Status | Scope | Pending | Owner artifact | Next action |
|---|---|---|---|---|---|
| `PEND-008` | `OPEN` | Distribution | Define the supported channels for a container and eventual standalone executable without assuming a binary prevents copying or replaces license obligations; the wheel already has automated clean installation. | `pyproject.toml`; `README.md`; `.github/workflows/quality.yaml`; future packaging and release configuration | Evaluate container, PyInstaller, or Nuitka with the Oracle driver and document the chosen matrix before offering binaries. |
| `PEND-017` | `OPEN` | Operations/security | `ldb-impl` should support a DDL-script generation mode for environments where admin credentials are not available or not stored. Instead of creating objects directly, it would emit the full DDL for roles, users, control tables, sequences, and database links so a DBA can review and apply it manually. History tables of business tables are not in scope: `ldb-impl` never managed them and `ldb-run` handles that gap through `ddl_pending_dir` (`DEC-021`). Name the flag so it is not confused with `ldb-run --mode SCRIPT`. | `limitsdb/cli/ldb_impl.py`; `limitsdb/db/ldb_engines.py` | Define the script output contract for `ldb-impl`; implement script generation for each object type; pick a flag name distinct from `--mode SCRIPT`; document in README § Bootstrap Database Objects. |
| `PEND-019` | `FUTURE` | Process/documentation | The `changelog:` gate introduced in AGENTS.md (requires each DEC with a user-visible change to carry a `changelog:` field) was created as part of 0.5.0 itself and therefore could not be applied retroactively to 0.5.0 DECISIONS.md entries. Before tagging 0.6.0, backfill `changelog:` fields in the relevant DEC entries in `docs/ai-state/DECISIONS.md` so the gate is self-consistent from that release onward. | `docs/ai-state/DECISIONS.md`; `AGENTS.md` | Add `changelog:` fields to all DEC entries that contributed lines to CHANGELOG 0.5.0 before preparing the 0.6.0 release. |
