"""Database connection and initialization using SQLAlchemy Core."""

from __future__ import annotations

from sqlalchemy import Connection as SAConnection
from sqlalchemy import Engine, create_engine

from .backend import get_backend, prepare_engine_url, resolve_backend_name
from .backends import InferDBBackend  # triggers registration
from .tables import metadata

# Backward-compatible re-export: integrations/inferdb.py imports this.
ensure_inferdb_duck_schema = InferDBBackend.ensure_inferdb_duck_schema

__all__ = [
    "DatabaseConnection",
    "ensure_inferdb_duck_schema",
    "metadata",
    "prepare_engine_url",
    "resolve_backend_name",
]


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
        backend_cls = type(get_backend(backend_name))
        backend_cls.configure_engine(engine)
        backend_cls.initialize_schema(engine)
        return cls(engine, backend_name=backend_name)

    @classmethod
    def open(cls, db_url: str) -> DatabaseConnection:
        """Open an existing database."""
        engine_url, backend_name = prepare_engine_url(db_url)
        engine = create_engine(engine_url)
        backend_cls = type(get_backend(backend_name))
        backend_cls.configure_engine(engine)
        return cls(engine, backend_name=backend_name)

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        self._engine.dispose()
