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

Note: profile parameter is optional, if omitted the .\<PROFILE\> filenames part is omited also

Options:

- `--overwrite` — replace existing files
- `--no-examples` — skip copying the ILM example
- `--config-dir` — scaffold under a custom root instead of `~/.config/TerminusDB`

## Secrets & Encryption

1. Edit `secrets.<PROFILE>.json` and set plaintext credentials, e.g.:

```json
{
  "source_credentials": "tdb/etm1tdb@host:1521/service",
  "history_credentials": "",
  "source_admin_credentials": "system/etm1alpha@host:1521/service",
  "history_admin_credentials": ""
}
```

2. Encrypt in place:

```bash
poetry run tdb-crypt --schema <SCHEMA> --profile <PROFILE>
```

The tool ensures an encryption key exists locally and replaces any non-empty plaintext with `enc:v1:aes256gcm:...`.
If you forget this step, the main loader will reject plaintext secrets by default.

Note: the local key is created in your user context and must be protected by OS permissions.

## Run ILM

```bash
poetry run tdb --schema <SCHEMA> --profile <PROFILE> --action SOURCE_ILM
# or
poetry run tdb --schema <SCHEMA> --profile <PROFILE> --action HISTORY_ILM
```

Common overrides:

```
--mode ALL|QUERY_ONLY                         # To control actual ILM execution: ALL: do ILM, QUERY_ONLY: do a dry run
--parallel-max <int>                          # Sets the grade of parallel tables to process
--chunk-size <int>                            # Sets how many rows to process in one commit chunck
--use-added-cols / --no-use-added-cols        # If to add columns to history env to independize cleanup
--add-tdb-columns / --no-add-tdb-columns      # If to add columns to register ilm process date and transfer time
--print-process / --no-print-process          # To print procedure for executing IML outside TerminusDB control
--log-level DEBUG|INFO|WARNING|ERROR|CRITICAL # Set logging LEVEL
--tdb-config-file <path>                      # bypass DB discovery from tdb_config table
--config-dir <root>                           # override config root discovery
--config-file <yaml>                          # extra overlay (highest priority)
--set key=value                               # generic override (supports dotted keys)
```

Examples:

```bash
# Dry-run with verbose logging
poetry run tdb --schema billing --profile prod --action SOURCE_ILM --print-process --log-level DEBUG

# Apply quick overrides without editing files
poetry run tdb --schema billing --profile prod --action SOURCE_ILM --set chunk_size=200000 --set parallel_max=8
```

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
# action: "SOURCE_ILM"  # ILM target: source (SOURCE_ILM) or history (HISTORY_ILM)
# mode: "ALL"           # Run everything (ALL) or only generate queries (QUERY_ONLY)
# chunk_size: 100000    # Rows per chunk when processing large tables
# use_added_cols: true  # Populate derived columns in history tables
# add_tdb_columns: true # Add TerminusDB execution-date columns in history tables
# print_process: false  # Dry-run: generate SQL script without executing
# parallel_max: 10      # Maximum number of parallel processes
# db_engine: "oracle"   # Database engine (choices: 'oracle', 'postgres')
# log_level: "INFO"     # Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
# tdb_config_file: null # YAML file with tables (bypass DB discovery)
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
