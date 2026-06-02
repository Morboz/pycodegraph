"""CodeGraph - Main entry point for the code knowledge graph system."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .config import (
    CODEGRAPH_DIR,
    CodeGraphConfig,
    create_default_config,
    get_db_url,
    load_config,
    save_config,
)
from .context.builder import ContextBuilder
from .db import DatabaseConnection
from .db.queries import QueryBuilder
from .extraction import ExtractionOrchestrator
from .graph import GraphQueryManager, GraphTraverser
from .resolution import create_resolver
from .search.searcher import NodeSearcher
from .types import (
    BuildContextOptions,
    Context,
    Edge,
    IndexResult,
    Node,
    Subgraph,
)


class CodeGraph:
    """Main CodeGraph class providing init, index, and query operations."""

    def __init__(
        self,
        db: DatabaseConnection,
        queries: QueryBuilder,
        config: CodeGraphConfig,
        project_root: str,
    ):
        self._db = db
        self._conn = db.get_connection()
        self._queries = queries
        self._searcher = NodeSearcher(queries)
        self._config = config
        self._project_root = project_root
        self._orchestrator = ExtractionOrchestrator(project_root, config, queries)
        self._traverser = GraphTraverser(queries)
        self._graph_manager = GraphQueryManager(queries)
        self._context_builder = ContextBuilder(project_root, queries, self._traverser)

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

        return cls(db, queries, config, root)

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

        return cls(db, queries, config, root)

    @classmethod
    def open_from_url(
        cls,
        db_url: str,
        project_root: str = "",
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
        """
        if db_url.startswith("sqlite:///"):
            db_path = Path(db_url[len("sqlite:///") :])
            if not db_path.exists():
                raise FileNotFoundError(f"SQLite database not found: {db_path}")

        db = DatabaseConnection.open(db_url)
        queries = QueryBuilder(db.get_connection())
        config = CodeGraphConfig(db_url=db_url)
        return cls(db, queries, config, project_root)

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
            resolver = create_resolver(self._project_root, self._queries)
            resolution_result = resolver.resolve_and_persist(on_progress)
            result.edges_created += resolution_result.stats.get("resolved", 0)
            result.refs_resolved = resolution_result.stats.get("resolved", 0)
            result.refs_unresolved = resolution_result.stats.get("unresolved", 0)

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
            resolver = create_resolver(self._project_root, self._queries)
            resolution_result = resolver.resolve_and_persist(on_progress)
            total_edges += resolution_result.stats.get("resolved", 0)
            refs_resolved = resolution_result.stats.get("resolved", 0)
            refs_unresolved = resolution_result.stats.get("unresolved", 0)

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

    # --- Context building ---

    def build_context(
        self,
        task_input,
        options: BuildContextOptions | None = None,
    ):
        """Build rich context for a task using hybrid search + graph traversal."""
        return self._context_builder.build_context(task_input, options)

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def project_root(self) -> str:
        return self._project_root

    @property
    def config(self) -> CodeGraphConfig:
        return self._config

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
