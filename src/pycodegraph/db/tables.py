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

project_metadata = Table(
    "project_metadata",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
)
