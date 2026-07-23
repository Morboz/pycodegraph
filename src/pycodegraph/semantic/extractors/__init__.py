"""Relation-specific extractors for the semantic evidence layer.

Each extractor reads the raw graph (nodes/edges/dataflow_edges via
:class:`QueryBuilder`) and emits typed :class:`SemanticRelation` rows with
embedded :class:`EvidenceRef` provenance.

Extractors are deliberately small and relation-specific — no generic
"traverse everything" path. This keeps the mapping from raw fact to typed
relation auditable, and matches the contract's requirement (section 6)
that each relation has a defined minimum direct evidence.
"""

from __future__ import annotations

from .calls import extract_calls
from .consumes_return import extract_consumes_return
from .exposes_public_surface import extract_exposes_public_surface
from .forwards_value_inter import extract_forwards_value
from .owns_control import extract_owns_control
from .reads_default import extract_reads_default

__all__ = [
    "extract_calls",
    "extract_consumes_return",
    "extract_exposes_public_surface",
    "extract_forwards_value",
    "extract_owns_control",
    "extract_reads_default",
]
