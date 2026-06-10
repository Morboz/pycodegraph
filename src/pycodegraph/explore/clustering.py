"""File-level source clustering — group nearby symbols, extract with line numbers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

from ..search.query_utils import is_test_file, is_test_query
from ..types import CONTAINER_KINDS, Edge, EdgeKind, Node, NodeKind, Subgraph

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
    max_importance: float = 0.0


# Importance assigned to edge-source-location ranges (matches TS CodeGraph
# tools.ts:2257-2274 at commit 7a3b2c1; value=2 in TS)
_EDGE_RANGE_IMPORTANCE = 2.0


class _Range(NamedTuple):
    """A single range used during clustering merge.

    Carries start/end lines, importance, and an optional Node reference.
    Edge-derived ranges have ``node=None`` but still carry ``file_path``
    inherited from their source node so that clusters composed entirely
    of edge ranges don't end up with ``file_path=""``.
    """

    start: int
    end: int
    importance: float
    file_path: str
    node: Node | None = None


def cluster_nodes_in_file(
    nodes: list[Node],
    scores: dict[str, float],
    gap_threshold: int = 15,
    file_line_count: int = 0,
    edges: list[Edge] | None = None,
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
    The redistribution uses a copy of *scores* so the caller's dict is
    not mutated.

    If filtering would remove *all* nodes, each envelope is placed in its
    own single-node cluster (bypassing the merge logic) so overlapping
    giant envelopes don't collapse into one mega-cluster.  Score
    redistribution is skipped in this fallback path to avoid envelope-
    to-envelope score inflation.

    **Edge source locations** (issue #46): When *edges* is provided,
    non-CONTAINS edges with valid line numbers are added as single-line
    ranges during clustering.  This increases the spatial spread of
    ranges, preventing dense method blocks (e.g., 37 QuerySet methods
    within gap_threshold) from merging into one monolithic cluster.
    Ports the TS CodeGraph's edge-line logic (tools.ts:2257-2274).
    """
    if not nodes:
        return []

    # Work on a copy of scores so we don't mutate the caller's dict
    scores = dict(scores)

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
            # Redistribute importance from removed envelopes to their
            # non-envelope children only (not to other envelopes).
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
        elif removed_envelopes:
            # Safety fallback: all nodes were envelopes.  Build
            # single-node clusters directly (bypassing the merge logic)
            # so overlapping envelopes don't collapse into one mega-cluster.
            # Skip score redistribution to avoid envelope-to-envelope inflation.
            return [
                FileCluster(
                    file_path=env.file_path,
                    start_line=env.start_line,
                    end_line=env.end_line,
                    symbols=[env],
                    importance=scores.get(env.id, 0.0),
                    max_importance=scores.get(env.id, 0.0),
                )
                for env in sorted(removed_envelopes, key=lambda n: n.start_line)
            ]

    def _extending_start(start: int, file_path: str) -> int:
        """Extend *start* upward to the enclosing envelope's start_line."""
        if not removed_envelopes:
            return start
        # Find the specific envelope(s) that contain this cluster's
        # first node and extend to their earliest start_line.
        enclosing = [
            env
            for env in removed_envelopes
            if env.file_path == file_path and env.start_line <= start <= env.end_line
        ]
        if enclosing:
            return min(env.start_line for env in enclosing)
        return start

    # ── Edge source locations (issue #46) ──────────────────────────────
    # Extract single-line ranges from non-CONTAINS edges with valid line
    # numbers.  These add spatial spread, preventing dense method blocks
    # from merging into one monolithic cluster.
    # Ports TS CodeGraph tools.ts:2257-2274.
    edge_ranges: list[_Range] = []
    if edges:
        # Dedup by (line, target) — two edges from *different* sources
        # at the same line targeting the same symbol are collapsed to
        # one range because they represent the same call site.
        seen_edge_lines: set[tuple[int, str]] = set()
        node_by_id: dict[str, Node] = {n.id: n for n in nodes}
        for edge in edges:
            if not edge.line or edge.line <= 0:
                continue
            if edge.kind == EdgeKind.CONTAINS:
                continue
            # Only include edges whose source is one of the nodes in this file
            if edge.source not in node_by_id:
                continue
            key = (edge.line, edge.target)
            if key in seen_edge_lines:
                continue
            seen_edge_lines.add(key)
            # Inherit file_path from the source node so edge-only
            # clusters don't end up with file_path=""
            src_node = node_by_id[edge.source]
            edge_ranges.append(
                _Range(
                    start=edge.line,
                    end=edge.line,
                    importance=_EDGE_RANGE_IMPORTANCE,
                    file_path=src_node.file_path,
                    node=None,
                )
            )

    # ── Merge nodes + edge ranges into clusters ────────────────────────
    # Build a unified sorted sequence of _Range and merge by gap_threshold.
    ranges: list[_Range] = []
    for n in nodes:
        if n.start_line > 0 and n.end_line >= n.start_line:
            ranges.append(
                _Range(
                    start=n.start_line,
                    end=n.end_line,
                    importance=scores.get(n.id, 0.0),
                    file_path=n.file_path,
                    node=n,
                )
            )
    ranges.extend(edge_ranges)
    ranges.sort(key=lambda r: (r.start, r.end))

    if not ranges:
        return []

    clusters: list[FileCluster] = []
    current_ranges = [ranges[0]]
    current_end = ranges[0].end

    def _emit_cluster(ranges_list: list[_Range], end_line: int) -> FileCluster:
        """Build a FileCluster from accumulated ranges."""
        cluster_nodes_list = [cr.node for cr in ranges_list if cr.node is not None]
        importance = sum(cr.importance for cr in ranges_list)
        max_imp = max(cr.importance for cr in ranges_list)
        # file_path: prefer the first real node; fall back to
        # edge-derived file_path (never "")
        fp = ""
        for cr in ranges_list:
            if cr.file_path:
                fp = cr.file_path
                break
        start = _extending_start(ranges_list[0].start, fp)
        return FileCluster(
            file_path=fp,
            start_line=start,
            end_line=end_line,
            symbols=cluster_nodes_list,
            importance=importance,
            max_importance=max_imp,
        )

    for r in ranges[1:]:
        if r.start <= current_end + gap_threshold:
            current_ranges.append(r)
            current_end = max(current_end, r.end)
        else:
            clusters.append(_emit_cluster(current_ranges, current_end))
            current_ranges = [r]
            current_end = r.end

    # Final cluster
    clusters.append(_emit_cluster(current_ranges, current_end))

    return clusters


# Rough estimate of characters per line for budget estimation.
# Most source code lines fall in the 40-80 char range; 60 is a
# reasonable midpoint.  Used by select_clusters_within_budget and the
# defensive skeletonization fallback in engine.py.
_CHARS_PER_LINE_ESTIMATE = 60


def select_clusters_within_budget(
    ranked_clusters: list[FileCluster],
    file_budget: int,
) -> list[FileCluster]:
    """Select clusters from a pre-ranked list within a per-file character budget.

    Clusters are assumed to be sorted by importance (descending).  The
    selection enforces *file_budget* for every cluster including the
    first.  If the very first cluster already exceeds the budget it is
    selected as a fallback (保底) so the file always has at least one
    section — but selection stops immediately, preventing further
    clusters from being added.

    The size estimate uses ``_CHARS_PER_LINE_ESTIMATE`` (60 chars/line).
    This fixes issue #31: previously the first cluster was
    unconditionally admitted regardless of budget.
    """
    selected_clusters: list[FileCluster] = []
    projected = 0
    for cluster in ranked_clusters:
        est = (cluster.end_line - cluster.start_line + 1) * _CHARS_PER_LINE_ESTIMATE
        if projected + est <= file_budget:
            selected_clusters.append(cluster)
            projected += est
        elif not selected_clusters:
            # Fallback: at least one cluster is selected, but stop here
            selected_clusters.append(cluster)
            projected += est
            break
        else:
            break
    return selected_clusters


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
    _is_test_q = is_test_query(query)

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
        is_low = 1 if (not _is_test_q and is_test_file(fp)) else 0
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
