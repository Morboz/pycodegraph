"""Reference resolution engine — converts UnresolvedReference records into Edge rows."""

from __future__ import annotations

import logging
import time
from typing import Optional, Callable

from ..types import Edge, EdgeKind, NodeKind, UnresolvedReference
from ..db.queries import QueryBuilder
from .types import UnresolvedRef, ResolvedRef, ResolutionResult, ImportMapping
from .builtins import is_builtin_or_external
from .import_resolver import resolve_via_import, extract_import_mappings, clear_import_mapping_cache
from .name_matcher import match_reference

logger = logging.getLogger(__name__)


class ResolutionContext:
    """Cached read layer over QueryBuilder for resolution."""

    def __init__(self, queries: QueryBuilder, project_root: str):
        self._queries = queries
        self._project_root = project_root

        self._nodes_by_file: dict[str, list] = {}
        self._nodes_by_name: dict[str, list] = {}
        self._nodes_by_qname: dict[str, list] = {}
        self._import_mappings: dict[str, list[ImportMapping]] = {}
        self._file_contents: dict[str, str] = {}
        self._known_files: Optional[set[str]] = None
        self._known_names: Optional[set[str]] = None

    def get_project_root(self) -> str:
        return self._project_root

    def get_nodes_by_name(self, name: str) -> list[Node]:
        if name not in self._nodes_by_name:
            self._nodes_by_name[name] = self._queries.get_nodes_by_name(name)
        return self._nodes_by_name[name]

    def get_nodes_by_qualified_name(self, qualified_name: str) -> list[Node]:
        if qualified_name not in self._nodes_by_qname:
            self._nodes_by_qname[qualified_name] = self._queries.get_nodes_by_qualified_name(qualified_name)
        return self._nodes_by_qname[qualified_name]

    def get_nodes_by_lower_name(self, lower_name: str) -> list[Node]:
        # No caching — used only by fuzzy matcher as a fallback
        return self._queries.get_nodes_by_lower_name(lower_name)

    def get_nodes_in_file(self, file_path: str) -> list[Node]:
        if file_path not in self._nodes_by_file:
            self._nodes_by_file[file_path] = self._queries.get_nodes_by_file(file_path)
        return self._nodes_by_file[file_path]

    def get_import_mappings(self, file_path: str, language: str) -> list[ImportMapping]:
        key = f"{language}:{file_path}"
        if key not in self._import_mappings:
            content = self.read_file(file_path)
            if content:
                self._import_mappings[key] = extract_import_mappings(file_path, content, language)
            else:
                self._import_mappings[key] = []
        return self._import_mappings[key]

    def read_file(self, file_path: str) -> Optional[str]:
        if file_path not in self._file_contents:
            try:
                full = f"{self._project_root}/{file_path}"
                with open(full) as f:
                    self._file_contents[file_path] = f.read()
            except (OSError, UnicodeDecodeError):
                self._file_contents[file_path] = ""
        return self._file_contents[file_path] or None

    def file_exists(self, rel_path: str) -> bool:
        if self._known_files is None:
            self._known_files = set(self._queries.get_all_file_paths())
        return rel_path in self._known_files

    @property
    def known_names(self) -> set[str]:
        if self._known_names is None:
            self._known_names = set(self._queries.get_all_node_names())
        return self._known_names

    def clear_caches(self) -> None:
        self._nodes_by_file.clear()
        self._nodes_by_name.clear()
        self._nodes_by_qname.clear()
        self._import_mappings.clear()
        self._file_contents.clear()
        self._known_files = None
        self._known_names = None
        clear_import_mapping_cache()


class ReferenceResolver:
    """Resolves UnresolvedReference records into Edge rows."""

    def __init__(self, project_root: str, queries: QueryBuilder):
        self._project_root = project_root
        self._queries = queries
        self._context = ResolutionContext(queries, project_root)

    def warm_caches(self) -> None:
        self._context.known_names
        self._context.file_exists("")

    def resolve_all(
        self,
        refs: list[UnresolvedRef],
        on_progress: Optional[Callable] = None,
    ) -> ResolutionResult:
        result = ResolutionResult()
        total = len(refs)

        for i, ref in enumerate(refs):
            if on_progress and i % 500 == 0:
                on_progress("resolution", i, total, ref.reference_name)

            resolved = self.resolve_one(ref)
            if resolved:
                result.resolved.append(resolved)
            else:
                result.unresolved.append(ref)

        result.stats = {
            "total": total,
            "resolved": len(result.resolved),
            "unresolved": len(result.unresolved),
            "by_method": self._count_by_method(result.resolved),
        }
        return result

    def resolve_one(self, ref: UnresolvedRef) -> Optional[ResolvedRef]:
        if not ref.reference_name or len(ref.reference_name) < 2:
            return None

        # IMPORTS refs: resolve to the import node in the same file,
        # regardless of whether the target module is external.
        if ref.reference_kind == EdgeKind.IMPORTS:
            return self._resolve_imports_ref(ref)

        if is_builtin_or_external(ref, self._context.known_names):
            return None

        # Fast pre-filter: skip if no symbol with any part of this name
        # exists in the codebase, unless it matches a local import
        # (import aliases / re-exports may rename symbols).
        if not self._has_any_possible_match(ref.reference_name) and not self._matches_any_import(ref):
            return None

        # Try import-based resolution first
        result = resolve_via_import(ref, self._context)
        if result:
            return result

        # Then name-based strategies
        return match_reference(ref, self._context)

    def _resolve_imports_ref(self, ref: UnresolvedRef) -> Optional[ResolvedRef]:
        """Resolve an IMPORTS reference to the import node in the same file."""
        nodes_in_file = self._context.get_nodes_in_file(ref.file_path)
        for node in nodes_in_file:
            if node.kind == NodeKind.IMPORT and node.name == ref.reference_name:
                return ResolvedRef(
                    original=ref,
                    target_node_id=node.id,
                    confidence=0.95,
                    resolved_by="import-node",
                )
        return None

    def _has_any_possible_match(self, name: str) -> bool:
        """Check if any part of the reference name exists as a known symbol."""
        known = self._context.known_names

        if name in known:
            return True

        # Qualified names: "obj.method" or "Class::method"
        for sep in (".", "::"):
            idx = name.find(sep)
            if idx > 0:
                receiver = name[:idx]
                member = name[idx + len(sep):]
                if receiver in known or member in known:
                    return True
                # Capitalized receiver for instance-method resolution
                capitalized = receiver[0].upper() + receiver[1:]
                if capitalized in known:
                    return True

        # Path-like: "snippets/drawer-menu.liquid"
        slash_idx = name.rfind("/")
        if slash_idx > 0:
            file_name = name[slash_idx + 1:]
            if file_name in known:
                return True

        return False

    def _matches_any_import(self, ref: UnresolvedRef) -> bool:
        """Check if the reference name matches any import in its file."""
        imports = self._context.get_import_mappings(ref.file_path, ref.language)
        for imp in imports:
            if imp.local_name == ref.reference_name or ref.reference_name.startswith(imp.local_name + "."):
                return True
        return False

    def create_edges(self, resolved: list[ResolvedRef]) -> list[Edge]:
        edges: list[Edge] = []
        for r in resolved:
            kind = self._promote_edge_kind(r.original.reference_kind, r.target_node_id)
            edges.append(Edge(
                source=r.original.from_node_id,
                target=r.target_node_id,
                kind=kind,
                metadata=None,
                line=r.original.line,
                col=r.original.column,
                provenance=f"resolution:{r.resolved_by}:{r.confidence:.2f}",
            ))
        return edges

    def resolve_and_persist(
        self,
        on_progress: Optional[Callable] = None,
    ) -> ResolutionResult:
        self.warm_caches()

        total_result = ResolutionResult()
        batch_size = 5000
        offset = 0

        while True:
            refs = self._queries.get_unresolved_refs_batch(offset=offset, limit=batch_size)
            if not refs:
                break

            internal_refs = [self._to_internal_ref(r) for r in refs]

            batch_result = self.resolve_all(internal_refs, on_progress)
            total_result.resolved.extend(batch_result.resolved)
            total_result.unresolved.extend(batch_result.unresolved)

            if batch_result.resolved:
                edges = self.create_edges(batch_result.resolved)
                self._queries.insert_edges(edges)

                resolved_dicts = [
                    {
                        "from_node_id": r.original.from_node_id,
                        "reference_name": r.original.reference_name,
                        "reference_kind": r.original.reference_kind.value,
                    }
                    for r in batch_result.resolved
                ]
                self._queries.delete_specific_resolved_refs(resolved_dicts)

            offset += batch_size

            # Clear per-batch caches to bound memory
            self._context.clear_caches()

        total_result.stats = {
            "total": len(total_result.resolved) + len(total_result.unresolved),
            "resolved": len(total_result.resolved),
            "unresolved": len(total_result.unresolved),
            "by_method": self._count_by_method(total_result.resolved),
        }
        return total_result

    # --- Internals ---

    def _promote_edge_kind(self, original_kind: EdgeKind, target_id: str) -> EdgeKind:
        if original_kind == EdgeKind.CALLS:
            target = self._queries.get_node_by_id(target_id)
            if target and target.kind in (NodeKind.CLASS, NodeKind.STRUCT):
                return EdgeKind.INSTANTIATES
        elif original_kind == EdgeKind.EXTENDS:
            target = self._queries.get_node_by_id(target_id)
            if target and target.kind in (NodeKind.INTERFACE, NodeKind.PROTOCOL, NodeKind.TRAIT):
                return EdgeKind.IMPLEMENTS
        return original_kind

    @staticmethod
    def _to_internal_ref(r: UnresolvedReference) -> UnresolvedRef:
        return UnresolvedRef(
            from_node_id=r.from_node_id,
            reference_name=r.reference_name,
            reference_kind=r.reference_kind,
            line=r.line,
            column=r.column,
            file_path=r.file_path,
            language=r.language,
        )

    @staticmethod
    def _count_by_method(resolved: list[ResolvedRef]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in resolved:
            counts[r.resolved_by] = counts.get(r.resolved_by, 0) + 1
        return counts


def create_resolver(project_root: str, queries: QueryBuilder) -> ReferenceResolver:
    return ReferenceResolver(project_root, queries)
