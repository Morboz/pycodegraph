"""Graph traversal algorithms - BFS and DFS for the code knowledge graph."""

from __future__ import annotations

from ..db.queries import QueryBuilder
from ..types import (
    CONTAINER_KINDS,
    Edge,
    EdgeKind,
    Node,
    Subgraph,
    TraversalOptions,
)

_DEFAULT_OPTIONS = TraversalOptions()


class GraphTraverser:
    """BFS and DFS traversal for the code knowledge graph.

    Constructed by ``CodeGraph._create_components``; inject where needed.
    """

    def __init__(self, queries: QueryBuilder) -> None:
        self._queries = queries

    def traverse_bfs(
        self, start_id: str, options: TraversalOptions | None = None
    ) -> Subgraph:
        opts = options or _DEFAULT_OPTIONS
        start_node = self._queries.get_node_by_id(start_id)
        if not start_node:
            return Subgraph()

        nodes: dict[str, Node] = {}
        edges: list[Edge] = []
        visited: set[str] = set()
        queue: list[tuple[Node, Edge | None, int]] = [(start_node, None, 0)]

        if opts.include_start:
            nodes[start_node.id] = start_node

        while queue and len(nodes) < opts.limit:
            node, edge, depth = queue.pop(0)
            if node.id in visited:
                continue
            visited.add(node.id)

            if edge:
                edges.append(edge)

            if depth >= opts.max_depth:
                continue

            adjacent = self._get_adjacent_edges(
                node.id, opts.direction, opts.edge_kinds
            )
            # Priority: contains > calls > other
            adjacent.sort(
                key=lambda e: (
                    0
                    if e.kind == EdgeKind.CONTAINS
                    else 1
                    if e.kind == EdgeKind.CALLS
                    else 2
                )
            )

            for adj_edge in adjacent:
                next_id = (
                    adj_edge.target if adj_edge.source == node.id else adj_edge.source
                )
                if next_id in visited:
                    continue
                next_node = self._queries.get_node_by_id(next_id)
                if not next_node:
                    continue
                if opts.node_kinds and next_node.kind not in opts.node_kinds:
                    continue
                nodes[next_node.id] = next_node
                queue.append((next_node, adj_edge, depth + 1))

        return Subgraph(nodes=nodes, edges=edges, roots=[start_id])

    def traverse_dfs(
        self, start_id: str, options: TraversalOptions | None = None
    ) -> Subgraph:
        opts = options or _DEFAULT_OPTIONS
        start_node = self._queries.get_node_by_id(start_id)
        if not start_node:
            return Subgraph()

        nodes: dict[str, Node] = {}
        edges: list[Edge] = []
        visited: set[str] = set()

        if opts.include_start:
            nodes[start_node.id] = start_node

        self._dfs_recursive(start_node, 0, opts, nodes, edges, visited)
        return Subgraph(nodes=nodes, edges=edges, roots=[start_id])

    def get_callers(self, node_id: str, max_depth: int = 1) -> list[tuple[Node, Edge]]:
        result: list[tuple[Node, Edge]] = []
        visited: set[str] = set()
        self._get_callers_recursive(node_id, max_depth, 0, result, visited)
        return result

    def get_callees(self, node_id: str, max_depth: int = 1) -> list[tuple[Node, Edge]]:
        result: list[tuple[Node, Edge]] = []
        visited: set[str] = set()
        self._get_callees_recursive(node_id, max_depth, 0, result, visited)
        return result

    def get_testers(self, node_id: str, max_depth: int = 1) -> list[tuple[Node, Edge]]:
        result: list[tuple[Node, Edge]] = []
        visited: set[str] = set()
        self._get_testers_recursive(node_id, max_depth, 0, result, visited)
        return result

    def get_tested_targets(
        self, node_id: str, max_depth: int = 1
    ) -> list[tuple[Node, Edge]]:
        result: list[tuple[Node, Edge]] = []
        visited: set[str] = set()
        self._get_tested_targets_recursive(node_id, max_depth, 0, result, visited)
        return result

    def get_call_graph(self, node_id: str, depth: int = 2) -> Subgraph:
        focal = self._queries.get_node_by_id(node_id)
        if not focal:
            return Subgraph()

        nodes: dict[str, Node] = {focal.id: focal}
        edges: list[Edge] = []

        for caller_node, caller_edge in self.get_callers(node_id, depth):
            nodes[caller_node.id] = caller_node
            edges.append(caller_edge)

        for callee_node, callee_edge in self.get_callees(node_id, depth):
            nodes[callee_node.id] = callee_node
            edges.append(callee_edge)

        return Subgraph(nodes=nodes, edges=edges, roots=[node_id])

    def get_type_hierarchy(self, node_id: str) -> Subgraph:
        focal = self._queries.get_node_by_id(node_id)
        if not focal:
            return Subgraph()

        nodes: dict[str, Node] = {focal.id: focal}
        edges: list[Edge] = []
        visited: set[str] = set()

        self._get_type_ancestors(node_id, nodes, edges, visited)
        self._get_type_descendants(node_id, nodes, edges, visited)
        return Subgraph(nodes=nodes, edges=edges, roots=[node_id])

    def find_usages(self, node_id: str) -> list[tuple[Node, Edge]]:
        result: list[tuple[Node, Edge]] = []
        incoming = self._queries.get_incoming_edges(node_id)
        for edge in incoming:
            source = self._queries.get_node_by_id(edge.source)
            if source:
                result.append((source, edge))
        return result

    def get_impact_radius(self, node_id: str, max_depth: int = 3) -> Subgraph:
        focal = self._queries.get_node_by_id(node_id)
        if not focal:
            return Subgraph()

        nodes: dict[str, Node] = {focal.id: focal}
        edges: list[Edge] = []
        visited: set[str] = set()

        self._get_impact_recursive(node_id, max_depth, 0, nodes, edges, visited)
        return Subgraph(nodes=nodes, edges=edges, roots=[node_id])

    def find_path(
        self,
        from_id: str,
        to_id: str,
        edge_kinds: list[EdgeKind] | None = None,
    ) -> list[tuple[Node, Edge | None]] | None:
        from_node = self._queries.get_node_by_id(from_id)
        to_node = self._queries.get_node_by_id(to_id)
        if not from_node or not to_node:
            return None

        visited: set[str] = set()
        queue: list[tuple[str, list[tuple[Node, Edge | None]]]] = [
            (from_id, [(from_node, None)])
        ]

        while queue:
            current_id, path = queue.pop(0)
            if current_id == to_id:
                return path
            if current_id in visited:
                continue
            visited.add(current_id)

            kind_strs = [k.value for k in edge_kinds] if edge_kinds else None
            outgoing = self._queries.get_outgoing_edges(current_id, kind_strs)
            for edge in outgoing:
                if edge.target not in visited:
                    next_node = self._queries.get_node_by_id(edge.target)
                    if next_node:
                        queue.append((edge.target, [*path, (next_node, edge)]))

        return None

    def get_ancestors(self, node_id: str) -> list[Node]:
        ancestors: list[Node] = []
        visited: set[str] = set()
        current_id = node_id

        while True:
            if current_id in visited:
                break
            visited.add(current_id)

            containing = self._queries.get_incoming_edges(
                current_id, [EdgeKind.CONTAINS.value]
            )
            if not containing:
                break

            parent = self._queries.get_node_by_id(containing[0].source)
            if parent:
                ancestors.append(parent)
                current_id = parent.id
            else:
                break

        return ancestors

    def get_children(self, node_id: str) -> list[Node]:
        contains_edges = self._queries.get_outgoing_edges(
            node_id, [EdgeKind.CONTAINS.value]
        )
        children: list[Node] = []
        for edge in contains_edges:
            child = self._queries.get_node_by_id(edge.target)
            if child:
                children.append(child)
        return children

    # =========================================================================
    # Private helpers
    # =========================================================================

    def _get_adjacent_edges(
        self,
        node_id: str,
        direction: str,
        edge_kinds: list[EdgeKind] | None,
    ) -> list[Edge]:
        kind_strs = [k.value for k in edge_kinds] if edge_kinds else None
        if direction == "outgoing":
            return self._queries.get_outgoing_edges(node_id, kind_strs)
        elif direction == "incoming":
            return self._queries.get_incoming_edges(node_id, kind_strs)
        else:
            return self._queries.get_outgoing_edges(
                node_id, kind_strs
            ) + self._queries.get_incoming_edges(node_id, kind_strs)

    def _dfs_recursive(
        self,
        node: Node,
        depth: int,
        opts: TraversalOptions,
        nodes: dict[str, Node],
        edges: list[Edge],
        visited: set[str],
    ) -> None:
        if node.id in visited or len(nodes) >= opts.limit or depth >= opts.max_depth:
            return
        visited.add(node.id)

        adjacent = self._get_adjacent_edges(node.id, opts.direction, opts.edge_kinds)
        for edge in adjacent:
            next_id = edge.target if edge.source == node.id else edge.source
            if next_id in visited:
                continue
            next_node = self._queries.get_node_by_id(next_id)
            if not next_node:
                continue
            if opts.node_kinds and next_node.kind not in opts.node_kinds:
                continue
            nodes[next_node.id] = next_node
            edges.append(edge)
            self._dfs_recursive(next_node, depth + 1, opts, nodes, edges, visited)

    def _get_callers_recursive(
        self,
        node_id: str,
        max_depth: int,
        current_depth: int,
        result: list[tuple[Node, Edge]],
        visited: set[str],
    ) -> None:
        if current_depth >= max_depth or node_id in visited:
            return
        visited.add(node_id)

        incoming = self._queries.get_incoming_edges(
            node_id,
            [EdgeKind.CALLS.value, EdgeKind.REFERENCES.value, EdgeKind.IMPORTS.value],
        )
        for edge in incoming:
            caller = self._queries.get_node_by_id(edge.source)
            if caller and caller.id not in visited:
                result.append((caller, edge))
                self._get_callers_recursive(
                    caller.id, max_depth, current_depth + 1, result, visited
                )

    def _get_callees_recursive(
        self,
        node_id: str,
        max_depth: int,
        current_depth: int,
        result: list[tuple[Node, Edge]],
        visited: set[str],
    ) -> None:
        if current_depth >= max_depth or node_id in visited:
            return
        visited.add(node_id)

        outgoing = self._queries.get_outgoing_edges(
            node_id,
            [EdgeKind.CALLS.value, EdgeKind.REFERENCES.value, EdgeKind.IMPORTS.value],
        )
        for edge in outgoing:
            callee = self._queries.get_node_by_id(edge.target)
            if callee and callee.id not in visited:
                result.append((callee, edge))
                self._get_callees_recursive(
                    callee.id, max_depth, current_depth + 1, result, visited
                )

    def _get_testers_recursive(
        self,
        node_id: str,
        max_depth: int,
        current_depth: int,
        result: list[tuple[Node, Edge]],
        visited: set[str],
    ) -> None:
        if current_depth >= max_depth or node_id in visited:
            return
        visited.add(node_id)

        incoming = self._queries.get_incoming_edges(
            node_id,
            [EdgeKind.TESTS.value],
        )
        for edge in incoming:
            tester = self._queries.get_node_by_id(edge.source)
            if tester and tester.id not in visited:
                result.append((tester, edge))
                self._get_testers_recursive(
                    tester.id, max_depth, current_depth + 1, result, visited
                )

    def _get_tested_targets_recursive(
        self,
        node_id: str,
        max_depth: int,
        current_depth: int,
        result: list[tuple[Node, Edge]],
        visited: set[str],
    ) -> None:
        if current_depth >= max_depth or node_id in visited:
            return
        visited.add(node_id)

        outgoing = self._queries.get_outgoing_edges(
            node_id,
            [EdgeKind.TESTS.value],
        )
        for edge in outgoing:
            target = self._queries.get_node_by_id(edge.target)
            if target and target.id not in visited:
                result.append((target, edge))
                self._get_tested_targets_recursive(
                    target.id, max_depth, current_depth + 1, result, visited
                )

    def _get_type_ancestors(
        self,
        node_id: str,
        nodes: dict[str, Node],
        edges: list[Edge],
        visited: set[str],
    ) -> None:
        if node_id in visited:
            return
        visited.add(node_id)

        outgoing = self._queries.get_outgoing_edges(
            node_id, [EdgeKind.EXTENDS.value, EdgeKind.IMPLEMENTS.value]
        )
        for edge in outgoing:
            parent = self._queries.get_node_by_id(edge.target)
            if parent and parent.id not in nodes:
                nodes[parent.id] = parent
                edges.append(edge)
                self._get_type_ancestors(parent.id, nodes, edges, visited)

    def _get_type_descendants(
        self,
        node_id: str,
        nodes: dict[str, Node],
        edges: list[Edge],
        visited: set[str],
    ) -> None:
        if node_id in visited:
            return
        visited.add(node_id)

        incoming = self._queries.get_incoming_edges(
            node_id, [EdgeKind.EXTENDS.value, EdgeKind.IMPLEMENTS.value]
        )
        for edge in incoming:
            child = self._queries.get_node_by_id(edge.source)
            if child and child.id not in nodes:
                nodes[child.id] = child
                edges.append(edge)
                self._get_type_descendants(child.id, nodes, edges, visited)

    def _get_impact_recursive(
        self,
        node_id: str,
        max_depth: int,
        current_depth: int,
        nodes: dict[str, Node],
        edges: list[Edge],
        visited: set[str],
    ) -> None:
        if current_depth >= max_depth or node_id in visited:
            return
        visited.add(node_id)

        # For container nodes, traverse into children
        focal = self._queries.get_node_by_id(node_id)
        if focal and focal.kind in CONTAINER_KINDS:
            contains = self._queries.get_outgoing_edges(
                node_id, [EdgeKind.CONTAINS.value]
            )
            for edge in contains:
                child = self._queries.get_node_by_id(edge.target)
                if child and child.id not in visited:
                    nodes[child.id] = child
                    edges.append(edge)
                    self._get_impact_recursive(
                        child.id, max_depth, current_depth, nodes, edges, visited
                    )

        incoming = self._queries.get_incoming_edges(node_id)
        for edge in incoming:
            source = self._queries.get_node_by_id(edge.source)
            if source and source.id not in nodes:
                nodes[source.id] = source
                edges.append(edge)
                self._get_impact_recursive(
                    source.id, max_depth, current_depth + 1, nodes, edges, visited
                )
