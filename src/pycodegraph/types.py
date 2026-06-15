"""Core type definitions for CodeGraph."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

# =============================================================================
# Enum-like constants
# =============================================================================


class NodeKind(StrEnum):
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


# Node kinds that represent containers (types that own children via
# CONTAINS edges).  Shared by traversal, clustering, and other modules
# so the definition stays in one place.
CONTAINER_KINDS: frozenset[NodeKind] = frozenset(
    [
        NodeKind.CLASS,
        NodeKind.INTERFACE,
        NodeKind.STRUCT,
        NodeKind.TRAIT,
        NodeKind.PROTOCOL,
        NodeKind.MODULE,
        NodeKind.ENUM,
    ]
)


class EdgeKind(StrEnum):
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
    TESTS = "tests"
    # Semantic marker only — dataflow edges are NOT stored in the ``edges``
    # table; they live in ``dataflow_edges`` keyed by line ranges, not Node IDs.
    DATAFLOW = "dataflow"


class Language(StrEnum):
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
    docstring: str | None = None
    signature: str | None = None
    visibility: str | None = None
    is_exported: bool = False
    is_async: bool = False
    is_static: bool = False
    is_abstract: bool = False
    decorators: str | None = None  # JSON
    type_parameters: str | None = None  # JSON


@dataclass
class Edge:
    source: str
    target: str
    kind: EdgeKind
    metadata: str | None = None  # JSON
    line: int | None = None
    col: int | None = None
    provenance: str | None = None


@dataclass
class DataflowEdge:
    """A data-flow fact: a *variable* flows from a source statement to a target
    statement, both line ranges within a single *function_id*.

    Unlike :class:`Edge`, endpoints are ``(file_path, start_line, end_line)``
    triples, not Node IDs — Statements are not Symbols and are not stored in the
    ``nodes`` table. Dataflow edges live in their own ``dataflow_edges`` table.
    """

    file_path: str
    source_start_line: int
    source_end_line: int
    target_start_line: int
    target_end_line: int
    variable: str
    function_id: str
    provenance: str | None = None


@dataclass
class StatementRef:
    """A reference to one statement in source text.

    The consumer-side handle for the Dataflow Slice API. A statement is the
    granularity unit of Dataflow Edges — identified by its position
    ``(file_path, start_line, end_line)`` in source text, never stored as a Node.
    """

    file_path: str
    start_line: int
    end_line: int
    source_text: str | None = None  # actual source, filled lazily by the slicer
    function_name: str | None = None  # function the statement lives in


@dataclass
class DataflowSliceEdge:
    """A data-flow edge as exposed to slice consumers.

    Maps a storage-layer :class:`DataflowEdge` to :class:`StatementRef`
    endpoints. Distinct from :class:`DataflowEdge` (the storage row), which is
    keyed by raw line ranges.
    """

    source: StatementRef
    target: StatementRef
    variable: str


@dataclass
class DataflowSlice:
    """The result of a bounded BFS over Dataflow Edges.

    ``seed`` is ``None`` when no dataflow edge touches the requested line
    (an empty slice).
    """

    statements: list[StatementRef] = field(default_factory=list)
    edges: list[DataflowSliceEdge] = field(default_factory=list)
    seed: StatementRef | None = None


@dataclass
class UnresolvedReference:
    from_node_id: str
    reference_name: str
    reference_kind: EdgeKind
    line: int
    column: int
    file_path: str = ""
    language: str = "unknown"


@dataclass
class ExtractionError:
    message: str
    severity: str = "error"
    file_path: str | None = None
    line: int | None = None
    column: int | None = None
    code: str | None = None


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
    errors: str | None = None  # JSON


@dataclass
class IndexResult:
    success: bool
    files_indexed: int = 0
    files_skipped: int = 0
    files_errored: int = 0
    nodes_created: int = 0
    edges_created: int = 0
    refs_resolved: int = 0
    refs_unresolved: int = 0
    errors: list[ExtractionError] = field(default_factory=list)
    duration_ms: int = 0


# =============================================================================
# Query Types
# =============================================================================


@dataclass
class Subgraph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    roots: list[str] = field(default_factory=list)


@dataclass
class TraversalOptions:
    max_depth: float = float("inf")
    edge_kinds: list[EdgeKind] = field(default_factory=list)
    node_kinds: list[NodeKind] = field(default_factory=list)
    direction: str = "outgoing"  # 'outgoing' | 'incoming' | 'both'
    limit: int = 1000
    include_start: bool = True


@dataclass
class SearchOptions:
    kinds: list[NodeKind] | None = None
    languages: list[Language] | None = None
    include_patterns: list[str] | None = None
    exclude_patterns: list[str] | None = None
    limit: int = 100
    offset: int = 0
    case_sensitive: bool = False


@dataclass
class SearchResult:
    node: Node
    score: float = 0.0
    highlights: list[str] | None = None


@dataclass
class Context:
    focal: Node | None = None
    ancestors: list[Node] = field(default_factory=list)
    children: list[Node] = field(default_factory=list)
    incoming_refs: list[dict] = field(default_factory=list)  # [{node, edge}]
    outgoing_refs: list[dict] = field(default_factory=list)  # [{node, edge}]
    types: list[Node] = field(default_factory=list)
    imports: list[Node] = field(default_factory=list)


@dataclass
class CodeBlock:
    content: str
    file_path: str
    start_line: int
    end_line: int
    language: Language
    node: Node | None = None


@dataclass
class BuildContextOptions:
    max_nodes: int = 20
    max_code_blocks: int = 5
    max_code_block_size: int = 1500
    include_code: bool = True
    format: str = "markdown"  # 'markdown' | 'json'
    search_limit: int = 3
    traversal_depth: int = 1
    min_score: float = 0.3


@dataclass
class FindRelevantContextOptions:
    search_limit: int = 3
    traversal_depth: int = 1
    max_nodes: int = 20
    min_score: float = 0.3
    edge_kinds: list[EdgeKind] = field(default_factory=list)
    node_kinds: list[NodeKind] = field(default_factory=list)


@dataclass
class TaskContext:
    query: str
    subgraph: Subgraph
    entry_points: list[Node] = field(default_factory=list)
    code_blocks: list[CodeBlock] = field(default_factory=list)
    related_files: list[str] = field(default_factory=list)
    summary: str = ""
    stats: dict | None = None


# =============================================================================
# Explore Types
# =============================================================================


@dataclass
class ExploreOptions:
    """Options for the explore() method."""

    max_files: int | None = None
    max_output_chars: int | None = None
    max_chars_per_file: int | None = None
    include_relationships: bool = True
    include_flow: bool = True
    include_blast_radius: bool = True


@dataclass
class ExploreOutputBudget:
    """Adaptive output budget determined by project size."""

    max_output_chars: int
    default_max_files: int
    max_chars_per_file: int
    gap_threshold: int
    max_symbols_in_header: int
    include_budget_note: bool = False

    @classmethod
    def from_file_count(cls, file_count: int) -> ExploreOutputBudget:
        """Compute budget based on indexed file count."""
        if file_count < 150:
            return cls(13_000, 4, 3_800, 7, 5)
        elif file_count < 500:
            return cls(18_000, 5, 3_800, 8, 6)
        elif file_count < 5_000:
            return cls(24_000, 8, 6_500, 12, 10, True)
        else:
            return cls(24_000, 8, 7_000, 15, 15, True)
