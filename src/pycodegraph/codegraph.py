"""CodeGraph - Main entry point for the code knowledge graph system."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Callable

from .types import (
    IndexResult, Node, Edge, Context, Subgraph, TaskContext,
    BuildContextOptions, FindRelevantContextOptions,
)
from .config import CodeGraphConfig
from .config import (
    CODEGRAPH_DIR, get_db_path, get_config_path, get_db_url,
    save_config, load_config, create_default_config,
)
from .db import DatabaseConnection
from .db.queries import QueryBuilder
from .extraction.orchestrator import ExtractionOrchestrator
from .graph import GraphTraverser, GraphQueryManager
from .context.builder import ContextBuilder
from .resolution import create_resolver


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
        config_overrides: Optional[dict] = None,
    ) -> CodeGraph:
        """Initialize a new CodeGraph project.

        Creates .codegraph/ directory, SQLite database, and default config.
        """
        root = str(Path(project_root).resolve())
        codegraph_dir = Path(root) / CODEGRAPH_DIR

        # For SQLite: check db file; for PG/other: skip (schema is idempotent)
        using_external_db = bool(
            config_overrides and config_overrides.get("db_url")
            and not config_overrides["db_url"].startswith("sqlite")
        )
        if not using_external_db and codegraph_dir.exists() and (codegraph_dir / "codegraph.db").exists():
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
            db_path = Path(db_url[len("sqlite:///"):])
            if not db_path.exists():
                raise FileNotFoundError(f"CodeGraph not initialized in {root}. Run init() first.")

        db = DatabaseConnection.open(db_url)
        queries = QueryBuilder(db.get_connection())

        return cls(db, queries, config, root)

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()

    # =========================================================================
    # Indexing
    # =========================================================================

    def index_all(
        self,
        on_progress: Optional[Callable] = None,
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
        on_progress: Optional[Callable] = None,
    ) -> IndexResult:
        """Apply incremental changes: index changed files, delete removed files, then resolve.

        Indexes all ``changed_files`` first, then deletes all ``removed_files``.
        If no extraction errors occur, ``resolve_and_persist`` is called once to
        rebuild cross-file reference edges.  If any file fails to index, resolution
        is skipped entirely and the returned ``IndexResult`` will have
        ``success=False`` and a non-empty ``errors`` list.

        Args:
            changed_files: Relative paths of files that were added or modified.
            removed_files: Relative paths of files that were deleted.
            on_progress: Optional callback(phase, current, total, current_file) for progress.

        Returns:
            IndexResult with:
              - ``success``: True when all files indexed without errors.
              - ``files_indexed``: Number of entries in ``changed_files``.
              - ``nodes_created``: Total nodes extracted from changed files.
              - ``edges_created``: Structural edges extracted plus resolved reference edges.
              - ``errors``: List of ExtractionError for any files that failed.
        """
        total_nodes = 0
        total_edges = 0
        errors = []

        for path in changed_files:
            result = self._orchestrator.index_file(path)
            total_nodes += len(result.nodes)
            total_edges += len(result.edges)
            if result.errors:
                errors.extend(result.errors)

        for path in removed_files:
            self._queries.delete_file(path)

        if not errors:
            resolver = create_resolver(self._project_root, self._queries)
            resolution_result = resolver.resolve_and_persist(on_progress)
            total_edges += resolution_result.stats.get("resolved", 0)

        return IndexResult(
            success=not errors,
            files_indexed=len(changed_files),
            nodes_created=total_nodes,
            edges_created=total_edges,
            errors=errors,
        )

    # =========================================================================
    # Queries
    # =========================================================================

    def get_node_by_id(self, node_id: str) -> Optional[Node]:
        return self._queries.get_node_by_id(node_id)

    def search(self, query: str, limit: int = 20) -> list[Node]:
        from .types import SearchOptions
        return [r.node for r in self._queries.search_nodes(query, SearchOptions(limit=limit))]

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

    def get_callers_deep(self, node_id: str, max_depth: int = 1) -> list[tuple[Node, Edge]]:
        """Find all callers of a function/method up to *max_depth* hops."""
        return self._traverser.get_callers(node_id, max_depth)

    def get_callees_deep(self, node_id: str, max_depth: int = 1) -> list[tuple[Node, Edge]]:
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
        options: Optional[BuildContextOptions] = None,
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
