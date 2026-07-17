"""Graphify-out → TOCS contract adapter.

Reads ``graph.json`` produced by the ``graphify`` skill (knowledge graph over
Ansible documentation) and converts it into :class:`SemanticEntity`,
:class:`SemanticRelation`, :class:`EvidenceRef`, and manifests as defined
by the TOCS semantic evidence contract.

Usage::

    adapter = GraphifyAdapter("/path/to/graphify-out/graph.json")
    result = adapter.build(built_at=1700000000)
    # result.dataset_manifest, result.capability_manifest, result._relations
"""

from .adapter import GraphifyAdapter

__all__ = ["GraphifyAdapter"]
