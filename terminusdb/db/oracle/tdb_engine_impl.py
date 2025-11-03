"""Oracle-specific provisioning helpers for the `tdb-impl` CLI."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Dict, Iterator, List, Sequence, Tuple

import oracledb

from terminusdb.core.tdb_logger import get_logger
from terminusdb.core.tdb_params_config import Config
from terminusdb.db.tdb_impl_specs import SEQUENCE_SPECS, TABLE_SPECS


class OracleEngineImplementer:
    """Provisioning helper that encapsulates Oracle-specific DDL."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.logger = get_logger("impl.oracle")

    @contextmanager
    def _connection(self, target: str) -> Iterator[oracledb.Connection]:
        user_attr = f"admin_{target}_username"
        pwd_attr = f"admin_{target}_password"
        dsn_attr = f"admin_{target}_dsn"
        user = getattr(self.config, user_attr, "") or getattr(self.config, f"{target}_username", "")
        password = getattr(self.config, pwd_attr, "") or getattr(self.config, f"{target}_password", "")
        dsn = getattr(self.config, dsn_attr, "") or getattr(self.config, f"{target}_dsn", "")
        if not all([user, password, dsn]):
            raise ValueError(f"Missing credentials to connect to {target} database")
        self.logger.debug("Connecting to %s as %s", target, user)
        conn = oracledb.connect(user=user, password=password, dsn=dsn)  # type: ignore[arg-type]
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------ metadata helpers ------------------------------
    def _table_exists(self, conn: oracledb.Connection, table_name: str) -> bool:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT 1 FROM user_tables WHERE table_name = :1", [table_name.upper()])
            return cursor.fetchone() is not None
        finally:
            cursor.close()

    def _sequence_exists(self, conn: oracledb.Connection, seq_name: str) -> bool:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT 1 FROM user_sequences WHERE sequence_name = :1", [seq_name.upper()])
            return cursor.fetchone() is not None
        finally:
            cursor.close()

    def _constraint_exists(self, conn: oracledb.Connection, constraint_name: str) -> bool:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT 1 FROM user_constraints WHERE constraint_name = :1",
                [constraint_name.upper()],
            )
            return cursor.fetchone() is not None
        finally:
            cursor.close()

    def _index_exists(self, conn: oracledb.Connection, index_name: str) -> bool:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT 1 FROM user_indexes WHERE index_name = :1", [index_name.upper()])
            return cursor.fetchone() is not None
        finally:
            cursor.close()

    def _db_link_exists(self, conn: oracledb.Connection, link_name: str) -> bool:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT 1 FROM user_db_links WHERE db_link = :1", [link_name.upper()])
            return cursor.fetchone() is not None
        finally:
            cursor.close()

    def _role_exists(self, conn: oracledb.Connection, role_name: str) -> bool:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT 1 FROM user_roles WHERE role = :1", [role_name.upper()])
            return cursor.fetchone() is not None
        finally:
            cursor.close()

    def _run_ddl(self, conn: oracledb.Connection, statement: str) -> None:
        cursor = conn.cursor()
        try:
            self.logger.debug("Executing DDL: %s", statement)
            cursor.execute(statement)
        finally:
            cursor.close()
        conn.commit()

    # ------------------------ creation helpers -----------------------------
    def ensure_tables(self, conn: oracledb.Connection) -> None:
        for table_name, spec in TABLE_SPECS.items():
            if not self._table_exists(conn, table_name):
                column_sql: List[str] = []
                for column in spec["columns"]:
                    col_name = column["name"].upper()
                    col_type = column["types"].get(self.config.db_engine)
                    if not col_type:
                        raise ValueError(
                            f"No type defined for engine {self.config.db_engine} in column {column['name']}"
                        )
                    pieces = [col_name, col_type]
                    default_val = column.get("default")
                    if default_val is not None:
                        pieces.append(f"DEFAULT {default_val}")
                    if column.get("nullable") is False:
                        pieces.append("NOT NULL")
                    column_sql.append(" ".join(pieces))
                create_stmt = (
                    f"CREATE TABLE {table_name.upper()} (\n    " + ",\n    ".join(column_sql) + "\n)"
                )
                self.logger.info("Creating table %s", table_name.upper())
                self._run_ddl(conn, create_stmt)
            pk = spec.get("primary_key")
            if pk:
                pk_name = pk["name"].upper()
                cols = ", ".join(col.upper() for col in pk["columns"])
                if not self._constraint_exists(conn, pk_name):
                    self._run_ddl(
                        conn,
                        f"ALTER TABLE {table_name.upper()} ADD CONSTRAINT {pk_name} PRIMARY KEY ({cols})",
                    )
            for index in spec.get("indexes", []):
                index_name = index["name"].upper()
                cols = ", ".join(col.upper() for col in index["columns"])
                unique = "UNIQUE " if index.get("unique") else ""
                if not self._index_exists(conn, index_name):
                    self._run_ddl(
                        conn,
                        f"CREATE {unique}INDEX {index_name} ON {table_name.upper()} ({cols})",
                    )

    def ensure_sequences(self, conn: oracledb.Connection) -> None:
        for seq_name, seq_spec in SEQUENCE_SPECS.items():
            if self._sequence_exists(conn, seq_name):
                continue
            start = seq_spec.get("start", "1") or "1"
            increment = seq_spec.get("increment", "1") or "1"
            stmt = (
                f"CREATE SEQUENCE {seq_name.upper()} START WITH {start} INCREMENT BY {increment}"
            )
            self.logger.info("Creating sequence %s", seq_name.upper())
            self._run_ddl(conn, stmt)

    def ensure_db_link(
        self,
        conn: oracledb.Connection,
        link_name: str,
        target_user: str,
        target_password: str,
        target_dsn: str,
    ) -> None:
        if not link_name:
            return
        if self._db_link_exists(conn, link_name):
            return
        if not all([target_user, target_password, target_dsn]):
            raise ValueError(f"Missing remote credentials to create database link {link_name}")
        safe_password = target_password.replace('"', '""')
        safe_dsn = target_dsn.replace("'", "''")
        stmt = (
            f"CREATE DATABASE LINK {link_name.upper()} CONNECT TO {target_user} IDENTIFIED BY \"{safe_password}\" "
            f"USING '{safe_dsn}'"
        )
        self.logger.info("Creating database link %s", link_name.upper())
        self._run_ddl(conn, stmt)

    def ensure_role(
        self,
        conn: oracledb.Connection,
        role_name: str,
        tables: Sequence[Tuple[str, str]],
    ) -> None:
        if not role_name:
            return
        if not self._role_exists(conn, role_name):
            self.logger.info("Creating role %s", role_name.upper())
            self._run_ddl(conn, f"CREATE ROLE {role_name.upper()}")
        if not tables:
            return
        grants: Dict[Tuple[str, str], List[str]] = {}
        for owner, table in tables:
            owner_u = owner.upper()
            table_u = table.upper()
            grants.setdefault((owner_u, table_u), []).extend([
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
            ])
        for (owner_u, table_u), privileges in grants.items():
            priv_list = ", ".join(sorted(set(privileges)))
            stmt = f"GRANT {priv_list} ON {owner_u}.{table_u} TO {role_name.upper()}"
            self.logger.info(
                "Granting %s on %s.%s to %s",
                priv_list,
                owner_u,
                table_u,
                role_name.upper(),
            )
            self._run_ddl(conn, stmt)

    # -------------------------- orchestration -------------------------------
    def provision(
        self,
        source_tables: Sequence[Tuple[str, str]],
        history_tables: Sequence[Tuple[str, str]],
    ) -> None:
        self.logger.info("Provisioning SOURCE database objects")
        with self._connection("source") as source_conn:
            self.ensure_tables(source_conn)
            self.ensure_sequences(source_conn)
            self.ensure_db_link(
                source_conn,
                self.config.history_dblink_name,
                self.config.history_username,
                self.config.history_password,
                self.config.history_dsn,
            )
            self.ensure_role(source_conn, self.config.source_role_name, source_tables)
        self.logger.info("Provisioning HISTORY database objects")
        with self._connection("history") as history_conn:
            self.ensure_tables(history_conn)
            self.ensure_sequences(history_conn)
            self.ensure_db_link(
                history_conn,
                self.config.source_dblink_name,
                self.config.source_username,
                self.config.source_password,
                self.config.source_dsn,
            )
            self.ensure_role(history_conn, self.config.history_role_name, history_tables)
