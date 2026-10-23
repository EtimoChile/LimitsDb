"""State and statistics of ILM executions for ``ldb-run --mode STATUS``.

The engine provides raw facts (``TableRunRecord``, ``SessionActivity``); this module decides the state of each
execution and formats the report, so the rules are the same for every engine.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from operator import attrgetter
from typing import Any

from limitsdb.core.ldb_errors import ExecutionError
from limitsdb.core.ldb_logger import get_logger
from limitsdb.core.ldb_status import Status
from limitsdb.db.ldb_engines import (
    ControlEntry,
    DatabaseEngine,
    LogEntry,
    MonitoringAccess,
    SessionActivity,
    TableRunRecord,
    WaitItem,
)

logger = get_logger("status_report")

RUNNING = "RUNNING"
OK = "OK"
ERRORS = "ERRORS"
PARTIAL = "PARTIAL"

WAIT_COVERAGE = 0.95  # the wait profile lists items until they cover this share of the total time
MIN_WAIT_SECONDS = 5.0  # a current wait is reported only after this long
STALE_SECONDS = 600.0  # without a verifiable session, a table silent for longer than this is not running
RUN_GAP_SECONDS = 300.0  # tables of one execution start within this time of the previous table's last activity
LOG_SCAN_ROWS = 50000  # most recent log rows read to find the executions

_TABLE_RUN_START = frozenset({Status.TABLE_START, Status.TABLE_PREVIEW_START})
_TABLE_RUN_END = frozenset({Status.TABLE_END, Status.ERROR, Status.TABLE_PREVIEW_END, Status.PREVIEW_ERROR})
_ROW_STATUSES = frozenset({Status.CHUNK_END, Status.LOB_END})
_ALREADY_PROCESSED = "is already processed"


@dataclass(frozen=True)
class TableStatus:
    """One table within one run, with the live facts of its session when it is being processed."""

    record: TableRunRecord
    running: bool
    session_verified: bool
    activity: SessionActivity | None = None


@dataclass(frozen=True)
class RunStatus:
    """One execution (one runner ``run_id``) in one environment."""

    run_id: str
    state: str
    started: datetime | None
    ended: datetime | None
    rows: int
    tables: tuple[TableStatus, ...]

    @property
    def duration_seconds(self) -> float | None:
        if self.started is None:
            return None
        end = self.ended if self.ended is not None and self.state != RUNNING else None
        return (end - self.started).total_seconds() if end is not None else None


@dataclass(frozen=True)
class EnvironmentStatus:
    """What STATUS found in one environment; ``unavailable`` explains why there are no runs."""

    env: str
    runs: tuple[RunStatus, ...] = ()
    access: MonitoringAccess | None = None
    unavailable: str | None = None
    now: datetime | None = None  # database clock when the report was read; ends running elapsed times
    grants_script: str | None = None  # file with the grants a DBA must run to open the views to the runtime user


def wait_profile(items: Iterable[WaitItem], coverage: float = WAIT_COVERAGE) -> list[tuple[WaitItem, float]]:
    """Wait events and CPU sorted by time, descending, up to the item that reaches ``coverage`` of the total.
    Returns:
        (item, share of the total) pairs; empty when there is no time to report."""
    ranked = sorted((item for item in items if item.seconds > 0), key=lambda item: item.seconds, reverse=True)
    total = sum(item.seconds for item in ranked)
    profile: list[tuple[WaitItem, float]] = []
    covered = 0.0
    for item in ranked:
        profile.append((item, item.seconds / total))
        covered += item.seconds
        if covered >= coverage * total:
            break
    return profile


def _record_from_log(entries: list[LogEntry]) -> TableRunRecord:
    first, last = entries[0], entries[-1]
    ends = [e.ended for e in entries if e.ended is not None]
    return TableRunRecord(
        run_id="",
        owner=first.owner,
        table_name=first.table_name,
        action=last.action,
        process_date=last.process_date,
        started=min(e.started for e in entries),
        ended=max(ends) if ends else None,
        last_status=last.status,
        message=last.message,
        rows=sum(e.rows for e in entries if e.status in _ROW_STATUSES),
        in_progress=False,
        skipped=last.status == Status.ERROR and _ALREADY_PROCESSED in (last.message or ""),
        last_activity=None,
        session=None,
    )


def _with_control(record: TableRunRecord, control: ControlEntry) -> TableRunRecord:
    """The control row says whether the table is still being processed, by whom and how many rows it holds."""
    in_progress = control.status in Status.IN_PROGRESS_STATUSES
    return replace(
        record,
        run_id=control.run_id or "",
        in_progress=in_progress,
        rows=control.rows if in_progress else record.rows,
        last_activity=control.last_activity if in_progress else None,
        session=control.session if in_progress else None,
    )


def _record_from_control(control: ControlEntry) -> TableRunRecord:
    """A table with a control row but no log rows (the log was purged or never written)."""
    in_progress = control.status in Status.IN_PROGRESS_STATUSES
    return TableRunRecord(
        run_id=control.run_id or "",
        owner=control.owner,
        table_name=control.table_name,
        action=control.action,
        process_date=control.process_date,
        started=control.started,
        ended=control.ended,
        last_status=control.status,
        message=None,
        rows=control.rows,
        in_progress=in_progress,
        skipped=False,
        last_activity=control.last_activity if in_progress else None,
        session=control.session if in_progress else None,
    )


def build_executions(
    log: Iterable[LogEntry], control: Iterable[ControlEntry], last: int
) -> list[tuple[str, list[TableRunRecord]]]:
    """Groups the log into executions by time and completes them with the control rows; PREVIEW is left out.

    The log carries no run identifier, so a table's rows are split into one run per ``TSTART`` (or after a terminal
    status), and the table runs are put in the same execution while each starts within ``RUN_GAP_SECONDS`` of the
    previous one's last activity and the table is not already in it (a run processes a table once).
    Returns:
        (label, records) of the ``last`` most recent executions, newest first; the label is the start time."""
    by_table: dict[tuple[str, str], list[LogEntry]] = {}
    for entry in sorted(log, key=attrgetter("log_id")):
        by_table.setdefault((entry.owner, entry.table_name), []).append(entry)
    controls = {(c.owner, c.table_name): c for c in control if c.status not in Status.PREVIEW_STATUSES}

    records: list[TableRunRecord] = []
    for key, entries in by_table.items():
        runs: list[list[LogEntry]] = []
        for entry in entries:
            if not runs or entry.status in _TABLE_RUN_START or runs[-1][-1].status in _TABLE_RUN_END:
                runs.append([])
            runs[-1].append(entry)
        runs = [run for run in runs if not any(e.status in Status.PREVIEW_STATUSES for e in run)]
        built = [_record_from_log(run) for run in runs]
        if built and key in controls:
            built[-1] = _with_control(built[-1], controls[key])
        records.extend(built)
    records.extend(_record_from_control(c) for key, c in controls.items() if key not in by_table)

    origin = datetime.min
    records.sort(key=lambda r: (r.started or origin, r.owner, r.table_name))
    executions: list[list[TableRunRecord]] = []
    tables: set[tuple[str, str]] = set()
    horizon = origin
    for record in records:
        start = record.started or origin
        key = (record.owner, record.table_name)
        if executions and key not in tables and start - horizon <= timedelta(seconds=RUN_GAP_SECONDS):
            executions[-1].append(record)
        else:
            executions.append([record])
            tables, horizon = set(), start
        tables.add(key)
        horizon = max(horizon, record.ended or record.last_activity or start)
    newest = executions[::-1][:last]
    return [(_label(group), sorted(group, key=attrgetter("owner", "table_name"))) for group in newest]


def _label(records: list[TableRunRecord]) -> str:
    starts = [r.started for r in records if r.started is not None]
    return min(starts).strftime("%Y%m%d-%H%M%S") if starts else "unknown"


def classify_run(tables: Iterable[TableStatus]) -> str:
    """RUNNING while a table is being processed; else ERRORS if any table failed, PARTIAL if any is incomplete."""
    statuses = list(tables)
    if any(table.running for table in statuses):
        return RUNNING
    records = [table.record for table in statuses if not table.record.skipped]
    if any(record.last_status == Status.ERROR for record in records):
        return ERRORS
    if any(record.last_status != Status.TABLE_END for record in records):
        return PARTIAL
    return OK


def _table_status(
    engine: DatabaseEngine,
    conn: Any,
    monitor_conn: Any,
    record: TableRunRecord,
    access: MonitoringAccess,
    now: datetime,
) -> TableStatus:
    if not record.in_progress:
        return TableStatus(record, running=False, session_verified=False)
    # The runner mutex is the authority: it is tried without waiting and released at once
    runner_alive = engine.is_run_alive(conn, record.owner, record.table_name, record.run_id)
    if runner_alive is False:
        return TableStatus(record, running=False, session_verified=True)
    activity: SessionActivity | None = None
    if record.session is not None and (access.session or access.progress or access.waits):
        activity = engine.get_session_activity(monitor_conn, record.session, access, MIN_WAIT_SECONDS)
    if runner_alive is True:
        return TableStatus(record, running=True, session_verified=True, activity=activity)
    if activity is not None and activity.alive is not None:
        return TableStatus(record, running=activity.alive, session_verified=True, activity=activity)
    # The session cannot be verified: trust the heartbeat the run keeps in the control table
    recent = record.last_activity is not None and (now - record.last_activity).total_seconds() <= STALE_SECONDS
    return TableStatus(record, running=recent, session_verified=False, activity=activity)


def collect_environment(
    engine: DatabaseEngine,
    conn: Any,
    env: str,
    last: int,
    access: MonitoringAccess,
    *,
    monitor_conn: Any | None = None,
    grants_script: str | None = None,
) -> EnvironmentStatus:
    """Reads the last ``last`` executions of one environment and the live state of the running ones.

    ``conn`` reads the control tables; the session views (what ``access`` allows) are read over ``monitor_conn``,
    which is ``conn`` unless a connection with more privileges was found."""
    monitor_conn = conn if monitor_conn is None else monitor_conn
    now = engine.get_system_date(conn)
    executions = build_executions(engine.get_log_entries(conn, LOG_SCAN_ROWS), engine.get_control_entries(conn), last)
    runs = []
    for run_id, group in executions:
        tables = [_table_status(engine, conn, monitor_conn, record, access, now) for record in group]
        records = [table.record for table in tables]
        starts = [r.started for r in records if r.started is not None]
        ends = [r.ended for r in records if r.ended is not None]
        runs.append(
            RunStatus(
                run_id=run_id,
                state=classify_run(tables),
                started=min(starts) if starts else None,
                ended=max(ends) if ends else None,
                rows=sum(r.rows for r in records),
                tables=tuple(tables),
            )
        )
    return EnvironmentStatus(env=env, runs=tuple(runs), access=access, now=now, grants_script=grants_script)


def _clock(seconds: float) -> str:
    total = round(seconds)
    return f"{total // 3600:d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _stamp(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value is not None else "-"


def _render_activity(table: TableStatus) -> list[str]:
    activity = table.activity
    if activity is None:
        return []
    lines: list[str] = []
    if activity.progress is not None:
        progress = activity.progress
        remaining = f", about {_clock(progress.remaining_seconds)} left" if progress.remaining_seconds else ""
        what = f" ({progress.description})" if progress.description else ""
        lines.append(f"      progress: {progress.percent:.1f}%{what}{remaining}")
    if activity.current_wait is not None:
        lines.append(f"      waiting {activity.current_wait.seconds:.0f} s on {activity.current_wait.name}")
    profile = wait_profile(activity.waits)
    if profile:
        lines.append(
            "      time (s): " + ", ".join(f"{item.name} {item.seconds:.1f} ({share:.0%})" for item, share in profile)
        )
    return lines


def render_environment(status: EnvironmentStatus) -> list[str]:
    """Report lines for one environment."""
    lines = [f"== {status.env} =="]
    if status.unavailable is not None:
        lines.append(f"  not available: {status.unavailable}")
        return lines
    if not status.runs:
        lines.append("  no executions recorded")
    for run in status.runs:
        duration = run.duration_seconds
        elapsed = f"  elapsed {_clock(duration)}" if duration is not None else ""
        lines.append(f"  run {run.run_id}  {run.state}  rows {run.rows:,}  started {_stamp(run.started)}{elapsed}")
        for table in run.tables:
            record = table.record
            if table.running:
                label = RUNNING
            elif record.in_progress:
                label = "INTERRUPTED"
            elif record.skipped:
                label = "ALREADY PROCESSED"
            else:
                label = record.last_status
            note = " (session not verified)" if record.in_progress and not table.session_verified else ""
            finished = (
                f" finished: {_stamp(record.ended)}" if record.ended is not None and not record.in_progress else ""
            )
            end = record.ended if record.ended is not None and not record.in_progress else status.now
            elapsed = (
                f" elapsed {_clock((end - record.started).total_seconds())}"
                if record.started is not None and end is not None
                else ""
            )
            lines.append(
                f"    {record.owner}.{record.table_name}  status: {label}  rows processed: {record.rows:,},"
                f" started: {_stamp(record.started)}{finished}{elapsed}{note}"
            )
            detail = [f"action: {record.action}"] if record.action else []
            if record.process_date is not None:
                detail.append(f"process date: {record.process_date}")
            if detail:
                lines.append("      " + ", ".join(detail))
            if table.running:
                lines.extend(_render_activity(table))
            elif record.last_status == Status.ERROR and not record.skipped and record.message:
                lines.append(f"      {record.message}")
    access = status.access
    if access is not None and access.grants:
        lines.append("  Session monitoring is skipped for what it cannot read: some views are not readable.")
        if status.grants_script is not None:
            lines.append(f"  Ask a DBA to run {status.grants_script} in SQL*Plus, then rerun STATUS.")
        else:
            lines.append("  Ask a DBA to run:")
            lines.extend(f"    {grant};" for grant in access.grants)
    return lines


def report_status(
    environments: Iterable[tuple[str, Callable[[], EnvironmentStatus]]],
    write: Callable[[str], None],
) -> int:
    """Writes the report of every environment; returns 1 when none of them could be read."""
    readable = 0
    for env, collect in environments:
        try:
            status = collect()
        except ExecutionError as exc:
            logger.error("%s: %s", env, exc)
            status = EnvironmentStatus(env=env, unavailable=str(exc))
        if status.unavailable is None:
            readable += 1
        for line in render_environment(status):
            write(line)
    return 0 if readable else 1
