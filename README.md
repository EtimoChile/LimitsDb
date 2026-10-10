# LimitsDb

Configuration-driven ILM (Information Lifecycle Management) runner for operational databases. It applies retention policies to your application tables—archiving, purging, and keeping datasets lean—so source stays fast while history remains auditable. LimitsDb is designed to support multiple database engines; **Oracle** is supported today, with an engine-agnostic ILM model that can extend to others.

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
  - **PostgreSQL**: planned for a future adapter; it is not currently accepted in configuration.

All Python dependencies are defined in `pyproject.toml`. For source installations, they are installed through Poetry.

## Installation

Clone the repository and install dependencies with Poetry:

```bash
git clone https://github.com/etimochile/limitsdb.git
cd limitsdb
poetry install
```

For development setup, running tests, and CI details see [DEVELOPMENT.md](DEVELOPMENT.md).

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

## CLI Commands

After `poetry install`, the following executables are available:

- `ldb-init` — scaffold configuration files for a named configuration set
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

Scaffold the configuration files for a named configuration set and optional environment:

```bash
poetry run ldb-init --schema <SCHEMA> [--profile <PROFILE>] [--config-dir <DIR>] [--overwrite] [--no-examples]
# examples:
# poetry run ldb-init --schema billing
# poetry run ldb-init --schema billing --profile prod
# poetry run ldb-init --schema billing --profile dev --config-dir /etc/limitsdb
# poetry run ldb-init --schema billing --profile dev --no-examples
```

`--schema` is a name you choose to group all configuration files for one
system or application (e.g., `billing`, `crm`). It is not a database schema;
it is simply the folder name under which the files live.

`--profile` is optional. Use it when the same configuration set targets
multiple environments (e.g., `dev`, `prod`). When supplied, the profile name
becomes part of every file name as `.<PROFILE>`. When omitted, file names carry
no extra suffix.

`--overwrite` replaces existing files. Without it, any file that already exists
is left unchanged; only missing files are created.

`--no-examples` skips copying the ILM example file (`ilm[.<PROFILE>].example.yml`).

`--config-dir` scaffolds under a custom root instead of `~/.config/LimitsDb`.

Generated files (default root is `~/.config/LimitsDb`):

```
~/.config/LimitsDb/
└─ schemas/
   └─ <SCHEMA>/
      ├─ config[.<PROFILE>].yml        # commented defaults + help (from the Config model)
      ├─ ilm[.<PROFILE>].yml           # empty list (you fill it)
      ├─ ilm[.<PROFILE>].example.yml   # example copied verbatim from the package
      └─ secrets[.<PROFILE>].json      # secret fields with empty values
```

## Secrets & Encryption

```bash
poetry run ldb-crypt --schema <SCHEMA> [--profile <PROFILE>] [--config-dir <DIR>]
```

1. Edit `secrets[.<PROFILE>].json` and fill in the credentials:

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
poetry run ldb-crypt --schema billing --profile prod
```

`ldb-crypt` creates or reuses a local encryption key and replaces every
non-empty plaintext value in `secrets[.<PROFILE>].json` with an
`enc:v1:aes256gcm:…` token. Already-encrypted values are left unchanged.
If this step is skipped, `ldb-run` and `ldb-impl` will encrypt any plaintext
secrets automatically before the first run. `ldb-crypt` is a convenience to
encrypt them without executing ILM.

The encryption key is stored in your user context and protected by OS
permissions. Never version or copy the key file.

## Bootstrap Database Objects

```bash
poetry run ldb-impl --schema <SCHEMA> [--profile <PROFILE>] [--config-dir <DIR>] [--config-file <YAML>] [--set key=value] [--log-level LEVEL]
```

`ldb-impl` creates the database objects that LimitsDb needs to operate. Run it
once before the first `ldb-run`, and again whenever a new environment is
provisioned. All operations are idempotent: existing objects are left unchanged
and only missing ones are created.

**`ldb-impl` is optional** if a DBA has already created all required objects
manually. LimitsDb does not require objects to have been created through
`ldb-impl`; it only requires that the objects exist with the expected structure
before `ldb-run` is executed.

What it creates in both source and history environments:

- **Role** — grants the application user the privileges required by LimitsDb.
- **User** — the application user with its default tablespace and role.
- **Control tables** — `LDB_CTL` (execution state per table) and `LDB_LOG`
  (audit log of each run).
- **Sequence** — `LDB_LOG_ID` for audit log identifiers.
- **Database link** — bidirectional links between source and history so the ILM
  engine can reach both environments from either side.

`ldb-impl` requires all four credential pairs to be present and encrypted in
`secrets[.<PROFILE>].json`: `source_password`, `history_password`,
`admin_source_password`, and `admin_history_password`. Run `ldb-crypt` first.

## Run ILM

```bash
poetry run ldb-run --schema <SCHEMA> [--profile <PROFILE>] --action SOURCE_ILM|HISTORY_ILM [options]
# examples:
# poetry run ldb-run --schema billing --profile prod --action SOURCE_ILM
# poetry run ldb-run --schema billing --action HISTORY_ILM
```

Common overrides:

```
--mode VALIDATE|PLAN|PREVIEW|SCRIPT|EXECUTE   # VALIDATE setup, PLAN DAG, PREVIEW simulate, SCRIPT emit SQL, EXECUTE run
--chunk-size <int>                            # Rows per chunk when processing large tables
--parallel-max <int>                          # Maximum number of tables processed in parallel
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

### Admin credentials in ldb-run

`ldb-run` uses admin credentials (`admin_source_password`,
`admin_history_password`) for two operations: granting table privileges to the
application role, and creating or altering history tables before an `EXECUTE`
run. Both operations are idempotent.

If admin credentials are absent from `secrets[.<PROFILE>].json`, the privilege
grants are skipped silently — the assumption is that grants are already in
place. History table management in `EXECUTE` mode still requires admin
credentials in the current version.

### Configuration parameters reference

| Parameter                | Purpose                                                                                                                                                         | Default / values                                                                     |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `action`                 | ILM target (`SOURCE_ILM` moves/purges source, `HISTORY_ILM` cleans downstream history).                                                                         | Default `SOURCE_ILM`; choices `SOURCE_ILM`, `HISTORY_ILM`.                           |
| `mode`                   | Execution mode: `VALIDATE` config/credentials, `PLAN` dependency order, `PREVIEW` simulate (a.k.a. legacy `DRY_RUN`), `SCRIPT` emit SQL, `EXECUTE` run changes. | Default `PREVIEW`; choices `VALIDATE`, `PLAN`, `PREVIEW`, `SCRIPT`, `EXECUTE`.       |
| `chunk_size`             | Rows per chunk when processing large tables.                                                                                                                    | Default `100000`.                                                                    |
| `use_added_columns`      | Populate derived columns in history tables.                                                                                                                     | Default `True` (boolean toggle).                                                     |
| `add_ldb_columns`        | Add LimitsDb execution-date columns in history tables.                                                                                                        | Default `True` (boolean toggle).                                                     |
| `parallel_max`           | Maximum number of parallel processes.                                                                                                                           | Default `10`.                                                                        |
| `db_engine`              | Database engine.                                                                                                                                                | Default and only current choice: `"oracle"`.                                        |
| `log_level`              | Logging level.                                                                                                                                                  | Default `"INFO"`; choices `"DEBUG"`, `"INFO"`, `"WARNING"`, `"ERROR"`, `"CRITICAL"`. |
| `schema`                 | Configuration set name: identifies a system or application (e.g., `billing`). Not a database schema — it is the folder that groups all configuration files for one target. | Required on CLI; no persisted default. |
| `profile`                | Environment name (e.g., `dev`, `prod`). Differentiates configuration files for different environments within the same configuration set. | Optional; default `null`. |
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
| `action`                 | `--action`                 | `LDB_ACTION`                 | `config[.<PROFILE>].yml: action`                   |
| `mode`                   | `--mode`                   | `LDB_MODE`                   | `config[.<PROFILE>].yml: mode`                     |
| `chunk_size`             | `--chunk-size`             | `LDB_CHUNK_SIZE`             | `config[.<PROFILE>].yml: chunk_size`               |
| `use_added_columns`      | —                          | —                            | `config[.<PROFILE>].yml: use_added_columns`        |
| `add_ldb_columns`        | —                          | —                            | `config[.<PROFILE>].yml: add_ldb_columns`          |
| `parallel_max`           | `--parallel-max`           | `LDB_PARALLEL_MAX`           | `config[.<PROFILE>].yml: parallel_max`             |
| `db_engine`              | `--db-engine`              | `LDB_DB_ENGINE`              | `config[.<PROFILE>].yml: db_engine`                |
| `log_level`              | `--log-level`              | `LDB_LOG_LEVEL`              | `config[.<PROFILE>].yml: log_level`                |
| `schema`                 | `--schema`                 | `LDB_SCHEMA`                 | CLI only (not stored).                             |
| `profile`                | `--profile`                | `LDB_PROFILE`                | CLI only (not stored).                             |
| `ilm_config_file`        | `--ilm-config-file`        | `LDB_ILM_CONFIG_FILE`        | CLI only (not stored).                             |
| `source_dsn`             | `--source-dsn`             | `LDB_SOURCE_DSN`             | `config[.<PROFILE>].yml: source_dsn`               |
| `source_username`        | `--source-username`        | `LDB_SOURCE_USERNAME`        | `config[.<PROFILE>].yml: source_username`          |
| `source_password`        | —                          | —                            | `secrets[.<PROFILE>].json: source_password`        |
| `history_dsn`            | `--history-dsn`            | `LDB_HISTORY_DSN`            | `config[.<PROFILE>].yml: history_dsn`              |
| `history_username`       | `--history-username`       | `LDB_HISTORY_USERNAME`       | `config[.<PROFILE>].yml: history_username`         |
| `history_password`       | —                          | —                            | `secrets[.<PROFILE>].json: history_password`       |
| `admin_source_username`  | `--admin-source-username`  | `LDB_ADMIN_SOURCE_USERNAME`  | `config[.<PROFILE>].yml: admin_source_username`    |
| `admin_source_password`  | —                          | —                            | `secrets[.<PROFILE>].json: admin_source_password`  |
| `admin_history_username` | `--admin-history-username` | `LDB_ADMIN_HISTORY_USERNAME` | `config[.<PROFILE>].yml: admin_history_username`   |
| `admin_history_password` | —                          | —                            | `secrets[.<PROFILE>].json: admin_history_password` |

Builder-only flags (`--config-dir`, `--config-file`, `--set`) control how overlays are discovered and have no equivalent entry in the configuration files.

## ILM Rule Semantics

Each entry in `ilm[.<PROFILE>].yml` merges table-level attributes with one or more rule conditions defined under `conds`. When `conds` is omitted, LimitsDb assumes a single active rule so the table remains eligible for processing.

- **Activation:** A condition runs only when `is_active: true`. Deactivating the sole condition for a table effectively removes that table from the run.
- **Retention windows:** `retain_months_source` is required whenever `purge_date_expr` is present. `retain_months_history` extends the history window but also depends on defining the source retention. During `HISTORY_ILM` runs, the engine sums source and history months to determine the cut-off date.
- **Date expressions:** `purge_date_expr` identifies the date column (or expression) that anchors retention. Prefix column references with `@` (for example, `"@DSP_DATE"`); the runner swaps the prefix for the proper table alias at execution time.
- **Additional filters:** Add optional filters through `additional_filter_expr` (source runs) and `history_addtl_filter_expr` (history runs). Both accept the same `@column` syntax, and the history expression falls back to the source expression when omitted.
- **Referencing tables:** Use `referencing_tables` to pull parent conditions into child tables. List entries as `<TABLE> <ALIAS>` (optionally `<OWNER>.<TABLE> <ALIAS>`) and supply matching `join_expr` fragments. The runner inherits active conditions from each referenced table and rewrites the join fragments—`@` becomes `inner` joins by default or `left outer` joins when `source_orphan_purge: true` to find orphans.
- **Historical predicate snapshot:** With `use_added_columns` enabled, every archived row preserves the values needed to reevaluate all its predicates in history, including values sourced from related tables. Distinct cut-off dates are stored in helper columns such as `ldb_date_<suffix>`. Predicates that share a date reuse the same helper column while retaining their own filters and retention windows. Other values sourced from related tables are materialized as `<column>_<table_alias>`.
- **Orphan handling:** Enable `source_orphan_purge` with `orphan_check_column` to archive source rows whose referenced parent is absent. The source query uses `left outer` joins and treats the row as an orphan when any comma-separated check column is `NULL`. `referencing_tables`, `join_expr`, `orphan_check_column`, `use_added_columns: true`, and a positive historical retention are required. Orphans follow the table's normal source-to-history flow rather than being deleted directly. LimitsDb snapshots this state in `LDB_IS_ORPHAN` and stores a fallback date so `HISTORY_ILM` can apply the configured retention without accessing the missing parent.

## Configuration Resolution (Overlay Order)

Lowest → highest priority:

1. System root: `/etc/limitsdb`
2. User root: `~/.config/LimitsDb`
3. `--config-file` (extra overlay)
4. Registered environment variables (`Env("LDB_*")` metadata)
5. `--<param> value` or `--set key=value`

After overlays, secrets from `secrets[.<PROFILE>].json` are loaded and decrypted. If any secret is plaintext and enforcement is enabled (default), the loader raises an error.

Tip: you can redirect both “system” and “user” roots to the same place with `--config-dir`.

## Configuration Files

### `config[.<PROFILE>].yml` (commented defaults + help)

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

`oracle` is the only supported `db_engine`. PostgreSQL remains a future implementation; any other value is rejected.

Uncomment and set values as needed. Secrets are not listed here (they live in `secrets[.<PROFILE>].json`).

### `ilm[.<PROFILE>].yml`

Empty list scaffolded by `ldb-init`. You define your ILM tables here.

### `ilm[.<PROFILE>].example.yml`

Comprehensive, commented example shipped in the package and copied verbatim by `ldb-init`.

### `secrets[.<PROFILE>].json`

Holds only secret fields (empty by default). Must be encrypted (run `ldb-crypt`) before the main runner will accept them.

## Environment Variables

Only the variables listed in the table above work as overrides; any other `LDB_*` variable is ignored. Examples:

- `LDB_PARALLEL_MAX=8` → `parallel_max: 8`
- `LDB_LOG_LEVEL=DEBUG` → `log_level: "DEBUG"`

Booleans accept `true/false`, `1/0`, `on/off` (case-insensitive).

`use_added_columns` and `add_ldb_columns` are intentionally file-only because changing the historical table shape
between runs can destabilize history processing. They cannot be overridden through environment variables or `--set`.

## Troubleshooting

- Plaintext secret detected  
  Run `poetry run ldb-crypt --schema <SCHEMA> [--profile <PROFILE>]`. Or set the value to `""` until you’re ready.

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
