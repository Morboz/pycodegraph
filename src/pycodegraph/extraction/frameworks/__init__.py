"""Framework extractors — detect Python web frameworks and extract ROUTE nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ...types import Node, UnresolvedReference


@dataclass
class FrameworkExtractionResult:
    """Nodes and references produced by a framework extractor."""

    nodes: list[Node] = field(default_factory=list)
    references: list[UnresolvedReference] = field(default_factory=list)


@runtime_checkable
class FileReader(Protocol):
    """Minimal read-only interface for project files."""

    def read_file(self, file_path: str) -> str | None: ...

    def file_exists(self, file_path: str) -> bool: ...

    def list_files(self) -> list[str]: ...


@runtime_checkable
class FrameworkExtractor(Protocol):
    """Protocol for a single framework extractor (e.g. Flask, Django)."""

    name: str

    def detect(self, file_reader: FileReader) -> bool:
        """Return True if this framework is present in the project."""
        ...

    def extract(self, file_path: str, content: str) -> FrameworkExtractionResult:
        """Extract ROUTE nodes and unresolved references from a source file."""
        ...


# Re-export concrete extractors for convenience
from .python import DjangoExtractor, FastAPIExtractor, FlaskExtractor  # noqa: E402

ALL_PYTHON_EXTRACTORS: list[type] = [FlaskExtractor, FastAPIExtractor, DjangoExtractor]


def detect_python_frameworks(file_reader: FileReader) -> list[FrameworkExtractor]:
    """Return instantiated extractors for every detected Python framework."""
    detected: list[FrameworkExtractor] = []
    for cls in ALL_PYTHON_EXTRACTORS:
        extractor = cls()  # type: ignore[operator]
        if extractor.detect(file_reader):
            detected.append(extractor)
    return detected
