"""SQLAlchemy Core table definitions for CodeGraph."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
)

metadata = MetaData()

schema_versions = Table(
    "schema_versions",
    metadata,
    Column("version", Integer, primary_key=True),
    Column("applied_at", BigInteger, nullable=False),
    Column("description", Text),
)

nodes = Table(
    "nodes",
    metadata,
    Column("id", Text, primary_key=True),
    Column("kind", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("qualified_name", Text, nullable=False),
    Column("file_path", Text, nullable=False),
    Column("language", Text, nullable=False),
    Column("start_line", Integer, nullable=False),
    Column("end_line", Integer, nullable=False),
    Column("start_column", Integer, nullable=False),
    Column("end_column", Integer, nullable=False),
    Column("docstring", Text),
    Column("signature", Text),
    Column("visibility", Text),
    Column("is_exported", Integer, default=0),
    Column("is_async", Integer, default=0),
    Column("is_static", Integer, default=0),
    Column("is_abstract", Integer, default=0),
    Column("decorators", Text),
    Column("type_parameters", Text),
    Column("updated_at", BigInteger, nullable=False),
)

edges = Table(
    "edges",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", Text, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False),
    Column("target", Text, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False),
    Column("kind", Text, nullable=False),
    Column("metadata", Text),
    Column("line", Integer),
    Column("col", Integer),
    Column("provenance", Text, default=None),
)

files = Table(
    "files",
    metadata,
    Column("path", Text, primary_key=True),
    Column("content_hash", Text, nullable=False),
    Column("language", Text, nullable=False),
    Column("size", Integer, nullable=False),
    Column("modified_at", Float, nullable=False),
    Column("indexed_at", BigInteger, nullable=False),
    Column("node_count", Integer, default=0),
    Column("errors", Text),
)

unresolved_refs = Table(
    "unresolved_refs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "from_node_id", Text, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False
    ),
    Column("reference_name", Text, nullable=False),
    Column("reference_kind", Text, nullable=False),
    Column("line", Integer, nullable=False),
    Column("col", Integer, nullable=False),
    Column("file_path", Text, nullable=False, server_default=""),
    Column("language", Text, nullable=False, server_default="unknown"),
)

dataflow_edges = Table(
    "dataflow_edges",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("file_path", Text, nullable=False),
    Column("source_start_line", Integer, nullable=False),
    Column("source_end_line", Integer, nullable=False),
    Column("target_start_line", Integer, nullable=False),
    Column("target_end_line", Integer, nullable=False),
    Column("variable", Text, nullable=False),
    Column("function_id", Text, nullable=False),
    Column("provenance", Text),
)

# --- Summary Claims semantic overlay (ADR-0004) ---------------------------
# Independent storage, separate from nodes/edges: a Summary Claim is not a
# Symbol (no qualified_name/source position) and is grounded through line-range
# spans in claim_grounding, not through Node references.

summary_claims = Table(
    "summary_claims",
    metadata,
    Column("id", Text, primary_key=True),
    Column("claim_type", Text, nullable=False),
    Column("claim_text", Text, nullable=False),
)

claim_grounding = Table(
    "claim_grounding",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "claim_id",
        Text,
        ForeignKey("summary_claims.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("file_path", Text, nullable=False),
    Column("start_line", Integer, nullable=False),
    Column("end_line", Integer, nullable=False),
    Column("relation", Text, nullable=False),
)

project_metadata = Table(
    "project_metadata",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
)

# --- Semantic evidence layer (TOCS contract) -------------------------------
# Typed SemanticRelation + embedded EvidenceRef, stored independently of the
# raw edges table. The raw graph (nodes/edges/dataflow_edges) is the backend
# representation; this layer is the provider-neutral contract surface. A
# SemanticRelation may map 1:1 to a raw edge (e.g. CALLS ← EdgeKind.CALLS) or
# be derived (e.g. OWNS_CONTROL ← PARAMETER node + CONTAINS edge), but the
# stored relation carries its own typed identity, provenance, and authority.

semantic_relations = Table(
    "semantic_relations",
    metadata,
    Column("relation_id", Text, primary_key=True),
    Column("dataset_id", Text, nullable=False),
    Column("subject_entity_id", Text, nullable=False),
    Column("relation_kind", Text, nullable=False),
    Column("object_entity_id", Text),
    Column("literal_object", Text),  # JSON-encoded; null when object is an entity
    Column("scenario_id", Text),
    Column("condition_expression", Text),  # JSON-encoded
    Column("modality", Text, nullable=False),
    Column("authority_scope", Text, nullable=False),
    Column("extraction_method", Text, nullable=False),
    Column("extractor_version", Text, nullable=False),
    Column("confidence", Float),
)

semantic_evidence_refs = Table(
    "semantic_evidence_refs",
    metadata,
    Column("evidence_ref_id", Text, primary_key=True),
    Column(
        "relation_id",
        Text,
        ForeignKey("semantic_relations.relation_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("evidence_kind", Text, nullable=False),
    Column("repository_id", Text, nullable=False),
    Column("revision", Text, nullable=False),
    Column("path_or_document_id", Text, nullable=False),
    Column("start_line", Integer),
    Column("end_line", Integer),
    Column("symbol_or_section", Text),
    Column("graph_node_ids", Text),  # JSON-encoded list
    Column("content_digest", Text, nullable=False),
    Column("excerpt", Text),
    Column("dataset_id", Text, nullable=False),
)

semantic_dataset_manifests = Table(
    "semantic_dataset_manifests",
    metadata,
    Column("build_id", Text, primary_key=True),
    Column("instance_id", Text, nullable=False),
    Column("graph_kind", Text, nullable=False),
    Column("repository_id", Text, nullable=False),
    Column("revision_scheme", Text, nullable=False),
    Column("revision_value", Text, nullable=False),
    Column("source_revision", Text),
    Column("revision_mapping_status", Text, nullable=False),
    Column("built_at", BigInteger, nullable=False),
    Column("schema_version", Text, nullable=False),
    Column("extractor_versions", Text, nullable=False),  # JSON-encoded
    Column("capabilities_ref", Text, nullable=False),
)

semantic_capability_manifests = Table(
    "semantic_capability_manifests",
    metadata,
    Column("capabilities_ref", Text, primary_key=True),
    Column("instance_id", Text, nullable=False),
    Column("schema_version", Text, nullable=False),
    Column("capabilities", Text, nullable=False),  # JSON-encoded dict
    Column("limitations", Text),  # JSON-encoded list
)
