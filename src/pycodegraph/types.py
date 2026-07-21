"""Core type definitions for CodeGraph."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

# =============================================================================
# Internal: InlineFact — typed fact extracted during Tree-sitter traversal
#
# Carried in-memory to SemanticLayerBuilder.build() via ExtractionResult /
# IndexResult / CodeGraph._last_inline_facts. Not persisted to DB at extraction
# time. Each InlineFact is flushed to a SemanticRelation row by the semantic
# layer during build_semantic_layer(). See issue #114.
# =============================================================================


@dataclass
class InlineFact:
    """A typed semantic fact extracted during Tree-sitter traversal.

    Carries enough context for SemanticLayerBuilder to assemble into a
    :class:`SemanticRelation` during :meth:`build_semantic_layer`. No DB reads
    are needed at flush time.

    ``subject_node_id`` is optional — when the subject is not a CodeGraph Node
    (e.g. a call site, which has no Node representation), the caller provides
    ``subject_qualified_name`` and ``source_locator`` instead. The flush
    helper uses ``::L{line}`` suffix on the qualified name for call-site
    subjects.
    """

    relation_kind: str
    subject_node_id: str | None
    subject_qualified_name: str
    subject_file_path: str
    object_literal: str | None = None
    object_node_id: str | None = None
    start_line: int = 0
    end_line: int = 0
    evidence_kind: str = ""
    extraction_method: str = ""
    metadata: dict = field(default_factory=dict)


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
    # InlineFacts collected during Tree-sitter traversal (issue #114).
    # Carried in-memory to SemanticLayerBuilder.build() — not persisted
    # to DB at extraction time. Empty for languages without an
    # ``extract_inline_facts`` hook.
    inline_facts: list[InlineFact] = field(default_factory=list)


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
    # InlineFacts aggregated across all files indexed in this run (issue
    # #114). Caller passes them to CodeGraph.build_semantic_layer() so
    # the semantic layer can flush them as SemanticRelation rows without
    # re-reading source files.
    inline_facts: list[InlineFact] = field(default_factory=list)


# =============================================================================
# Summary Claim Types (ADR-0004 semantic overlay)
# =============================================================================


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


# =============================================================================
# Summary Claims (semantic overlay — ADR-0004)
# =============================================================================


@dataclass
class ClaimGrounding:
    """A code location a Summary Claim is grounded in.

    Grounding spans are line ranges in source text, not Node references: a span
    persists even when it corresponds to no indexed Node. ``relation`` is the
    discriminator for how the claim relates to the location (e.g. ``subject``,
    ``evidence``).
    """

    file_path: str
    start_line: int
    end_line: int
    relation: str


@dataclass
class SummaryClaim:
    """An external, LLM-derived proposition about code, stored independently of
    the deterministic ``nodes``/``edges`` graph (ADR-0004).

    A claim is not a Symbol — it has no ``qualified_name``, ``file_path``, or
    source position of its own; it is grounded through its :class:`ClaimGrounding`
    spans. ``claim_type`` is the single discriminator (e.g.
    ``behavior_contract``) replacing the rejected multi-node-kind design.
    """

    claim_type: str
    claim_text: str
    groundings: list[ClaimGrounding] = field(default_factory=list)


@dataclass
class ClaimHit:
    """A retrieved Summary Claim bundled with its grounding spans.

    Returned by claim full-text search. No Node objects are attached — callers
    receive line ranges only. ``score`` is the (positive) FTS relevance score.
    """

    claim_text: str
    claim_type: str
    groundings: list[ClaimGrounding] = field(default_factory=list)
    score: float = 0.0
