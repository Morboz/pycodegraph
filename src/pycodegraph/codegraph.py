"""CodeGraph - Main entry point for the code knowledge graph system."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .claims import ClaimOverlay
from .config import (
    CODEGRAPH_DIR,
    CodeGraphConfig,
    create_default_config,
    get_db_url,
    load_config,
    save_config,
)
from .db import DatabaseConnection
from .db.queries import QueryBuilder
from .explore.engine import ExploreEngine
from .extraction import ExtractionOrchestrator
from .fs import FileProvider, LocalFileProvider
from .graph import GraphQueryManager, GraphTraverser
from .resolution import create_resolver
from .resolution.resolver import ReferenceResolver
from .search.query_utils import derive_project_name_tokens
from .search.searcher import NodeSearcher
from .semantic import SemanticBuildResult, SemanticLayerBuilder
from .test_analysis import TestAnalyzer
from .types import (
    ClaimHit,
    Context,
    Edge,
    ExploreOptions,
    IndexResult,
    InlineFact,
    Node,
    Subgraph,
    SummaryClaim,
)


def _create_components(
    project_root: str,
    config: CodeGraphConfig,
    queries: QueryBuilder,
    file_provider: FileProvider | None = None,
) -> tuple[
    NodeSearcher,
    ExtractionOrchestrator,
    GraphTraverser,
    GraphQueryManager,
    ExploreEngine,
    ReferenceResolver,
    TestAnalyzer,
    FileProvider,
]:
    """Build all collaborator objects for CodeGraph."""
    if file_provider is None:
        file_provider = LocalFileProvider(project_root)
    try:
        project_name_tokens = derive_project_name_tokens(project_root)
    except Exception:
        project_name_tokens = set()
    searcher = NodeSearcher(queries, project_name_tokens=project_name_tokens)
    orchestrator = ExtractionOrchestrator(project_root, config, queries)
    traverser = GraphTraverser(queries)
    graph_manager = GraphQueryManager(queries, traverser)
    explore_engine = ExploreEngine(
        project_root, queries, traverser, searcher, file_provider
    )
    resolver = create_resolver(project_root, queries, file_provider)
    test_analyzer = TestAnalyzer(queries)
    return (
        searcher,
        orchestrator,
        traverser,
        graph_manager,
        explore_engine,
        resolver,
        test_analyzer,
        file_provider,
    )


class CodeGraph:
    """Main CodeGraph class providing init, index, and query operations."""

    def __init__(
        self,
        db: DatabaseConnection,
        queries: QueryBuilder,
        config: CodeGraphConfig,
        project_root: str,
        *,
        searcher: NodeSearcher,
        orchestrator: ExtractionOrchestrator,
        traverser: GraphTraverser,
        graph_manager: GraphQueryManager,
        explore_engine: ExploreEngine,
        resolver: ReferenceResolver,
        test_analyzer: TestAnalyzer,
        file_provider: FileProvider | None = None,
    ):
        self._db = db
        self._conn = db.get_connection()
        self._queries = queries
        self._searcher = searcher
        self._config = config
        self._project_root = project_root
        self._orchestrator = orchestrator
        self._traverser = traverser
        self._graph_manager = graph_manager
        self._explore_engine = explore_engine
        self._resolver = resolver
        self._test_analyzer = test_analyzer
        self._claim_overlay = ClaimOverlay(queries)
        # FileProvider for reading source files on demand (issue #116).
        # Default to LocalFileProvider(project_root) when caller didn't pass one.
        if file_provider is None:
            file_provider = LocalFileProvider(project_root)
        self._file_provider: FileProvider = file_provider
        # Cache for InlineFacts from the most recent index_all() call
        # (issue #114). Passed to build_semantic_layer() when not explicitly
        # provided, so the caller can index_all() then build_semantic_layer()
        # without re-reading source files.
        self._last_inline_facts: list[InlineFact] = []

    # =========================================================================
    # Lifecycle
    # =========================================================================

    @classmethod
    def init(
        cls,
        project_root: str,
        config_overrides: dict | None = None,
    ) -> CodeGraph:
        """Initialize a new CodeGraph project.

        Creates .codegraph/ directory, SQLite database, and default config.
        """
        root = str(Path(project_root).resolve())
        codegraph_dir = Path(root) / CODEGRAPH_DIR

        # For SQLite: check db file; for PG/other: skip (schema is idempotent)
        using_external_db = bool(
            config_overrides
            and config_overrides.get("db_url")
            and not config_overrides["db_url"].startswith("sqlite")
        )
        if (
            not using_external_db
            and codegraph_dir.exists()
            and (codegraph_dir / "codegraph.db").exists()
        ):
            raise FileExistsError(f"CodeGraph already initialized in {root}")

        # Create directory
        codegraph_dir.mkdir(parents=True, exist_ok=True)
        gitignore_path = codegraph_dir / ".gitignore"
        gitignore_path.write_text("*.db\n*.db-wal\n*.db-shm\ncache/\n*.log\n.dirty\n")

        # Save config
        config = create_default_config(root)
        if config_overrides:
            for k, v in config_overrides.items():
                if hasattr(config, k):
                    setattr(config, k, v)
        save_config(root, config)

        # Initialize database
        db_url = get_db_url(root, config)
        db = DatabaseConnection.initialize(db_url)
        queries = QueryBuilder(db.get_connection())

        (
            searcher,
            orchestrator,
            traverser,
            graph_manager,
            explore_engine,
            resolver,
            test_analyzer,
            file_provider,
        ) = _create_components(root, config, queries)
        return cls(
            db,
            queries,
            config,
            root,
            searcher=searcher,
            orchestrator=orchestrator,
            traverser=traverser,
            graph_manager=graph_manager,
            explore_engine=explore_engine,
            resolver=resolver,
            test_analyzer=test_analyzer,
            file_provider=file_provider,
        )

    @classmethod
    def open(cls, project_root: str) -> CodeGraph:
        """Open an existing CodeGraph project."""
        root = str(Path(project_root).resolve())

        config = load_config(root)
        db_url = get_db_url(root, config)

        if db_url.startswith("sqlite:///"):
            db_path = Path(db_url[len("sqlite:///") :])
            if not db_path.exists():
                raise FileNotFoundError(
                    f"CodeGraph not initialized in {root}. Run init() first."
                )

        db = DatabaseConnection.open(db_url)
        queries = QueryBuilder(db.get_connection())

        (
            searcher,
            orchestrator,
            traverser,
            graph_manager,
            explore_engine,
            resolver,
            test_analyzer,
            file_provider,
        ) = _create_components(root, config, queries)
        return cls(
            db,
            queries,
            config,
            root,
            searcher=searcher,
            orchestrator=orchestrator,
            traverser=traverser,
            graph_manager=graph_manager,
            explore_engine=explore_engine,
            resolver=resolver,
            test_analyzer=test_analyzer,
            file_provider=file_provider,
        )

    @classmethod
    def open_from_url(
        cls,
        db_url: str,
        project_root: str = "",
        file_provider: FileProvider | None = None,
    ) -> CodeGraph:
        """Open a CodeGraph from an explicit DB URL (e.g., a PostgreSQL schema URL).

        Unlike open(), this does not require a .codegraph/ directory on disk.
        Useful for connecting to externally-managed databases.

        Args:
            db_url: The database URL to connect to.
            project_root: Root directory of the project. Defaults to ``""`` (empty
                string), which resolves to the current working directory when
                ``index_*`` methods are called. Pass an explicit path if you intend
                to use any indexing methods.
            file_provider: Optional :class:`FileProvider` for reading source files.
                When ``None`` (default), a :class:`LocalFileProvider` is used,
                which reads from the local filesystem at *project_root*. Pass a
                custom provider (e.g. one that reads from a database table) when
                source files are not available on the local filesystem.
        """
        if db_url.startswith("sqlite:///"):
            db_path = Path(db_url[len("sqlite:///") :])
            if not db_path.exists():
                raise FileNotFoundError(f"SQLite database not found: {db_path}")

        db = DatabaseConnection.open(db_url)
        queries = QueryBuilder(db.get_connection())
        config = CodeGraphConfig(db_url=db_url)

        (
            searcher,
            orchestrator,
            traverser,
            graph_manager,
            explore_engine,
            resolver,
            test_analyzer,
            fp,
        ) = _create_components(project_root, config, queries, file_provider)
        return cls(
            db,
            queries,
            config,
            project_root,
            searcher=searcher,
            orchestrator=orchestrator,
            traverser=traverser,
            graph_manager=graph_manager,
            explore_engine=explore_engine,
            resolver=resolver,
            test_analyzer=test_analyzer,
            file_provider=fp,
        )

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()

    # =========================================================================
    # Indexing
    # =========================================================================

    def index_all(
        self,
        on_progress: Callable | None = None,
    ) -> IndexResult:
        """Index all files in the project.

        Args:
            on_progress: Optional callback(phase, current, total, current_file) for progress.
        """
        result = self._orchestrator.index_all(on_progress)

        if result.success:
            resolution_result = self._resolver.resolve_and_persist(on_progress)
            result.edges_created += resolution_result.stats.get("resolved", 0)
            result.refs_resolved = resolution_result.stats.get("resolved", 0)
            result.refs_unresolved = resolution_result.stats.get("unresolved", 0)

            test_analysis_result = self._test_analyzer.analyze_and_persist(on_progress)
            result.edges_created += test_analysis_result.edges_created

        # Cache inline_facts so build_semantic_layer() can flush them
        # without the caller explicitly passing them (issue #114).
        self._last_inline_facts = result.inline_facts

        return result

    def index_file(self, file_path: str) -> None:
        """Index a single file (relative path)."""
        self._orchestrator.index_file(file_path)

    def delete_file(self, file_path: str) -> None:
        """Remove a file and all its nodes, edges, unresolved references, and file record from the graph."""
        self._queries.delete_file(file_path)

    def apply_delta(
        self,
        changed_files: list[str],
        removed_files: list[str],
        *,
        on_progress: Callable | None = None,
    ) -> IndexResult:
        """Apply incremental changes: index changed files, delete removed files, then resolve.

        Indexes all ``changed_files`` first, then deletes all ``removed_files``.
        If no fatal extraction errors (``severity=="error"``) occur,
        ``resolve_and_persist`` is called once to rebuild cross-file reference
        edges.  Non-fatal errors (e.g. ``severity=="warning"``) are collected and
        returned but do not prevent resolution or flip ``success`` to ``False``.

        Args:
            changed_files: Relative paths of files that were added or modified.
            removed_files: Relative paths of files that were deleted.
            on_progress: Optional callback(phase, current, total, current_file) for progress.

        Returns:
            IndexResult with:
              - ``success``: True when no fatal (severity=="error") extraction errors occurred.
              - ``files_indexed``: Number of entries in ``changed_files``.
              - ``nodes_created``: Total nodes extracted from changed files.
              - ``edges_created``: Structural edges extracted plus resolved reference edges.
              - ``refs_resolved``: Number of cross-file references resolved.
              - ``refs_unresolved``: Number of cross-file references left unresolved.
              - ``errors``: List of all ExtractionErrors (including warnings) for any files.
        """
        total_nodes = 0
        total_edges = 0
        errors = []
        refs_resolved = 0
        refs_unresolved = 0

        for path in changed_files:
            result = self._orchestrator.index_file(path)
            total_nodes += len(result.nodes)
            total_edges += len(result.edges)
            if result.errors:
                errors.extend(result.errors)

        for path in removed_files:
            self._queries.delete_file(path)

        fatal_errors = [e for e in errors if e.severity == "error"]
        if not fatal_errors:
            resolution_result = self._resolver.resolve_and_persist(on_progress)
            total_edges += resolution_result.stats.get("resolved", 0)
            refs_resolved = resolution_result.stats.get("resolved", 0)
            refs_unresolved = resolution_result.stats.get("unresolved", 0)

            test_analysis_result = self._test_analyzer.analyze_and_persist(on_progress)
            total_edges += test_analysis_result.edges_created

        return IndexResult(
            success=not fatal_errors,
            files_indexed=len(changed_files),
            nodes_created=total_nodes,
            edges_created=total_edges,
            refs_resolved=refs_resolved,
            refs_unresolved=refs_unresolved,
            errors=errors,
        )

    # =========================================================================
    # Semantic evidence layer (TOCS contract)
    # =========================================================================

    def build_semantic_layer(
        self,
        *,
        repository_id: str,
        revision_value: str,
        built_at: int,
        instance_id: str = "default",
        inline_facts: list[InlineFact] | None = None,
    ) -> SemanticBuildResult:
        """Build the TOCS semantic evidence layer over the indexed graph.

        Runs relation-specific extractors, measures capability support, and
        publishes the dataset + capability manifests. Opt-in and separate
        from :meth:`index_all` so existing users are unaffected while the
        contract layer matures.

        Caller supplies ``built_at`` (epoch seconds) and ``revision_value``
        (full git commit id when available — COMMON-002) so the build stays
        deterministic from the caller's perspective; this library does not
        read the system clock or the git CLI.

        ``inline_facts`` (issue #114): typed facts collected during the
        preceding Tree-sitter traversal. When ``None`` (the default), falls
        back to the cached ``_last_inline_facts`` from the most recent
        :meth:`index_all` call. Pass an explicit list to override.

        Skeleton state: registered extractors return empty relation lists,
        so the returned manifests will show every capability as
        ``unavailable``. The pipeline shape and manifest computation are
        what's exercised — real extraction logic lands one relation at a
        time.
        """
        facts = inline_facts if inline_facts is not None else self._last_inline_facts
        builder = SemanticLayerBuilder(
            self._queries,
            repository_id=repository_id,
            revision_value=revision_value,
            instance_id=instance_id,
            file_provider=self._file_provider,
        )
        return builder.build(built_at=built_at, inline_facts=facts)

    # =========================================================================
    # Queries
    # =========================================================================

    def get_node_by_id(self, node_id: str) -> Node | None:
        return self._queries.get_node_by_id(node_id)

    def search(self, query: str, limit: int = 20) -> list[Node]:
        from .types import SearchOptions

        return [
            r.node
            for r in self._searcher.search_nodes(query, SearchOptions(limit=limit))
        ]

    # --- Summary Claims (ADR-0004 semantic overlay) ---

    def load_claims(self, claims: list[SummaryClaim]) -> None:
        """Bulk-load hand-authored Summary Claims with their grounding spans."""
        self._claim_overlay.load_claims(claims)

    def clear_claims(self) -> None:
        """Remove all Summary Claims and their grounding spans."""
        self._claim_overlay.clear_claims()

    def search_claims_fts(
        self,
        query: str,
        claim_type: str | None = None,
        limit: int = 20,
    ) -> list[ClaimHit]:
        """Retrieve Summary Claims by natural-language full-text search.

        Each result bundles the claim's grounding spans as line ranges; no Node
        objects are returned. Results are ranked by FTS score.
        """
        return self._claim_overlay.search_claims_fts(query, claim_type, limit)

    def get_callers(self, node_id: str) -> list[Edge]:
        return self._queries.get_callers(node_id)

    def get_callees(self, node_id: str) -> list[Edge]:
        return self._queries.get_callees(node_id)

    def get_stats(self) -> dict:
        return self._queries.get_stats()

    def get_all_nodes(self, limit: int = 50000, offset: int = 0) -> list[Node]:
        return self._queries.get_all_nodes(limit, offset)

    def get_all_edges(self, limit: int = 100000, offset: int = 0) -> list[Edge]:
        return self._queries.get_all_edges(limit, offset)

    # --- Graph queries ---

    def get_context(self, node_id: str) -> Context:
        """Get full context for a node (ancestors, children, refs, types, imports)."""
        return self._graph_manager.get_context(node_id)

    def get_callers_deep(
        self, node_id: str, max_depth: int = 1
    ) -> list[tuple[Node, Edge]]:
        """Find all callers of a function/method up to *max_depth* hops."""
        return self._traverser.get_callers(node_id, max_depth)

    def get_callees_deep(
        self, node_id: str, max_depth: int = 1
    ) -> list[tuple[Node, Edge]]:
        """Find all functions/methods called by a function up to *max_depth* hops."""
        return self._traverser.get_callees(node_id, max_depth)

    def get_testers(self, node_id: str, max_depth: int = 1) -> list[tuple[Node, Edge]]:
        """Find all test nodes that have TESTS edges pointing to the given Node."""
        return self._traverser.get_testers(node_id, max_depth)

    def get_tested_targets(
        self, node_id: str, max_depth: int = 1
    ) -> list[tuple[Node, Edge]]:
        """Find all Nodes that the given Node has TESTS edges pointing to."""
        return self._traverser.get_tested_targets(node_id, max_depth)

    def get_call_graph(self, node_id: str, depth: int = 2) -> Subgraph:
        """Get the call graph (callers + callees) for a function."""
        return self._traverser.get_call_graph(node_id, depth)

    def get_type_hierarchy(self, node_id: str) -> Subgraph:
        """Get the type hierarchy (extends/implements) for a class/interface."""
        return self._traverser.get_type_hierarchy(node_id)

    def find_usages(self, node_id: str) -> list[tuple[Node, Edge]]:
        """Find all usages of a symbol."""
        return self._traverser.find_usages(node_id)

    def get_impact_radius(self, node_id: str, max_depth: int = 3) -> Subgraph:
        """Calculate the impact radius of changing a node."""
        return self._traverser.get_impact_radius(node_id, max_depth)

    def get_file_dependencies(self, file_path: str) -> list[str]:
        """Get all files that this file imports from."""
        return self._graph_manager.get_file_dependencies(file_path)

    def get_file_dependents(self, file_path: str) -> list[str]:
        """Get all files that import from this file."""
        return self._graph_manager.get_file_dependents(file_path)

    # --- Exploration ---

    def explore(
        self,
        query: str,
        options: ExploreOptions | None = None,
    ) -> str:
        """Explore the codebase for a query using graph-based ranking.

        Groups source by file with line numbers, traces call chains among
        named symbols, and respects adaptive output budgets.
        Returns a formatted string suitable for LLM context windows.
        """
        return self._explore_engine.explore(query, options)

    # ------------------------------------------------------------------
    # Semantic explore (issue #123) — natural-language query that
    # returns function signature + parametr forwarding chains + relation
    # statistics as formatted text, without full source exploration.
    # ------------------------------------------------------------------

    def semantic_explore(
        self,
        query: str,
        *,
        max_chain_hops: int = 8,
    ) -> str:
        """Explore a function's semantic properties by natural-language query.

        Given a query like ``"uri 这个函数"``, this method:
        1. Searches the graph for the best matching function/class node
        2. Returns the function signature and parameter list
        3. For each parameter that has a FORWARDS_VALUE forwarding chain,
           prints the complete propagation path
        4. Shows relation statistics for the matched node

        Args:
            query: Natural-language query to identify the function/class.
            max_chain_hops: Maximum BFS depth for forwarding-chain queries.

        Returns:
            Formatted text (similar to ``explore()`` style).
        """
        from .semantic.store import read_relations
        from .semantic.types import RelationKind

        # ── 1. Semantic locate: find the best matching function/class ──
        nodes: list[Node] = self.search(query, limit=20)
        # Prefer function/method nodes; fall back to any named node
        target: Node | None = None
        target_nodes: list[Node] = []
        for n in nodes:
            if n.kind.value in ("function", "method", "class"):
                if target is None:
                    target = n
                target_nodes.append(n)

        if target is None:
            return f"[semantic_explore] No function/class found for query: {query}"

        # Build a list of all matched function/method/class names
        matched_names = [n.qualified_name for n in target_nodes if n.qualified_name]

        # ── 2. Build output sections ──
        lines: list[str] = []
        sep = "=" * 60
        lines.append(sep)
        lines.append(f"semantic_explore query: {query}")
        lines.append(sep)
        lines.append("")

        # ── 2a. Matched nodes (show top 2, rest by name) ──
        for idx, n in enumerate(target_nodes):
            if idx == 0 or idx == 1:
                qn = n.qualified_name or n.name
                fp = n.file_path or n.file_path
                fl = n.start_line or n.start_line
                sig = (n.signature or "")[:100]
                lines.append(f"  {n.kind.value}: {qn}")
                lines.append(f"    file: {fp}:{fl}")
                if sig:
                    lines.append(f"    signature: {sig}")
            elif idx == len(target_nodes) - 1:
                lines.append(f"  ... ({len(target_nodes)} matches total)")

        # ── 2b. Parameter forwarding chains ──
        conn = self._queries.connection
        all_fv = read_relations(conn, relation_kind=RelationKind.FORWARDS_VALUE)
        # Index inter FORWARDS_VALUE by caller_func
        inter_by_caller: dict[str, list] = {}
        for r in all_fv:
            ce = r.condition_expression
            if not ce or ce.get("forwards_type") != "inter":
                continue
            caller_func = (
                r.subject_entity_id.split("::")[0]
                if "::" in r.subject_entity_id
                else r.subject_entity_id
            )
            inter_by_caller.setdefault(caller_func, []).append(r)

        # Check each matched function for forwarding chains.
        # Only the FIRST (best) match gets full chain expansion; the rest
        # are listed by name only to keep output focused.
        shown_chain_header = False
        for match_idx, matched_name in enumerate(matched_names):
            if matched_name not in inter_by_caller:
                continue
            edges = inter_by_caller[matched_name]
            # Group by caller_param
            by_param: dict[str, list] = {}
            for e in edges:
                cp = (e.condition_expression or {}).get("caller_param", "")
                if cp:
                    by_param.setdefault(cp, []).append(e)

            if not by_param:
                continue
            if match_idx > 0:
                # Secondary matches: list name + param count only
                lines.append(
                    f"  (also matched: {matched_name} — {len(by_param)} params forwarded)"
                )
                continue
            if not shown_chain_header:
                lines.append("")
                lines.append("── 参数透传链 ──")
                shown_chain_header = True

            for param in sorted(by_param):
                param_edges = by_param[param]
                # Inline BFS for this param's chain
                chain = _bfs_forwards_chain(
                    conn, matched_name, param, max_hops=max_chain_hops
                )
                if not chain:
                    continue
                lines.append(f"  {param}:")
                for h in chain:
                    site = (
                        h["call_site"].split("::L")[1]
                        if "::L" in h["call_site"]
                        else "?"
                    )
                    arrow = f"──[{h['arg_type']}, L{site}]──>"
                    lines.append(
                        f"    → {h['callee_func']}.{h['callee_param']}  ({arrow})"
                    )
                total_hops = max(h["hop"] for h in chain)
                lines.append(f"    ({total_hops} 跳, {len(param_edges)} 转发边)")

        # ── 2c. Relation statistics ──
        lines.append("")
        lines.append("── 语义层统计 ──")
        for rk in RelationKind:
            count = len(read_relations(conn, relation_kind=rk))
            if count > 0:
                lines.append(f"    {rk.value:35s} {count:>6,}")

        lines.append("")
        lines.append(sep)
        return "\n".join(lines)

    # =============================================================================
    # Properties
    # =============================================================================

    @property
    def project_root(self) -> str:
        return self._project_root

    def set_file_provider(self, file_provider: FileProvider) -> None:
        """Replace the :class:`FileProvider` on all internal components.

        Useful when a cached :class:`CodeGraph` instance was originally
        opened without a provider and a later caller needs to inject one
        (e.g. after an LRU-cache hit in a store).
        """
        self._explore_engine.set_file_provider(file_provider)
        self._resolver.set_file_provider(file_provider)

    @property
    def config(self) -> CodeGraphConfig:
        return self._config

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _bfs_forwards_chain(
    conn, start_func: str, start_param: str, *, max_hops: int = 8
) -> list[dict]:
    """BFS over inter-procedural FORWARDS_VALUE relations.

    Returns list of hops, each a dict with hop/caller_func/caller_param/
    callee_func/callee_param/call_site/arg_type.
    """
    from collections import deque

    from .semantic.store import read_relations
    from .semantic.types import RelationKind

    all_fv = read_relations(conn, relation_kind=RelationKind.FORWARDS_VALUE)
    out_edges: dict[tuple[str, str], list] = {}
    for r in all_fv:
        ce = r.condition_expression
        if not ce or ce.get("forwards_type") != "inter":
            continue
        caller_func = (
            r.subject_entity_id.split("::")[0]
            if "::" in r.subject_entity_id
            else r.subject_entity_id
        )
        caller_param = ce.get("caller_param")
        if not caller_param:
            continue
        out_edges.setdefault((caller_func, caller_param), []).append(r)

    chain: list[dict] = []
    visited: set[tuple[str, str]] = set()
    queue: deque = deque([(start_func, start_param, 0)])

    while queue:
        func, param, hop = queue.popleft()
        if hop >= max_hops:
            continue
        key = (func, param)
        if key in visited:
            continue
        visited.add(key)

        for edge in out_edges.get(key, []):
            ce = edge.condition_expression
            obj = str(edge.literal_object or "")
            if "." in obj:
                callee_func, callee_param = obj.rsplit(".", 1)
            else:
                callee_func, callee_param = obj, ce.get("callee_param", "")
            chain.append(
                {
                    "hop": hop + 1,
                    "caller_func": func,
                    "caller_param": param,
                    "callee_func": callee_func,
                    "callee_param": callee_param,
                    "call_site": edge.subject_entity_id,
                    "arg_type": ce.get("arg_type", "?"),
                }
            )
            next_key = (callee_func, callee_param)
            if next_key not in visited:
                queue.append((callee_func, callee_param, hop + 1))

    return chain
