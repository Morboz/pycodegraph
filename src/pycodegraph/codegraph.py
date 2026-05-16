"""CodeGraph - Main entry point for the code knowledge graph system."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Callable

from .types import IndexResult, Node, Edge
from .config import CodeGraphConfig
from .config import (
    CODEGRAPH_DIR, get_db_path, get_config_path,
    save_config, load_config, create_default_config,
)
from .db import DatabaseConnection
from .db.queries import QueryBuilder
from .extraction.orchestrator import ExtractionOrchestrator


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
        self._queries = queries
        self._config = config
        self._project_root = project_root
        self._orchestrator = ExtractionOrchestrator(project_root, config, queries)

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

        if codegraph_dir.exists() and (codegraph_dir / "codegraph.db").exists():
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
        db_path = get_db_path(root)
        db = DatabaseConnection.initialize(str(db_path))
        queries = QueryBuilder(db.db)

        return cls(db, queries, config, root)

    @classmethod
    def open(cls, project_root: str) -> CodeGraph:
        """Open an existing CodeGraph project."""
        root = str(Path(project_root).resolve())
        db_path = get_db_path(root)

        if not db_path.exists():
            raise FileNotFoundError(f"CodeGraph not initialized in {root}. Run init() first.")

        config = load_config(root)
        db = DatabaseConnection.open(str(db_path))
        queries = QueryBuilder(db.db)

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
        return self._orchestrator.index_all(on_progress)

    def index_file(self, file_path: str) -> None:
        """Index a single file (relative path)."""
        self._orchestrator.index_file(file_path)

    # =========================================================================
    # Queries
    # =========================================================================

    def get_node_by_id(self, node_id: str) -> Optional[Node]:
        return self._queries.get_node_by_id(node_id)

    def search(self, query: str, limit: int = 20) -> list[Node]:
        return self._queries.search_nodes(query, limit)

    def get_callers(self, node_id: str) -> list[Edge]:
        return self._queries.get_callers(node_id)

    def get_callees(self, node_id: str) -> list[Edge]:
        return self._queries.get_callees(node_id)

    def get_stats(self) -> dict:
        return self._queries.get_stats()

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
