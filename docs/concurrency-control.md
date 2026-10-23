# Concurrency and state control

This document specifies how LimitsDb guarantees that a table is processed by at
most one worker at a time, how it records progress so an interrupted run can be
continued, and how `EXECUTE` and `PREVIEW` runs interact. It is written for
contributors and as the reference for trusting the tool with real data.

Each rule carries a marker:

- **[current]**: implemented in the code today.
- **[target]**: agreed design, not implemented yet. Only `PEND-026` (deferred) remains,
  in `docs/ai-state/PENDING.md`.

Where this document and the code disagree on a **[current]** rule, the code is
the bug or this document is stale; record the conflict in `PENDING.md`.

## 1. Modalities

The mechanism has two modalities. The guarantees of section 2 apply in full
only to the first.

| Modality | Used by | Lock table and `run_id` | Protection |
|---|---|---|---|
| Runner | `ldb-run` in `EXECUTE` and `PREVIEW` | Yes (sections 4 and 5) | Mutex per table, `ldb_ctl` row lock, `run_id` check |
| Lock-less | `SCRIPT` blocks run by hand (for example from SQL*Plus), in `EXECUTE` or `PREVIEW` | No | `ldb_ctl` row lock and the 5 s `TSTART` guard only, with the known gaps of section 9 |

A lock-less block must run without the runner and without the lock table: it
has no `run_id`, so the key check would reject it. The lock-less modality is the
behavior of the tool before this design and is kept unchanged.

## 2. Goals and guarantees

In the runner modality, for every source table `(owner, table_name)`:

1. At most one worker moves its rows at any time, even if two runners start
   simultaneously, a runner loses its database session, or a runner dies while
   its workers are alive.
2. A row is never inserted into history twice because two workers processed the
   same table concurrently.
3. After an interruption, an `EXECUTE` of the same `process_date` continues where
   the previous one stopped; finished tables stay finished and the rows already
   processed are kept.
4. A worker that is not the owner of a table never overwrites the control state
   of the run that is.
5. Every abandoned run is closed in `ldb_log` with its processed rows.

Non-goals: protecting a block that an operator edits and runs by hand beyond the
row lock and the time guard of section 6.4; bounding the rollback cost of a long
single-statement LOB table; surviving a network cut during a long statement
before the next chunk boundary.

## 3. Data model

| Object | Status | Purpose |
|---|---|---|
| `ldb_ctl` | [current] | One row per `(ctl_owner, ctl_table_name)`: `ctl_status`, `ctl_process_date`, `ctl_process_start`, `ctl_process_end`, `ctl_rows_processed`, `ctl_action`, `ctl_plsql`, `ctl_run_id`, and `ctl_db_session_id` (the session of the worker, composed by the engine, written at `TSTART`; Oracle: `instance.sid.audsid`). Its row lock means "a worker is working on this table". |
| `ldb_log` | [current] | Append-only history: one entry per status change and per chunk (it has no run id: STATUS groups it by time). |
| Lock row per table | [current] | One row per `(owner, table_name)` that a runner locks for its whole run. It is never updated. |
| Key row per table | [current] | `run_id` (uuid of the runner invocation), mode, host, pid, start time. Written by the runner after it holds the lock; read by workers. |

The lock and key rows are keyed per table, not per runner, because runners over
different schemas or table sets may run at the same time.

### 3.1 Status values

| Status | Meaning | Mode |
|---|---|---|
| `TSTART` | Table processing started | EXECUTE [current] |
| `CSTART` / `CEND` | Chunk started / ended | EXECUTE [current] |
| `LSTART` / `LEND` | Long single statement (LOB `SOURCE_ILM` path) started / ended | EXECUTE [current] |
| `TEND` | Table finished | EXECUTE [current] |
| `ERROR` | Table failed | EXECUTE [current] |
| `SKIPPED` | In-memory only; never persisted | EXECUTE [current] |
| `PTSTART`, `PCSTART`, `PCEND`, `PLSTART`, `PLEND`, `PTEND`, `PERROR` | The EXECUTE statuses prefixed with `P` | PREVIEW [current] |

PREVIEW and EXECUTE statuses are disjoint [current]. Terminal statuses are `TEND`
and `PTEND`.

`ldb-run --mode STATUS` only reads these tables. It rebuilds the executions from `ldb_log` by time (a table's rows are split at each `TSTART`; table runs that
start within 5 minutes of the previous one's last activity, and do not repeat a table, form one execution),
ignores every run that wrote a PREVIEW status, and takes a table as being processed while its `ldb_ctl` row still carries
the run's id in `TSTART`, `CSTART`, `CEND`, `LSTART` or `LEND` and the registered session (instance +
`sid` + `audsid` in `gv$session`) is alive. Liveness is proven first by trying the table's `'L'` row of
`ldb_run_lock` with `FOR UPDATE NOWAIT` and rolling back immediately: `ORA-00054` means a live runner, an obtained
lock means none (the probe holds nothing). SCRIPT runs and tables without that row fall back to `gv$session`, then to the heartbeat. A table whose last log row is `ERROR` with the `-20003`
message ("already processed") is neither an error nor unfinished work.

### 3.2 Error codes

| Code | Raised by | Meaning | Writes `ldb_ctl`? |
|---|---|---|---|
| `-20001` | `check_save_status` | Row locked by another session (`ORA-00054`) | No |
| `-20002` | `check_save_status` | `TSTART` within 5 s of an unfinished run's start | No |
| `-20003` | `check_save_status` | Table already processed for this date | No |
| `-20004` | `check_referencing_tables` | A referencing table is not finished for this date | Yes (`ERROR`) |
| `-20005` [current] | `check_save_status` | Run ownership lost: the worker's `run_id` is no longer current | No |
| `-20006` [current] | `check_save_status` | PREVIEW refused: an EXECUTE exists for this `process_date` | No |

A loser (`-20001/2/3/5/6`) leaves `ldb_ctl` untouched in `check_save_status`, in
the block `exception` handler and in `process_table`, because the row belongs to
another run.

## 4. Layer 1: runner start [current]

The runner acquires the right to process its tables before launching any worker.

1. **Anchor rows.** For each configured table, `INSERT … WHERE NOT EXISTS` into
   the lock table with its own commit; `ORA-00001` from a concurrent insert is
   ignored. Existing rows, locked or not, are never modified here. Each anchor is its own transaction, so a lost race
   never undoes the anchors committed before it; a lock query that returns no row stops the run.
2. **Mutex.** On a dedicated connection that never commits, rolls back or runs
   DDL while the run lasts, for each table in sorted order:
   `SELECT … FOR UPDATE NOWAIT` on its lock row. `ORA-00054` means another runner
   is alive on that table: roll back, log which runner holds it (from the key
   row) and exit `1`. The winner of a simultaneous start is decided only by this
   `NOWAIT`.
3. **Key.** With the locks held, a short separate connection writes the key row
   (`run_id`, mode, host, pid, start) and commits. Only a lock holder writes it,
   so no second lock round is needed.
4. **Launch** the workers.
5. **Liveness.** The runner periodically runs a trivial query on the mutex
   connection to avoid idle-session and network-idle disconnects. If it fails,
   the runner stops launching tables and exits `1`; running workers detect the
   loss through section 5.

The mutex connection is separate from every worker connection. Worker commits
therefore never release the mutex.

The mutex is mode-agnostic: `EXECUTE` and `PREVIEW` runners exclude each other on
shared tables exactly as two `EXECUTE` runners do.

## 5. Layer 2: worker block

The generated PL/SQL block runs in one session per table and calls
`check_save_status` at each step. `check_save_status` takes
`SELECT … FOR UPDATE NOWAIT` on the table's `ldb_ctl` row, updates it and
inserts into `ldb_log`. The row lock is held until the next `commit`.

| Step | Action |
|---|---|
| `TSTART` | Lock the row. Reject with `-20001` (locked), `-20002` (`status` not terminal and `(SYSDATE - ctl_process_start) * 86400 < 5`), `-20003` (`TEND` for this date). [current] Verify `run_id` ( [current]). Then `check_referencing_tables` (`-20004`) and `commit`. |
| `CSTART` | Lock the row; with it locked, verify `run_id` ( [current]); any non-null sqlcode raises ( [current]; today the result is ignored). Then `fetch`. |
| Chunk work | Insert into history (`SOURCE_ILM`), delete by `rowid`. All under the row lock. |
| `CEND` | Update counters, `commit`. A non-null sqlcode raises ( [current]). |
| LOB path | `LSTART` locks the row without committing, the single statement runs under that lock, `LEND` and `TEND` close the transaction. [current] |
| `TEND` | Update, `commit`. |
| Error | `rollback`; `check_save_status(ERROR)` unless the sqlcode is `-20001/2/3/5/6`; `commit`; re-raise (except `-20003`). |

In the lock-less modality the block keeps the `ldb_ctl` row lock and the `TSTART`
guards but carries no `run_id`, skips the key check, and does not use the lock
table. [current] changes to the block that do not depend on the lock table or the
`run_id` (raising on a failed `CSTART`/`CEND`, and `LSTART`/`LEND` in the LOB
path) apply to both modalities.

### 5.1 Why the chunk gap is safe [current]

After the `commit` that ends a chunk the row is free until the next `CSTART`. A
worker of another run can take it in that gap. The previous worker is stopped
before it processes anything else:

- if the new worker holds the row at the previous worker's next `CSTART`, the
  `NOWAIT` fails with `-20001` and the block raises;
- if the row is free, the previous worker locks it, finds its `run_id`
  overwritten and raises `-20005`.

Overlap is zero chunks. The new worker never sees rows already deleted, because
its cursor opens after the `commit` that released the row, and Oracle fixes a
cursor's snapshot at `OPEN`.

## 6. Layer 3: orchestrator, states and process dates

### 6.1 Orchestrator [current]

`ldb_exec_ilm` keeps a pool of at most `parallel_max` worker processes. A table
is launched when it is not `skip`, not already finished or skipped, not active,
and all its referencing tables are `TEND` or `PTEND` in memory. When a worker
returns, its status is recorded in memory; an `ERROR` without row progress
becomes `SKIPPED` and is not retried in that run. The run exits `0` only if
`ldb_ctl` shows every non-skipped table in `TEND`.

### 6.2 `process_date`

The runner takes `process_date` from the database `SYSDATE`. It cannot be set
from the CLI or the environment; only a `SCRIPT` block receives it through
`&ldb_process_date` (renamed from `&process_date`, `PEND-027`).

### 6.3 Continuation and restart rules [current]

These rules are enforced in PL/SQL, in `check_save_status` at `TSTART`, so they
hold identically for the runner and for a lock-less `SCRIPT` block, which has no
Python around it. The block receives the mode as a parameter (`&ldb_mode` in a
script) and derives the status prefix from it (`P` for `PREVIEW`); the mode of
the new run is therefore known to `check_save_status` from the status it
receives (`TSTART` or `PTSTART`). Python only reports: it reads the previous
rows before launching a worker to print the resumed-process message and to keep
the "`ERROR` without progress becomes `SKIPPED`" rule.

| Previous state of the table | New run | Result |
|---|---|---|
| Incomplete EXECUTE, same `process_date` | EXECUTE | Continuation: `TEND` tables stay finished, `ctl_rows_processed` is kept, the runner reports that the process was resumed and the rows processed previously. |
| Incomplete EXECUTE, other `process_date` | EXECUTE or PREVIEW | Not a continuation: the abandoned date is closed and the row restarts with the new date and counter 0. |
| Incomplete PREVIEW, any date | EXECUTE or PREVIEW | Not a continuation (a PREVIEW deletes nothing): the abandoned run is closed and the row restarts. |
| Any EXECUTE status, same `process_date` | PREVIEW | Refused with `-20006`. |
| Running PREVIEW or EXECUTE | any | Refused by the mutex (section 4) and by the row lock. |

The rule does not depend on which date is greater.

Closing an abandoned run writes one `ldb_log` entry before the row is
overwritten: status `ERROR` (abandoned EXECUTE) or `PERROR` (abandoned PREVIEW),
`log_process_end` equal to the last `CEND` timestamp, the rows processed so far,
and the message "Incomplete process closed by a later process".

Resuming never skips a guard. A restart within 5 s of a crash gets `-20002`.

Because a continuation keeps `ctl_rows_processed`, the runner must read the value
before launching a worker and subtract it to report the rows of the current run,
and to keep the rule that an `ERROR` without progress becomes `SKIPPED`.

### 6.4 Time guard

The 5 s guard of `TSTART` applies to in-progress statuses only (an `ERROR` can be
retried at once) and counts from the last chunk end: `CEND` refreshes
`ctl_process_end`, which acts as a heartbeat, so a live run is never older than 5 s
at a chunk boundary [current] (`PEND-026`).

Every run is identified by `ldb_ctl.ctl_run_id`, written at `TSTART`. A runner uses
its uuid, checked also against the lock table; a SCRIPT generates `'S' ||
rawtohex(sys_guid())` and has no lock row. `CSTART`, `LSTART` and `ERROR` of a
run whose id is no longer the row's raise `-20005` [current].

## 7. Invariants

1. Table data is only modified while the worker holds the table's `ldb_ctl` row
   lock; the `commit` of a chunk releases it.
2. A chunk starts only if, with the row locked, the worker's `run_id` is the
   current one [current].
3. Two runners cannot hold the lock row of the same table; the key is written
   only by the lock holder [current].
4. A worker that lost ownership writes nothing to `ldb_ctl` [current].
5. `ctl_status` of a finished table for a date is never changed by a run of the
   same date (`-20003`).

## 8. Failure scenarios

| Failure | Behavior |
|---|---|
| Two runners start at once on the same table | One wins the `NOWAIT`; the other exits `1` [current]. |
| Runners on disjoint tables | Run in parallel. |
| Runner loses its database session, workers alive | Another runner can take the lock and overwrite the key; old workers stop at their next chunk start with `-20005`. Overlap is zero chunks [current]. |
| Runner killed, workers alive | Same as above. |
| Worker killed | Its session ends and the row lock is released; the state stays non-terminal and can be continued. |
| Network cut during a long chunk | The worker continues until its next chunk boundary. |
| LOB single statement fails | The whole statement is rolled back; `LSTART` kept the table exclusive meanwhile [current]. |
| Compile error, missing procedure, lost connection | Not recorded by the block; `process_table` records the `ERROR` only if `ldb_ctl` has no `ERROR` of this run yet (`PEND-022`). |
| Referencing table not finished | `-20004`, `ERROR` persisted, `SKIPPED` in memory, run exits `1`. |

## 9. Known limits

- Blocks run by hand (`SCRIPT`, lock-less modality) have no mutex: two of them,
  or one of them and a runner, are separated by the `ldb_ctl` row lock, the
  heartbeat guard and `ctl_run_id`. A SCRIPT that stays idle for more than 5 s
  between chunks (a stopped session) can be taken over by a later start; it then
  stops with `-20005` at its next chunk.
- A loss of ownership is detected only at chunk starts; the LOB path has none
  after `LSTART`, which holds the row lock instead.
- Clock regressions on the database can produce a smaller `process_date` than an
  abandoned one; the general rule of section 6.3 applies.
- The rollback of a long single-statement LOB table undoes all its work.
