# Configuration contract and typed-model migration

This document characterizes the current configuration contract and defines an
incremental internal migration. It does not introduce a new public YAML shape,
change overlay precedence, or remove the database-backed ILM fallback.

## Current public contract

Runtime configuration is a flat mapping. `Config.from_dict()` rejects unknown
top-level keys and `Config.to_dict()` returns the same flat shape. In
particular, `chunk_size` is a global execution parameter, not an ILM-table
attribute or a database column:

```text
LDB_CHUNK_SIZE -> chunk_size -> l_chunk_size
```

The effective value order, from lowest to highest priority, is:

1. system `config.<PROFILE>.yml`;
2. user `config.<PROFILE>.yml`;
3. `--config-file` overlay;
4. `LDB_*` environment variables;
5. explicit CLI flags and `--set`;
6. decrypted values from `secrets.<PROFILE>.json` for fields marked secret.

A single underscore in an environment name belongs to the flat Python key
(`LDB_CHUNK_SIZE` becomes `chunk_size`). A double underscore is the reserved
separator for a nested override (`LDB_GROUP__VALUE` becomes `group.value`).

The ILM policy input remains independent from runtime configuration. An
explicit ILM YAML file is normalized to row mappings. Without that override,
the runner may obtain ILM rows from the configured database path. Changing or
removing that fallback is outside this migration.

## Internal model

`Config` remains the compatibility facade and aggregate. It exposes immutable
typed views while the flat public fields and serialization remain unchanged:

- `ExecutionConfig`: action, mode, chunk size, parallelism, logging, script
  generation, and history-column behavior;
- `DatabaseEndpoint`: DSN and application credentials for one environment;
- `ConnectionConfig`: source and history `DatabaseEndpoint` values plus the
  selected engine;
- `AdminEndpoint`: administrative credentials and default tablespace for one
  environment;
- `AdministrationConfig`: source/history admin endpoints, role names, and
  database-link names;
- `RuntimeContext`: schema, profile, and optional ILM file location;
- `Config`: compatibility aggregate of the preceding views.

Secrets remain values of the relevant endpoint contracts; metadata identifying
secret fields remains centralized so template generation, masking, encryption,
and loading cannot develop separate lists.

ILM parsing exposes normalized `IlmRule` records. The runner uses
`ProcessedTableConfig` for the mutable state derived from those rules, while
engine methods accept read-only `Mapping` contracts. Runtime values remain
dictionaries for compatibility; their permitted keys and value types are now
checked statically. Engine-specific SQL, identifiers, and Oracle types do not
enter the normalized rule contract.

## Implemented compatibility sequence

1. Characterize the existing flat keys, validation, aliases, overlay order,
   secret priority, and ILM normalization with tests.
2. Add typed contracts and views while keeping CLI, YAML, environment names,
   defaults, and `Config.to_dict()` unchanged.
3. Move CLI, core runner, credential selection, and Oracle consumers to the
   typed views.
4. Type normalized ILM rules and mutable processing state while retaining
   mapping-compatible engine boundaries.

Any nested public YAML format remains outside this work and would require a
separate compatibility decision, migration tests, README update, and changelog
entry.

## Required invariants

- `DRY_RUN` continues to normalize to `PREVIEW`.
- `SCRIPT` continues to enable script generation.
- `PLAN` remains usable without database credentials.
- Connection validation remains action- and mode-sensitive.
- Unknown public configuration keys continue to fail closed.
- Secret values never become CLI flags or appear unmasked in logs.
- No migration step executes SQL or connects to a real database.
