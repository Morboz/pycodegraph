"""Helpers shared across semantic relation extractors."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ...types import Node
from ..types import (
    EntityKind,
    EvidenceKind,
    EvidenceRef,
    SemanticEntity,
    SourceLocator,
)

if TYPE_CHECKING:
    from ..db.queries import QueryBuilder


@runtime_checkable
class _BuilderLike(Protocol):
    """Structural type for what extractors need from SemanticLayerBuilder.

    Breaking the ``semantic.extractor → semantic.extractors`` import cycle
    (import-linter ``pycodegraph.semantic/C1``). Each extractor reads the
    builder's queries/dataset/repository/revision — passing a real
    :class:`pycodegraph.semantic.SemanticLayerBuilder` satisfies this protocol.
    """

    def queries(self) -> QueryBuilder: ...

    def dataset_id(self) -> str: ...

    def repository_id(self) -> str: ...

    def revision_value(self) -> str: ...


# =============================================================================
# Entity construction
# =============================================================================

# NodeKind → EntityKind mapping. EntityKind is the smaller contract vocabulary;
# NodeKind is the backend's richer set. Kinds absent here (file, struct,
# trait, protocol, …) have no contract representation yet and are skipped.
_NODE_KIND_TO_ENTITY_KIND: dict[str, EntityKind] = {
    "module": EntityKind.MODULE,
    "class": EntityKind.CLASS,
    "function": EntityKind.FUNCTION,
    "method": EntityKind.METHOD,
    "parameter": EntityKind.PARAMETER,
    "field": EntityKind.FIELD,
}


def entity_for_node(
    node: Node, dataset_id: str, repository_id: str
) -> SemanticEntity | None:
    """Map a raw Node to a contract SemanticEntity.

    Returns None when the Node's kind has no contract representation (e.g.
    FILE, IMPORT) — those don't participate in typed relations yet.
    """
    entity_kind = _NODE_KIND_TO_ENTITY_KIND.get(node.kind.value)
    if entity_kind is None:
        return None
    return SemanticEntity(
        entity_id=node.id,  # Node.id is already deterministic (file+kind+qname)
        repository_id=repository_id,
        entity_kind=entity_kind,
        canonical_name=node.name,
        dataset_id=dataset_id,
        qualified_name=node.qualified_name,
        language=node.language.value
        if hasattr(node.language, "value")
        else str(node.language),
        scope=_derive_scope(node),
        source_locator=SourceLocator(
            path_or_document_id=node.file_path,
            start_line=node.start_line,
            end_line=node.end_line,
            symbol_or_section=node.qualified_name,
            graph_node_ids=[node.id],
        ),
    )


def _derive_scope(node: Node) -> str | None:
    """Scope = everything in qualified_name before the final segment.

    ``Request::open`` → ``Request``. ``create_user`` (no scope) → None.
    Used for alias disambiguation (COMMON-005) and subject resolution.
    """
    if "::" in node.qualified_name:
        return node.qualified_name.rsplit("::", 1)[0]
    return None


# =============================================================================
# Evidence construction
# =============================================================================


def source_evidence(
    node: Node,
    queries: QueryBuilder,
    repository_id: str,
    revision: str,
    dataset_id: str,
    relation_id_hint: str = "",
) -> EvidenceRef:
    """Build a SOURCE EvidenceRef anchored at *node*'s location.

    The content_digest reuses the file's content_hash from the files table
    (COMMON-014: a source span is required; the file-level hash is the
    available provenance — span-level hashing is a future refinement).

    ``relation_id_hint`` is mixed into the evidence_ref_id to prevent
    collisions when the same source location backs multiple relations
    (e.g. a caller with two different callees).
    """
    file_record = queries.get_file_by_path(node.file_path)
    digest = file_record.content_hash if file_record else _fallback_digest(node)
    return EvidenceRef(
        evidence_ref_id=_evidence_id(node, dataset_id, relation_id_hint),
        evidence_kind=EvidenceKind.SOURCE,
        repository_id=repository_id,
        revision=revision,
        locator=SourceLocator(
            path_or_document_id=node.file_path,
            start_line=node.start_line,
            end_line=node.end_line,
            symbol_or_section=node.qualified_name,
            graph_node_ids=[node.id],
        ),
        content_digest=digest,
        dataset_id=dataset_id,
    )


def _evidence_id(node: Node, dataset_id: str, hint: str = "") -> str:
    raw = f"{dataset_id}|{node.id}|source|{hint}"
    return "ev:" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _fallback_digest(node: Node) -> str:
    raw = f"{node.file_path}|{node.start_line}|{node.end_line}"
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# =============================================================================
# Relation ID
# =============================================================================


def relation_id(
    relation_kind: str,
    subject_id: str,
    object_id: str | None,
    literal_object: object | None,
    dataset_id: str,
) -> str:
    """Deterministic relation_id (BUILD-005).

    Two identical relations (same subject, kind, object, dataset) produce the
    same id, making re-builds idempotent.
    """
    obj_part = object_id if object_id else f"literal:{literal_object}"
    raw = f"{dataset_id}|{relation_kind}|{subject_id}|{obj_part}"
    return "rel:" + hashlib.sha256(raw.encode()).hexdigest()[:16]
