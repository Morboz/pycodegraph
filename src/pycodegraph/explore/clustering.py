"""File-level source clustering — group nearby symbols, extract with line numbers."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..search.query_utils import is_test_file
from ..types import Node, NodeKind, Subgraph


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
) -> list[FileCluster]:
    """Group nearby nodes in a file into contiguous clusters.

    Nodes whose line ranges overlap or are within *gap_threshold* blank
    lines of each other are merged into a single cluster.
    """
    if not nodes:
        return []

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
            clusters.append(
                FileCluster(
                    file_path=current_nodes[0].file_path,
                    start_line=current_nodes[0].start_line,
                    end_line=current_end,
                    symbols=list(current_nodes),
                    importance=importance,
                )
            )
            current_nodes = [node]
            current_end = node.end_line

    # Final cluster
    importance = sum(scores.get(n.id, 0.0) for n in current_nodes)
    clusters.append(
        FileCluster(
            file_path=current_nodes[0].file_path,
            start_line=current_nodes[0].start_line,
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
    project_root: str,
    file_path: str,
    clusters: list[FileCluster],
    context_padding: int = 3,
) -> str:
    """Read source file and extract cluster ranges with cat -n line numbers."""
    abs_path = os.path.join(project_root, file_path)
    if not os.path.exists(abs_path):
        return ""

    try:
        with open(abs_path) as f:
            lines = f.read().split("\n")
    except (OSError, UnicodeDecodeError):
        return ""

    sections: list[str] = []
    gap_marker = "\n... (gap) ...\n"

    for cluster in sorted(clusters, key=lambda c: c.start_line):
        start_idx = max(0, cluster.start_line - 1 - context_padding)
        end_idx = min(len(lines), cluster.end + context_padding)
        slice_lines = lines[start_idx:end_idx]
        # cat -n style: line_number\tcode
        numbered = "\n".join(
            f"{start_idx + 1 + i}\t{line}" for i, line in enumerate(slice_lines)
        )
        sections.append(numbered)

    return gap_marker.join(sections)


def extract_whole_file(
    project_root: str,
    file_path: str,
) -> str | None:
    """Read entire file with cat -n line numbers. Returns None if too large or missing."""
    abs_path = os.path.join(project_root, file_path)
    if not os.path.exists(abs_path):
        return None

    try:
        with open(abs_path) as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
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
