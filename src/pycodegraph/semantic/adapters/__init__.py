"""Adapter package — providers that translate external graph datasets into the
TOCS semantic evidence contract (SemanticEntity + SemanticRelation +
EvidenceRef + manifests).

The first adapter is :mod:`graphify` (DocGraph from the graphify-out skill).
"""

from .graphify import GraphifyAdapter

__all__ = ["GraphifyAdapter"]
