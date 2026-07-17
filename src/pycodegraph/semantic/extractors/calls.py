"""CALLS extractor — caller FUNCTION/METHOD → callee FUNCTION/METHOD.

Direct mapping from :data:`EdgeKind.CALLS`. Each raw call edge becomes one
typed SemanticRelation with SOURCE provenance at the caller's definition.

Section 6.2: ``calls`` — static call edge. For value propagation this is
structural evidence only (``forwards_value`` is the direct relation); but for
"does A call B" topology questions, ``calls`` is itself direct.
"""

from __future__ import annotations

from ...types import EdgeKind
from ..types import (
    AuthorityScope,
    ExtractionMethod,
    Modality,
    RelationKind,
    SemanticRelation,
)
from ._common import _BuilderLike, relation_id, source_evidence

_EXTRACTOR_VERSION = "0.1.0"


def extract_calls(builder: _BuilderLike) -> list[SemanticRelation]:
    """Emit one CALLS relation per raw EdgeKind.CALLS edge."""
    queries = builder.queries()
    dataset_id = builder.dataset_id()
    repository_id = builder.repository_id()
    revision = builder.revision_value()

    relations: list[SemanticRelation] = []
    seen: set[str] = set()  # dedupe within one build
    edges = queries.get_all_edges(limit=200000)
    for edge in edges:
        if edge.kind != EdgeKind.CALLS:
            continue
        caller = queries.get_node_by_id(edge.source)
        callee = queries.get_node_by_id(edge.target)
        if caller is None or callee is None:
            continue
        rid = relation_id(
            RelationKind.CALLS.value, edge.source, edge.target, None, dataset_id
        )
        if rid in seen:
            continue
        seen.add(rid)
        relations.append(
            SemanticRelation(
                relation_id=rid,
                subject_entity_id=edge.source,
                relation_kind=RelationKind.CALLS,
                authority_scope=AuthorityScope.IMPLEMENTATION_TOPOLOGY,
                modality=Modality.OBSERVED,
                extraction_method=ExtractionMethod.STATIC_ANALYSIS,
                extractor_version=_EXTRACTOR_VERSION,
                dataset_id=dataset_id,
                evidence_refs=[
                    source_evidence(
                        caller,
                        queries,
                        repository_id,
                        revision,
                        dataset_id,
                        relation_id_hint=rid,
                    )
                ],
                object_entity_id=edge.target,
            )
        )
    return relations
