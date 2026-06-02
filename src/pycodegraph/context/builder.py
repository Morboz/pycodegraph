"""Context builder - builds rich context by combining search with graph traversal."""

from __future__ import annotations

import os
import re

from ..db.queries import QueryBuilder
from ..graph.traversal import GraphTraverser
from ..search.query_utils import (
    extract_search_terms,
    get_stem_variants,
    is_test_file,
)
from ..search.searcher import NodeSearcher
from ..types import (
    BuildContextOptions,
    CodeBlock,
    Edge,
    EdgeKind,
    FindRelevantContextOptions,
    Node,
    NodeKind,
    SearchOptions,
    SearchResult,
    Subgraph,
    TaskContext,
    TraversalOptions,
)
from .formatter import format_context_as_json, format_context_as_markdown

# Node kinds with high information value in context results
_HIGH_VALUE_NODE_KINDS: list[NodeKind] = [
    NodeKind.FUNCTION,
    NodeKind.METHOD,
    NodeKind.CLASS,
    NodeKind.INTERFACE,
    NodeKind.TYPE_ALIAS,
    NodeKind.STRUCT,
    NodeKind.TRAIT,
    NodeKind.COMPONENT,
    NodeKind.ROUTE,
    NodeKind.VARIABLE,
    NodeKind.CONSTANT,
    NodeKind.ENUM,
    NodeKind.MODULE,
    NodeKind.NAMESPACE,
]

_DEFAULT_BUILD_OPTIONS = BuildContextOptions()
_DEFAULT_FIND_OPTIONS = FindRelevantContextOptions(
    node_kinds=_HIGH_VALUE_NODE_KINDS,
)

# Common English words to filter from symbol extraction
_COMMON_WORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "have",
        "been",
        "will",
        "would",
        "could",
        "should",
        "does",
        "done",
        "make",
        "made",
        "use",
        "used",
        "using",
        "work",
        "works",
        "find",
        "found",
        "show",
        "call",
        "called",
        "calling",
        "get",
        "set",
        "add",
        "all",
        "any",
        "how",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "not",
        "but",
        "are",
        "was",
        "were",
        "has",
        "had",
        "its",
        "can",
        "did",
        "may",
        "also",
        "into",
        "than",
        "then",
        "them",
        "each",
        "other",
        "some",
        "such",
        "only",
        "same",
        "about",
        "after",
        "before",
        "between",
        "through",
        "during",
        "without",
        "again",
        "further",
        "once",
        "here",
        "there",
        "both",
        "just",
        "more",
        "most",
        "very",
        "being",
        "having",
        "doing",
        "system",
        "need",
        "needs",
        "want",
        "wants",
        "like",
        "look",
        "change",
        "changes",
        "changed",
        "changing",
        "layer",
        "handle",
        "handles",
        "handling",
        "incoming",
        "outgoing",
        "data",
        "flow",
        "flows",
        "level",
        "levels",
        "request",
        "requests",
        "response",
        "responses",
        "implement",
        "implements",
        "implementation",
        "interface",
        "interfaces",
        "class",
        "classes",
        "method",
        "methods",
        "trigger",
        "triggers",
        "affected",
        "affect",
        "affects",
        "else",
        "code",
        "failing",
        "failed",
        "silently",
        "decide",
        "decides",
        "return",
        "returns",
        "returned",
        "take",
        "takes",
        "taken",
        "check",
        "checks",
        "checked",
        "create",
        "creates",
        "created",
        "read",
        "reads",
        "write",
        "writes",
        "written",
        "start",
        "starts",
        "stop",
        "stops",
        "run",
        "runs",
        "running",
    }
)


def _extract_symbols_from_query(query: str) -> list[str]:
    """Extract likely symbol names from a natural language query."""
    symbols: set[str] = set()

    # CamelCase
    for m in re.finditer(
        r"\b([A-Z][a-z]+(?:[A-Z][a-z]*)*|[a-z]+(?:[A-Z][a-z]*)+)\b",
        query,
    ):
        if len(m.group(1)) >= 2:
            symbols.add(m.group(1))

    # snake_case
    for m in re.finditer(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b", query, re.IGNORECASE):
        if len(m.group(1)) >= 3:
            symbols.add(m.group(1))

    # SCREAMING_SNAKE
    for m in re.finditer(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b", query):
        if m.group(1):
            symbols.add(m.group(1))

    # Acronyms
    for m in re.finditer(r"\b([A-Z]{2,})\b", query):
        if m.group(1):
            symbols.add(m.group(1))

    # dot.notation
    for m in re.finditer(
        r"\b([a-zA-Z][a-zA-Z0-9]*(?:\.[a-zA-Z][a-zA-Z0-9]*)+)\b", query
    ):
        parts = m.group(1).split(".")
        for part in parts:
            if len(part) >= 2:
                symbols.add(part)
        symbols.add(m.group(1))

    # Plain lowercase identifiers
    for m in re.finditer(r"\b([a-z][a-z0-9]{2,})\b", query):
        symbols.add(m.group(1))

    return [s for s in symbols if s.lower() not in _COMMON_WORDS]


class ContextBuilder:
    """Coordinates semantic search and graph traversal to build context."""

    def __init__(
        self, project_root: str, queries: QueryBuilder, traverser: GraphTraverser
    ) -> None:
        self._project_root = project_root
        self._queries = queries
        self._searcher = NodeSearcher(queries)
        self._traverser = traverser

    def build_context(
        self,
        task_input: str | dict,
        options: BuildContextOptions | None = None,
    ) -> TaskContext | str:
        """Build context for a task."""
        if options is None:
            opts = _DEFAULT_BUILD_OPTIONS
        elif isinstance(options, dict):
            opts = BuildContextOptions(**options)
        else:
            opts = options

        query = (
            task_input
            if isinstance(task_input, str)
            else task_input.get("title", "")
            + (
                f": {task_input['description']}"
                if task_input.get("description")
                else ""
            )
        )

        subgraph = self.find_relevant_context(
            query,
            FindRelevantContextOptions(
                search_limit=opts.search_limit,
                traversal_depth=opts.traversal_depth,
                max_nodes=opts.max_nodes,
                min_score=opts.min_score,
            ),
        )

        entry_points = [
            subgraph.nodes[rid] for rid in subgraph.roots if rid in subgraph.nodes
        ]

        code_blocks = (
            self._extract_code_blocks(
                subgraph, opts.max_code_blocks, opts.max_code_block_size
            )
            if opts.include_code
            else []
        )

        related_files = sorted({n.file_path for n in subgraph.nodes.values()})
        summary = self._generate_summary(query, subgraph, entry_points)
        stats = {
            "node_count": len(subgraph.nodes),
            "edge_count": len(subgraph.edges),
            "file_count": len(related_files),
            "code_block_count": len(code_blocks),
            "total_code_size": sum(len(b.content) for b in code_blocks),
        }

        context = TaskContext(
            query=query,
            subgraph=subgraph,
            entry_points=entry_points,
            code_blocks=code_blocks,
            related_files=related_files,
            summary=summary,
            stats=stats,
        )

        if opts.format == "markdown":
            return format_context_as_markdown(context)
        elif opts.format == "json":
            return format_context_as_json(context)
        return context

    def find_relevant_context(
        self,
        query: str,
        options: FindRelevantContextOptions | None = None,
    ) -> Subgraph:
        """Hybrid search pipeline combining exact lookup + FTS + graph traversal."""
        opts = options or _DEFAULT_FIND_OPTIONS
        nodes: dict[str, Node] = {}
        edges: list[Edge] = []
        roots: list[str] = []

        if not query or not query.strip():
            return Subgraph(nodes=nodes, edges=edges, roots=roots)

        # === HYBRID SEARCH ===

        # Step 1: Extract symbols and look up exact matches
        symbols = _extract_symbols_from_query(query)
        exact_matches: list[SearchResult] = []

        if symbols:
            kind_filter = (
                [k.value for k in opts.node_kinds] if opts.node_kinds else None
            )
            exact_matches = (
                self._searcher.find_nodes_by_exact_name(
                    symbols,
                    options=SearchOptions(kinds=[NodeKind(k) for k in kind_filter]),
                )
                if kind_filter
                else self._searcher.find_nodes_by_exact_name(symbols)
            )

            # Co-location boost
            if len(exact_matches) > 1:
                file_symbol_counts: dict[str, set[str]] = {}
                for r in exact_matches:
                    file_symbol_counts.setdefault(r.node.file_path, set()).add(
                        r.node.name.lower()
                    )
                for r in exact_matches:
                    count = len(file_symbol_counts.get(r.node.file_path, set()))
                    if count > 1:
                        r.score += (count - 1) * 20
                exact_matches.sort(key=lambda r: r.score, reverse=True)

            exact_matches = exact_matches[: max(opts.search_limit * 2, 1)]

        # Step 2b: Definition prefix matching with stem variants
        if symbols:
            definition_kinds = [
                NodeKind.CLASS,
                NodeKind.INTERFACE,
                NodeKind.STRUCT,
                NodeKind.TRAIT,
                NodeKind.PROTOCOL,
                NodeKind.ENUM,
                NodeKind.TYPE_ALIAS,
            ]
            expanded = set(symbols)
            for sym in symbols:
                expanded.update(get_stem_variants(sym))

            for sym in expanded:
                title_cased = sym[0].upper() + sym[1:].lower() if sym else ""
                if title_cased == sym:
                    continue
                kind_strs = [k.value for k in definition_kinds]
                prefix_results = self._searcher.find_nodes_by_name_substring(
                    title_cased,
                    kinds=kind_strs,
                    limit=30,
                )
                matched: list[SearchResult] = []
                for r in prefix_results:
                    if r.node.name.lower().startswith(title_cased.lower()):
                        brevity = max(0, 10 - (len(r.node.name) - len(title_cased)) / 3)
                        matched.append(
                            SearchResult(
                                node=r.node,
                                score=r.score + 15 + brevity,
                            )
                        )
                matched.sort(key=lambda r: r.score, reverse=True)
                for r in matched[: opts.search_limit]:
                    if not any(e.node.id == r.node.id for e in exact_matches):
                        exact_matches.append(r)

            exact_matches.sort(key=lambda r: r.score, reverse=True)
            exact_matches = exact_matches[: opts.search_limit * 3]

        # Step 3: Text search
        text_results: list[SearchResult] = []
        search_terms = extract_search_terms(query)
        if search_terms:
            term_results_map: dict[str, dict] = {}
            search_kinds = (
                [k.value for k in opts.node_kinds]
                if opts.node_kinds
                else [k.value for k in _HIGH_VALUE_NODE_KINDS]
            )
            for term in search_terms:
                term_results = self._searcher.search_nodes(
                    term,
                    SearchOptions(
                        kinds=[NodeKind(k) for k in search_kinds],
                        limit=opts.search_limit * 2,
                    ),
                )
                for r in term_results:
                    existing = term_results_map.get(r.node.id)
                    if existing:
                        existing["hits"] += 1
                        existing["result"].score = max(
                            existing["result"].score, r.score
                        )
                    else:
                        term_results_map[r.node.id] = {"result": r, "hits": 1}

            text_results = sorted(
                [
                    SearchResult(
                        node=info["result"].node,
                        score=info["result"].score + (info["hits"] - 1) * 5,
                    )
                    for info in term_results_map.values()
                ],
                key=lambda r: r.score,
                reverse=True,
            )[: opts.search_limit * 2]

        # Step 4: Merge results
        result_by_id: dict[str, SearchResult] = {}
        search_results: list[SearchResult] = []

        for r in exact_matches:
            if r.node.id in result_by_id:
                result_by_id[r.node.id].score = max(
                    result_by_id[r.node.id].score, r.score
                )
            else:
                result_by_id[r.node.id] = r
                search_results.append(r)

        for r in text_results:
            if r.node.id in result_by_id:
                result_by_id[r.node.id].score = max(
                    result_by_id[r.node.id].score, r.score
                )
            else:
                result_by_id[r.node.id] = r
                search_results.append(r)

        # Deprioritize test files
        query_lower = query.lower()
        is_test_query = "test" in query_lower or "spec" in query_lower
        if not is_test_query:
            for r in search_results:
                if is_test_file(r.node.file_path):
                    r.score *= 0.3

        search_results.sort(key=lambda r: r.score, reverse=True)
        search_results = search_results[: opts.search_limit * 3]

        filtered = [r for r in search_results if r.score >= opts.min_score]
        filtered = self._resolve_imports_to_definitions(filtered)
        filtered = filtered[: opts.search_limit]

        for r in filtered:
            nodes[r.node.id] = r.node
            roots.append(r.node.id)

        # Type hierarchy expansion
        type_kinds = {
            NodeKind.CLASS,
            NodeKind.INTERFACE,
            NodeKind.STRUCT,
            NodeKind.TRAIT,
            NodeKind.PROTOCOL,
        }
        max_hierarchy = max(1, opts.max_nodes // 4)
        hierarchy_added = 0

        for r in filtered:
            if hierarchy_added >= max_hierarchy:
                break
            if r.node.kind in type_kinds:
                hierarchy = self._traverser.get_type_hierarchy(r.node.id)
                for nid, h_node in hierarchy.nodes.items():
                    if nid not in nodes:
                        nodes[nid] = h_node
                        hierarchy_added += 1
                for edge in hierarchy.edges:
                    if not any(
                        e.source == edge.source
                        and e.target == edge.target
                        and e.kind == edge.kind
                        for e in edges
                    ):
                        edges.append(edge)

        # BFS traversal from entry points
        node_kind_filter = opts.node_kinds if opts.node_kinds else None

        for r in filtered:
            traversal = self._traverser.traverse_bfs(
                r.node.id,
                TraversalOptions(
                    max_depth=opts.traversal_depth,
                    edge_kinds=opts.edge_kinds if opts.edge_kinds else [],
                    node_kinds=node_kind_filter or [],
                    direction="both",
                    limit=max(1, opts.max_nodes // max(1, len(filtered))),
                ),
            )

            for nid, t_node in traversal.nodes.items():
                if nid not in nodes:
                    nodes[nid] = t_node

            for edge in traversal.edges:
                if not any(
                    e.source == edge.source
                    and e.target == edge.target
                    and e.kind == edge.kind
                    for e in edges
                ):
                    edges.append(edge)

        # Per-file diversity cap (~20%)
        max_per_file = max(5, opts.max_nodes // 5)
        file_counts: dict[str, list[str]] = {}
        for nid, node in nodes.items():
            file_counts.setdefault(node.file_path, []).append(nid)

        root_set = set(roots)
        kind_priority = {
            NodeKind.CLASS: 3,
            NodeKind.INTERFACE: 3,
            NodeKind.STRUCT: 3,
            NodeKind.TRAIT: 3,
            NodeKind.PROTOCOL: 3,
            NodeKind.ENUM: 3,
        }
        for _, node_ids in file_counts.items():
            if len(node_ids) <= max_per_file:
                continue
            node_ids.sort(
                key=lambda nid: (
                    (10 if nid in root_set else 0)
                    + kind_priority.get(nodes[nid].kind, 0)
                ),
                reverse=True,
            )
            for nid in node_ids[max_per_file:]:
                del nodes[nid]

        # Cap non-production nodes
        if not is_test_query:
            max_non_prod = max(3, opts.max_nodes * 15 // 100)
            non_prod = [nid for nid, n in nodes.items() if is_test_file(n.file_path)]
            if len(non_prod) > max_non_prod:
                for nid in non_prod[max_non_prod:]:
                    del nodes[nid]
                    if nid in roots:
                        roots.remove(nid)

        # Filter edges to kept nodes
        edges = [e for e in edges if e.source in nodes and e.target in nodes]

        # Edge recovery: discover edges between selected nodes
        recovery_kinds = [
            EdgeKind.CALLS,
            EdgeKind.EXTENDS,
            EdgeKind.IMPLEMENTS,
            EdgeKind.REFERENCES,
            EdgeKind.OVERRIDES,
        ]
        recovered = self._queries.find_edges_between_nodes(
            list(nodes.keys()), [k.value for k in recovery_kinds]
        )
        existing_keys = {f"{e.source}:{e.target}:{e.kind}" for e in edges}
        for edge in recovered:
            key = f"{edge.source}:{edge.target}:{edge.kind}"
            if key not in existing_keys:
                edges.append(edge)
                existing_keys.add(key)

        return Subgraph(nodes=nodes, edges=edges, roots=roots)

    def get_code(self, node_id: str) -> str | None:
        """Get source code for a node."""
        node = self._queries.get_node_by_id(node_id)
        if not node:
            return None
        return self._extract_node_code(node)

    # =========================================================================
    # Private helpers
    # =========================================================================

    def _extract_node_code(self, node: Node) -> str | None:
        file_path = os.path.join(self._project_root, node.file_path)
        if not os.path.exists(file_path):
            return None
        try:
            with open(file_path) as f:
                lines = f.read().split("\n")
            start = max(0, node.start_line - 1)
            end = min(len(lines), node.end_line)
            return "\n".join(lines[start:end])
        except (OSError, UnicodeDecodeError):
            return None

    def _extract_code_blocks(
        self,
        subgraph: Subgraph,
        max_blocks: int,
        max_size: int,
    ) -> list[CodeBlock]:
        blocks: list[CodeBlock] = []
        priority: list[Node] = []

        # Entry points first
        for rid in subgraph.roots:
            node = subgraph.nodes.get(rid)
            if node:
                priority.append(node)

        # Functions/methods
        for node in subgraph.nodes.values():
            if node.id not in subgraph.roots and node.kind in (
                NodeKind.FUNCTION,
                NodeKind.METHOD,
            ):
                priority.append(node)

        # Classes
        for node in subgraph.nodes.values():
            if node.id not in subgraph.roots and node.kind == NodeKind.CLASS:
                priority.append(node)

        for node in priority:
            if len(blocks) >= max_blocks:
                break
            code = self._extract_node_code(node)
            if code:
                truncated = (
                    code[:max_size] + "\n# ... truncated ..."
                    if len(code) > max_size
                    else code
                )
                blocks.append(
                    CodeBlock(
                        content=truncated,
                        file_path=node.file_path,
                        start_line=node.start_line,
                        end_line=node.end_line,
                        language=node.language,
                        node=node,
                    )
                )

        return blocks

    def _resolve_imports_to_definitions(
        self, results: list[SearchResult]
    ) -> list[SearchResult]:
        resolved: list[SearchResult] = []
        seen: set[str] = set()

        for r in results:
            node = r.node
            if node.kind not in (NodeKind.IMPORT, NodeKind.EXPORT):
                if node.id not in seen:
                    seen.add(node.id)
                    resolved.append(r)
                continue

            edge_kind = (
                EdgeKind.IMPORTS.value
                if node.kind == NodeKind.IMPORT
                else EdgeKind.EXPORTS.value
            )
            outgoing = self._queries.get_outgoing_edges(node.id, [edge_kind])

            for edge in outgoing:
                target = self._queries.get_node_by_id(edge.target)
                if target and target.id not in seen:
                    seen.add(target.id)
                    resolved.append(SearchResult(node=target, score=r.score))

        return resolved

    def _generate_summary(
        self, query: str, subgraph: Subgraph, entry_points: list[Node]
    ) -> str:
        entry_names = ", ".join(n.name for n in entry_points[:3])
        remaining = (
            f" and {len(entry_points) - 3} more" if len(entry_points) > 3 else ""
        )
        files = sorted({n.file_path for n in subgraph.nodes.values()})
        return (
            f"Found {len(subgraph.nodes)} relevant code symbols across {len(files)} files. "
            f"Key entry points: {entry_names}{remaining}. "
            f"{len(subgraph.edges)} relationships identified."
        )


def create_context_builder(
    project_root: str,
    queries: QueryBuilder,
    traverser: GraphTraverser,
) -> ContextBuilder:
    return ContextBuilder(project_root, queries, traverser)
