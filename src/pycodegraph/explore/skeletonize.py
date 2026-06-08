"""Skeletonization — reduce large files to signatures + selected full bodies.

When a file is a "god-file" (many named/entry methods whose bodies would
exceed the per-file budget), skeletonization switches to per-symbol
rendering: high-priority methods (on-spine, uniquely-named, entry) get
full body; everything else gets only the signature line.

This is the Python port of the TS CodeGraph's adaptive-explore-sizing
mechanism (``src/mcp/tools.ts:2045-2159``).
"""

from __future__ import annotations

from ..types import Node, NodeKind, Subgraph

# Kinds whose body is meaningful to show in full
_CALLABLE_BODY_KINDS: frozenset[NodeKind] = frozenset(
    [NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.PROPERTY]
)

# Threshold: names with this many or fewer global definitions are "specific"
_UNIQUE_NAME_THRESHOLD = 3


def compute_unique_named_node_ids(
    named_node_ids: set[str],
    subgraph: Subgraph,
) -> set[str]:
    """Identify named nodes whose names have ≤3 definitions in the subgraph.

    A hyper-polymorphic name like ``as_sql`` (110 defs) must NOT spare
    every sibling file, so only "specific" names (≤3 defs) count as
    unique.  This matches the TS CodeGraph's ``uniqueNamedNodeIds``
    logic.
    """
    if not named_node_ids:
        return set()

    # Count definitions by name across ALL subgraph nodes
    name_counts: dict[str, int] = {}
    for node in subgraph.nodes.values():
        name_counts[node.name] = name_counts.get(node.name, 0) + 1

    unique_ids: set[str] = set()
    for nid in named_node_ids:
        named_node: Node | None = subgraph.nodes.get(nid)
        if named_node and name_counts.get(named_node.name, 0) <= _UNIQUE_NAME_THRESHOLD:
            unique_ids.add(nid)

    return unique_ids


def should_skeletonize(
    file_nodes: list[Node],
    path_node_ids: set[str],
    named_node_ids: set[str],
    unique_named_node_ids: set[str],
    entry_node_ids: set[str],
    file_lines: list[str],
    max_chars_per_file: int,
) -> bool:
    """Detect whether a file needs per-symbol skeletonization.

    A file is a "god-file" when the total body chars of high-priority
    callables (spine, uniquely-named, entry) exceeds
    ``max_chars_per_file``.  This covers two scenarios from the TS
    CodeGraph:

    1. **On-spine god-file**: The file has nodes on the flow spine AND
       the spine + uniquely-named callable bodies exceed the budget AND
       there are off-path uniquely-named callables.  (Matches TS
       ``onSpineGodFile``.)

    2. **Named/entry body overflow**: Even without a flow spine, if the
       combined bodies of all named/entry callables far exceed the
       per-file budget, skeletonization kicks in.  This handles the
       common case where the query names methods in a large file but
       flow tracing doesn't produce a spine (e.g. class + method names
       that are related via CONTAINS, not CALLS).
    """
    file_node_ids = {n.id for n in file_nodes}
    has_spine_node = bool(file_node_ids & path_node_ids)

    # The set of high-priority callables whose bodies we'd want to show
    # in full: spine, uniquely-named, or entry nodes
    high_prio_ids = path_node_ids | unique_named_node_ids | entry_node_ids

    # Compute total body chars for high-priority callables in this file
    named_body_chars = 0
    for n in file_nodes:
        if (
            n.kind in _CALLABLE_BODY_KINDS
            and n.id in high_prio_ids
            and n.start_line > 0
            and n.end_line >= n.start_line
        ):
            body = "\n".join(file_lines[n.start_line - 1 : n.end_line])
            named_body_chars += len(body)

    # If named/entry bodies fit within budget, no skeletonization needed
    if named_body_chars <= max_chars_per_file:
        return False

    # --- On-spine god-file check (matches TS CodeGraph exactly) ---
    if has_spine_node:
        # Need at least one off-path uniquely-named callable
        has_off_path_unique = any(
            n.kind in _CALLABLE_BODY_KINDS
            and n.id in unique_named_node_ids
            and n.id not in path_node_ids
            for n in file_nodes
        )
        return has_off_path_unique

    # --- Named/entry body overflow (no spine) ---
    # If high-priority callable bodies significantly exceed budget,
    # skeletonize.  This handles cases where flow tracing doesn't produce
    # a spine but the file still has too much named content.
    # Require at least 2 high-priority callables to trigger (avoids
    # skeletonizing a file with just one large method).
    named_callables = [
        n
        for n in file_nodes
        if n.kind in _CALLABLE_BODY_KINDS and n.id in high_prio_ids
    ]
    return len(named_callables) >= 2


def render_skeletonized(
    file_nodes: list[Node],
    file_lines: list[str],
    path_node_ids: set[str],
    named_node_ids: set[str],
    unique_named_node_ids: set[str],
    entry_node_ids: set[str],
    max_chars_per_file: int,
) -> tuple[str, str]:
    """Render a skeletonized file: priority methods get full body, rest get signatures.

    Priority levels (lower = higher priority):
    - 0: On-spine callable (flow path) → always full body
    - 1: Uniquely-named callable (agent specifically named it) → full body
    - 2: Entry-point callable → full body
    - 99: Everything else → signature only

    The total chars of all full-body methods is capped at
    ``max_chars_per_file * 1.5`` (the ``bodyCap``).

    Returns:
        A tuple of (rendered_source, tag) where tag is "focused" if any
        method has a full body, or "skeleton" if signatures only.
    """
    body_cap = int(max_chars_per_file * 1.5)

    # Assign priority
    def priority(n: Node) -> int:
        if n.kind not in _CALLABLE_BODY_KINDS:
            return 99
        if n.id in path_node_ids:
            return 0
        if n.id in unique_named_node_ids:
            return 1
        if n.id in entry_node_ids:
            return 2
        return 99

    # Select which symbols get full body (greedy by priority)
    body_ids: set[str] = set()
    body_chars = 0
    sorted_by_prio = sorted(
        [n for n in file_nodes if n.start_line > 0 and n.end_line >= n.start_line],
        key=lambda n: priority(n),
    )
    for n in sorted_by_prio:
        if priority(n) >= 99:
            continue
        sz = len("\n".join(file_lines[n.start_line - 1 : n.end_line]))
        if body_chars + sz > body_cap and body_ids:
            continue
        body_ids.add(n.id)
        body_chars += sz

    # Render in source order
    lines: list[str] = []
    covered_until = 0
    sig_count = 0
    sig_max = 24  # max signatures to show

    for n in sorted(file_nodes, key=lambda n: n.start_line):
        if n.start_line <= covered_until:
            continue
        if n.kind not in _CALLABLE_BODY_KINDS:
            continue

        if n.id in body_ids:
            # Full body with line numbers
            body = "\n".join(file_lines[n.start_line - 1 : n.end_line])
            for i, line in enumerate(body.split("\n")):
                lines.append(f"{n.start_line + i}\t{line}")
            covered_until = n.end_line
        else:
            # Signature only: find the line containing the symbol name
            if sig_count >= sig_max:
                # Count remaining signatures for elision message
                remaining = sum(
                    1
                    for m in file_nodes
                    if m.start_line > n.start_line
                    and m.kind in _CALLABLE_BODY_KINDS
                    and m.id not in body_ids
                )
                if remaining > 0:
                    lines.append(f"    ... +{remaining} more (signatures elided)")
                break
            for offset in range(4):
                line_idx = n.start_line - 1 + offset
                if line_idx < len(file_lines) and n.name in file_lines[line_idx]:
                    lines.append(f"{line_idx + 1}\t{file_lines[line_idx].strip()}")
                    sig_count += 1
                    break

    tag = "focused" if body_ids else "skeleton"
    return "\n".join(lines), tag
