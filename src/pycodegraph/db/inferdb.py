"""InferDB lifecycle helpers for CodeGraph databases."""

from __future__ import annotations

import os
from collections.abc import Callable

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL

from pycodegraph.config import CodeGraphConfig


def _mysql_identifier(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def _duck_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _raw_driver_execute(engine: Engine, sql: str) -> None:
    with engine.connect() as conn:
        raw = conn.connection.driver_connection
        with raw.cursor() as cursor:
            cursor.execute(sql)


def ensure_inferdb_duck_schema(engine: Engine, database: str | None = None) -> None:
    """Ensure InferDB's DuckDB catalog has ltmdb_sql.<database>."""
    if database is None:
        with engine.connect() as conn:
            database = conn.engine.url.database
    if not database:
        return
    _raw_driver_execute(
        engine,
        f"/*+ duck_execute */ CREATE SCHEMA IF NOT EXISTS ltmdb_sql.{_duck_identifier(database)}",
    )


class InferDBCodeGraphBackend:
    """Prepare InferDB databases for pycodegraph's InferDB backend.

    The helper owns the lifecycle details that callers otherwise need to know:
    MySQL database creation, DuckDB `ltmdb_sql.<database>` schema creation, and
    pycodegraph's `?backend=inferdb` URL marker.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 3307,
        user: str = "root",
        password: str = "",
        drivername: str = "mysql+pymysql",
        engine_factory: Callable[[str], Engine] = create_engine,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.drivername = drivername
        self._engine_factory = engine_factory
        self._admin_engine = engine_factory(self._url(database=None, backend_marker=False))

    @classmethod
    def from_env(cls, prefix: str = "INFERDB_") -> InferDBCodeGraphBackend:
        """Create a backend helper from environment variables."""
        return cls(
            host=os.getenv(f"{prefix}HOST", "127.0.0.1"),
            port=int(os.getenv(f"{prefix}PORT", "3307")),
            user=os.getenv(f"{prefix}USER", "root"),
            password=os.getenv(f"{prefix}PASSWORD", ""),
        )

    def ensure_database(self, database: str) -> str:
        """Ensure a database is ready for pycodegraph InferDB writes."""
        with self._admin_engine.connect() as conn:
            conn.execute(text(f"CREATE DATABASE IF NOT EXISTS {_mysql_identifier(database)}"))
        _raw_driver_execute(
            self._admin_engine,
            f"/*+ duck_execute */ CREATE SCHEMA IF NOT EXISTS ltmdb_sql.{_duck_identifier(database)}",
        )
        return self.database_url(database)

    def existing_database_url(self, database: str) -> str | None:
        """Return a pycodegraph db_url only when the database exists."""
        with self._admin_engine.connect() as conn:
            row = conn.execute(
                text("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = :name"),
                {"name": database},
            ).first()
        if row is None:
            return None
        ensure_inferdb_duck_schema(self._admin_engine, database)
        return self.database_url(database)

    def drop_database(self, database: str) -> None:
        """Drop a pycodegraph InferDB database and its DuckDB schema."""
        with self._admin_engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {_mysql_identifier(database)}"))
        _raw_driver_execute(
            self._admin_engine,
            f"/*+ duck_execute */ DROP SCHEMA IF EXISTS ltmdb_sql.{_duck_identifier(database)} CASCADE",
        )

    def database_url(self, database: str) -> str:
        """Return a db_url that selects pycodegraph's InferDB dialect."""
        return self._url(database=database, backend_marker=True)

    def init_codegraph(self, project_root: str, database: str):
        """Initialize CodeGraph in an InferDB database."""
        from pycodegraph import CodeGraph

        db_url = self.ensure_database(database)
        return CodeGraph.init(project_root, config_overrides={"db_url": db_url})

    def open_codegraph(self, database: str):
        """Open CodeGraph directly from an existing InferDB database."""
        from pycodegraph import CodeGraph

        db_url = self.existing_database_url(database)
        if db_url is None:
            return None
        return CodeGraph.open_from_url(db_url)

    def _url(self, *, database: str | None, backend_marker: bool) -> str:
        query = {"backend": "inferdb"} if backend_marker else {}
        return URL.create(
            drivername=self.drivername,
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=database,
            query=query,
        ).render_as_string(hide_password=False)
