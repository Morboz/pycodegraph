"""In-memory index over all nodes for O(1) resolution lookups."""

from __future__ import annotations

from collections.abc import Callable

from ..db.queries import QueryBuilder
from ..fs import FileProvider, LocalFileProvider
from ..types import Node
from ..utils.cache import LRUCache
from ._types import ImportMapping

# Sentinel cached in place of ``None`` to distinguish "file not found"
# from an empty (but valid) file.
_FILE_MISSING = "\x00pycodegraph:missing\x00"


class ResolutionContext:
    """In-memory index over all nodes for O(1) resolution lookups.

    ``warm_caches()`` loads every node in a single SELECT and builds
    dicts keyed by name, qualified_name, file_path, lower(name), and id.
    All subsequent lookups are pure dict operations — zero DB queries.
    """

    def __init__(
        self,
        queries: QueryBuilder,
        project_root: str,
        import_mapping_fn: Callable[[str, str, str], list[ImportMapping]] | None = None,
        file_provider: FileProvider | None = None,
    ):
        self._queries = queries
        self._project_root = project_root
        self._import_mapping_fn = import_mapping_fn
        self._file_provider: FileProvider = file_provider or LocalFileProvider(
            project_root
        )

        # Populated by warm_caches()
        self._id_index: dict[str, Node] = {}
        self._name_index: dict[str, list[Node]] = {}
        self._qname_index: dict[str, list[Node]] = {}
        self._file_index: dict[str, list[Node]] = {}
        self._lower_name_index: dict[str, list[Node]] = {}

        # Per-file data (LRU-bounded to limit memory)
        self._import_mappings: LRUCache[list[ImportMapping]] = LRUCache(5000)
        self._file_contents: LRUCache[str] = LRUCache(1000)

        self._known_files: set[str] = set()
        self._known_names: set[str] = set()
        self._caches_warmed = False

    def get_project_root(self) -> str:
        return self._project_root

    def warm_caches(self) -> None:
        """Load all nodes into memory and build lookup indexes. Idempotent."""
        if self._caches_warmed:
            return

        all_nodes = self._queries.get_all_nodes(limit=500000)
        for node in all_nodes:
            self._id_index[node.id] = node
            self._name_index.setdefault(node.name, []).append(node)
            if node.qualified_name:
                self._qname_index.setdefault(node.qualified_name, []).append(node)
            self._file_index.setdefault(node.file_path, []).append(node)
            self._lower_name_index.setdefault(node.name.lower(), []).append(node)

        self._known_names = set(self._name_index.keys())
        self._known_files = set(self._file_index.keys())
        self._caches_warmed = True

    # --- Node lookups (all in-memory, zero DB queries) ---

    def get_node_by_id(self, node_id: str) -> Node | None:
        return self._id_index.get(node_id)

    def get_nodes_by_name(self, name: str) -> list[Node]:
        return self._name_index.get(name, [])

    def get_nodes_by_qualified_name(self, qualified_name: str) -> list[Node]:
        return self._qname_index.get(qualified_name, [])

    def get_nodes_by_lower_name(self, lower_name: str) -> list[Node]:
        return self._lower_name_index.get(lower_name, [])

    def get_nodes_in_file(self, file_path: str) -> list[Node]:
        return self._file_index.get(file_path, [])

    # --- Per-file data (still LRU-cached) ---

    def get_import_mappings(self, file_path: str, language: str) -> list[ImportMapping]:
        key = f"{language}:{file_path}"
        cached = self._import_mappings.get(key)
        if cached is not None:
            return cached
        content = self.read_file(file_path)
        if content and self._import_mapping_fn:
            result = self._import_mapping_fn(file_path, content, language)
        else:
            result = []
        self._import_mappings.put(key, result)
        return result

    def read_file(self, file_path: str) -> str | None:
        cached = self._file_contents.get(file_path)
        if cached is not None:
            return None if cached == _FILE_MISSING else cached
        content = self._file_provider.read_file(file_path)
        self._file_contents.put(
            file_path, content if content is not None else _FILE_MISSING
        )
        return content

    def file_exists(self, rel_path: str) -> bool:
        return rel_path in self._known_files

    def set_file_provider(self, file_provider: FileProvider) -> None:
        """Replace the :class:`FileProvider` and clear file-content cache."""
        self._file_provider = file_provider
        self._file_contents._cache.clear()

    @property
    def known_names(self) -> set[str]:
        return self._known_names
