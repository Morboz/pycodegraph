"""File-level source clustering — group nearby symbols, extract with line numbers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..search.query_utils import is_test_file
from ..types import CONTAINER_KINDS, Node, NodeKind, Subgraph

if TYPE_CHECKING:
    from ..fs import FileProvider

# Envelope kinds = CONTAINER_KINDS (shared with traversal.py) plus
# additional enclosing kinds that don't own children via CONTAINS edges
# but still act as wrappers whose span should not merge children.
_ENVELOPE_KINDS: frozenset[NodeKind] = CONTAINER_KINDS | frozenset(
    [
        NodeKind.FILE,
        NodeKind.NAMESPACE,
        NodeKind.COMPONENT,
    ]
)


@dataclass
class FileCluster:
    """A contiguous range of lines within a file, containing symbols."""

    file_path: str
    start_line: int
    end_line: int
    symbols: list[Node] = field(default_factory=list)
    importance: float = 0.0


def cluster_nodes_in_file(
    nodes: list[Node],
    scores: dict[str, float],
    gap_threshold: int = 15,
    file_line_count: int = 0,
) -> list[FileCluster]:
    """Group nearby nodes in a file into contiguous clusters.

    Nodes whose line ranges overlap or are within *gap_threshold* blank
    lines of each other are merged into a single cluster.

    Envelope (container) nodes that span more than 50% of the file are
    filtered out before clustering so they do not merge all their children
    into a single giant cluster.  Their children (methods, fields, etc.)
    are still clustered individually.

    When envelope nodes are filtered out, their importance scores are
    redistributed equally to child nodes that fall within the envelope's
    line range, preventing named-symbol boosts from being silently lost.

    If filtering would remove *all* nodes, each envelope node is retained
    individually (without merging) so the giant-cluster problem is not
    reintroduced.
    """
    if not nodes:
        return []

    # Filter out envelope nodes covering >50% of the file
    removed_envelopes: list[Node] = []
    if file_line_count > 0:
        half_lines = file_line_count * 0.5
        filtered: list[Node] = []
        for n in nodes:
            if (
                n.kind in _ENVELOPE_KINDS
                and (n.end_line - n.start_line + 1) > half_lines
            ):
                removed_envelopes.append(n)
            else:
                filtered.append(n)

        if filtered:
            nodes = filtered
        elif removed_envelopes:
            # Safety fallback: don't restore the original list (which
            # reintroduces overlapping giant envelopes → one mega-cluster).
            # Instead, keep each removed envelope as its own standalone
            # node so they form separate single-node clusters.
            nodes = list(removed_envelopes)

        # Redistribute importance from removed envelopes to their children
        for env in removed_envelopes:
            env_score = scores.get(env.id, 0.0)
            if env_score > 0:
                children = [
                    n
                    for n in nodes
                    if n.start_line >= env.start_line and n.end_line <= env.end_line
                ]
                if children:
                    per_child = env_score / len(children)
                    for child in children:
                        scores[child.id] = scores.get(child.id, 0.0) + per_child

    # Record the earliest start_line among removed envelopes so that
    # clusters whose first node is a child of a filtered envelope can
    # extend upward to include the class definition / decorators.
    _envelope_floor: dict[str, int] = {}
    for env in removed_envelopes:
        _envelope_floor[env.file_path] = min(
            _envelope_floor.get(env.file_path, env.start_line), env.start_line
        )

    sorted_nodes = sorted(nodes, key=lambda n: n.start_line)
    clusters: list[FileCluster] = []

    current_nodes = [sorted_nodes[0]]
    current_end = sorted_nodes[0].end_line

    for node in sorted_nodes[1:]:
        if node.start_line <= current_end + gap_threshold:
            current_nodes.append(node)
            current_end = max(current_end, node.end_line)
        else:
            importance = sum(scores.get(n.id, 0.0) for n in current_nodes)
            start = current_nodes[0].start_line
            # Extend upward to the envelope's start_line if this cluster
            # begins inside a filtered envelope (captures class def / decorators)
            if (
                removed_envelopes
                and _envelope_floor.get(current_nodes[0].file_path, start) < start
                and any(
                    env.start_line <= start <= env.end_line for env in removed_envelopes
                )
            ):
                start = _envelope_floor[current_nodes[0].file_path]
            clusters.append(
                FileCluster(
                    file_path=current_nodes[0].file_path,
                    start_line=start,
                    end_line=current_end,
                    symbols=list(current_nodes),
                    importance=importance,
                )
            )
            current_nodes = [node]
            current_end = node.end_line

    # Final cluster
    importance = sum(scores.get(n.id, 0.0) for n in current_nodes)
    start = current_nodes[0].start_line
    if (
        removed_envelopes
        and _envelope_floor.get(current_nodes[0].file_path, start) < start
        and any(env.start_line <= start <= env.end_line for env in removed_envelopes)
    ):
        start = _envelope_floor[current_nodes[0].file_path]
    clusters.append(
        FileCluster(
            file_path=current_nodes[0].file_path,
            start_line=start,
            end_line=current_end,
            symbols=list(current_nodes),
            importance=importance,
        )
    )

    return clusters


def score_files(
    subgraph: Subgraph,
    named_node_ids: set[str],
    entry_node_ids: set[str],
) -> dict[str, float]:
    """Score each file by the relevance of its nodes.

    Named symbols (agent explicitly named) → +50
    Entry points (search roots) → +10
    Directly connected to entry → +3
    Other → +1
    """
    connected_to_entry: set[str] = set()
    for edge in subgraph.edges:
        if edge.source in entry_node_ids:
            connected_to_entry.add(edge.target)
        if edge.target in entry_node_ids:
            connected_to_entry.add(edge.source)

    file_scores: dict[str, float] = {}
    for node in subgraph.nodes.values():
        if node.kind in (NodeKind.IMPORT, NodeKind.EXPORT):
            continue
        if node.id in named_node_ids:
            boost = 50.0
        elif node.id in entry_node_ids:
            boost = 10.0
        elif node.id in connected_to_entry:
            boost = 3.0
        else:
            boost = 1.0
        file_scores[node.file_path] = file_scores.get(node.file_path, 0.0) + boost

    return file_scores


def select_files(
    file_scores: dict[str, float],
    subgraph: Subgraph,
    named_node_ids: set[str],
    max_files: int,
    query: str,
) -> list[str]:
    """Select and rank files for output.

    Ordering: named-symbol files first, then by score.
    Test/spec files are deprioritized unless the query mentions tests.
    """
    query_lower = query.lower()
    is_test_query = "test" in query_lower or "spec" in query_lower

    # Determine which files contain named symbols
    named_files: set[str] = set()
    for nid in named_node_ids:
        node = subgraph.nodes.get(nid)
        if node:
            named_files.add(node.file_path)

    def sort_key(fp: str) -> tuple:
        # Named files first (0 before 1)
        is_named = 0 if fp in named_files else 1
        # Non-test before test
        is_low = 1 if (not is_test_query and is_test_file(fp)) else 0
        # Score descending → negate
        score = -file_scores.get(fp, 0.0)
        return (is_named, is_low, score)

    sorted_files = sorted(file_scores.keys(), key=sort_key)
    return sorted_files[:max_files]


def extract_source_with_line_numbers(
    file_provider: FileProvider,
    file_path: str,
    clusters: list[FileCluster],
    context_padding: int = 3,
) -> str:
    """Read source file and extract cluster ranges with cat -n line numbers."""
    content = file_provider.read_file(file_path)
    if content is None:
        return ""
    lines = content.split("\n")

    sections: list[str] = []
    gap_marker = "\n... (gap) ...\n"

    for cluster in sorted(clusters, key=lambda c: c.start_line):
        start_idx = max(0, cluster.start_line - 1 - context_padding)
        end_idx = min(len(lines), cluster.end_line + context_padding)
        slice_lines = lines[start_idx:end_idx]
        # cat -n style: line_number\tcode
        numbered = "\n".join(
            f"{start_idx + 1 + i}\t{line}" for i, line in enumerate(slice_lines)
        )
        sections.append(numbered)

    return gap_marker.join(sections)


def extract_whole_file(
    file_provider: FileProvider,
    file_path: str,
) -> str | None:
    """Read entire file with cat -n line numbers. Returns None if missing or unreadable."""
    content = file_provider.read_file(file_path)
    if content is None:
        return None

    lines = content.split("\n")
    # Strip trailing blank lines
    while lines and lines[-1].strip() == "":
        lines.pop()

    return "\n".join(f"{i + 1}\t{line}" for i, line in enumerate(lines))


def get_file_language(subgraph: Subgraph, file_path: str) -> str:
    """Get the language for a file from its nodes."""
    for node in subgraph.nodes.values():
        if node.file_path == file_path:
            return node.language.value
    return ""
