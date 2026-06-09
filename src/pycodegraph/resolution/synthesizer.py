"""Interface/Protocol dispatch synthesis.

Synthesizes CALLS edges from base-type methods to concrete implementations,
enabling dynamic-dispatch call-graph traversal for ABC and Protocol hierarchies.

Algorithm (adapted from CodeGraph's ``interfaceOverrideEdges``):

1. Collect all IMPLEMENTS and EXTENDS edges.
2. For each (concrete_class, base_class) pair, transitively resolve
   the full set of base types.
3. For each base type, find its child methods (via CONTAINS edges).
4. For each base method, find a same-named method in the concrete class.
5. Create a CALLS edge from base_method → concrete_method with
   provenance ``heuristic:synthesis``.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from ..db.queries import QueryBuilder
from ..types import Edge, EdgeKind, Node, NodeKind
from ._context import ResolutionContext

logger = logging.getLogger(__name__)

# Node kinds that can serve as interface-like base types.
_INTERFACE_KINDS: frozenset[NodeKind] = frozenset(
    [
        NodeKind.INTERFACE,
        NodeKind.PROTOCOL,
        NodeKind.TRAIT,
        # Python ABC classes are extracted as NodeKind.CLASS, but
        # they still participate in dispatch synthesis when referenced
        # via EXTENDS/IMPLEMENTS edges.
        NodeKind.CLASS,
    ]
)

_METHOD_KINDS: frozenset[NodeKind] = frozenset([NodeKind.METHOD, NodeKind.FUNCTION])


def synthesize_interface_dispatch(
    queries: QueryBuilder,
    context: ResolutionContext,
) -> list[Edge]:
    """Synthesize CALLS edges for ABC/Protocol interface dispatch.

    Parameters
    ----------
    queries:
        Database query builder for edge lookups.
    context:
        In-memory resolution context for node lookups.

    Returns
    -------
    list[Edge]
        Newly synthesized CALLS edges with provenance ``heuristic:synthesis``.
    """
    # 1. Gather all IMPLEMENTS and EXTENDS edges.
    all_edges = queries.get_all_edges(limit=500000)
    impl_extends = [
        e for e in all_edges if e.kind in (EdgeKind.IMPLEMENTS, EdgeKind.EXTENDS)
    ]

    if not impl_extends:
        return []

    # 2. Build a map: concrete_class_id -> set of base_class_ids (direct).
    direct_bases: dict[str, set[str]] = defaultdict(set)
    for e in impl_extends:
        # Edge direction: source (concrete) -> target (base)
        direct_bases[e.source].add(e.target)

    # 3. Transitively resolve all base types for each concrete class.
    def _all_bases(class_id: str, visited: set[str] | None = None) -> set[str]:
        if visited is None:
            visited = set()
        if class_id in visited:
            return set()
        visited.add(class_id)
        result: set[str] = set()
        for base_id in direct_bases.get(class_id, set()):
            result.add(base_id)
            result.update(_all_bases(base_id, visited))
        return result

    # 4. Build containment map: parent_id -> list of child nodes (methods).
    contains_edges = [e for e in all_edges if e.kind == EdgeKind.CONTAINS]
    children_by_parent: dict[str, list[Node]] = defaultdict(list)
    for e in contains_edges:
        child = context.get_node_by_id(e.target)
        if child and child.kind in _METHOD_KINDS:
            children_by_parent[e.source].append(child)

    # 5. For each concrete class, find dispatch edges.
    synthesized: list[Edge] = []
    seen: set[tuple[str, str]] = set()  # (source_method_id, target_method_id)

    for concrete_id, base_ids in ((k, _all_bases(k)) for k in direct_bases):
        concrete_node = context.get_node_by_id(concrete_id)
        if not concrete_node or concrete_node.kind not in (
            NodeKind.CLASS,
            NodeKind.STRUCT,
        ):
            continue

        concrete_methods = children_by_parent.get(concrete_id, [])
        concrete_by_name: dict[str, Node] = {m.name: m for m in concrete_methods}

        for base_id in base_ids:
            base_node = context.get_node_by_id(base_id)
            if not base_node or base_node.kind not in _INTERFACE_KINDS:
                continue

            base_methods = children_by_parent.get(base_id, [])
            for base_method in base_methods:
                concrete_method = concrete_by_name.get(base_method.name)
                if concrete_method is None:
                    continue

                pair = (base_method.id, concrete_method.id)
                if pair in seen:
                    continue
                seen.add(pair)

                synthesized.append(
                    Edge(
                        source=base_method.id,
                        target=concrete_method.id,
                        kind=EdgeKind.CALLS,
                        provenance="heuristic:synthesis",
                    )
                )

    logger.info("Synthesized %d interface dispatch edges", len(synthesized))
    return synthesized
