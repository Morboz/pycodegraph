"""Explore output formatter — compose the final LLM-ready text."""

from __future__ import annotations

from ..types import Edge, Node, Subgraph


def format_header(query: str, node_count: int, file_count: int) -> str:
    """Format the exploration header."""
    return "\n".join(
        [
            f"## Exploration: {query}",
            "",
            f"Found {node_count} symbols across {file_count} files.",
            "",
        ]
    )


def format_source_section(
    file_path: str,
    symbols: list[Node],
    source: str,
    language: str,
    max_symbols_in_header: int = 10,
    tag: str = "",
) -> str:
    """Format a single file's source section."""
    names = list(
        dict.fromkeys(
            f"{n.name}({n.kind.value})"
            for n in symbols
            if n.kind.value not in ("import", "export")
        )
    )
    header_names = names[:max_symbols_in_header]
    omitted = len(names) - len(header_names)
    header_suffix = (
        f"{', '.join(header_names)}, +{omitted} more"
        if omitted > 0
        else ", ".join(header_names)
    )

    tag_suffix = f" · {tag}" if tag else ""

    lines = [
        f"#### {file_path} — {header_suffix}{tag_suffix}",
        "",
        f"```{language}",
        source,
        "```",
        "",
    ]
    return "\n".join(lines)


def format_relationships(
    edges: list[Edge],
    subgraph: Subgraph,
    max_per_kind: int = 10,
) -> str:
    """Format relationships section grouped by edge kind."""
    # Skip 'contains' — implied by file grouping
    significant = [e for e in edges if e.kind != "contains"]
    if not significant:
        return ""

    by_kind: dict[str, list[tuple[str, str]]] = {}
    for edge in significant:
        source_node = subgraph.nodes.get(edge.source)
        target_node = subgraph.nodes.get(edge.target)
        if not source_node or not target_node:
            continue
        group = by_kind.setdefault(edge.kind, [])
        group.append((source_node.name, target_node.name))

    if not by_kind:
        return ""

    lines = ["### Relationships", ""]
    for kind, pairs in by_kind.items():
        lines.append(f"**{kind}:**")
        for src, tgt in pairs[:max_per_kind]:
            lines.append(f"- {src} → {tgt}")
        if len(pairs) > max_per_kind:
            lines.append(f"- ... and {len(pairs) - max_per_kind} more")
        lines.append("")

    return "\n".join(lines)


def format_remaining_files(
    remaining: list[tuple[str, list[Node]]],
    max_files: int = 10,
) -> str:
    """Format the 'not shown above' trailing list."""
    if not remaining:
        return ""

    lines = ["### Not shown above — explore these names for their source", ""]
    for fp, nodes in remaining[:max_files]:
        symbols = ", ".join(f"{n.name}:{n.start_line}" for n in nodes[:5])
        lines.append(f"- {fp}: {symbols}")
    if len(remaining) > max_files:
        lines.append(f"- ... and {len(remaining) - max_files} more files")

    return "\n".join(lines)


def format_completeness_signal(
    files_included: int,
    any_trimmed: bool = False,
) -> str:
    """Format the completeness reminder."""
    if files_included > 0:
        return (
            f"> **Complete source for {files_included} files is included above "
            f"— do NOT re-read them.** If your question also needs files/symbols "
            f'listed under "Not shown above", make ANOTHER explore targeting '
            f"those names — it returns line-numbered source and is cheaper than reading."
        )
    if any_trimmed:
        return (
            "> Some file sections were trimmed for size. For a specific symbol you "
            "still need, run another explore with its exact name."
        )
    return ""
