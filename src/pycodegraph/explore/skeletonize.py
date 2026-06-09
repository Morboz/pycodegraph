"""Skeletonization — reduce large files to signatures + selected full bodies.

When a file is a "god-file" (many named/entry methods whose bodies would
exceed the per-file budget), skeletonization switches to per-symbol
rendering: high-priority methods (on-spine, uniquely-named, entry) get
full body; everything else gets only the signature line.

This is the Python port of the TS CodeGraph's adaptive-explore-sizing
mechanism (``src/mcp/tools.ts:2045-2159``).
"""

from __future__ import annotations

import re

from ..types import Node, NodeKind, Subgraph

# Kinds whose body is meaningful to show in full
_CALLABLE_BODY_KINDS: frozenset[NodeKind] = frozenset(
    [NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.PROPERTY]
)

# Threshold: names with this many or fewer global definitions are "specific"
_UNIQUE_NAME_THRESHOLD = 3

# Body cap multiplier: skeletonized output can exceed max_chars_per_file
# by this factor, matching the TS CodeGraph's bodyCap = maxCharsPerFile * 1.5
_BODY_CAP_MULTIPLIER = 1.5

# Maximum signature-only lines to emit before eliding the rest.
# Keeps output compact for files with hundreds of methods.
_MAX_SIGNATURES = 24


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
    unique_named_node_ids: set[str],
    entry_node_ids: set[str],
    file_lines: list[str],
    max_chars_per_file: int,
) -> bool:
    """Detect whether a file needs per-symbol skeletonization.

    A file is a "god-file" when the total body chars of high-priority
    callables (spine, uniquely-named, entry) exceeds
    ``max_chars_per_file``.  This covers three scenarios:

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

    3. **Density fallback**: When flow tracing produces an empty spine
       (``path_node_ids`` is empty, typically because ``find_flow_chain``
       cannot cross dynamic-dispatch boundaries), and no nodes are
       uniquely-named or entry, a file with ≥20 callable nodes is still
       a "god-file" that needs skeletonization.  Without this fallback,
       such files fall through to the clustering path, which can produce
       oversized clusters that swallow the output budget.
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
    # (unless density fallback applies — see below)
    if named_body_chars <= max_chars_per_file and high_prio_ids:
        return False

    # --- On-spine god-file check ---
    # The TS CodeGraph requires off-path uniquely-named callables,
    # but we also trigger when there are off-path entry callables
    # whose bodies contribute to the budget overflow.  This handles
    # the case where all named methods are overloaded (>3 defs)
    # and thus not "unique" — the file still needs skeletonization.
    if has_spine_node:
        has_off_path_high_prio = any(
            n.kind in _CALLABLE_BODY_KINDS
            and n.id in high_prio_ids
            and n.id not in path_node_ids
            for n in file_nodes
        )
        return has_off_path_high_prio

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
    if len(named_callables) >= 2:
        return True

    # --- Density fallback (no spine, no high-prio coverage) ---
    # When flow tracing produces an empty spine (path_node_ids is empty)
    # and no nodes are uniquely-named or entry, a file with many callable
    # nodes is still a "god-file" that needs skeletonization.  Without
    # this fallback, such files fall through to the clustering path,
    # which can produce oversized clusters that swallow the output budget.
    # This matches the TS CodeGraph's behavior where
    # buildFlowFromNamedSymbols usually finds a spine via full-graph
    # BFS + synth edges; when it cannot, density still controls output.
    if not has_spine_node:
        callable_count = sum(1 for n in file_nodes if n.kind in _CALLABLE_BODY_KINDS)
        if callable_count >= 20:
            return True

    return False


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

    ``named_node_ids`` is currently unused in the priority calculation
    (overloaded names that aren't unique get priority 99, same as
    unnamed callables) but is kept as a parameter so that future
    priority tiers can distinguish "agent-named-but-overloaded" from
    "truly anonymous" without changing the call signature.

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
    body_cap = int(max_chars_per_file * _BODY_CAP_MULTIPLIER)

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
    sig_max = _MAX_SIGNATURES

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
                if line_idx >= len(file_lines):
                    break
                line_text = file_lines[line_idx]
                # Use word-boundary regex to avoid substring false
                # positives (e.g. node "get" matching "target")
                if re.search(rf"\b{re.escape(n.name)}\b", line_text):
                    lines.append(f"{line_idx + 1}\t{line_text.strip()}")
                    sig_count += 1
                    break
            else:
                # Fallback: emit the first line of the callable even
                # if the name wasn't found within 4 lines (e.g.
                # decorator-only lines), so the callable doesn't
                # silently vanish from output.
                if n.start_line - 1 < len(file_lines):
                    lines.append(
                        f"{n.start_line}\t{file_lines[n.start_line - 1].strip()}"
                    )
                    sig_count += 1

    tag = "focused" if body_ids else "skeleton"
    return "\n".join(lines), tag
