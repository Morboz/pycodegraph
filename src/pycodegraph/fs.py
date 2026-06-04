"""File provider abstraction for reading source code from arbitrary backends.

Defines a :class:`FileProvider` protocol and a default :class:`LocalFileProvider`
that reads from the local filesystem. Users can inject custom providers (e.g.
reading from a database table) into the explore and context pipelines.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class FileProvider(Protocol):
    """Protocol for reading source file content.

    Implementations can read from the local filesystem, a database
    table, object storage, or any other backend.

    The protocol is **structural** — any object with ``read_file`` and
    ``file_exists`` methods satisfies the contract.  Subclassing is
    optional but recommended for clarity.
    """

    def read_file(self, file_path: str) -> str | None:
        """Read the full content of a source file.

        Args:
            file_path: Relative file path (e.g. ``"src/main.py"``).

        Returns:
            File content as a string, or ``None`` if the file does
            not exist or cannot be read.
        """
        ...

    def file_exists(self, file_path: str) -> bool:
        """Check whether a source file exists.

        Args:
            file_path: Relative file path.

        Returns:
            ``True`` if the file can be read; ``False`` otherwise.
        """
        ...


class LocalFileProvider:
    """Reads source files from the local filesystem.

    Args:
        project_root: Absolute path to the project root directory.
            Relative ``file_path`` values are resolved against this
            root via ``os.path.join``.
    """

    def __init__(self, project_root: str) -> None:
        self._project_root = project_root

    def read_file(self, file_path: str) -> str | None:
        """Read *file_path* from the local filesystem.

        Returns:
            File content as a string, or ``None`` if the file is
            missing or unreadable.
        """
        abs_path = os.path.join(self._project_root, file_path)
        try:
            with open(abs_path) as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return None

    def file_exists(self, file_path: str) -> bool:
        """Check whether *file_path* exists on the local filesystem."""
        abs_path = os.path.join(self._project_root, file_path)
        return os.path.exists(abs_path)
