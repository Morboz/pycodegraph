"""Flow tracing — find call chains among explicitly-named symbols."""

from __future__ import annotations

from ..types import EdgeKind, Node

if __name__ == "__type_checking__":
    from ..graph.traversal import GraphTraverser


def find_flow_chain(
    named_symbol_ids: set[str],
    traverser: GraphTraverser,
    max_depth: int = 8,
    max_bridge: int = 1,
) -> list[dict]:
    """Find the longest call chain among named symbols.

    BFS from each named symbol along ``calls`` edges, looking for
    other named symbols.  Intermediate (unnamed) hops are limited to
    *max_bridge* consecutive steps to avoid wandering into a
    god-function's fan-out.

    Returns a list of dicts ``{"node": Node, "edge": Edge | None}``
    representing the longest chain found.
    """
    if len(named_symbol_ids) < 2:
        return []

    # Collect named node data
    named: dict[str, Node] = {}
    for nid in named_symbol_ids:
        try:
            callees = traverser.get_callees(nid, max_depth=1)
            if callees:
                named[nid] = callees[0][0]  # just to verify the node exists
        except Exception:
            pass

    best_chain: list[dict] = []

    for seed_id in named_symbol_ids:
        # BFS from seed
        parent: dict[str, dict | None] = {seed_id: None}
        queue: list[tuple[str, int, int]] = [(seed_id, 0, 0)]
        # (node_id, depth, consecutive_unnamed_hops)

        deep_id: str | None = None
        deep_depth = 0

        for item in queue:
            current_id, depth, streak = item
            if len(parent) > 1500:
                break

            if (
                current_id != seed_id
                and current_id in named_symbol_ids
                and depth > deep_depth
            ):
                deep_id = current_id
                deep_depth = depth

            if depth >= max_depth - 1:
                continue

            try:
                callees = traverser.get_callees(current_id, max_depth=1)
            except Exception:
                continue

            for callee_node, edge in callees:
                if edge.kind != EdgeKind.CALLS:
                    continue
                if callee_node.id in parent:
                    continue
                new_streak = 0 if callee_node.id in named_symbol_ids else streak + 1
                if new_streak > max_bridge:
                    continue
                parent[callee_node.id] = {
                    "prev": current_id,
                    "node": callee_node,
                    "edge": edge,
                }
                queue.append((callee_node.id, depth + 1, new_streak))

        if not deep_id:
            continue

        # Reconstruct chain
        chain: list[dict] = []
        cur: str | None = deep_id
        while cur is not None:
            p = parent.get(cur)
            if p is None:
                # Seed node
                try:
                    node = traverser.get_callees(cur, max_depth=1)
                    seed_node = node[0][0] if node else None
                except Exception:
                    seed_node = None
                if seed_node:
                    chain.append({"node": seed_node, "edge": None})
                break
            chain.append({"node": p["node"], "edge": p["edge"]})
            cur = p["prev"]

        chain.reverse()
        if len(chain) > len(best_chain):
            best_chain = chain

    return best_chain if len(best_chain) >= 3 else []


def format_flow_chain(chain: list[dict]) -> str:
    """Format a flow chain as markdown text."""
    if not chain:
        return ""

    lines = [
        "## Flow (call path among the symbols you queried)",
        "",
    ]

    for i, step in enumerate(chain):
        node = step["node"]
        edge = step.get("edge")
        loc = f":{node.start_line}" if node.start_line else ""
        if edge is not None:
            lines.append(f"   ↓ {edge.kind.value}")
        lines.append(f"{i + 1}. {node.name} ({node.file_path}{loc})")

    lines.append("")
    return "\n".join(lines)
