"""Random-Walk-with-Restart (Personalized PageRank) for graph-based ranking.

Ranks nodes by structural proximity to seed nodes over the
call/reference/import graph. This is the ranking signal that text
search (FTS/bm25) cannot provide: relevance by STRUCTURE, not words.

Pure Python implementation using dicts — no numpy/scipy dependency.
"""

from __future__ import annotations

from ..types import Edge, EdgeKind, Node

# Edge kinds that carry structural relevance for RWR
_RANK_EDGE_KINDS: frozenset[str] = frozenset(
    {
        EdgeKind.CALLS,
        EdgeKind.REFERENCES,
        EdgeKind.IMPORTS,
        EdgeKind.EXTENDS,
        EdgeKind.IMPLEMENTS,
        EdgeKind.OVERRIDES,
        EdgeKind.INSTANTIATES,
        EdgeKind.TYPE_OF,
    }
)


def compute_rwr(
    seed_scores: dict[str, float],
    edges: list[Edge],
    all_node_ids: list[str],
    alpha: float = 0.25,
    iterations: int = 25,
) -> dict[str, float]:
    """Compute Personalized PageRank from seed nodes.

    Args:
        seed_scores: Initial scores for seed nodes (node_id -> score).
            Higher values mean stronger personalization.
        edges: Graph edges to build adjacency from.
        all_node_ids: All node IDs in the candidate subgraph.
        alpha: Restart probability. Lower = more diffusion from seeds.
        iterations: Number of power iterations.

    Returns:
        Dict of node_id -> RWR score.  Sum of all scores ≈ 1.0.
    """
    n = len(all_node_ids)
    if n == 0:
        return {}

    # Build adjacency (undirected — reachable both directions)
    node_set = set(all_node_ids)
    adjacency: dict[str, list[str]] = {nid: [] for nid in all_node_ids}

    for edge in edges:
        if edge.kind not in _RANK_EDGE_KINDS:
            continue
        if edge.source in node_set and edge.target in node_set:
            adjacency[edge.source].append(edge.target)
            adjacency[edge.target].append(edge.source)

    # Initialize personalization vector
    total_seed = sum(seed_scores.values())
    if total_seed == 0:
        # Fallback: uniform over all nodes
        uniform = 1.0 / n
        return {nid: uniform for nid in all_node_ids}

    personalization: dict[str, float] = {}
    for nid in all_node_ids:
        personalization[nid] = seed_scores.get(nid, 0.0) / total_seed

    # Initialize scores = personalization
    scores = dict(personalization)

    # Power iterations
    for _ in range(iterations):
        new_scores: dict[str, float] = {nid: 0.0 for nid in all_node_ids}

        for nid in all_node_ids:
            s = scores[nid]
            if s == 0.0:
                continue

            neighbors = adjacency[nid]
            degree = len(neighbors)
            if degree == 0:
                # Dangling node: distribute mass via teleportation
                share = (1.0 - alpha) * s / n
                for other in all_node_ids:
                    new_scores[other] += share
            else:
                share = (1.0 - alpha) * s / degree
                for nb in neighbors:
                    new_scores[nb] += share

        # Restart: add personalization
        for nid in all_node_ids:
            new_scores[nid] += alpha * personalization[nid]

        scores = new_scores

    return scores


def aggregate_to_file_level(
    node_scores: dict[str, float],
    nodes: dict[str, Node],
) -> dict[str, float]:
    """Aggregate per-node RWR scores to per-file scores (sum)."""
    from ..types import NodeKind

    file_scores: dict[str, float] = {}
    for nid, score in node_scores.items():
        node = nodes.get(nid)
        if node is None:
            continue
        if node.kind in (NodeKind.IMPORT, NodeKind.EXPORT):
            continue
        file_scores[node.file_path] = file_scores.get(node.file_path, 0.0) + score
    return file_scores
