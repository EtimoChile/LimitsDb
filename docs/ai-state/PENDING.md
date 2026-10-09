---
status: active
authority: operational-state
scope: development/maintenance/release
last-reviewed: 2026-10-09
---

# Open pending items

Compact queue. Each pending item must identify an owner artifact and a next
action. When resolved, remove it from this table and record any durable
decision that resulted in `DECISIONS.md`.

Allowed states: `OPEN`, `IN_PROGRESS`, `DEFERRED`, and `FUTURE`.

| ID | Status | Scope | Pending | Owner artifact | Next action |
|---|---|---|---|---|---|
| `PEND-006` | `OPEN` | Publication/CI | Complete the publication gate on the current quality CI: publish only from a tagged, authorized version with provenance and a protected environment. | `.github/workflows/quality.yaml`; future release workflow; `pyproject.toml`; `CHANGELOG.md` | Define the channel and protected credentials, validate first on TestPyPI or a private registry, and design the release authorization; do not publish or create tags without an explicit request. Requires `PEND-014`. |
| `PEND-008` | `OPEN` | Distribution | Define the supported channels for a container and eventual standalone executable without assuming a binary prevents copying or replaces license obligations; the wheel already has automated clean installation. | `pyproject.toml`; `README.md`; `.github/workflows/quality.yaml`; future packaging and release configuration | Evaluate container, PyInstaller, or Nuitka with the Oracle driver and document the chosen matrix before offering binaries. |
| `PEND-015` | `OPEN` | Operations/security | `ldb-run --mode EXECUTE` opens an admin connection unconditionally to manage history table structure, even when all tables already exist. Clients who pre-create all objects manually and do not want to store admin credentials cannot omit them today. Make the admin connection conditional on whether history DDL is actually needed; when pre-existing objects are detected as up-to-date, skip the connection entirely. | `limitsdb/core/ldb_runner.py` (`ldb_run`, `_ensure_history_tables`) | Inspect history table state before opening the admin connection; open it only when create or alter operations are required. Document the resulting behavior in README § Bootstrap Database Objects and § Run ILM. |
| `PEND-016` | `OPEN` | Operations/security | When admin credentials are absent and history table DDL differences are detected, `ldb-run` should not fail silently or proceed with missing alterations. Instead it should skip all history environment changes and emit a proposed DDL script (equivalent to `--mode SCRIPT` scoped to the structural delta) so the DBA can review and apply it manually. This enables operation with application-only credentials in environments where schema ownership is managed externally. | `limitsdb/core/ldb_runner.py`; `limitsdb/db/ldb_engines.py` (`ensure_table_structure`) | Define the delta-script output contract; implement credential-absent detection in `_ensure_history_tables`; emit the script to stdout or a configurable path; document in README. Depends on `PEND-015`. |
| `PEND-017` | `OPEN` | Operations/security | `ldb-impl` should support a DDL-script generation mode for environments where admin credentials are not available or not stored. Instead of creating objects directly, it would emit the full DDL for roles, users, control tables, sequences, and database links so a DBA can review and apply it manually. Complements `PEND-016`: together they establish a consistent pattern across both commands — with admin credentials LimitsDb manages objects directly; without them it produces the script. Consider a shared flag (e.g. `--script`) or a dedicated mode consistent with `ldb-run --mode SCRIPT`. | `limitsdb/cli/ldb_impl.py`; `limitsdb/db/ldb_engines.py` | Define the script output contract for `ldb-impl`; implement script generation for each object type; align the flag or mode name with `ldb-run` and `PEND-016`; document in README § Bootstrap Database Objects. |
