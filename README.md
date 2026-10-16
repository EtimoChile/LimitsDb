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

- **Python**: 3.12+ — https://www.python.org/downloads/
- **Oracle**: `oracledb` runs in thin mode (pure Python, no Oracle Instant Client required).
- **PostgreSQL**: planned for a future adapter; not currently accepted in configuration.

## Installation

### Recommended — pipx (no virtual environment management needed)

```bash
pip install pipx
pipx install limitsdb
```

`pipx` creates and manages the virtual environment automatically. After installation,
`ldb-run`, `ldb-impl`, `ldb-crypt`, and `ldb-init` are available in your terminal
on any platform without activation.

### Alternative — pip with a virtual environment

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install limitsdb
```

### Air-gapped or internet-restricted servers

For production database servers without internet access (common in enterprise Oracle
environments on Linux, AIX, or Solaris), download the packages on a machine that
does have access and transfer them to the target server.

**Step 1 — Download on a connected machine**

If the target server is Linux x86_64:

```bash
pip download limitsdb \
  --dest ./limitsdb-bundle \
  --platform linux_x86_64 \
  --python-version 312 \
  --only-binary=:all:
```

For the same platform as the current machine (Windows or macOS build host
copying to an identical Linux target, omit `--platform` and `--python-version`):

```bash
pip download limitsdb --dest ./limitsdb-bundle
```

**Step 2 — Transfer to the target server**

```bash
scp -r ./limitsdb-bundle user@server:/opt/limitsdb-bundle
```

**Step 3 — Install on the target server (no internet required)**

```bash
python3 -m venv /opt/limitsdb-venv
source /opt/limitsdb-venv/bin/activate
pip install --no-index --find-links /opt/limitsdb-bundle limitsdb
ldb-run --help
```

> **Note for AIX and Solaris**: `oracledb` may not have a prebuilt wheel for your
> platform. If `pip download --only-binary=:all:` finds nothing, the package must
> be compiled from source on the target, which requires Python development headers
> and a C compiler (`gcc` or `xlc`). Contact your system administrator before
> proceeding.

For development setup (cloning the repo, running tests, CI) see [CONTRIBUTING.md](CONTRIBUTING.md).

## Quickstart

Spin up a minimal end-to-end run with the built-in scaffolding tools:

1. **Create config files** for a schema/profile pair:

   ```bash
   ldb-init --schema billing --profile dev
   ```

2. **Fill in connection details** in `~/.config/LimitsDb/schemas/billing/config.dev.yml` and secrets in `secrets.dev.json`.

3. **Encrypt secrets** in place:

   ```bash
   ldb-crypt --schema billing --profile dev
   ```

4. **Preview the ILM run** (applies the required DDL and grants, moves no data):

   ```bash
   ldb-run --schema billing --profile dev --action SOURCE_ILM --mode PREVIEW
   ```

5. **Execute for real** once you are satisfied with the plan:

   ```bash
   ldb-run --schema billing --profile dev --action SOURCE_ILM --mode EXECUTE
   ```

## CLI Commands

After installation, the following commands are available directly in your terminal:

- `ldb-init` — scaffold configuration files for a named configuration set
- `ldb-crypt` — encrypt cleartext secrets in place
- `ldb-impl` — verify and create DB control schemas and objects
- `ldb-run` — main ILM runner

```bash
ldb-init --help
ldb-crypt --help
ldb-impl --help
ldb-run --help
```

## Initialize Configuration

Scaffold the configuration files for a named configuration set and optional environment:

```bash
ldb-init --schema <SCHEMA> [--profile <PROFILE>] [--config-dir <DIR>] [--overwrite] [--no-examples]
# examples:
# ldb-init --schema billing
# ldb-init --schema billing --profile prod
# ldb-init --schema billing --profile dev --config-dir /etc/limitsdb
# ldb-init --schema billing --profile dev --no-examples
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
ldb-crypt --schema <SCHEMA> [--profile <PROFILE>] [--config-dir <DIR>]
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
ldb-crypt --schema billing --profile prod
```

`ldb-crypt` creates or reuses a local encryption key and replaces every
non-empty plaintext value in `secrets[.<PROFILE>].json` with an
`enc:v1:aes256gcm:…` token. Already-encrypted values are left unchanged.
If this step is skipped, `ldb-run` and `ldb-impl` will encrypt any plaintext
secrets automatically before the first run, provided the secrets file is in the
default user configuration directory or an explicit `--config-dir` is supplied.
`ldb-crypt` is a convenience to encrypt them without executing ILM.

The encryption key is stored in your user context and protected by OS
permissions. Never version or copy the key file.

## Bootstrap Database Objects

```bash
ldb-impl --schema <SCHEMA> [--profile <PROFILE>] [--config-dir <DIR>] [--config-file <YAML>] [--set key=value] [--log-level LEVEL]
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

`ldb-impl` needs the runtime credentials (`source_password`, `history_password`)
encrypted in `secrets[.<PROFILE>].json`. Run `ldb-crypt` first.

### Without admin credentials

Admin credentials (`admin_source_*`, `admin_history_*`) are optional per
database. When they are absent for a database, `ldb-impl` creates nothing in it
and writes a SQL*Plus script for a DBA instead, one file per database:
`bootstrap_<schema>_<source|history>_<YYYYMMDD_HHMMSS>.sql` under
`ddl_pending_dir` (the same parameter as `ldb-run`: `--ddl-pending-dir`,
`LDB_DDL_PENDING_DIR`, default `ldb_ddl_pending`). A database whose admin credentials are present is still
provisioned directly. Generating scripts is not an error (exit code 0, with a
warning naming each file); if a script cannot be written, `ldb-impl` exits 1.

The script:

- stops at the first error (`whenever sqlerror exit failure`) and ends with a
  `LimitsDb: OK` banner on success;
- contains no passwords: it prompts for all of them (hidden input) at its start,
  before it changes anything;
- can be rerun: each object is created in a PL/SQL block that ignores only its
  own "already exists" error, and an existing database link is skipped;
- creates the database link after reconnecting as the runtime user, who owns it.
  It asks for that user's password once and reuses it to reconnect. An existing user keeps its password, so the one entered must
  match it; the script says so before it reconnects.

Run it in SQL*Plus connected as a user with administrative privileges:
`sqlplus admin@<dsn> @bootstrap_<schema>_<env>_<timestamp>.sql`.

When `ldb-impl` has admin credentials and the runtime user already existed with
a different password, the reconnection as that user is rejected (ORA-01017).
`ldb-impl` then logs that the user rejected the configured password, naming the
user and the database, and exits 1; it never changes the password of an existing
user.

## Run ILM

```bash
ldb-run --schema <SCHEMA> [--profile <PROFILE>] --action SOURCE_ILM|HISTORY_ILM [options]
# examples:
# ldb-run --schema billing --profile prod --action SOURCE_ILM
# ldb-run --schema billing --action HISTORY_ILM
```

Common overrides:

```
--mode VALIDATE|PREPARE|PLAN|PREVIEW|SCRIPT|EXECUTE   # VALIDATE setup and privileges (read-only), PREPARE grants and objects, PLAN DAG, PREVIEW run without moving data, SCRIPT emit SQL, EXECUTE run
--chunk-size <int>                            # Rows per chunk when processing large tables
--parallel-max <int>                          # Maximum number of tables processed in parallel
--ddl-pending-dir <path>                      # Where failed history DDL is written for a DBA (default: ldb_ddl_pending)
--history-baseline EMPTY|SOURCE               # SCRIPT without a readable history: assume no history objects (default) or the same objects as source
--log-level DEBUG|INFO|WARNING|ERROR|CRITICAL # Adjust logger verbosity
--ilm-config-file <path>                      # Point to a specific ILM YAML file
--config-dir <root>                           # Override config root discovery
--config-file <yaml>                          # Extra overlay (highest priority)
--set key=value                               # Generic override (supports dotted keys)
```

Examples:

```bash
# Dry-run with verbose logging
ldb-run --schema billing --profile prod --action SOURCE_ILM --mode PREVIEW --log-level DEBUG

# Apply quick overrides without editing files
ldb-run --schema billing --profile prod --action SOURCE_ILM --mode EXECUTE --set chunk_size=200000 --set parallel_max=8
```

### Admin credentials in ldb-run

`ldb-run` uses admin credentials (`admin_source_password`,
`admin_history_password`) for two operations: granting table privileges to the
application role, and creating or altering history tables before a `PREVIEW` or
`EXECUTE` run (`PREVIEW` needs the objects in place to validate and time the
queries; it skips only the data movement). Both operations are idempotent.

A `PREVIEW` finishes each table with the status `TPEND` in `ldb_ctl`, not `TEND`, so it never marks a
table as processed for the day: an `EXECUTE` run afterwards (same day) still moves the data. A `TPEND`
left by a previous `PREVIEW` is ignored by the next run. After upgrading, rerun `ldb-impl` so the
supporting procedures (`check_save_status`, `check_referencing_tables`) know the new status.

If source admin credentials are absent from `secrets[.<PROFILE>].json`, the
source privilege grants are not applied. `ldb-run` checks with the runtime
connection which privileges are missing through the role and logs a warning
naming them; a table the runtime user cannot see is skipped with a warning.
Run `--mode PREPARE` to get the missing grants as a file for a DBA.

History table management follows the same principle:

- **Admin credentials present, tables already up to date**: the admin connection
  is not opened. No DDL is executed.
- **Admin credentials present, DDL changes are required**: the admin connection
  is opened and the changes are applied as before.
- **Admin credentials absent, tables already up to date**: `ldb-run` proceeds
  without an admin connection. This is the normal case for environments where a
  DBA pre-creates all history objects and admin credentials are not stored.
- **Admin credentials absent, history schema cannot be inspected** (for example
  the runtime connection or dictionary query fails): `ldb-run` exits with code 1
  and logs the cause. It does not run ILM against a history schema whose state is
  unknown.
- **Admin credentials absent, DDL changes are required**: `ldb-run` applies the
  DDL directly over the runtime history connection, with no prior privilege
  check. It succeeds when the runtime user owns the objects or holds the needed
  privileges (for example `ALTER`). Any DDL statement that fails, for whatever
  reason, is recorded and the remaining statements are still attempted. If at least one
  statement failed, `ldb-run` writes the failed statements, each preceded by its
  error, to `history_ddl_<schema>_<YYYYMMDD_HHMMSS>.sql` under `ddl_pending_dir`
  (default `ldb_ddl_pending`), logs an error naming the file, and exits with
  code 1. The file is plain SQL for SQL*Plus (no PL/SQL): it stops at the first
  failing statement with a non-zero exit code, and only a run that reaches the
  final `LimitsDb: OK` banner applied everything. The recorded errors are
  comments. A DBA reviews and runs the file, then `ldb-run` is rerun. Nothing is
  written to stdout, and the directory is created only when a file is written.
  If the file cannot be written (for example the directory is not writable),
  `ldb-run` logs the error with the failed statements and exits with code 1.

### Preparing grants and objects with `--mode PREPARE`

`--mode PREPARE` brings permissions and objects into place without running any
ILM work, so the problems show up before `SCRIPT`, `PREVIEW` or `EXECUTE`. It
is safe to rerun and meant to run first (`VALIDATE`, `PREPARE`, then the rest):

- With source admin credentials (`SOURCE_ILM`) it grants the source tables to
  the source role. Without them it writes `source_grants_<schema>_<ts>.sql` under
  `ddl_pending_dir` for a DBA, with only the privileges the runtime user does not
  receive yet through the role, and warns. Once the grants are in place no file
  is written.
- It checks, with the runtime connections alone, that the source and history
  roles are enabled for the runtime users and give them `SELECT`, `INSERT`,
  `UPDATE` and `DELETE` on every configured table. A role that is granted but
  not a default role gives nothing.
- It creates or alters the history tables and grants them to the history role,
  with the same admin or runtime-connection rules as `EXECUTE`: statements that
  fail go to the pending file.
- It exits with code 1 if the runtime user still cannot see a configured table
  (it does not exist or `SELECT` is not granted). Other modes only skip such a
  table with a warning.

`VALIDATE` runs the same privilege check without changing anything: a gap on
the action's own environment fails it, a gap on the other environment is a
warning, since `PREPARE` creates the history objects. `SCRIPT`, `PREVIEW` and
`EXECUTE` warn about the gaps they find.

`PREVIEW` and `EXECUTE` keep applying DDL and grants themselves, so a change in
source reaches history without failing the run.

### History DDL in `--mode SCRIPT`

`--mode SCRIPT` applies no DDL and no history grants to the databases. The ILM SQL blocks, which
run many times, go to stdout. The one-time statements go to files under
`ddl_pending_dir`, so they stay out of the script that is rerun:

- `history_ddl_<schema>_<YYYYMMDD_HHMMSS>.sql`: the history table DDL followed by
  the grants on the history tables for the history role. Run it against the
  history database.
- `source_grants_<schema>_<YYYYMMDD_HHMMSS>.sql` (`SOURCE_ILM` only): the grants
  on the source tables for the source role. Run it against the source database.

The runtime user reads the column metadata of the source tables through their
grants, so when source admin credentials exist `SCRIPT` applies the source
grants itself and writes no `source_grants` file. Without them the file is
written and `ldb-run` logs a warning. In every mode, a table the runtime user
cannot see (it does not exist, or `SELECT` is not granted) is skipped with a
warning instead of failing the run, and so is a table that references it.

Both files are plain SQL for SQL*Plus that stop at the first failing statement; the
grants are unconditional and safe to run again. The history DDL is always
emitted, computed like this:

- **A history connection works**: the admin connection is used when admin
  credentials exist, the runtime one otherwise. The current history structure is
  read, and the file holds only the missing changes.
- **Neither works or there are no credentials**: `history_baseline` states what to
  assume. `EMPTY` (default) assumes history has no objects, so every table, key
  and index is created. `SOURCE` assumes history holds the same objects as
  source, so only the history-specific differences are emitted (for example the
  `LDB_PROCESS_DATE` column and the matching primary key).

Without admin credentials, `ldb-run` applies the history grants right after the
DDL over the runtime connection. Any grant the runtime user cannot apply goes to
the same pending file as the failed DDL, so a DBA can run it.

### Configuration parameters reference

| Parameter                | Purpose                                                                                                                                                         | Default / values                                                                     |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `action`                 | ILM target (`SOURCE_ILM` moves/purges source, `HISTORY_ILM` cleans downstream history).                                                                         | Default `SOURCE_ILM`; choices `SOURCE_ILM`, `HISTORY_ILM`.                           |
| `mode`                   | Execution mode: `VALIDATE` config, credentials and role privileges (read-only), `PREPARE` apply grants and history objects without running ILM, `PLAN` dependency order, `PREVIEW` run the real queries and apply DDL and grants but do not insert or delete data (a.k.a. legacy `DRY_RUN`), `SCRIPT` emit the history DDL and the ILM SQL without applying anything, `EXECUTE` run changes. | Default `PREVIEW`; choices `VALIDATE`, `PLAN`, `PREVIEW`, `SCRIPT`, `EXECUTE`.       |
| `chunk_size`             | Rows per chunk when processing large tables.                                                                                                                    | Default `100000`.                                                                    |
| `use_added_columns`      | Populate derived columns in history tables.                                                                                                                     | Default `True` (boolean toggle).                                                     |
| `add_ldb_columns`        | Add LimitsDb execution-date columns in history tables.                                                                                                        | Default `True` (boolean toggle).                                                     |
| `parallel_max`           | Maximum number of parallel processes.                                                                                                                           | Default `10`.                                                                        |
| `ddl_pending_dir`        | Directory where `ldb-run` writes history DDL it could not apply without admin credentials, and the DDL and grants files of `--mode SCRIPT`.                                                                      | Default `ldb_ddl_pending`.                                                           |
| `history_baseline`       | State assumed for the history tables by `--mode SCRIPT` when it cannot read them over the runtime history connection: `EMPTY` (no objects) or `SOURCE` (same objects as source). | Default `EMPTY`; choices `EMPTY`, `SOURCE`. |
| `db_engine`              | Database engine.                                                                                                                                                | Default and only current choice: `"oracle"`.                                        |
| `log_level`              | Logging level.                                                                                                                                                  | Default `"INFO"`; choices `"DEBUG"`, `"INFO"`, `"WARNING"`, `"ERROR"`, `"CRITICAL"`. |
| `schema`                 | Configuration set name: identifies a system or application (e.g., `billing`). Not a database schema — it is the folder that groups all configuration files for one target. | Required on CLI; no persisted default. |
| `profile`                | Environment name (e.g., `dev`, `prod`). Differentiates configuration files for different environments within the same configuration set. | Optional; default `null`. |
| `ilm_config_file`        | YAML file with tables (bypass DB discovery).                                                                                                                    | Optional; default `null`.                                                            |
| `source_dsn`             | Source DSN / connection descriptor.                                                                                                                             | Default empty string.                                                                |
| `source_username`        | Source username, as named in the data dictionary: lowercase (`ldb`) is a quoted user created as `"ldb"`, uppercase (`LDB`) a regular one.                    |  | Default empty string.                                                                |
| `source_password`        | Source password stored in secrets.                                                                                                                              | Default empty string; encrypted in `secrets.<PROFILE>.json`.                         |
| `history_dsn`            | History DSN / connection descriptor.                                                                                                                            | Default empty string.                                                                |
| `history_username`       | History username, with the same case rules as `source_username`.                                                                                                || Default empty string.                                                                |
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
| `ddl_pending_dir`        | `--ddl-pending-dir`        | `LDB_DDL_PENDING_DIR`        | `config[.<PROFILE>].yml: ddl_pending_dir`          |
| `history_baseline`       | `--history-baseline`       | `LDB_HISTORY_BASELINE`       | `config[.<PROFILE>].yml: history_baseline`         |
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
# mode: "PREVIEW"       # VALIDATE | PREPARE | PLAN | PREVIEW | SCRIPT | EXECUTE
# chunk_size: 100000    # Rows per chunk when processing large tables
# use_added_columns: True  # Populate derived columns in history tables
# add_ldb_columns: True  # Add LimitsDb execution-date columns in history tables
# parallel_max: 10      # Maximum number of parallel processes
# ddl_pending_dir: "ldb_ddl_pending"  # Where failed history DDL is written for a DBA
# history_baseline: "EMPTY"  # SCRIPT without readable history: EMPTY | SOURCE
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
  Run `ldb-crypt --schema <SCHEMA> [--profile <PROFILE>]`. Or set the value to `""` until you’re ready.

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
