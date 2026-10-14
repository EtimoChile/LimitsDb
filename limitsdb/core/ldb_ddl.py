"""Single delivery point for generated DDL: apply it to a database or write it to a file (DEC-026).

Generating the statements is the engine's job and is the same in every mode. What differs is the
destination, and that choice lives here, outside ``DatabaseEngine``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from limitsdb.core.ldb_params_config import Config
from limitsdb.db.ldb_engines import DatabaseEngine

logger = logging.getLogger(__name__)

DdlDestination = Literal["execute", "file"]
DdlEnv = Literal["SOURCE", "HISTORY"]


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
    pending_dir = Path(config.execution.ddl_pending_dir)
    pending_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = pending_dir / f"{file_stem}_{config.context.schema}_{stamp}.sql"
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
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def deliver_ddl(
    config: Config,
    engine: DatabaseEngine,
    statements: Sequence[str],
    *,
    destination: DdlDestination,
    env: DdlEnv,
    file_stem: str,
    description: str,
    connection: Any | None = None,
    grants: Sequence[str] = (),
) -> Path | None:
    """Deliver DDL (and the grants that go with it) to its destination.

    - ``destination="file"``: write ``statements`` then ``grants`` to a file under ``ddl_pending_dir``
      and touch no database. ``connection`` is not needed. Returns the file path.
    - ``destination="execute"``: run ``statements`` then ``grants`` over ``connection`` (the caller
      opens it with the credentials that fit, admin or runtime). Every statement that fails is
      written, with its error, to a file under ``ddl_pending_dir`` and the process exits 1 after all
      were attempted, so a DBA can run the file. Returns None when everything was applied.

    A file that cannot be written is logged with the pending statements and exits 1.
    """
    if destination == "file":
        entries: list[tuple[str | None, str]] = [(None, s) for s in [*statements, *grants]]
        if not entries:
            return None
        try:
            path = write_ddl_file(config, file_stem, description, entries, env=env)
        except OSError as exc:
            logger.error("The DDL file could not be written under %s: %s.", config.execution.ddl_pending_dir, exc)
            raise SystemExit(1) from exc
        logger.info("%s: %d statement(s) written to %s", file_stem, len(entries), path)
        return path

    total = len(statements) + len(grants)
    if not total:
        return None
    if connection is None:
        raise ValueError("deliver_ddl needs a connection for destination 'execute'")
    failures = engine.execute_ddl_statements(connection, [*statements, *grants])
    if not failures:
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
        "%d of %d DDL change(s) could not be applied. They were written to %s; "
        "ask a DBA to review and run that file, then rerun.",
        len(failures),
        total,
        path,
    )
    raise SystemExit(1)
