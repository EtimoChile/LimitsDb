# LimitsDb — Test organization

Complements [`docs/prompts/test-authoring.md`](test-authoring.md) (R1–R7):
that document defines **how** to write each test; this one defines **where** it lives.

---

## Grouping principle

Each test file covers **one functional contract defined in the specification**,
not a source module. The source of truth for deciding what belongs in each file
is the specification document that governs it, in order of precedence:

1. `README.md`
2. `limitsdb/resources/ilm.example.yml`
3. `docs/configuration-contract.md`
4. `docs/exception-handling.md`
5. Public docstrings

---

## Groupings by functionality

| Spec contract | Source | Test file |
|---|---|---|
| Configuration file scaffolding (`ldb-init`) | README § *Initialize Configuration* | `test_config_scaffolding.py` |
| Secret encryption and management (`ldb-crypt`) | README § *Secrets & Encryption* | `test_secret_encryption.py` |
| Configuration loading and overlay | README § *Configuration Resolution* + `docs/configuration-contract.md` | `test_config_overlay.py` |
| ILM policy semantics (YAML) | README § *ILM Rule Semantics* + `ilm.example.yml` | `test_ilm_policy_loading.py` |
| Execution modes and ILM execution | README § *Run ILM* (VALIDATE / PLAN / PREVIEW / SCRIPT / EXECUTE) | `test_run_ilm.py` |
| Database engine selection | README § *Configuration parameters* (`db_engine`) | `test_engine_selection.py` |
| Log level control | README § *Configuration parameters* (`log_level`) | `test_log_level_control.py` |
| CLI entry points | README § *CLI Commands* (ldb-run, ldb-crypt, ldb-impl, ldb-init) | `test_cli_entry_points.py` |
| Oracle-specific SQL and DDL | README § *Run ILM* + Oracle engine | `adapters/oracle/test_oracle_sql_generation.py` |
| Oracle schema objects | README § *Run ILM* (control/history objects) | `adapters/oracle/test_oracle_schema_objects.py` |
| Oracle connection operations | README § *Run ILM* + `docs/exception-handling.md` § oracle | `adapters/oracle/test_oracle_connection_ops.py` |
| Oracle E2E (real database) | README § *Oracle integration tests* | `integration/oracle/test_oracle_ephemeral.py` |

Each file adversarializes the contract it covers (R6): error paths
(`ConfigurationError`, `SecretError`, `DatabaseConnectionError`, `ValidationError`,
`ExecutionError`) are not a separate group; they belong in the file for the contract
that raises them, per `docs/exception-handling.md`.

---

## Contract boundaries

**`test_config_overlay.py`** covers exclusively the invariants of
`docs/configuration-contract.md`: layer precedence, typed views
(`ExecutionConfig`, `ConnectionConfig`, `AdministrationConfig`, `RuntimeContext`),
`DRY_RUN → PREVIEW` normalization, YAML-only persistent keys, and rejection of
unknown keys. It does not cover credentials or execution modes.

**`test_run_ilm.py`** covers two sub-contracts of README § *Run ILM* that coexist
in the same file because they share the same `Config` input object:
- **Modes** (`--mode`): which credentials each mode requires, which operations it
  performs, what output it produces (PLAN prints DAG, SCRIPT emits SQL, VALIDATE
  checks connections).
- **Execution**: dependency graph, parallelism, retry with progress, cycles,
  orphan processing, historical column snapshotting.

**`test_cli_entry_points.py`** covers the contract of each command as a process:
required flags, exit codes, user-facing error messages. It does not duplicate
configuration logic (that is `test_config_overlay.py`) or ILM execution logic
(that is `test_run_ilm.py`).

**`test_config_scaffolding.py`** covers the `ldb-init` contract (README § *Initialize
Configuration*): which configuration files are created, with what content, and the
behavior of `--no-examples`, `--overwrite`, and automatic encryption. `--schema`
and `--profile` are the namespace and environment names that determine the output
paths; the test does not treat them as database objects. It does not cover the
subsequent loading of those files (that is `test_config_overlay.py`).

---

## Directory structure

```
tests/
│
├── # ── Engine-agnostic core (no real database) ───────────────────────
├── test_log_level_control.py      — README § log_level
├── test_secret_encryption.py      — README § Secrets & Encryption
├── test_ilm_policy_loading.py     — README § ILM Rule Semantics
├── test_config_overlay.py         — README § Configuration Resolution +
│                                    docs/configuration-contract.md
├── test_run_ilm.py                — README § Run ILM (modes + execution)
├── test_config_scaffolding.py     — README § Initialize Configuration
├── test_engine_selection.py       — README § db_engine
├── test_cli_entry_points.py       — README § CLI Commands
│
├── adapters/   ← adapter tests without a real database (connection mocks)
│   ├── oracle/
│   │   ├── test_oracle_sql_generation.py    — Oracle SQL/PL/SQL specifics
│   │   ├── test_oracle_schema_objects.py    — DDL: tables, users, roles, sequences
│   │   └── test_oracle_connection_ops.py    — operations on LDB_CTL/LDB_LOG
│   └── postgresql/                          ← when the adapter is added
│
└── integration/
    ├── oracle/
    │   └── test_oracle_ephemeral.py         — E2E with ephemeral Oracle database
    └── postgresql/
```

---

## Level criteria

| Level | Where | What defines it |
|---|---|---|
| **core** | `tests/test_*.py` | Engine-agnostic contract from the README or `docs/configuration-contract.md`. |
| **adapter** | `tests/adapters/<engine>/` | Engine-specific SQL/DDL behavior, without a real database (connection mock). |
| **integration** | `tests/integration/<engine>/` | E2E against an ephemeral real database; requires engine environment variables. |

---

## Prerequisites before adding a second engine

1. Create `tests/adapters/oracle/` with the three files and a `conftest.py`
   containing the `oracledb.Connection` mock.
2. Migrate `test_related_history_filter_is_snapshotted_and_rewritten` from
   `test_run_ilm.py` to `adapters/oracle/test_oracle_sql_generation.py`; the
   equivalent core test verifies the snapshotting logic with an engine-neutral mock.
3. Move `tests/integration/test_oracle_ephemeral.py` →
   `tests/integration/oracle/test_oracle_ephemeral.py`.
4. Add `postgres_integration` to the markers in `pyproject.toml`.
