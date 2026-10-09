---
status: active
authority: operational-state
scope: development/maintenance/release
last-reviewed: 2026-10-09
last-updated: 2026-10-09
---

# Current state

Compact project memory. Authoritative for coordination and operational state,
but does not replace the sources defined in `AGENTS.md`.

## Current situation

- The integration branch is `development`; `main` represents stable versions
  per `README.md`.
- The version declared in `pyproject.toml` is `0.5.0` and requires Python
  `>=3.12,<4.0`.
- The current name is `LimitsDb`, the Python package is `limitsdb`, and the
  public technical prefix is `ldb` / `LDB`; the change from the previous name
  is incompatible and documented in `README.md` and `CHANGELOG.md`.
- The package implements configuration-driven ILM. Oracle is the available
  adapter and the only engine accepted by configuration; PostgreSQL remains a
  future implementation and is rejected before connecting. The `DatabaseEngine`
  contract keeps the extension to other engines. `PEND-007` is resolved.
- The published interfaces are `ldb-run`, `ldb-init`, `ldb-crypt`, and
  `ldb-impl`.
- Configuration is resolved by file, `LDB_*` variables, and `--set` options;
  secrets are loaded separately and required to be encrypted in the normal path.
- ILM operations can archive, purge, create objects, and manage privileges.
  They are not executed against a real database without explicit authorization
  and an identified environment.
- The test suite lives in `tests/`. Local tests are 146 (non-oracle-integration)
  and 11 E2E (oracle_integration). Ruff validates format and lint, and strict
  `mypy` completes without findings across the 23 package modules. The test
  layout follows `docs/prompts/test-organization.md`: core contracts in `tests/test_*.py`,
  Oracle adapter tests (no real DB) in `tests/adapters/oracle/`, and E2E with an
  ephemeral Oracle DB in `tests/integration/oracle/`. `PEND-018` is resolved:
  `test_ldb_meta_status.py` (R5 violations; behaviors covered elsewhere) and
  `test_oracle_engine_utils.py` (module-named, top-level location) were retired;
  their behaviors were redistributed into three new adapter files
  (`test_oracle_sql_generation.py`, `test_oracle_schema_objects.py`,
  `test_oracle_connection_ops.py`), each named after a functional contract and
  containing R1–R7 compliant tests (spec citations, Given/When/Then structure,
  public interfaces with documented R2 exceptions). Two tests that called private
  helpers with no documented contract and whose behaviors were covered elsewhere
  (`test_identifier_formatting_and_quoting`, `test_register_and_fetch_connection_env_round_trip`)
  were removed. The duplicate `tests/integration/test_oracle_ephemeral.py` was
  removed; the canonical E2E file is
  `tests/integration/oracle/test_oracle_ilm_lifecycle.py` (renamed from
  `test_oracle_ephemeral.py`, which described the tool not the functionality). The unit
  baseline verified on 2026-10-07 is 97 passing tests (expanded from 81 with
  local tests for `ldb-init`, PLAN and VALIDATE modes, script generation, and
  Oracle PK and error-logging methods). `PEND-005`, `PEND-012`, `PEND-009` are
  resolved. Run `37853724666` on `ead02f1` (51 new tests) verified 160 local
  tests and 11 E2E passing (combined coverage 91%; TOTAL 2564 statements, 159
  not covered, 816 branches, 107 partial).
- The public configuration retains flat keys and system, user, explicit file,
  environment, CLI, and secrets precedence. The core consumes immutable typed
  views for execution, connections, administration, and context; normalized ILM
  rules and derived table state also have typed contracts. `PEND-004` is
  resolved.
- Environment variables are enabled and mapped exclusively through the
  `Env(...)` metadata of `Config`; all their public names use the `LDB_` prefix,
  including `LDB_ILM_CONFIG_FILE`. `use_added_columns` and `add_ldb_columns`
  are fixed in the persistent YAML configuration of the environment and do not
  accept flags, environment variables, or `--set`, because varying these
  settings can destabilize historical processing. `PEND-011` is resolved.
- Configuration, secrets, validation, connection, and execution boundaries
  expose chained domain errors. Invalid secrets files stop the operation
  without being overwritten, and worker failures produce a recoverable result
  identified by table. The coordinator retries a partial failure in the same run
  only if the processed counter increased; without progress it converts it to
  `SKIPPED`.
- `source_orphan_purge` archives source rows whose `left outer join` finds no
  relationship, detected by any `orphan_check_column IS NULL`. It requires
  derived snapshots and positive historical retention; it persists state in
  `LDB_IS_ORPHAN` and, if a relationship date is null, stores the process date
  so that `HISTORY_ILM` can apply the evaluable retention. The current local
  suite contains 81 tests and a separate Oracle E2E case is pending execution
  on the ephemeral runner. `PEND-010` is resolved.
- The workflow `.github/workflows/quality.yaml` runs on Python 3.12 the format,
  lint, typing, tests, pre-commit, build, distribution verification, and clean
  wheel installation gates. Authorized publishing from tagged versions remains
  pending (`PEND-006`). GITHUB_TOKEN permissions are declared at job level
  (`contents: read`); the workflow level fixes `permissions: {}` as default
  deny. `.github/CODEOWNERS` requires review from `@etimochile` on all changes.
  Branch protection rules in the GitHub UI remain to be configured (`PEND-014`).
- The workflow `.github/workflows/oracle-integration.yaml` starts an ephemeral
  Oracle Free instance in a Linux runner for relevant pull requests and manual
  runs; it does not run on every `push`. It uses a digest-pinned image and
  per-job random credentials, masked before exposing them to later steps. Its
  first real GitHub execution completed successfully: image download in 49
  seconds, Oracle availability about 28 seconds after startup, 2.393 GiB of
  memory and 5.4 GiB of disk free when running tests. The expanded suite took
  4.16 seconds and maintained 5.4 GiB free. The workflow also measures combined
  line and branch coverage for the local and E2E suites, including CLI
  subprocesses and workers, and retains text, XML, and HTML reports for 14 days.
  Run `37689648223` on `faa3209` verified 80 local tests and 6 E2E passing, with
  81.42% lines, 67.46% branches, and 78% combined; `PEND-013` is resolved and
  no minimum threshold is enforced yet.
- `docs/prompts/test-authoring.md` defines the mandatory test authoring rules:
  tests must be derived from the specification (README, ilm.example.yml, docs/),
  not from the implementation. `AGENTS.md` references it as required reading
  before generating or modifying tests.
- All project files — source code, tests, documentation, and configuration
  comments — are written in English. `AGENTS.md` § Language is the governing
  rule.
- Inversiones Etimo SpA maintains the project. Applicable terms are distributed
  in the license, notices, and commercial edition files at the root.

## Active decisions required to continue

- `DEC-001`: maintain compact persistent memory in `docs/ai-state/`.
- `DEC-002`: preserve the boundary between engine-independent core and engine
  adapters.
- `DEC-003`: treat every ILM or administrative execution against real databases
  as an explicitly authorized action.
- `DEC-004`: `AGENTS.md` is the only constitution; tool-specific files are
  minimal bridges.
- `DEC-005`: use `LimitsDb`, `limitsdb`, and `ldb` / `LDB` as the project's
  identity and public prefixes.
- `DEC-006`: maintain Python 3.12 as the current cycle implementation and do
  not start a Java rewrite without new evidence and a successor decision.
- `DEC-007`: unify format and lint with Ruff at 120 columns, maintain strict
  `mypy` as the typing authority, and reproduce the controls in pre-commit and
  CI.
- `DEC-008`: translate failures at subsystem boundaries to chained domain errors
  and allow broad catches only at documented boundaries.
- `DEC-009`: keep the public configuration flat and internally migrate in phases
  toward typed, immutable contracts.
- `DEC-010`: run Oracle integration on an ephemeral database inside a GitHub
  Actions Linux runner, without external access to the persistent cloud database.
- `DEC-011`: maintain ILM policies exclusively in configuration files and retire
  the use of `LDB_CNF` without automatically deleting legacy objects. `PEND-009`
  is resolved: `_get_conf_rows` and `DatabaseEngine.load_config` removed;
  `LDB_CNF`/`LDB_CNF_ID` retired from bootstrap DDL; `_load_offline_rows`
  raises `ConfigurationError` when no ILM file exists; 108 local tests pass.
- `DEC-012`: each historical row preserves the values needed to reevaluate all
  its predicates, including cut-off dates and additional columns from related
  tables; `LDB_DATE_<suffix>` uses a differentiator, not a SQL alias.
- `DEC-013`: fix `use_added_columns` and `add_ldb_columns` exclusively in the
  persistent environment configuration and exclude them from CLI, environment,
  and `--set`.
- `DEC-014`: accept only Oracle until another adapter exists and reject
  PostgreSQL early in configuration, CLI, and engine loading.
- `DEC-015`: measure coverage jointly for local and Oracle E2E suites, including
  child processes, and verify the baseline before enforcing a threshold.
- `DEC-016`: archive orphans detected by outer join, using the process date as a
  fallback for their related control dates.
- `DEC-017`: protect `development` with required status checks, required PR
  review, and per-job `contents: read` permissions.
- `DEC-018`: all project files are written in English; this rule is stated in
  `AGENTS.md` § Language.
