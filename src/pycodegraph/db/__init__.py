"""Database connection and initialization using SQLAlchemy Core."""

from __future__ import annotations

from sqlalchemy import create_engine, event, text, Engine
from sqlalchemy import Connection as SAConnection

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


class DatabaseConnection:
    """Wraps a SQLAlchemy Engine with dialect-specific initialization."""

    def __init__(self, engine: Engine, connection: SAConnection | None = None) -> None:
        self._engine = engine
        self._connection = connection

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def dialect_name(self) -> str:
        return self._engine.dialect.name

    def get_connection(self) -> SAConnection:
        if self._connection is None:
            self._connection = self._engine.connect()
        return self._connection

    @classmethod
    def initialize(cls, db_url: str) -> DatabaseConnection:
        """Create a new database with full schema."""
        engine = create_engine(db_url)

        if engine.dialect.name == "sqlite":
            event.listen(engine, "connect", _apply_sqlite_pragmas)

        with engine.begin() as conn:
            metadata.create_all(conn)
            if engine.dialect.name == "sqlite":
                conn.execute(text(
                    "INSERT OR IGNORE INTO schema_versions (version, applied_at, description)"
                    " VALUES (1, :ts, 'Initial schema')"
                ), {"ts": _now_ms()})
            else:
                conn.execute(text(
                    "INSERT INTO schema_versions (version, applied_at, description)"
                    " VALUES (1, :ts, 'Initial schema') ON CONFLICT DO NOTHING"
                ), {"ts": _now_ms()})

        if engine.dialect.name == "sqlite":
            _init_sqlite_fts(engine)
        elif engine.dialect.name == "postgresql":
            _init_postgresql_fts(engine)

        return cls(engine)

    @classmethod
    def open(cls, db_url: str) -> DatabaseConnection:
        """Open an existing database."""
        engine = create_engine(db_url)

        if engine.dialect.name == "sqlite":
            event.listen(engine, "connect", _apply_sqlite_pragmas)

        return cls(engine)

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        self._engine.dispose()


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)
