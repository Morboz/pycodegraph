"""Database connection and initialization using SQLAlchemy Core."""

from __future__ import annotations

from sqlalchemy import create_engine, event, text, Engine
from sqlalchemy import Connection as SAConnection
from sqlalchemy.engine import make_url

from .inferdb import ensure_inferdb_duck_schema
from .tables import metadata, schema_versions


def _apply_sqlite_pragmas(dbapi_connection, connection_record):
    """Apply SQLite performance PRAGMAs on each new connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA busy_timeout = 120000")
    cursor.execute("PRAGMA synchronous = NORMAL")
    cursor.execute("PRAGMA cache_size = -64000")
    cursor.execute("PRAGMA temp_store = MEMORY")
    cursor.execute("PRAGMA mmap_size = 268435456")
    cursor.close()


def _init_sqlite_fts(engine: Engine) -> None:
    """Create FTS5 virtual table and sync triggers for SQLite."""
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5("
            "id, name, qualified_name, docstring, signature,"
            "content='nodes', content_rowid='rowid')"
        ))
        conn.execute(text(
            "CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN"
            "  INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)"
            "  VALUES (NEW.rowid, NEW.id, NEW.name, NEW.qualified_name, NEW.docstring, NEW.signature);"
            "END"
        ))
        conn.execute(text(
            "CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN"
            "  INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature)"
            "  VALUES ('delete', OLD.rowid, OLD.id, OLD.name, OLD.qualified_name, OLD.docstring, OLD.signature);"
            "END"
        ))
        conn.execute(text(
            "CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE ON nodes BEGIN"
            "  INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature)"
            "  VALUES ('delete', OLD.rowid, OLD.id, OLD.name, OLD.qualified_name, OLD.docstring, OLD.signature);"
            "  INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)"
            "  VALUES (NEW.rowid, NEW.id, NEW.name, NEW.qualified_name, NEW.docstring, NEW.signature);"
            "END"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_nodes_lower_name ON nodes(lower(name))"
        ))


def _init_postgresql_fts(engine: Engine) -> None:
    """Create tsvector column, GIN index, and auto-update trigger for PostgreSQL."""
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE EXTENSION IF NOT EXISTS pg_trgm"
        ))
        conn.execute(text(
            "ALTER TABLE nodes ADD COLUMN IF NOT EXISTS fts tsvector"
            " GENERATED ALWAYS AS ("
            "  setweight(to_tsvector('simple', coalesce(name, '')), 'A') ||"
            "  setweight(to_tsvector('simple', coalesce(qualified_name, '')), 'B') ||"
            "  setweight(to_tsvector('simple', coalesce(docstring, '')), 'C') ||"
            "  setweight(to_tsvector('simple', coalesce(signature, '')), 'D')"
            " ) STORED"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_nodes_fts ON nodes USING GIN (fts)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_nodes_lower_name ON nodes (lower(name))"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_nodes_name_trgm ON nodes USING GIN (name gin_trgm_ops)"
        ))


def _init_inferdb_schema(engine: Engine) -> None:
    """Create MySQL-compatible tables for InferDB."""
    ensure_inferdb_duck_schema(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_versions ("
            "version INT PRIMARY KEY,"
            "applied_at BIGINT NOT NULL,"
            "description TEXT"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS nodes ("
            "id VARCHAR(512) PRIMARY KEY,"
            "kind VARCHAR(64) NOT NULL,"
            "name VARCHAR(512) NOT NULL,"
            "qualified_name VARCHAR(2048) NOT NULL,"
            "file_path VARCHAR(768) NOT NULL,"
            "language VARCHAR(64) NOT NULL,"
            "start_line INT NOT NULL,"
            "end_line INT NOT NULL,"
            "start_column INT NOT NULL,"
            "end_column INT NOT NULL,"
            "docstring TEXT,"
            "signature TEXT,"
            "visibility VARCHAR(64),"
            "is_exported INT DEFAULT 0,"
            "is_async INT DEFAULT 0,"
            "is_static INT DEFAULT 0,"
            "is_abstract INT DEFAULT 0,"
            "decorators TEXT,"
            "type_parameters TEXT,"
            "updated_at BIGINT NOT NULL,"
            "fts_text TEXT"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS edges ("
            "id BIGINT AUTO_INCREMENT PRIMARY KEY,"
            "source VARCHAR(512) NOT NULL,"
            "target VARCHAR(512) NOT NULL,"
            "kind VARCHAR(64) NOT NULL,"
            "metadata TEXT,"
            "line INT,"
            "col INT,"
            "provenance TEXT"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS files ("
            "path VARCHAR(768) PRIMARY KEY,"
            "content_hash VARCHAR(128) NOT NULL,"
            "language VARCHAR(64) NOT NULL,"
            "size INT NOT NULL,"
            "modified_at DOUBLE NOT NULL,"
            "indexed_at BIGINT NOT NULL,"
            "node_count INT DEFAULT 0,"
            "errors TEXT"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS unresolved_refs ("
            "id BIGINT AUTO_INCREMENT PRIMARY KEY,"
            "from_node_id VARCHAR(512) NOT NULL,"
            "reference_name TEXT NOT NULL,"
            "reference_kind VARCHAR(64) NOT NULL,"
            "line INT NOT NULL,"
            "col INT NOT NULL,"
            "candidates TEXT,"
            "file_path VARCHAR(768) NOT NULL DEFAULT '',"
            "language VARCHAR(64) NOT NULL DEFAULT 'unknown'"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS project_metadata ("
            "`key` VARCHAR(255) PRIMARY KEY,"
            "value TEXT NOT NULL,"
            "updated_at BIGINT NOT NULL"
            ")"
        ))
        conn.execute(text(
            "INSERT IGNORE INTO schema_versions (version, applied_at, description)"
            " VALUES (1, :ts, 'Initial schema')"
        ), {"ts": _now_ms()})


def resolve_backend_name(db_url: str, dialect_name: str) -> str:
    """Resolve pycodegraph's logical backend from a SQLAlchemy URL and driver dialect."""
    backend = make_url(db_url).query.get("backend")
    if isinstance(backend, tuple):
        backend = backend[0] if backend else None
    if dialect_name == "mysql" and backend == "inferdb":
        return "inferdb"
    return dialect_name


def prepare_engine_url(db_url: str) -> tuple[str, str]:
    """Return a DBAPI-safe engine URL and pycodegraph's logical backend name."""
    url = make_url(db_url)
    backend_name = resolve_backend_name(db_url, url.get_backend_name())
    engine_url = url.difference_update_query(["backend"])
    return engine_url.render_as_string(hide_password=False), backend_name


class DatabaseConnection:
    """Wraps a SQLAlchemy Engine with dialect-specific initialization."""

    def __init__(
        self,
        engine: Engine,
        connection: SAConnection | None = None,
        backend_name: str | None = None,
    ) -> None:
        self._engine = engine
        self._connection = connection
        self._backend_name = backend_name or engine.dialect.name

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def dialect_name(self) -> str:
        return self._backend_name

    def get_connection(self) -> SAConnection:
        if self._connection is None:
            self._connection = self._engine.connect()
        self._connection.info["pycodegraph_backend"] = self._backend_name
        return self._connection

    @classmethod
    def initialize(cls, db_url: str) -> DatabaseConnection:
        """Create a new database with full schema."""
        engine_url, backend_name = prepare_engine_url(db_url)
        engine = create_engine(engine_url)

        if backend_name == "sqlite":
            event.listen(engine, "connect", _apply_sqlite_pragmas)

        if backend_name == "inferdb":
            _init_inferdb_schema(engine)
        else:
            with engine.begin() as conn:
                metadata.create_all(conn)
                if backend_name == "sqlite":
                    conn.execute(text(
                        "INSERT OR IGNORE INTO schema_versions (version, applied_at, description)"
                        " VALUES (1, :ts, 'Initial schema')"
                    ), {"ts": _now_ms()})
                else:
                    conn.execute(text(
                        "INSERT INTO schema_versions (version, applied_at, description)"
                        " VALUES (1, :ts, 'Initial schema') ON CONFLICT DO NOTHING"
                    ), {"ts": _now_ms()})

        if backend_name == "sqlite":
            _init_sqlite_fts(engine)
        elif backend_name == "postgresql":
            _init_postgresql_fts(engine)

        return cls(engine, backend_name=backend_name)

    @classmethod
    def open(cls, db_url: str) -> DatabaseConnection:
        """Open an existing database."""
        engine_url, backend_name = prepare_engine_url(db_url)
        engine = create_engine(engine_url)

        if backend_name == "sqlite":
            event.listen(engine, "connect", _apply_sqlite_pragmas)

        return cls(engine, backend_name=backend_name)

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        self._engine.dispose()


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)
