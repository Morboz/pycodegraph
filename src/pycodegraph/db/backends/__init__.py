"""Built-in database backends for pycodegraph.

Importing this module triggers registration of all built-in backends
via :func:`~pycodegraph.db.backend.register_backend`.
"""

from .inferdb import InferDBBackend
from .postgresql import PostgreSQLBackend
from .sqlite import SQLiteBackend

__all__ = [
    "InferDBBackend",
    "PostgreSQLBackend",
    "SQLiteBackend",
]
