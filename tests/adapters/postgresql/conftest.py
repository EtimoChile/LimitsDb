# Shared fakes for PostgreSQL adapter tests (no real database, no pg8000 connection).
from collections import deque
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pg8000.dbapi

NOW = datetime(2026, 10, 28, 12, 0, 0)


def pg_error(sqlstate: str, message: str) -> pg8000.dbapi.DatabaseError:
    """An error shaped like pg8000's: the server's fields travel in a dict."""
    return pg8000.dbapi.DatabaseError({"C": sqlstate, "M": message})


class FakeCursor:
    def __init__(self, conn: "FakeConn") -> None:
        self.conn = conn
        self.row: Any = None
        self.rows: list[Any] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.conn.log.append((" ".join(sql.split()), params))
        self.row, self.rows = None, []
        result = self.conn.respond(self.conn, " ".join(sql.split()), params)
        if isinstance(result, list):
            self.rows = result
            self.row = result[0] if result else None
        else:
            self.row = result

    def fetchone(self) -> Any:
        return self.row

    def fetchall(self) -> list[Any]:
        return self.rows

    def close(self) -> None:
        pass


class FakeConn:
    """Records every statement; ``respond(conn, sql, params)`` returns a row or a list of rows (or raises)."""

    def __init__(self, respond: Callable[["FakeConn", str, Any], Any] | None = None) -> None:
        self.respond = respond or (lambda conn, sql, params: (1,))
        self.log: list[tuple[str, Any]] = []
        self.notices: deque[dict[bytes, bytes]] = deque(maxlen=100)
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.log.append(("COMMIT", None))

    def rollback(self) -> None:
        self.log.append(("ROLLBACK", None))

    def close(self) -> None:
        self.closed = True

    def statements(self) -> list[str]:
        return [sql for sql, _ in self.log]
