# TerminusDB

Configuration-driven ILM (Information Lifecycle Management) runner for operational databases. It applies retention policies to your application tables—archiving, purging, and keeping datasets lean—so source stays fast while history remains auditable. TerminusDB is designed to support multiple database engines; **Oracle** is supported today, with an engine-agnostic ILM model that can extend to others.

## Overview (What it does)

- **Executes ILM policies on real schemas.** You describe which tables are in scope, how they relate (parents/children), and the rules that decide when rows move to history or are purged.
- **Targets source and history environments.** Choose whether to act on **source** (move/purge) or **history** (downstream cleanup) for the same ILM model.
- **Retains exactly what you need.** Per-table, per-condition retention windows (e.g., “keep 3 months in source, 11 months in history”), with the ability to define multiple conditions per table.
- **Understands relationships.** Reference parent tables, define join expressions, and optionally purge **orphans** when child rows lose their parent.
- **Handles heavy tables safely.** Chunked processing and parallelism keep large operations predictable and observable.
- **Works with hints and special cases.** Add optimizer hints, mark LOB-heavy tables, and tune behavior per table.
- **Dry-run anytime.** Generate the exact operations without executing them, to review and approve planned changes.
- **Keeps secrets safe.** Database credentials are stored encrypted and transparently decrypted at runtime.

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

All Python dependencies are defined in `pyproject.toml` and installed by Poetry.

## Install (Poetry)

```bash
unzip terminusdb.zip
cd TerminusDB
poetry install
```

## CLI Commands

After `poetry install`, the following executables are available:

- `tdb-run` — main ILM runner
- `tdb-init` — scaffold per-schema configuration files
- `tdb-crypt` — encrypt cleartext secrets in place

From shell:

```bash
poetry run tdb-run --help
poetry run tdb-init --help
poetry run tdb-crypt --help
```

## Initialize Configuration

Create the schema/profile structure and template files:

```bash
poetry run tdb-init --schema <SCHEMA> --profile <PROFILE>
# examples:
# poetry run tdb-init --schema billing --profile prod
# poetry run tdb-init --schema billing --profile dev --config-dir /etc/terminusdb
```

Generated files (default root is `~/.config/TerminusDB`):

```
~/.config/TerminusDB/
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
- `--config-dir` — scaffold under a custom root instead of `~/.config/TerminusDB`

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
poetry run tdb-crypt --schema <SCHEMA> --profile <PROFILE>
```

TerminusDB derives the list of secrets from the configuration model. Currently, `secret_keys_from_config()` produces the following keys, which must live in `secrets.<PROFILE>.json` and are always stored encrypted:

- `source_password`
- `history_password`
- `admin_source_password`
- `admin_history_password`

The tool ensures an encryption key exists locally and replaces any non-empty plaintext with `enc:v1:aes256gcm:...`.
If you forget this step, the main loader will reject plaintext secrets by default.

Note: the local key is created in your user context and must be protected by OS permissions.

## Run ILM

```bash
poetry run tdb-run --schema <SCHEMA> --profile <PROFILE> --action SOURCE_ILM
# or
poetry run tdb-run --schema <SCHEMA> --profile <PROFILE> --action HISTORY_ILM
```

Common overrides:

```
--mode ALL|QUERY_ONLY                         # Run everything or only generate queries (dry-run)
--generate-script / --no-generate-script      # Emit SQL to disk instead of executing it
--chunk-size <int>                            # Rows per chunk when processing large tables
--parallel-max <int>                          # Maximum number of tables processed in parallel
--use-added-columns / --no-use-added-columns  # Toggle helper columns in history tables
--add-tdb-columns / --no-add-tdb-columns      # Toggle ILM execution timestamp columns
--log-level DEBUG|INFO|WARNING|ERROR|CRITICAL # Adjust logger verbosity
--ilm-config-file <path>                      # Point to a specific ILM YAML file
--config-dir <root>                           # Override config root discovery
--config-file <yaml>                          # Extra overlay (highest priority)
--set key=value                               # Generic override (supports dotted keys)
```

Examples:

```bash
# Dry-run with verbose logging
poetry run tdb-run --schema billing --profile prod --action SOURCE_ILM --generate-script --log-level DEBUG

# Apply quick overrides without editing files
poetry run tdb-run --schema billing --profile prod --action SOURCE_ILM --set chunk_size=200000 --set parallel_max=8
```

### Configuration parameters reference

| Parameter | Purpose | Default / values |
| --- | --- | --- |
| `action` | ILM target (`SOURCE_ILM` moves/purges source, `HISTORY_ILM` cleans downstream history). | Default `SOURCE_ILM`; choices `SOURCE_ILM`, `HISTORY_ILM`. |
| `mode` | Run everything or only generate queries (dry-run). | Default `ALL`; choices `ALL`, `QUERY_ONLY`. |
| `chunk_size` | Rows per chunk when processing large tables. | Default `100000`. |
| `use_added_columns` | Populate derived columns in history tables. | Default `True` (boolean toggle). |
| `add_tdb_columns` | Add TerminusDB execution-date columns in history tables. | Default `True` (boolean toggle). |
| `generate_script` | Dry-run: generate SQL script without executing. | Default `False` (boolean toggle). |
| `parallel_max` | Maximum number of parallel processes. | Default `10`. |
| `db_engine` | Database engine. | Default `"oracle"`; choices `"oracle"`, `"postgres"`. |
| `log_level` | Logging level. | Default `"INFO"`; choices `"DEBUG"`, `"INFO"`, `"WARNING"`, `"ERROR"`, `"CRITICAL"`. |
| `schema` | Schema name (folder under `schemas/`). | Required on CLI; no persisted default. |
| `profile` | Profile name (e.g., `dev`, `prod`). | Optional; default `null`. |
| `ilm_config_file` | YAML file with tables (bypass DB discovery). | Optional; default `null`. |
| `source_dsn` | Source DSN / connection descriptor. | Default empty string. |
| `source_username` | Source username. | Default empty string. |
| `source_password` | Source password stored in secrets. | Default empty string; encrypted in `secrets.<PROFILE>.json`. |
| `history_dsn` | History DSN / connection descriptor. | Default empty string. |
| `history_username` | History username. | Default empty string. |
| `history_password` | History password stored in secrets. | Default empty string; encrypted in `secrets.<PROFILE>.json`. |
| `admin_source_dsn` | Admin source DSN / connection descriptor. | Default empty string. |
| `admin_source_username` | Admin source username. | Default empty string. |
| `admin_source_password` | Admin source password stored in secrets. | Default empty string; encrypted in `secrets.<PROFILE>.json`. |
| `admin_history_dsn` | Admin history DSN / connection descriptor. | Default empty string. |
| `admin_history_username` | Admin history username. | Default empty string. |
| `admin_history_password` | Admin history password stored in secrets. | Default empty string; encrypted in `secrets.<PROFILE>.json`. |

#### Overrides matrix (CLI, env, YAML)

| Parameter | CLI flag | Environment variable | YAML key |
| --- | --- | --- | --- |
| `action` | `--action` | `TDB_ACTION` | `config.<PROFILE>.yml: action` |
| `mode` | `--mode` | `TDB_MODE` | `config.<PROFILE>.yml: mode` |
| `chunk_size` | `--chunk-size` | `TDB_CHUNK_SIZE` | `config.<PROFILE>.yml: chunk_size` |
| `use_added_columns` | `--use-added-columns` | `TDB_USE_ADDED_COLS` | `config.<PROFILE>.yml: use_added_columns` |
| `add_tdb_columns` | `--add-tdb-columns` | `TDB_ADD_TDB_COLUMNS` | `config.<PROFILE>.yml: add_tdb_columns` |
| `generate_script` | `--generate-script` | `TDB_GENERATE_SCRIPT` | `config.<PROFILE>.yml: generate_script` |
| `parallel_max` | `--parallel-max` | `TDB_PARALLEL_MAX` | `config.<PROFILE>.yml: parallel_max` |
| `db_engine` | `--db-engine` | `TDB_DB_ENGINE` | `config.<PROFILE>.yml: db_engine` |
| `log_level` | `--log-level` | `TDB_LOG_LEVEL` | `config.<PROFILE>.yml: log_level` |
| `schema` | `--schema` | `TDB_SCHEMA` | CLI only (not stored). |
| `profile` | `--profile` | `TDB_PROFILE` | CLI only (not stored). |
| `ilm_config_file` | `--ilm-config-file` | `ILM_CONFIG_FILE` | CLI only (not stored). |
| `source_dsn` | `--source-dsn` | `TDB_SOURCE_DSN` | `config.<PROFILE>.yml: source_dsn` |
| `source_username` | `--source-username` | `TDB_SOURCE_USERNAME` | `config.<PROFILE>.yml: source_username` |
| `source_password` | — | — | `secrets.<PROFILE>.json: source_password` |
| `history_dsn` | `--history-dsn` | `TDB_HISTORY_DSN` | `config.<PROFILE>.yml: history_dsn` |
| `history_username` | `--history-username` | `TDB_HISTORY_USERNAME` | `config.<PROFILE>.yml: history_username` |
| `history_password` | — | — | `secrets.<PROFILE>.json: history_password` |
| `admin_source_dsn` | `--admin-source-dsn` | `TDB_ADMIN_SOURCE_DSN` | `config.<PROFILE>.yml: admin_source_dsn` |
| `admin_source_username` | `--admin-source-username` | `TDB_ADMIN_SOURCE_USERNAME` | `config.<PROFILE>.yml: admin_source_username` |
| `admin_source_password` | — | — | `secrets.<PROFILE>.json: admin_source_password` |
| `admin_history_dsn` | `--admin-history-dsn` | `TDB_ADMIN_HISTORY_DSN` | `config.<PROFILE>.yml: admin_history_dsn` |
| `admin_history_username` | `--admin-history-username` | `TDB_ADMIN_HISTORY_USERNAME` | `config.<PROFILE>.yml: admin_history_username` |
| `admin_history_password` | — | — | `secrets.<PROFILE>.json: admin_history_password` |

Builder-only flags (`--config-dir`, `--config-file`, `--set`) control how overlays are discovered and do not map to configuration keys.

## ILM Rule Semantics

Each entry in `ilm.<PROFILE>.yml` merges table-level attributes with one or more rule conditions defined under `conds`. When `conds` is omitted, TerminusDB assumes a single active rule so the table remains eligible for processing.

- **Activation:** A condition runs only when `is_active: true`. Deactivating the sole condition for a table effectively removes that table from the run.
- **Retention windows:** `retain_months_source` is required whenever `purge_date_expr` is present. `retain_months_history` extends the history window but also depends on defining the source retention. During `HISTORY_ILM` runs, the engine sums source and history months to determine the cut-off date.
- **Date expressions:** `purge_date_expr` identifies the date column (or expression) that anchors retention. Prefix column references with `@` (for example, `"@DSP_DATE"`); the runner swaps the prefix for the proper table alias at execution time.
- **Additional filters:** Add optional filters through `additional_filter_expr` (source runs) and `history_addtl_filter_expr` (history runs). Both accept the same `@column` syntax, and the history expression falls back to the source expression when omitted.
- **Referencing tables:** Use `referencing_tables` to pull parent conditions into child tables. List entries as `<TABLE> <ALIAS>` (optionally `<OWNER>.<TABLE> <ALIAS>`) and supply matching `join_expr` fragments. The runner inherits active conditions from each referenced table and rewrites the join fragments—`@` becomes `inner` joins by default or `left outer` joins when `source_orphan_purge: true` to find orphans.
- **Orphan handling:** Enable `source_orphan_purge` with `orphan_check_column` to delete child rows whose parents no longer qualify. The engine also materializes helper columns (e.g., `tdb_date_<alias>`) when `use_added_columns` is enabled so history cleanups can reference parent timestamps.

## Configuration Resolution (Overlay Order)

Lowest → highest priority:

1. System root: `/etc/terminusdb`
2. User root: `~/.config/TerminusDB`
3. `--config-file` (extra overlay)
4. Environment variables (`TDB_*`)
5. `--<param> value` or `--set key=value`

After overlays, secrets from `secrets.<PROFILE>.json` are loaded and decrypted. If any secret is plaintext and enforcement is enabled (default), the loader raises an error.

Tip: you can redirect both “system” and “user” roots to the same place with `--config-dir`.

## Configuration Files

### `config.<PROFILE>.yml` (commented defaults + help)

Generated from the Config model (single source of truth).
Every key is commented; the default value and short help appear inline. Example:

```yaml
# action: "SOURCE_ILM"  # ILM target: months_keep_history_max (SOURCE_ILM) or history (HISTORY_ILM)
# mode: "ALL"           # Run everything (ALL) or only generate queries (QUERY_ONLY)
# chunk_size: 100000    # Rows per chunk when processing large tables
# use_added_columns: True  # Populate derived columns in history tables
# add_tdb_columns: True  # Add TerminusDB execution-date columns in history tables
# generate_script: False  # Dry-run: generate SQL script without executing
# parallel_max: 10      # Maximum number of parallel processes
# db_engine: "oracle"   # Database engine
# log_level: "INFO"     # Logging level
```

Uncomment and set values as needed. Secrets are not listed here (they live in `secrets.<PROFILE>.json`).

### `ilm.<PROFILE>.yml`

Empty list scaffolded by `tdb-init`. You define your ILM tables here.

### `ilm.<PROFILE>.example.yml`

Comprehensive, commented example shipped in the package and copied verbatim by `tdb-init`.

### `secrets.<PROFILE>.json`

Holds only secret fields (empty by default). Must be encrypted (run `tdb-crypt`) before the main runner will accept them.

## Environment Variables

Any variable prefixed with `TDB_` becomes a config override (underscores become dots). Examples:

- `TDB_PARALLEL_MAX=8` → `parallel_max: 8`
- `TDB_LOG_LEVEL=DEBUG` → `log_level: "DEBUG"`

Booleans accept `true/false`, `1/0`, `on/off` (case-insensitive).

## Development

Format, lint, type-check:

```bash
poetry run black .
poetry run flake8
poetry run mypy .
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
  Run `poetry run tdb-crypt --schema <SCHEMA> --profile <PROFILE>`. Or set the value to `""` until you’re ready.

- Oracle thick mode required  
  Install Oracle Instant Client and configure library paths (`PATH` on Windows, `LD_LIBRARY_PATH` on Linux/macOS).

- Configuration not found  
  Use `tdb-init` to scaffold files or pass `--config-dir` to point at the correct root.

- Unexpected key in config  
  The loader is strict—unknown keys raise an error. Fix typos or remove unused entries.

## License

Proprietary. All rights reserved.
