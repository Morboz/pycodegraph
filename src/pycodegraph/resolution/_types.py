"""Internal data structures for the reference resolution phase."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..types import UnresolvedReference


@dataclass
class ResolvedRef:
    """A successfully resolved reference."""

    original: UnresolvedReference
    target_node_id: str
    confidence: float
    resolved_by: str  # 'exact-match', 'import', 'qualified-name', 'fuzzy', 'instance-method', 'file-path'


@dataclass
class ResolutionResult:
    """Result of a resolution pass."""

    resolved: list[ResolvedRef] = field(default_factory=list)
    unresolved: list[UnresolvedReference] = field(default_factory=list)
    stats: dict = field(
        default_factory=lambda: {
            "total": 0,
            "resolved": 0,
            "unresolved": 0,
            "by_method": {},
        }
    )


@dataclass
class ImportMapping:
    """Maps a local import name to its source module and exported name."""

    local_name: str
    exported_name: str
    source: str
    is_default: bool = False
    is_namespace: bool = False
