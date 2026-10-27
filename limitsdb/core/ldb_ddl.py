"""Single delivery point for generated DDL: apply it to a database or write it to a file (DEC-026).

The engine generates the statements and, unless it is given a ``ddl_sink``, executes them. What
differs between modes is the destination, and that choice lives here, outside ``DatabaseEngine``:
``deliver_ddl`` hands the engine either a sink that collects the statements for a file or a list that
collects the failures of the statements it executes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal

from limitsdb.core.ldb_params_config import Config
from limitsdb.db.ldb_engines import DdlFailures, DdlSink

logger = logging.getLogger(__name__)

DdlDestination = Literal["execute", "file"]
DdlEnv = Literal["SOURCE", "HISTORY"]


def write_pending_file(pending_dir: Path, file_stem: str, text: str) -> Path:
    """Write ``text`` to a new ``<file_stem>_<timestamp>[_<n>].sql`` under ``pending_dir``.

    The file is created exclusively, so a file from an earlier run is never truncated: when the
    name is taken the counter suffix ``_2``, ``_3``... is tried until the creation succeeds. The
    operating system decides the winner, so concurrent processes cannot share a name.
    """
    pending_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    attempt = 1
    while True:
        suffix = "" if attempt == 1 else f"_{attempt}"
        path = pending_dir / f"{file_stem}_{stamp}{suffix}.sql"
        try:
            handle = path.open("x", encoding="utf-8")
        except FileExistsError:
            attempt += 1
            continue
        try:
            with handle:
                handle.write(text)
        except OSError:
            path.unlink(missing_ok=True)
            raise
        return path.resolve()


def write_ddl_file(
    config: Config,
    file_stem: str,
    description: str,
    entries: Sequence[tuple[str | None, str]],
    *,
    env: DdlEnv = "HISTORY",
) -> Path:
    """Write statements to a SQL*Plus file under ``ddl_pending_dir`` for a DBA to run once.

    Each entry is (comment, statement); the comment, when present, precedes the statement.
    """
    user = config.history_username if env == "HISTORY" else config.source_username
    lines = [
        f"-- LimitsDb: {description}",
        f"-- Schema: {config.context.schema}; {env.lower()} user: {user}",
        "-- Run it from SQL*Plus as a user with the required privileges.",
        "-- Plain SQL, no PL/SQL. Execution stops at the first failing statement and SQL*Plus",
        "-- exits with a non-zero code; reaching the final OK banner means every statement worked.",
        "whenever oserror exit failure",
        "whenever sqlerror exit failure",
        "set echo on define off sqlblanklines on",
        "",
    ]
    for comment, statement in entries:
        if comment:
            lines.append(f"-- {comment}")
        lines.append(f"{statement};")
        lines.append("")
    lines.append(f"prompt LimitsDb: OK - {len(entries)} statement(s) applied.")
    lines.append("exit success")
    lines.append("")
    return write_pending_file(
        Path(config.execution.ddl_pending_dir), f"{file_stem}_{config.context.schema}", "\n".join(lines)
    )


def render_script(title: str, fragments: Sequence[str]) -> str:
    """Compose a SQL*Plus script from engine fragments that are already terminated and ready to run.

    Used by ``ldb-impl``: the fragments ask for the passwords they need, so the script can be rerun
    by a DBA and embeds no secret.
    """
    lines = [
        f"-- LimitsDb: bootstrap DDL for {title}.",
        "-- Run it in SQL*Plus connected as a user with administrative privileges on this database.",
        "-- It asks for the passwords it needs and can be rerun: objects that already exist are skipped.",
        "-- The database link section reconnects as the runtime user, who owns the link.",
        "whenever oserror exit failure",
        "whenever sqlerror exit failure",
        "set echo on define on verify off sqlblanklines on serveroutput on",
        "",
        *fragments,
        "",
        f"prompt LimitsDb: OK - bootstrap for {title} completed.",
        "exit success",
        "",
    ]
    return "\n".join(lines)


def deliver_ddl(
    config: Config,
    produce: Callable[[DdlSink | None, DdlFailures | None], None],
    *,
    destination: DdlDestination,
    env: DdlEnv,
    file_stem: str,
    description: str,
) -> Path | None:
    """Deliver the DDL that ``produce`` asks the engine for to its destination.

    ``produce(ddl_sink, ddl_failures)`` calls the engine's ``ensure_*`` methods, passing both
    arguments through. The caller opens whatever connection fits (admin or runtime) inside it.

    - ``destination="file"``: ``produce`` gets a sink and the statements it collects are written to a
      file under ``ddl_pending_dir``; no database is changed. Returns the file path, or None when
      there was nothing to write.
    - ``destination="execute"``: ``produce`` gets a failure list and the engine executes the
      statements. Every statement that fails is written, with its error, to a file under
      ``ddl_pending_dir`` and the process exits 1 after all were attempted, so a DBA can run the
      file. Returns None when everything was applied.

    A file that cannot be written is logged with the pending statements and exits 1.
    """
    if destination == "file":
        collected: list[str] = []
        produce(collected.append, None)
        if not collected:
            return None
        try:
            path = write_ddl_file(config, file_stem, description, [(None, s) for s in collected], env=env)
        except OSError as exc:
            logger.error("The DDL file could not be written under %s: %s.", config.execution.ddl_pending_dir, exc)
            raise SystemExit(1) from exc
        logger.info("%s: %d statement(s) written to %s", file_stem, len(collected), path)
        return path

    failures: DdlFailures = []
    producer_error: BaseException | None = None
    try:
        produce(None, failures)
    except Exception as exc:
        producer_error = exc
    if not failures:
        if producer_error is not None:
            raise producer_error
        return None
    try:
        path = write_ddl_file(
            config,
            file_stem,
            f"{description} These statements failed when ldb-run applied them; rerun ldb-run afterwards.",
            [(f"Failed with: {error}", statement) for statement, error in failures],
            env=env,
        )
    except OSError as exc:
        logger.error(
            "%d DDL change(s) could not be applied and the pending file could not be written under %s: %s. "
            "Failed statements: %s",
            len(failures),
            config.execution.ddl_pending_dir,
            exc,
            "; ".join(" ".join(statement.split()) for statement, _ in failures),
        )
        raise SystemExit(1) from exc
    logger.error(
        "%d DDL change(s) could not be applied. They were written to %s; "
        "ask a DBA to review and run that file, then rerun.",
        len(failures),
        path,
    )
    if producer_error is not None:
        raise producer_error
    raise SystemExit(1)
