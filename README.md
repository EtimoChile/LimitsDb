# LimitsDb

Configuration-driven ILM (Information Lifecycle Management) runner for operational databases. It applies retention policies to your application tables—archiving, purging, and keeping datasets lean—so source stays fast while history remains auditable. LimitsDb is designed to support multiple database engines; **Oracle** is supported today, with an engine-agnostic ILM model that can extend to others.

> **Renamed project:** LimitsDb replaces the former TerminusDB name. The rename is intentionally consistent and breaking: Python imports now use `limitsdb`, commands use `ldb-*`, environment variables and database objects use `LDB_*`, and local state is stored under LimitsDb/limitsdb paths. Existing installations must migrate those names before upgrading.

## Overview (What it does)

- **Executes ILM policies on real schemas.** You describe which tables are in scope, how they relate (parents/children), and the rules that decide when rows move to history or are purged.
- **Targets source and history environments.** Choose whether to act on **source** (move/purge) or **history** (downstream cleanup) for the same ILM model.
- **Retains exactly what you need.** Per-table, per-condition retention windows (e.g., “keep 3 months in source, 11 months in history”), with the ability to define multiple conditions per table.
- **Understands relationships.** Reference parent tables, define join expressions, and optionally purge **orphans** when child rows lose their parent.
- **Handles heavy tables safely.** Chunked processing and parallelism keep large operations predictable and observable.
- **Works with hints and special cases.** Add optimizer hints, mark LOB-heavy tables, and tune behavior per table.
- **Dry-run anytime.** Generate the exact operations without executing them, to review and approve planned changes.
- **Keeps secrets safe.** Database credentials are stored encrypted and transparently decrypted at runtime.
- **Automates control DB objects creation.** Creates missing control schemas and DB objects.
- **Automates history objects creation and updates.** Creates missing history tables and correct columns, contraints and indexes for ILM executions.

### Typical outcomes

- **Slimmer source**: old/inactive rows are moved or purged on a schedule.
- **Auditable history**: history schemas keep what you configure, for the period you define.
- **Predictable cost/perf**: smaller hot datasets, fewer full-table scans, faster maintenance windows.

### How you use it (at a glance)

1. **Model your ILM**: list the tables, joins to parents, and one or more conditions with retention windows and optional filters.
2. **Choose the target**: run against **SOURCE_ILM** (move/purge in source) or **HISTORY_ILM** (history-side cleanup).
3. **Run safely**: start with **Query-only / Dry-run** to review the actions; then run for real.
4. **Repeat** on your preferred cadence (via scheduler/cron or your release pipeline).

---

## Requirements

- **Python**: 3.12+
- **Poetry**: 1.6+ (install from https://python-poetry.org/docs/#installation)
- **Database drivers**
  - **Oracle**: `oracledb` (thin mode works out of the box; for thick mode, install Oracle Instant Client and set PATH/LD_LIBRARY_PATH).
  - **PostgreSQL**: planned. You can wire your own engine layer later if needed.

All Python dependencies are defined in `pyproject.toml`. For source installations, they are installed through Poetry.

## Installation

Clone the repository and install dependencies with Poetry:

```bash
git clone https://github.com/etimochile/limitsdb.git
cd limitsdb
poetry install
```

## Quickstart

Spin up a minimal end-to-end run with the built-in scaffolding tools:

1. **Create config files** for a schema/profile pair:

   ```bash
   poetry run ldb-init --schema billing --profile dev
   ```

2. **Fill in connection details** in `~/.config/LimitsDb/schemas/billing/config.dev.yml` and secrets in `secrets.dev.json`.

3. **Encrypt secrets** in place:

   ```bash
   poetry run ldb-crypt --schema billing --profile dev
   ```

4. **Dry-run the ILM plan** (no data changes):

   ```bash
   poetry run ldb-run --schema billing --profile dev --action SOURCE_ILM --mode PREVIEW
   ```

5. **Execute for real** once you are satisfied with the plan:

   ```bash
   poetry run ldb-run --schema billing --profile dev --action SOURCE_ILM --mode EXECUTE
   ```

## Development & Testing

Install the development dependencies with Poetry 2.5.1 or newer:

```bash
poetry install --with dev
```

Then execute the tests (optionally collecting coverage):

```bash
poetry run pytest -q
poetry run coverage run -m pytest -q && poetry run coverage report
```

Install the Git hook so Ruff, mypy, and pytest run automatically on commits:

```bash
poetry run pre-commit install
# or, if you are using the active Python environment directly:
python -m pre_commit install
```

You can lint/test everything locally without committing via:

```bash
poetry run pre-commit run --all-files
```

Pull requests and pushes to `development` or `main` run the same quality gates
in GitHub Actions, then build and validate the distributions and install the
wheel in a clean environment.

Changes that affect the Oracle adapter, runner, or integration suite also run a
separate Oracle integration workflow. It starts an Oracle Database Free
container inside the Linux runner, uses only credentials local to that job, and
destroys the database when the job finishes. The workflow does not connect to a
shared or production database. These tests exercise LimitsDb adapter behavior
against a real Oracle instance; they do not attempt to test Oracle itself. The
end-to-end happy path provisions separate source and history schemas through
`ldb-impl`, loads operational fixture rows, executes both `SOURCE_ILM` and
`HISTORY_ILM`, and verifies the resulting data and audit records.

The integration tests are excluded from the default test command. To run them
against an explicitly disposable Oracle instance:

```bash
LDB_ORACLE_TEST_DSN=localhost:1521/FREEPDB1 \
LDB_ORACLE_TEST_USER=system \
LDB_ORACLE_TEST_PASSWORD=<test-only-password> \
poetry run pytest -m oracle_integration
```

Never point these tests at an operational or shared database: they create and
drop objects whose names begin with `LDBT_`.

## CLI Commands

After `poetry install`, the following executables are available:

- `ldb-init` — scaffold per-schema configuration files
- `ldb-crypt` — encrypt cleartext secrets in place
- `ldb-impl` — verify and create DB control schemas and objects
- `ldb-run` — main ILM runner

From shell:

```bash
poetry run ldb-init --help
poetry run ldb-crypt --help
poetry run ldb-impl --help
poetry run ldb-run --help
```

## Initialize Configuration

Create the schema/profile structure and template files:

```bash
poetry run ldb-init --schema <SCHEMA> --profile <PROFILE>
# examples:
# poetry run ldb-init --schema billing --profile prod
# poetry run ldb-init --schema billing --profile dev --config-dir /etc/limitsdb
```

Generated files (default root is `~/.config/LimitsDb`):

```
~/.config/LimitsDb/
└─ schemas/
   └─ <SCHEMA>/
      ├─ config.<PROFILE>.yml        # commented defaults + help (from the Config model)
      ├─ ilm.<PROFILE>.yml           # empty list (you fill it)
      ├─ ilm.<PROFILE>.example.yml   # example copied verbatim from the package
      └─ secrets.<PROFILE>.json      # secret fields with empty values
```

Note: the profile parameter is optional. If omitted, the `.<PROFILE>` filename part is omitted as well.

Options:

- `--overwrite` — replace existing files
- `--no-examples` — skip copying the ILM example
- `--config-dir` — scaffold under a custom root instead of `~/.config/LimitsDb`

## Secrets & Encryption

1. Edit `secrets.<PROFILE>.json` and set plaintext credentials, e.g.:

```json
{
  "source_password": "",
  "history_password": "",
  "admin_source_password": "",
  "admin_history_password": ""
}
```

2. Encrypt in place:

```bash
poetry run ldb-crypt --schema <SCHEMA> --profile <PROFILE>
```

LimitsDb derives the list of secrets from the configuration model. Currently, `secret_keys_from_config()` produces the following keys, which must live in `secrets.<PROFILE>.json` and are always stored encrypted:

- `source_password`
- `history_password`
- `admin_source_password`
- `admin_history_password`

The tool ensures an encryption key exists locally and replaces any non-empty plaintext with `enc:v1:aes256gcm:...`.
If you forget this step, the main loader will reject plaintext secrets by default.

Note: the local key is created in your user context and must be protected by OS permissions.

## Run ILM

```bash
poetry run ldb-run --schema <SCHEMA> --profile <PROFILE> --action SOURCE_ILM
# or
poetry run ldb-run --schema <SCHEMA> --profile <PROFILE> --action HISTORY_ILM
```

Common overrides:

```
--mode VALIDATE|PLAN|PREVIEW|SCRIPT|EXECUTE   # VALIDATE setup, PLAN DAG, PREVIEW simulate, SCRIPT emit SQL, EXECUTE run
--chunk-size <int>                            # Rows per chunk when processing large tables
--parallel-max <int>                          # Maximum number of tables processed in parallel
--use-added-columns / --no-use-added-columns  # Toggle helper columns in history tables
--add-ldb-columns / --no-add-ldb-columns      # Toggle ILM execution timestamp columns
--log-level DEBUG|INFO|WARNING|ERROR|CRITICAL # Adjust logger verbosity
--ilm-config-file <path>                      # Point to a specific ILM YAML file
--config-dir <root>                           # Override config root discovery
--config-file <yaml>                          # Extra overlay (highest priority)
--set key=value                               # Generic override (supports dotted keys)
```

Examples:

```bash
# Dry-run with verbose logging
poetry run ldb-run --schema billing --profile prod --action SOURCE_ILM --mode PREVIEW --log-level DEBUG

# Apply quick overrides without editing files
poetry run ldb-run --schema billing --profile prod --action SOURCE_ILM --mode EXECUTE --set chunk_size=200000 --set parallel_max=8
```

### Configuration parameters reference

| Parameter                | Purpose                                                                                                                                                         | Default / values                                                                     |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `action`                 | ILM target (`SOURCE_ILM` moves/purges source, `HISTORY_ILM` cleans downstream history).                                                                         | Default `SOURCE_ILM`; choices `SOURCE_ILM`, `HISTORY_ILM`.                           |
| `mode`                   | Execution mode: `VALIDATE` config/credentials, `PLAN` dependency order, `PREVIEW` simulate (a.k.a. legacy `DRY_RUN`), `SCRIPT` emit SQL, `EXECUTE` run changes. | Default `PREVIEW`; choices `VALIDATE`, `PLAN`, `PREVIEW`, `SCRIPT`, `EXECUTE`.       |
| `chunk_size`             | Rows per chunk when processing large tables.                                                                                                                    | Default `100000`.                                                                    |
| `use_added_columns`      | Populate derived columns in history tables.                                                                                                                     | Default `True` (boolean toggle).                                                     |
| `add_ldb_columns`        | Add LimitsDb execution-date columns in history tables.                                                                                                        | Default `True` (boolean toggle).                                                     |
| `parallel_max`           | Maximum number of parallel processes.                                                                                                                           | Default `10`.                                                                        |
| `db_engine`              | Database engine.                                                                                                                                                | Default `"oracle"`; choices `"oracle"`, `"postgres"`.                                |
| `log_level`              | Logging level.                                                                                                                                                  | Default `"INFO"`; choices `"DEBUG"`, `"INFO"`, `"WARNING"`, `"ERROR"`, `"CRITICAL"`. |
| `schema`                 | Schema name (folder under `schemas/`).                                                                                                                          | Required on CLI; no persisted default.                                               |
| `profile`                | Profile name (e.g., `dev`, `prod`).                                                                                                                             | Optional; default `null`.                                                            |
| `ilm_config_file`        | YAML file with tables (bypass DB discovery).                                                                                                                    | Optional; default `null`.                                                            |
| `source_dsn`             | Source DSN / connection descriptor.                                                                                                                             | Default empty string.                                                                |
| `source_username`        | Source username.                                                                                                                                                | Default empty string.                                                                |
| `source_password`        | Source password stored in secrets.                                                                                                                              | Default empty string; encrypted in `secrets.<PROFILE>.json`.                         |
| `history_dsn`            | History DSN / connection descriptor.                                                                                                                            | Default empty string.                                                                |
| `history_username`       | History username.                                                                                                                                               | Default empty string.                                                                |
| `history_password`       | History password stored in secrets.                                                                                                                             | Default empty string; encrypted in `secrets.<PROFILE>.json`.                         |
| `admin_source_username`  | Admin source username.                                                                                                                                          | Default empty string.                                                                |
| `admin_source_password`  | Admin source password stored in secrets.                                                                                                                        | Default empty string; encrypted in `secrets.<PROFILE>.json`.                         |
| `admin_history_username` | Admin history username.                                                                                                                                         | Default empty string.                                                                |
| `admin_history_password` | Admin history password stored in secrets.                                                                                                                       | Default empty string; encrypted in `secrets.<PROFILE>.json`.                         |

#### Overrides matrix (CLI, env, YAML)

| Parameter                | CLI flag                   | Environment variable         | YAML key                                         |
| ------------------------ | -------------------------- | ---------------------------- | ------------------------------------------------ |
| `action`                 | `--action`                 | `LDB_ACTION`                 | `config.<PROFILE>.yml: action`                   |
| `mode`                   | `--mode`                   | `LDB_MODE`                   | `config.<PROFILE>.yml: mode`                     |
| `chunk_size`             | `--chunk-size`             | `LDB_CHUNK_SIZE`             | `config.<PROFILE>.yml: chunk_size`               |
| `use_added_columns`      | `--use-added-columns`      | `LDB_USE_ADDED_COLS`         | `config.<PROFILE>.yml: use_added_columns`        |
| `add_ldb_columns`        | `--add-ldb-columns`        | `LDB_ADD_LDB_COLUMNS`        | `config.<PROFILE>.yml: add_ldb_columns`          |
| `parallel_max`           | `--parallel-max`           | `LDB_PARALLEL_MAX`           | `config.<PROFILE>.yml: parallel_max`             |
| `db_engine`              | `--db-engine`              | `LDB_DB_ENGINE`              | `config.<PROFILE>.yml: db_engine`                |
| `log_level`              | `--log-level`              | `LDB_LOG_LEVEL`              | `config.<PROFILE>.yml: log_level`                |
| `schema`                 | `--schema`                 | `LDB_SCHEMA`                 | CLI only (not stored).                           |
| `profile`                | `--profile`                | `LDB_PROFILE`                | CLI only (not stored).                           |
| `ilm_config_file`        | `--ilm-config-file`        | `ILM_CONFIG_FILE`            | CLI only (not stored).                           |
| `source_dsn`             | `--source-dsn`             | `LDB_SOURCE_DSN`             | `config.<PROFILE>.yml: source_dsn`               |
| `source_username`        | `--source-username`        | `LDB_SOURCE_USERNAME`        | `config.<PROFILE>.yml: source_username`          |
| `source_password`        | —                          | —                            | `secrets.<PROFILE>.json: source_password`        |
| `history_dsn`            | `--history-dsn`            | `LDB_HISTORY_DSN`            | `config.<PROFILE>.yml: history_dsn`              |
| `history_username`       | `--history-username`       | `LDB_HISTORY_USERNAME`       | `config.<PROFILE>.yml: history_username`         |
| `history_password`       | —                          | —                            | `secrets.<PROFILE>.json: history_password`       |
| `admin_source_username`  | `--admin-source-username`  | `LDB_ADMIN_SOURCE_USERNAME`  | `config.<PROFILE>.yml: admin_source_username`    |
| `admin_source_password`  | —                          | —                            | `secrets.<PROFILE>.json: admin_source_password`  |
| `admin_history_username` | `--admin-history-username` | `LDB_ADMIN_HISTORY_USERNAME` | `config.<PROFILE>.yml: admin_history_username`   |
| `admin_history_password` | —                          | —                            | `secrets.<PROFILE>.json: admin_history_password` |

Builder-only flags (`--config-dir`, `--config-file`, `--set`) control how overlays are discovered and do not map to configuration keys.

## ILM Rule Semantics

Each entry in `ilm.<PROFILE>.yml` merges table-level attributes with one or more rule conditions defined under `conds`. When `conds` is omitted, LimitsDb assumes a single active rule so the table remains eligible for processing.

- **Activation:** A condition runs only when `is_active: true`. Deactivating the sole condition for a table effectively removes that table from the run.
- **Retention windows:** `retain_months_source` is required whenever `purge_date_expr` is present. `retain_months_history` extends the history window but also depends on defining the source retention. During `HISTORY_ILM` runs, the engine sums source and history months to determine the cut-off date.
- **Date expressions:** `purge_date_expr` identifies the date column (or expression) that anchors retention. Prefix column references with `@` (for example, `"@DSP_DATE"`); the runner swaps the prefix for the proper table alias at execution time.
- **Additional filters:** Add optional filters through `additional_filter_expr` (source runs) and `history_addtl_filter_expr` (history runs). Both accept the same `@column` syntax, and the history expression falls back to the source expression when omitted.
- **Referencing tables:** Use `referencing_tables` to pull parent conditions into child tables. List entries as `<TABLE> <ALIAS>` (optionally `<OWNER>.<TABLE> <ALIAS>`) and supply matching `join_expr` fragments. The runner inherits active conditions from each referenced table and rewrites the join fragments—`@` becomes `inner` joins by default or `left outer` joins when `source_orphan_purge: true` to find orphans.
- **Orphan handling:** Enable `source_orphan_purge` with `orphan_check_column` to delete child rows whose parents no longer qualify. The engine also materializes helper columns (e.g., `ldb_date_<alias>`) when `use_added_columns` is enabled so history cleanups can reference parent timestamps.

## Configuration Resolution (Overlay Order)

Lowest → highest priority:

1. System root: `/etc/limitsdb`
2. User root: `~/.config/LimitsDb`
3. `--config-file` (extra overlay)
4. Environment variables (`LDB_*`)
5. `--<param> value` or `--set key=value`

After overlays, secrets from `secrets.<PROFILE>.json` are loaded and decrypted. If any secret is plaintext and enforcement is enabled (default), the loader raises an error.

Tip: you can redirect both “system” and “user” roots to the same place with `--config-dir`.

## Configuration Files

### `config.<PROFILE>.yml` (commented defaults + help)

Generated from the Config model (single source of truth).
Every key is commented; the default value and short help appear inline. Example:

```yaml
# action: "SOURCE_ILM"  # ILM target: months_keep_history_max (SOURCE_ILM) or history (HISTORY_ILM)
# mode: "PREVIEW"       # VALIDATE | PLAN | PREVIEW | SCRIPT | EXECUTE (PREVIEW mirrors legacy DRY_RUN)
# chunk_size: 100000    # Rows per chunk when processing large tables
# use_added_columns: True  # Populate derived columns in history tables
# add_ldb_columns: True  # Add LimitsDb execution-date columns in history tables
# parallel_max: 10      # Maximum number of parallel processes
# db_engine: "oracle"   # Database engine
# log_level: "INFO"     # Logging level
```

Uncomment and set values as needed. Secrets are not listed here (they live in `secrets.<PROFILE>.json`).

### `ilm.<PROFILE>.yml`

Empty list scaffolded by `ldb-init`. You define your ILM tables here.

### `ilm.<PROFILE>.example.yml`

Comprehensive, commented example shipped in the package and copied verbatim by `ldb-init`.

### `secrets.<PROFILE>.json`

Holds only secret fields (empty by default). Must be encrypted (run `ldb-crypt`) before the main runner will accept them.

## Environment Variables

Any variable prefixed with `LDB_` becomes a config override (underscores become dots). Examples:

- `LDB_PARALLEL_MAX=8` → `parallel_max: 8`
- `LDB_LOG_LEVEL=DEBUG` → `log_level: "DEBUG"`

Booleans accept `true/false`, `1/0`, `on/off` (case-insensitive).

## Development

Format, lint, type-check:

```bash
poetry run ruff format .
poetry run ruff check .
poetry run mypy limitsdb
poetry run pytest
```

Add dependencies:

```bash
poetry add <package>
```

Update dependencies:

```bash
poetry update
```

## Troubleshooting

- Plaintext secret detected  
  Run `poetry run ldb-crypt --schema <SCHEMA> --profile <PROFILE>`. Or set the value to `""` until you’re ready.

- Oracle thick mode required  
  Install Oracle Instant Client and configure library paths (`PATH` on Windows, `LD_LIBRARY_PATH` on Linux/macOS).

- Configuration not found  
  Use `ldb-init` to scaffold files or pass `--config-dir` to point at the correct root.

- Unexpected key in config  
  The loader is strict—unknown keys raise an error. Fix typos or remove unused entries.

- Invalid or unreadable secrets file
  LimitsDb stops before database work and leaves the file unchanged. Repair the JSON or key, then rerun `ldb-crypt`.

Low-level configuration, secret, connection, validation, and execution failures are exposed as domain errors while
preserving their original cause. See [exception handling](docs/exception-handling.md) for the boundary contract and
broad-catch inventory.

## License

LimitsDb is released under the **Apache License 2.0**.
You are free to use, modify, and redistribute the software — including for commercial purposes — provided that you comply with the terms of that license.

All open-source source code of LimitsDb remains free and community-driven.
However, **Inversiones Etimo SpA** retains the exclusive right to produce, brand, and distribute proprietary or commercial editions of LimitsDb that may include additional features, services, or licensing terms.

📄 See [LICENSE](LICENSE), [LICENSE-DUAL.md](LICENSE-DUAL.md), and [NOTICE](NOTICE) for details.

---

## Governance and Ownership

LimitsDb is maintained by **Inversiones Etimo SpA**
📧 contacto@etimo.cl  
🌐 [https://www.etimo.cl](https://www.etimo.cl)

### Project Governance

- **Maintainer:** Inversiones Etimo SpA (core architecture, releases, roadmap).
- **Community contributions:** welcomed via pull requests under the [CONTRIBUTING.md](CONTRIBUTING.md) guidelines.
- **Issue tracking & discussions:** handled publicly through GitHub Issues and Discussions.
- **Release model:**
  - `development` → integration branch for new work.
  - `main` → stable, production-ready releases.
  - Periodic tags and changelogs define official versions.

### Ownership

All intellectual property and trademarks for LimitsDb are owned by **Inversiones Etimo SpA**.
Open-source distribution under the Apache 2.0 license does **not** transfer ownership of the software or brand.  
Any proprietary extensions, hosted services, or commercial editions may be offered solely by Inversiones Etimo SpA.
