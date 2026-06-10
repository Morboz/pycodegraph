"""Graph query manager - higher-level query functions built on traversal."""

from __future__ import annotations

import re
from collections.abc import Callable

from ..db.queries import QueryBuilder
from ..types import Context, Edge, EdgeKind, Node, NodeKind, Subgraph
from .traversal import GraphTraverser


class GraphQueryManager:
    """Complex graph queries using traversal algorithms."""

    def __init__(self, queries: QueryBuilder, traverser: GraphTraverser) -> None:
        self._queries = queries
        self._traverser = traverser

    def get_context(self, node_id: str) -> Context:
        """Get full context for a node: ancestors, children, refs, types, imports."""
        focal = self._queries.get_node_by_id(node_id)
        if not focal:
            raise ValueError(f"Node not found: {node_id}")

        ancestors = self._traverser.get_ancestors(node_id)
        children = self._traverser.get_children(node_id)

        # Incoming refs (skip contains)
        incoming_refs: list[dict] = []
        for edge in self._queries.get_incoming_edges(node_id):
            if edge.kind == EdgeKind.CONTAINS:
                continue
            node = self._queries.get_node_by_id(edge.source)
            if node:
                incoming_refs.append({"node": node, "edge": edge})

        # Outgoing refs (skip contains)
        outgoing_refs: list[dict] = []
        for edge in self._queries.get_outgoing_edges(node_id):
            if edge.kind == EdgeKind.CONTAINS:
                continue
            node = self._queries.get_node_by_id(edge.target)
            if node:
                outgoing_refs.append({"node": node, "edge": edge})

        # Type info
        types: list[Node] = []
        for kind in (EdgeKind.TYPE_OF, EdgeKind.RETURNS):
            for edge in self._queries.get_outgoing_edges(node_id, [kind.value]):
                type_node = self._queries.get_node_by_id(edge.target)
                if type_node and not any(t.id == type_node.id for t in types):
                    types.append(type_node)

        # Imports
        imports: list[Node] = []
        file_node = next((a for a in ancestors if a.kind == NodeKind.FILE), None)
        if file_node:
            for edge in self._queries.get_outgoing_edges(
                file_node.id, [EdgeKind.IMPORTS.value]
            ):
                import_node = self._queries.get_node_by_id(edge.target)
                if import_node:
                    imports.append(import_node)

        return Context(
            focal=focal,
            ancestors=ancestors,
            children=children,
            incoming_refs=incoming_refs,
            outgoing_refs=outgoing_refs,
            types=types,
            imports=imports,
        )

    def get_file_dependencies(self, file_path: str) -> list[str]:
        """Get all files that this file imports from."""
        nodes = self._queries.get_nodes_by_file(file_path)
        file_node = next((n for n in nodes if n.kind == NodeKind.FILE), None)
        if not file_node:
            return []

        dependencies: set[str] = set()
        for edge in self._queries.get_outgoing_edges(
            file_node.id, [EdgeKind.IMPORTS.value]
        ):
            target = self._queries.get_node_by_id(edge.target)
            if target and target.file_path != file_path:
                dependencies.add(target.file_path)

        return list(dependencies)

    def get_file_dependents(self, file_path: str) -> list[str]:
        """Get all files that import from this file."""
        nodes = self._queries.get_nodes_by_file(file_path)
        dependents: set[str] = set()

        # File-level incoming imports
        file_node = next((n for n in nodes if n.kind == NodeKind.FILE), None)
        if file_node:
            for edge in self._queries.get_incoming_edges(
                file_node.id, [EdgeKind.IMPORTS.value]
            ):
                source = self._queries.get_node_by_id(edge.source)
                if source and source.file_path != file_path:
                    dependents.add(source.file_path)

        # Node-level imports of exported symbols.
        # Python has no explicit export system, so all top-level symbols
        # (classes, functions, etc.) are implicitly importable.
        _IMPLICIT_EXPORT_KINDS = frozenset(
            [
                NodeKind.CLASS,
                NodeKind.FUNCTION,
                NodeKind.VARIABLE,
                NodeKind.CONSTANT,
            ]
        )
        for node in nodes:
            if node.is_exported or (
                node.language == "python"
                and node.kind in _IMPLICIT_EXPORT_KINDS
                and node.kind != NodeKind.FILE
            ):
                for edge in self._queries.get_incoming_edges(
                    node.id, [EdgeKind.IMPORTS.value]
                ):
                    source = self._queries.get_node_by_id(edge.source)
                    if source and source.file_path != file_path:
                        dependents.add(source.file_path)

        return list(dependents)

    def get_exported_symbols(self, file_path: str) -> list[Node]:
        """Get all symbols exported by a file."""
        return [n for n in self._queries.get_nodes_by_file(file_path) if n.is_exported]

    def find_by_qualified_name(self, pattern: str) -> list[Node]:
        """Find symbols by qualified name pattern (supports * wildcard)."""
        regex_pattern = (
            pattern.replace(".", r"\.")
            .replace("+", r"\+")
            .replace("^", r"\^")
            .replace("$", r"\$")
            .replace("{", r"\{")
            .replace("}", r"\}")
            .replace("(", r"\(")
            .replace(")", r"\)")
            .replace("|", r"\|")
            .replace("[", r"\[")
            .replace("]", r"\]")
            .replace("*", ".*")
            .replace("?", ".")
        )
        regex = re.compile(f"^{regex_pattern}$")

        result: list[Node] = []
        target_kinds = [
            NodeKind.CLASS,
            NodeKind.FUNCTION,
            NodeKind.METHOD,
            NodeKind.INTERFACE,
            NodeKind.TYPE_ALIAS,
            NodeKind.VARIABLE,
            NodeKind.CONSTANT,
        ]
        for kind in target_kinds:
            for node in self._queries.get_nodes_by_kind(kind):
                if regex.match(node.qualified_name):
                    result.append(node)
        return result

    def get_module_structure(self) -> dict[str, list[str]]:
        """Get directory → files mapping."""
        files = self._queries.get_all_files()
        structure: dict[str, list[str]] = {}
        for f in files:
            parts = f.path.split("/")
            dir_path = "/".join(parts[:-1]) or "."
            structure.setdefault(dir_path, []).append(f.path)
        return structure

    def find_circular_dependencies(self) -> list[list[str]]:
        """Detect circular dependencies using DFS."""
        files = self._queries.get_all_files()
        cycles: list[list[str]] = []
        visited: set[str] = set()
        recursion_stack: set[str] = set()

        def dfs(file_path: str, path: list[str]) -> None:
            if file_path in recursion_stack:
                cycle_start = path.index(file_path) if file_path in path else -1
                if cycle_start != -1:
                    cycles.append(path[cycle_start:])
                return
            if file_path in visited:
                return

            visited.add(file_path)
            recursion_stack.add(file_path)

            for dep in self.get_file_dependencies(file_path):
                dfs(dep, [*path, file_path])

            recursion_stack.discard(file_path)

        for f in files:
            if f.path not in visited:
                dfs(f.path, [])

        return cycles

    def get_node_metrics(self, node_id: str) -> dict:
        """Get complexity metrics for a node."""
        incoming = self._queries.get_incoming_edges(node_id)
        outgoing = self._queries.get_outgoing_edges(node_id)

        calls = [e for e in outgoing if e.kind == EdgeKind.CALLS]
        callers = [e for e in incoming if e.kind == EdgeKind.CALLS]
        contains = [e for e in outgoing if e.kind == EdgeKind.CONTAINS]
        ancestors = self._traverser.get_ancestors(node_id)

        return {
            "incoming_edge_count": len(incoming),
            "outgoing_edge_count": len(outgoing),
            "call_count": len(calls),
            "caller_count": len(callers),
            "child_count": len(contains),
            "depth": len(ancestors),
        }

    def find_dead_code(self, kinds: list[NodeKind] | None = None) -> list[Node]:
        """Find unreferenced nodes (potential dead code)."""
        target_kinds = kinds or [NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS]
        dead_code: list[Node] = []

        for kind in target_kinds:
            for node in self._queries.get_nodes_by_kind(kind):
                if node.is_exported:
                    continue
                incoming = self._queries.get_incoming_edges(node.id)
                refs = [e for e in incoming if e.kind != EdgeKind.CONTAINS]
                if not refs:
                    dead_code.append(node)

        return dead_code

    def get_filtered_subgraph(
        self,
        filter_fn: Callable[[Node], bool],
        include_edges: bool = True,
    ) -> Subgraph:
        """Get subgraph of nodes matching a filter."""
        nodes: dict[str, Node] = {}
        edges: list[Edge] = []

        target_kinds = [
            NodeKind.FILE,
            NodeKind.MODULE,
            NodeKind.CLASS,
            NodeKind.STRUCT,
            NodeKind.INTERFACE,
            NodeKind.TRAIT,
            NodeKind.FUNCTION,
            NodeKind.METHOD,
            NodeKind.VARIABLE,
            NodeKind.CONSTANT,
            NodeKind.ENUM,
            NodeKind.TYPE_ALIAS,
        ]

        for kind in target_kinds:
            for node in self._queries.get_nodes_by_kind(kind):
                if filter_fn(node):
                    nodes[node.id] = node

        if include_edges:
            for node_id in nodes:
                for edge in self._queries.get_outgoing_edges(node_id):
                    if edge.target in nodes:
                        edges.append(edge)

        return Subgraph(nodes=nodes, edges=edges, roots=[])
