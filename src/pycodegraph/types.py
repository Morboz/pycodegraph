"""Core type definitions for CodeGraph."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# =============================================================================
# Enum-like constants
# =============================================================================

class NodeKind(str, Enum):
    FILE = "file"
    MODULE = "module"
    CLASS = "class"
    STRUCT = "struct"
    INTERFACE = "interface"
    TRAIT = "trait"
    PROTOCOL = "protocol"
    FUNCTION = "function"
    METHOD = "method"
    PROPERTY = "property"
    FIELD = "field"
    VARIABLE = "variable"
    CONSTANT = "constant"
    ENUM = "enum"
    ENUM_MEMBER = "enum_member"
    TYPE_ALIAS = "type_alias"
    NAMESPACE = "namespace"
    PARAMETER = "parameter"
    IMPORT = "import"
    EXPORT = "export"
    ROUTE = "route"
    COMPONENT = "component"


class EdgeKind(str, Enum):
    CONTAINS = "contains"
    CALLS = "calls"
    IMPORTS = "imports"
    EXPORTS = "exports"
    EXTENDS = "extends"
    IMPLEMENTS = "implements"
    REFERENCES = "references"
    TYPE_OF = "type_of"
    RETURNS = "returns"
    INSTANTIATES = "instantiates"
    OVERRIDES = "overrides"
    DECORATES = "decorates"


class Language(str, Enum):
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    TSX = "tsx"
    JSX = "jsx"
    PYTHON = "python"
    GO = "go"
    RUST = "rust"
    JAVA = "java"
    C = "c"
    CPP = "cpp"
    CSHARP = "csharp"
    PHP = "php"
    RUBY = "ruby"
    SWIFT = "swift"
    KOTLIN = "kotlin"
    DART = "dart"
    UNKNOWN = "unknown"


# =============================================================================
# Core Graph Types
# =============================================================================

@dataclass
class Node:
    id: str
    kind: NodeKind
    name: str
    qualified_name: str
    file_path: str
    language: Language
    start_line: int
    end_line: int
    start_column: int
    end_column: int
    updated_at: int
    docstring: Optional[str] = None
    signature: Optional[str] = None
    visibility: Optional[str] = None
    is_exported: bool = False
    is_async: bool = False
    is_static: bool = False
    is_abstract: bool = False
    decorators: Optional[str] = None  # JSON
    type_parameters: Optional[str] = None  # JSON


@dataclass
class Edge:
    source: str
    target: str
    kind: EdgeKind
    metadata: Optional[str] = None  # JSON
    line: Optional[int] = None
    col: Optional[int] = None
    provenance: Optional[str] = None


@dataclass
class UnresolvedReference:
    from_node_id: str
    reference_name: str
    reference_kind: EdgeKind
    line: int
    column: int
    file_path: str = ""
    language: str = "unknown"
    candidates: Optional[str] = None  # JSON


@dataclass
class ExtractionError:
    message: str
    severity: str = "error"
    file_path: Optional[str] = None
    line: Optional[int] = None
    column: Optional[int] = None
    code: Optional[str] = None


@dataclass
class ExtractionResult:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    unresolved_references: list[UnresolvedReference] = field(default_factory=list)
    errors: list[ExtractionError] = field(default_factory=list)
    duration_ms: int = 0


@dataclass
class FileRecord:
    path: str
    content_hash: str
    language: Language
    size: int
    modified_at: float
    indexed_at: int
    node_count: int = 0
    errors: Optional[str] = None  # JSON


@dataclass
class IndexResult:
    success: bool
    files_indexed: int = 0
    files_skipped: int = 0
    files_errored: int = 0
    nodes_created: int = 0
    edges_created: int = 0
    errors: list[ExtractionError] = field(default_factory=list)
    duration_ms: int = 0
