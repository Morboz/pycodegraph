"""Reference resolution engine — converts UnresolvedReference records into Edge rows."""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..db.queries import QueryBuilder
from ..fs import FileProvider
from ..types import Edge, EdgeKind, NodeKind, UnresolvedReference
from ._context import ResolutionContext
from ._types import ResolutionResult, ResolvedRef, UnresolvedRef
from .builtins import is_builtin_or_external
from .import_resolver import (
    extract_import_mappings,
    resolve_python_module_member,
    resolve_via_import,
)
from .name_matcher import match_reference
from .synthesizer import synthesize_interface_dispatch

logger = logging.getLogger(__name__)


class ReferenceResolver:
    """Resolves UnresolvedReference records into Edge rows."""

    def __init__(
        self,
        project_root: str,
        queries: QueryBuilder,
        file_provider: FileProvider | None = None,
    ):
        self._project_root = project_root
        self._queries = queries
        self._context = ResolutionContext(
            queries, project_root, extract_import_mappings, file_provider
        )

    def set_file_provider(self, file_provider: FileProvider) -> None:
        """Replace the :class:`FileProvider` used for resolution."""
        self._context.set_file_provider(file_provider)

    def warm_caches(self) -> None:
        self._context.warm_caches()

    def resolve_all(
        self,
        refs: list[UnresolvedRef],
        on_progress: Callable | None = None,
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

    def resolve_one(self, ref: UnresolvedRef) -> ResolvedRef | None:
        if not ref.reference_name or len(ref.reference_name) < 2:
            return None

        # IMPORTS refs: first try to resolve to the import node in the same file.
        # If no matching IMPORT node exists (e.g., per-name refs from
        # "from X import Y"), fall through to import-based resolution so that
        # the actual target symbol can be found.
        if ref.reference_kind == EdgeKind.IMPORTS:
            result = self._resolve_imports_ref(ref)
            if result:
                return result
            # Fall through: per-name IMPORTS refs (from "from X import Y")
            # are resolved via import mappings, not via IMPORT nodes.

        if is_builtin_or_external(ref, self._context.known_names):
            return None

        # Fast pre-filter: skip if no symbol with any part of this name
        # exists in the codebase, unless it matches a local import
        # (import aliases / re-exports may rename symbols).
        if not self._has_any_possible_match(
            ref.reference_name
        ) and not self._matches_any_import(ref):
            return None

        # Try import-based resolution first
        result = resolve_via_import(ref, self._context)
        if result:
            return result

        # Python module member resolution (e.g., utils.helper())
        if ref.language == "python":
            result = resolve_python_module_member(ref, self._context)
            if result:
                return result

        # Then name-based strategies
        return match_reference(ref, self._context)

    def _resolve_imports_ref(self, ref: UnresolvedRef) -> ResolvedRef | None:
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
                member = name[idx + len(sep) :]
                if receiver in known or member in known:
                    return True
                # Capitalized receiver for instance-method resolution
                capitalized = receiver[0].upper() + receiver[1:]
                if capitalized in known:
                    return True

        # Path-like: "snippets/drawer-menu.liquid"
        slash_idx = name.rfind("/")
        if slash_idx > 0:
            file_name = name[slash_idx + 1 :]
            if file_name in known:
                return True

        return False

    def _matches_any_import(self, ref: UnresolvedRef) -> bool:
        """Check if the reference name matches any import in its file."""
        imports = self._context.get_import_mappings(ref.file_path, ref.language)
        for imp in imports:
            if imp.local_name == ref.reference_name or ref.reference_name.startswith(
                imp.local_name + "."
            ):
                return True
        return False

    def create_edges(self, resolved: list[ResolvedRef]) -> list[Edge]:
        edges: list[Edge] = []
        for r in resolved:
            kind = self._promote_edge_kind(r.original.reference_kind, r.target_node_id)
            edges.append(
                Edge(
                    source=r.original.from_node_id,
                    target=r.target_node_id,
                    kind=kind,
                    metadata=None,
                    line=r.original.line,
                    col=r.original.column,
                    provenance=f"resolution:{r.resolved_by}:{r.confidence:.2f}",
                )
            )
        return edges

    def resolve_and_persist(
        self,
        on_progress: Callable | None = None,
    ) -> ResolutionResult:
        self.warm_caches()

        # Load all unresolved refs at once (single query)
        all_db_refs = self._queries.get_all_unresolved_refs(limit=200000)
        if not all_db_refs:
            return ResolutionResult()

        total = len(all_db_refs)
        internal_refs = [self._to_internal_ref(r) for r in all_db_refs]

        # Resolve all in memory (zero DB queries for node lookups)
        result = self.resolve_all(internal_refs, on_progress)

        # Bulk insert edges (single query)
        if result.resolved:
            edges = self.create_edges(result.resolved)
            self._queries.insert_edges(edges)

        # Delete all processed refs (single truncate)
        self._queries.delete_all_unresolved_refs()

        # Synthesize interface dispatch edges (ABC/Protocol CALLS)
        synth_edges = synthesize_interface_dispatch(self._queries, self._context)
        if synth_edges:
            self._queries.insert_edges(synth_edges)

        result.stats = {
            "total": total,
            "resolved": len(result.resolved),
            "unresolved": len(result.unresolved),
            "by_method": self._count_by_method(result.resolved),
        }
        return result

    # --- Internals ---

    def _promote_edge_kind(self, original_kind: EdgeKind, target_id: str) -> EdgeKind:
        target = self._context.get_node_by_id(target_id)
        if original_kind == EdgeKind.CALLS:
            if target and target.kind in (NodeKind.CLASS, NodeKind.STRUCT):
                return EdgeKind.INSTANTIATES
        elif (
            original_kind == EdgeKind.EXTENDS
            and target
            and target.kind
            in (
                NodeKind.INTERFACE,
                NodeKind.PROTOCOL,
                NodeKind.TRAIT,
            )
        ):
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


def create_resolver(
    project_root: str,
    queries: QueryBuilder,
    file_provider: FileProvider | None = None,
) -> ReferenceResolver:
    return ReferenceResolver(project_root, queries, file_provider)
