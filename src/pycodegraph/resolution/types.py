"""Internal data structures for the reference resolution phase."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..types import EdgeKind


@dataclass
class UnresolvedRef:
    """Denormalized internal format used during resolution."""
    from_node_id: str
    reference_name: str
    reference_kind: EdgeKind
    line: int
    column: int
    file_path: str = ""
    language: str = "unknown"


@dataclass
class ResolvedRef:
    """A successfully resolved reference."""
    original: UnresolvedRef
    target_node_id: str
    confidence: float
    resolved_by: str  # 'exact-match', 'import', 'qualified-name', 'fuzzy', 'instance-method', 'file-path'


@dataclass
class ResolutionResult:
    """Result of a resolution pass."""
    resolved: list[ResolvedRef] = field(default_factory=list)
    unresolved: list[UnresolvedRef] = field(default_factory=list)
    stats: dict = field(default_factory=lambda: {
        "total": 0, "resolved": 0, "unresolved": 0, "by_method": {},
    })


@dataclass
class ImportMapping:
    """Maps a local import name to its source module and exported name."""
    local_name: str
    exported_name: str
    source: str
    is_default: bool = False
    is_namespace: bool = False
