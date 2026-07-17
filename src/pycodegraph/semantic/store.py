"""Persistence for the semantic evidence layer.

Writes typed :class:`SemanticRelation` rows (with embedded
:class:`EvidenceRef`) and the dataset/capability manifests to dedicated
tables (``semantic_relations``, ``semantic_evidence_refs``,
``semantic_dataset_manifests``, ``semantic_capability_manifests``),
separate from the raw ``edges`` table (decision A: independent storage so
the contract layer doesn't pollute the raw graph and vice versa).

Read side: fetch relations by ``relation_kind`` and optional subject entity
id, rehydrating the full :class:`SemanticRelation` + :class:`EvidenceRef`
graph for the query handler.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Connection, delete, insert, select

from ..db.tables import (
    semantic_capability_manifests,
    semantic_dataset_manifests,
    semantic_entities,
    semantic_evidence_refs,
    semantic_relations,
)
from .types import (
    AliasKind,
    AuthorityScope,
    CapabilityName,
    CapabilitySupport,
    DatasetRevision,
    EvidenceKind,
    EvidenceRef,
    ExtractionMethod,
    GraphCapabilityManifest,
    GraphDatasetManifest,
    GraphKind,
    Modality,
    RelationKind,
    RevisionMappingStatus,
    RevisionScheme,
    SemanticEntity,
    SemanticEntityAlias,
    SemanticRelation,
    SourceLocator,
)

# =============================================================================
# Manifest writes
# =============================================================================


def write_dataset_manifest(conn: Connection, manifest: GraphDatasetManifest) -> None:
    """Upsert one dataset manifest row."""
    conn.execute(
        delete(semantic_dataset_manifests).where(
            semantic_dataset_manifests.c.build_id == manifest.build_id
        )
    )
    conn.execute(
        insert(semantic_dataset_manifests).values(
            build_id=manifest.build_id,
            instance_id=manifest.instance_id,
            graph_kind=manifest.graph_kind.value,
            repository_id=manifest.repository_id,
            revision_scheme=manifest.revision.scheme.value,
            revision_value=manifest.revision.value,
            source_revision=manifest.revision.source_revision,
            revision_mapping_status=manifest.revision.mapping_status.value,
            built_at=manifest.built_at,
            schema_version=manifest.schema_version,
            extractor_versions=json.dumps(manifest.extractor_versions),
            capabilities_ref=manifest.capabilities_ref,
        )
    )


def write_capability_manifest(
    conn: Connection, manifest: GraphCapabilityManifest
) -> None:
    """Upsert one capability manifest row."""
    conn.execute(
        delete(semantic_capability_manifests).where(
            semantic_capability_manifests.c.capabilities_ref == manifest.instance_id
        )
    )
    conn.execute(
        insert(semantic_capability_manifests).values(
            capabilities_ref=manifest.instance_id,
            instance_id=manifest.instance_id,
            schema_version=manifest.schema_version,
            capabilities=json.dumps(
                {k.value: v.value for k, v in manifest.capabilities.items()}
            ),
            limitations=json.dumps(manifest.limitations),
        )
    )


# =============================================================================
# Relation writes
# =============================================================================


def write_relations(conn: Connection, relations: list[SemanticRelation]) -> None:
    """Insert relations and their evidence refs.

    Idempotent on ``relation_id``: existing rows are deleted first so
    re-running a build with the same IDs (deterministic) doesn't duplicate.
    """
    if not relations:
        return
    relation_ids = [r.relation_id for r in relations]
    conn.execute(
        delete(semantic_relations).where(
            semantic_relations.c.relation_id.in_(relation_ids)
        )
    )
    conn.execute(
        delete(semantic_evidence_refs).where(
            semantic_evidence_refs.c.relation_id.in_(relation_ids)
        )
    )
    rel_rows: list[dict[str, Any]] = []
    ev_rows: list[dict[str, Any]] = []
    for r in relations:
        rel_rows.append(_relation_row(r))
        for er in r.evidence_refs:
            ev_rows.append(_evidence_row(r.relation_id, er))
    conn.execute(insert(semantic_relations), rel_rows)
    if ev_rows:
        conn.execute(insert(semantic_evidence_refs), ev_rows)


def _relation_row(r: SemanticRelation) -> dict[str, Any]:
    return {
        "relation_id": r.relation_id,
        "dataset_id": r.dataset_id,
        "subject_entity_id": r.subject_entity_id,
        "relation_kind": r.relation_kind.value,
        "object_entity_id": r.object_entity_id,
        "literal_object": json.dumps(r.literal_object)
        if r.literal_object is not None
        else None,
        "scenario_id": r.scenario_id,
        "condition_expression": json.dumps(r.condition_expression)
        if r.condition_expression
        else None,
        "modality": r.modality.value,
        "authority_scope": r.authority_scope.value,
        "extraction_method": r.extraction_method.value,
        "extractor_version": r.extractor_version,
        "confidence": r.confidence,
    }


def _evidence_row(relation_id: str, er: EvidenceRef) -> dict[str, Any]:
    return {
        "evidence_ref_id": er.evidence_ref_id,
        "relation_id": relation_id,
        "evidence_kind": er.evidence_kind.value,
        "repository_id": er.repository_id,
        "revision": er.revision,
        "path_or_document_id": er.locator.path_or_document_id,
        "start_line": er.locator.start_line,
        "end_line": er.locator.end_line,
        "symbol_or_section": er.locator.symbol_or_section,
        "graph_node_ids": json.dumps(er.locator.graph_node_ids),
        "content_digest": er.content_digest,
        "excerpt": er.excerpt,
        "dataset_id": er.dataset_id,
    }


# =============================================================================
# Reads (for the query handler)
# =============================================================================


def read_relations(
    conn: Connection,
    relation_kind: RelationKind,
    subject_entity_ids: list[str] | None = None,
    dataset_ids: list[str] | None = None,
) -> list[SemanticRelation]:
    """Read relations of one kind, optionally filtered by subject and/or dataset.

    When ``dataset_ids`` is provided (cross-graph composition, issue #107),
    only relations whose ``dataset_id`` is in the list are returned. When
    omitted, relations from all datasets are returned (current behavior).

    Rehydrates each relation with its full evidence ref list. Order is
    deterministic by ``relation_id`` (QUERY-004).
    """
    stmt = (
        select(semantic_relations)
        .where(semantic_relations.c.relation_kind == relation_kind.value)
        .order_by(semantic_relations.c.relation_id)
    )
    if subject_entity_ids is not None:
        if not subject_entity_ids:
            return []
        stmt = stmt.where(
            semantic_relations.c.subject_entity_id.in_(subject_entity_ids)
        )
    if dataset_ids is not None:
        if not dataset_ids:
            return []
        stmt = stmt.where(semantic_relations.c.dataset_id.in_(dataset_ids))
    rel_rows = list(conn.execute(stmt).fetchall())
    if not rel_rows:
        return []
    rel_ids = [row.relation_id for row in rel_rows]
    ev_rows = list(
        conn.execute(
            select(semantic_evidence_refs)
            .where(semantic_evidence_refs.c.relation_id.in_(rel_ids))
            .order_by(semantic_evidence_refs.c.evidence_ref_id)
        ).fetchall()
    )
    ev_by_rel: dict[str, list[EvidenceRef]] = {}
    for er in ev_rows:
        ev_by_rel.setdefault(er.relation_id, []).append(_evidence_from_row(er))
    return [
        _relation_from_row(row, ev_by_rel.get(row.relation_id, [])) for row in rel_rows
    ]


def read_dataset_manifest(
    conn: Connection, build_id: str
) -> GraphDatasetManifest | None:
    row = conn.execute(
        select(semantic_dataset_manifests).where(
            semantic_dataset_manifests.c.build_id == build_id
        )
    ).fetchone()
    if row is None:
        return None
    return _dataset_manifest_from_row(row)


def read_capability_manifest(
    conn: Connection, capabilities_ref: str
) -> GraphCapabilityManifest | None:
    row = conn.execute(
        select(semantic_capability_manifests).where(
            semantic_capability_manifests.c.capabilities_ref == capabilities_ref
        )
    ).fetchone()
    if row is None:
        return None
    caps_raw: dict[str, str] = json.loads(row.capabilities)
    return GraphCapabilityManifest(
        instance_id=row.instance_id,
        schema_version=row.schema_version,
        capabilities={
            CapabilityName(k): CapabilitySupport(v) for k, v in caps_raw.items()
        },
        limitations=json.loads(row.limitations) if row.limitations else [],
    )


def read_latest_dataset_manifest(
    conn: Connection,
) -> GraphDatasetManifest | None:
    """The most recently built dataset manifest (highest built_at)."""
    row = conn.execute(
        select(semantic_dataset_manifests).order_by(
            semantic_dataset_manifests.c.built_at.desc()
        )
    ).first()
    if row is None:
        return None
    return _dataset_manifest_from_row(row)


def read_latest_dataset_manifests(
    conn: Connection,
) -> list[GraphDatasetManifest]:
    """All dataset manifests in the DB, most-recent-first.

    Cross-graph composition (issue #107): when CodeGraph and DocGraph share
    one DB, this returns both manifests. Each is distinguished by
    ``graph_kind`` (CODE_GRAPH vs DOC_GRAPH) and ``build_id``.
    """
    rows = list(
        conn.execute(
            select(semantic_dataset_manifests).order_by(
                semantic_dataset_manifests.c.built_at.desc()
            )
        ).fetchall()
    )
    return [_dataset_manifest_from_row(row) for row in rows]


# =============================================================================
# Entity writes
# =============================================================================


def write_entities(conn: Connection, entities: list[SemanticEntity]) -> None:
    """Upsert semantic entities.

    Idempotent on ``entity_id``: existing rows are deleted first so
    re-running a build doesn't duplicate. Call once per build after
    relations are written.
    """
    if not entities:
        return
    entity_ids = [e.entity_id for e in entities]
    conn.execute(
        delete(semantic_entities).where(semantic_entities.c.entity_id.in_(entity_ids))
    )
    rows: list[dict[str, Any]] = [_entity_row(e) for e in entities]
    conn.execute(insert(semantic_entities), rows)


def _entity_row(e: SemanticEntity) -> dict[str, Any]:
    return {
        "entity_id": e.entity_id,
        "repository_id": e.repository_id,
        "entity_kind": e.entity_kind.value,
        "canonical_name": e.canonical_name,
        "dataset_id": e.dataset_id,
        "qualified_name": e.qualified_name,
        "language": e.language,
        "scope": e.scope,
        "aliases": json.dumps(
            [
                {"value": a.value, "alias_kind": a.alias_kind.value, "scope": a.scope}
                for a in e.aliases
            ]
        ),
        "source_locator": json.dumps(
            {
                "path_or_document_id": e.source_locator.path_or_document_id,
                "start_line": e.source_locator.start_line,
                "end_line": e.source_locator.end_line,
                "symbol_or_section": e.source_locator.symbol_or_section,
                "graph_node_ids": e.source_locator.graph_node_ids,
            }
        )
        if e.source_locator
        else None,
    }


# =============================================================================
# Entity reads
# =============================================================================


def read_entity(conn: Connection, entity_id: str) -> SemanticEntity | None:
    """Read one entity by ID."""
    row = conn.execute(
        select(semantic_entities).where(semantic_entities.c.entity_id == entity_id)
    ).fetchone()
    if row is None:
        return None
    return _entity_from_row(row)


def read_entities_by_name(
    conn: Connection,
    name: str,
    dataset_ids: list[str] | None = None,
) -> list[SemanticEntity]:
    """Find entities whose canonical_name matches **exactly**.

    When ``dataset_ids`` is provided, only entities whose ``dataset_id`` is
    in the list are returned. Used by the query handler's subject resolution
    to find DocGraph entities by name (XG-003).
    """
    stmt = select(semantic_entities).where(semantic_entities.c.canonical_name == name)
    if dataset_ids is not None:
        if not dataset_ids:
            return []
        stmt = stmt.where(semantic_entities.c.dataset_id.in_(dataset_ids))
    rows = list(conn.execute(stmt).fetchall())
    return [_entity_from_row(row) for row in rows]


def read_entities(
    conn: Connection, dataset_ids: list[str] | None = None
) -> list[SemanticEntity]:
    """All entities, optionally filtered by dataset."""
    stmt = select(semantic_entities)
    if dataset_ids is not None:
        if not dataset_ids:
            return []
        stmt = stmt.where(semantic_entities.c.dataset_id.in_(dataset_ids))
    rows = list(conn.execute(stmt).fetchall())
    return [_entity_from_row(row) for row in rows]


# =============================================================================
# Row → dataclass rehydration
# =============================================================================


def _relation_from_row(row: Any, evidence_refs: list[EvidenceRef]) -> SemanticRelation:
    return SemanticRelation(
        relation_id=row.relation_id,
        subject_entity_id=row.subject_entity_id,
        relation_kind=RelationKind(row.relation_kind),
        authority_scope=AuthorityScope(row.authority_scope),
        modality=Modality(row.modality),
        extraction_method=ExtractionMethod(row.extraction_method),
        extractor_version=row.extractor_version,
        dataset_id=row.dataset_id,
        evidence_refs=evidence_refs,
        object_entity_id=row.object_entity_id,
        literal_object=json.loads(row.literal_object) if row.literal_object else None,
        scenario_id=row.scenario_id,
        condition_expression=json.loads(row.condition_expression)
        if row.condition_expression
        else None,
        confidence=row.confidence,
    )


def _evidence_from_row(row: Any) -> EvidenceRef:
    return EvidenceRef(
        evidence_ref_id=row.evidence_ref_id,
        evidence_kind=EvidenceKind(row.evidence_kind),
        repository_id=row.repository_id,
        revision=row.revision,
        locator=SourceLocator(
            path_or_document_id=row.path_or_document_id,
            start_line=row.start_line,
            end_line=row.end_line,
            symbol_or_section=row.symbol_or_section,
            graph_node_ids=json.loads(row.graph_node_ids) if row.graph_node_ids else [],
        ),
        content_digest=row.content_digest,
        dataset_id=row.dataset_id,
        excerpt=row.excerpt,
    )


def _dataset_manifest_from_row(row: Any) -> GraphDatasetManifest:
    return GraphDatasetManifest(
        instance_id=row.instance_id,
        graph_kind=GraphKind(row.graph_kind),
        repository_id=row.repository_id,
        revision=DatasetRevision(
            scheme=RevisionScheme(row.revision_scheme),
            value=row.revision_value,
            mapping_status=RevisionMappingStatus(row.revision_mapping_status),
            source_revision=row.source_revision,
        ),
        build_id=row.build_id,
        built_at=row.built_at,
        schema_version=row.schema_version,
        extractor_versions=json.loads(row.extractor_versions),
        capabilities_ref=row.capabilities_ref,
    )


def _entity_from_row(row: Any) -> SemanticEntity:
    aliases_raw = json.loads(row.aliases) if row.aliases else []
    sl_raw = json.loads(row.source_locator) if row.source_locator else None
    return SemanticEntity(
        entity_id=row.entity_id,
        repository_id=row.repository_id,
        entity_kind=row.entity_kind,  # StrEnum accepts raw string
        canonical_name=row.canonical_name,
        dataset_id=row.dataset_id,
        qualified_name=row.qualified_name,
        language=row.language,
        scope=row.scope,
        aliases=[
            SemanticEntityAlias(
                value=a["value"],
                alias_kind=AliasKind(a["alias_kind"]),
                scope=a.get("scope"),
            )
            for a in aliases_raw
        ],
        source_locator=SourceLocator(
            path_or_document_id=sl_raw["path_or_document_id"],
            start_line=sl_raw.get("start_line"),
            end_line=sl_raw.get("end_line"),
            symbol_or_section=sl_raw.get("symbol_or_section"),
            graph_node_ids=sl_raw.get("graph_node_ids", []),
        )
        if sl_raw
        else None,
    )
