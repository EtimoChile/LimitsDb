# Exception handling

LimitsDb translates failures at subsystem boundaries into the hierarchy in
`limitsdb/core/ldb_errors.py`:

- `ConfigurationError`: configuration input cannot be read or interpreted.
- `SecretError`: secret files, master keys, or decryption fail.
- `DatabaseConnectionError`: a database connection cannot be established.
- `ValidationError`: configuration or runtime state violates the domain contract.
- `ExecutionError`: ILM, status persistence, or administrative work fails.

Translated errors use exception chaining so callers can inspect `__cause__`.
Messages identify the operation and file or table where useful, but never
include secret values.

## Broad-catch inventory

The 25 broad catches present at the start of `PEND-003` were classified as
follows. This inventory records why a broad catch remains or how it was removed;
it is not permission to add new broad catches without an equivalent boundary.

| Area | Original count | Classification and disposition |
|---|---:|---|
| `ldb_crypto.py` | 3 | One unsafe permission silence now raises `SecretError`; two base64 fallbacks catch only expected decoding errors. |
| `ldb_utils.py` | 2 | Unsafe malformed-JSON silences removed; both paths raise `SecretError` and preserve the file. |
| `cli/ldb_run.py`, `cli/ldb_impl.py` | 2 | Unsafe auto-encryption "log and continue" catches removed; secret failures now stop before execution or administration. |
| `ldb_runner.py` | 4 | Connection probing and VALIDATE convert failures into explicit unsuccessful outcomes; worker execution and recovery persistence are process boundaries that log evidence and return a typed error result. |
| `oracle/ldb_engine_impl.py` | 14 | Connection, configuration, query, status, and DDL boundaries translate and chain domain errors. Transactional DDL/admin boundaries roll back before raising. Connection close is the sole best-effort cleanup boundary and logs the failure. |

Worker scheduling also guards `Future.result()`: process-pool, serialization,
and unexpected child failures become a table-level error result instead of
terminating the coordinator without identifying the affected table.
