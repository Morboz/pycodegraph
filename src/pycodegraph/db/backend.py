"""Backend ABC, registry, and URL resolution for database dialects."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from sqlalchemy import Connection, Engine
from sqlalchemy.engine import make_url

__all__ = [
    "Backend",
    "get_backend",
    "get_registered_backend_names",
    "prepare_engine_url",
    "register_backend",
    "resolve_backend_name",
]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_BACKEND_REGISTRY: dict[str, type[Backend]] = {}


def register_backend(cls: type[Backend]) -> type[Backend]:
    """Decorator to register a :class:`Backend` subclass by its ``name``."""
    _BACKEND_REGISTRY[cls.name] = cls
    return cls


def get_backend(name: str) -> Backend:
    """Instantiate a registered :class:`Backend` by name.

    If *name* is not yet in the registry, attempt to lazily import
    ``pycodegraph.db.backends.<name>`` which triggers ``@register_backend``.

    Raises :class:`ValueError` if *name* is not in the registry after
    the import attempt.
    """
    cls = _BACKEND_REGISTRY.get(name)
    if cls is None:
        import contextlib
        import importlib

        with contextlib.suppress(ImportError):
            importlib.import_module(f".backends.{name}", __package__)
        cls = _BACKEND_REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown backend: {name!r}. Registered: {list(_BACKEND_REGISTRY)}"
        )
    return cls()


def get_registered_backend_names() -> list[str]:
    """Return the names of all registered backends."""
    return list(_BACKEND_REGISTRY)


# ---------------------------------------------------------------------------
# URL resolution
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Backend ABC
# ---------------------------------------------------------------------------


class Backend(ABC):
    r"""Abstract base class for database backend behavior.

    Each backend encapsulates:
    - Schema lifecycle (create tables, FTS indexes, seed data)
    - Engine configuration (PRAGMAs, event listeners)
    - Query dialect (insert/upsert/FTS/edge queries)

    To add a new backend:

    1. Subclass :class:`Backend` and implement all ``@abstractmethod``\s.
    2. Set ``name`` to a unique string (e.g. ``"duckdb"``).
    3. Decorate the class with ``@register_backend``.
    """

    name: ClassVar[str]

    # -------------------------------------------------------------------
    # Schema lifecycle (classmethods — no instance state needed)
    # -------------------------------------------------------------------

    @classmethod
    @abstractmethod
    def configure_engine(cls, engine: Engine) -> None:
        """Configure *engine* after creation (PRAGMAs, event listeners, etc.).

        Called by both :meth:`DatabaseConnection.initialize` and
        :meth:`DatabaseConnection.open`.
        """

    @classmethod
    @abstractmethod
    def initialize_schema(cls, engine: Engine) -> None:
        """Create all tables, indexes, FTS structures, and seed data.

        Called only by :meth:`DatabaseConnection.initialize`.
        """

    # -------------------------------------------------------------------
    # Query dialect (instance methods — stateless)
    # -------------------------------------------------------------------

    @abstractmethod
    def insert_nodes_ignore(self):
        """Return a SQLAlchemy ``Insert`` statement that ignores conflicts on ``id``."""

    @abstractmethod
    def upsert_file(self, row: dict[str, Any]):
        """Return a SQLAlchemy ``Insert`` statement that upserts a file record."""

    @abstractmethod
    def find_edges_between_nodes(
        self,
        conn: Connection,
        node_ids: list[str],
        kinds: list[str] | None = None,
    ) -> list[Any]:
        """Return edge rows between the given *node_ids*."""

    @abstractmethod
    def search_nodes_fts(
        self,
        conn: Connection,
        query_text: str,
        kinds: list[str] | None,
        languages: list[str] | None,
        limit: int,
        offset: int,
    ) -> list[Any]:
        """Execute a full-text search and return node rows with an FTS score."""

    def search_claims_fts(
        self,
        conn: Connection,
        query_text: str,
        claim_type: str | None,
        limit: int,
    ) -> list[Any]:
        """Execute a full-text search over Summary Claims.

        Returns rows (id, claim_type, claim_text, score). Backends without a
        claim FTS index raise :class:`NotImplementedError`; the caller treats
        any failure as "no results".
        """
        raise NotImplementedError(
            f"{self.name} backend does not implement claim full-text search"
        )

    @abstractmethod
    def after_nodes_changed(self, conn: Connection) -> None:
        """Hook for backends that maintain external node search indexes."""

    @abstractmethod
    def prepare_node_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Prepare node insert rows for backend-specific columns."""
