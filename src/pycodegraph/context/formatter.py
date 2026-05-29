"""Context formatter - formats TaskContext as markdown or JSON."""

from __future__ import annotations

from ..types import Edge, Node, Subgraph, TaskContext


def format_context_as_markdown(context: TaskContext) -> str:
    """Format context as compact markdown."""
    lines: list[str] = []

    lines.append("## Code Context\n")
    lines.append(f"**Query:** {context.query}\n")

    # Entry points
    if context.entry_points:
        lines.append("### Entry Points\n")
        for node in context.entry_points:
            loc = f":{node.start_line}" if node.start_line else ""
            lines.append(
                f"- **{node.name}** ({node.kind.value}) - {node.file_path}{loc}"
            )
            if node.signature:
                lines.append(f"  `{node.signature}`")
        lines.append("")

    # Related symbols (skip entry points, limit 10)
    entry_ids = {n.id for n in context.entry_points}
    other = [n for n in context.subgraph.nodes.values() if n.id not in entry_ids][:10]
    if other:
        lines.append("### Related Symbols\n")
        by_file: dict[str, list[Node]] = {}
        for node in other:
            by_file.setdefault(node.file_path, []).append(node)
        for file_path, nodes in by_file.items():
            names = ", ".join(f"{n.name}:{n.start_line}" for n in nodes)
            lines.append(f"- {file_path}: {names}")
        lines.append("")

    # Code blocks
    if context.code_blocks:
        lines.append("### Code\n")
        for block in context.code_blocks:
            name = block.node.name if block.node else "Unknown"
            lines.append(f"#### {name} ({block.file_path}:{block.start_line})\n")
            lines.append(f"```{block.language.value}")
            lines.append(block.content)
            lines.append("```\n")

    return "\n".join(lines)


def format_context_as_json(context: TaskContext) -> str:
    """Format context as structured JSON."""
    import json

    serializable = {
        "query": context.query,
        "summary": context.summary,
        "entryPoints": [_serialize_node(n) for n in context.entry_points],
        "nodes": [_serialize_node(n) for n in context.subgraph.nodes.values()],
        "edges": [_serialize_edge(e) for e in context.subgraph.edges],
        "codeBlocks": [
            {
                "filePath": b.file_path,
                "startLine": b.start_line,
                "endLine": b.end_line,
                "language": b.language.value,
                "content": b.content,
                "nodeName": b.node.name if b.node else None,
                "nodeKind": b.node.kind.value if b.node else None,
            }
            for b in context.code_blocks
        ],
        "relatedFiles": context.related_files,
        "stats": context.stats,
    }
    return json.dumps(serializable, indent=2)


def format_subgraph_tree(subgraph: Subgraph, entry_points: list[Node]) -> str:
    """Format a subgraph as an ASCII tree structure."""
    lines: list[str] = []
    printed: set[str] = set()

    # Build adjacency list
    outgoing: dict[str, list[Edge]] = {}
    for edge in subgraph.edges:
        outgoing.setdefault(edge.source, []).append(edge)

    # Print entry points as tree roots
    for entry in entry_points:
        _format_node_tree(entry, subgraph, outgoing, printed, lines, 0, "")
        lines.append("")

    # Remaining nodes
    remaining = [n for n in subgraph.nodes.values() if n.id not in printed]
    if 0 < len(remaining) <= 10:
        lines.append("Other relevant symbols:")
        for node in remaining:
            loc = f":{node.start_line}" if node.start_line else ""
            lines.append(f"  {node.kind.value}: {node.name} ({node.file_path}{loc})")
    elif len(remaining) > 10:
        lines.append(f"... and {len(remaining)} more related symbols")

    return "\n".join(lines).strip()


def _format_node_tree(
    node: Node,
    subgraph: Subgraph,
    outgoing: dict[str, list[Edge]],
    printed: set[str],
    lines: list[str],
    depth: int,
    prefix: str,
) -> None:
    if node.id in printed:
        return
    printed.add(node.id)

    loc = f":{node.start_line}" if node.start_line else ""
    sig = f" - {node.signature[:50]}" if node.signature else ""
    lines.append(f"{prefix}{node.kind.value}: {node.name} ({node.file_path}{loc}){sig}")

    edges = outgoing.get(node.id, [])
    significant = [
        e
        for e in edges
        if e.kind.value in ("calls", "extends", "implements", "imports", "references")
    ]

    by_kind: dict[str, list[Edge]] = {}
    for edge in significant:
        by_kind.setdefault(edge.kind.value, []).append(edge)

    new_prefix = prefix + "  "
    for kind, kind_edges in by_kind.items():
        if len(kind_edges) > 3:
            names = ", ".join(
                subgraph.nodes.get(
                    e.target,
                    Node(
                        id="",
                        kind=node.kind,
                        name="unknown",
                        qualified_name="",
                        file_path="",
                        language=node.language,
                        start_line=0,
                        end_line=0,
                        start_column=0,
                        end_column=0,
                        updated_at=0,
                    ),
                ).name
                for e in kind_edges[:3]
            )
            lines.append(
                f"{new_prefix}├── {kind}: {names} and {len(kind_edges) - 3} more"
            )
        else:
            for i, edge in enumerate(kind_edges):
                target = subgraph.nodes.get(edge.target)
                name = target.name if target else "unknown"
                connector = "└──" if i == len(kind_edges) - 1 else "├──"
                lines.append(f"{new_prefix}{connector} {kind} → {name}")

    if depth < 1:
        for edge in significant[:3]:
            target = subgraph.nodes.get(edge.target)
            if target and target.id not in printed:
                _format_node_tree(
                    target, subgraph, outgoing, printed, lines, depth + 1, new_prefix
                )


def _serialize_node(node: Node) -> dict:
    return {
        "id": node.id,
        "kind": node.kind.value,
        "name": node.name,
        "qualifiedName": node.qualified_name,
        "filePath": node.file_path,
        "language": node.language.value,
        "startLine": node.start_line,
        "endLine": node.end_line,
        "signature": node.signature,
        "docstring": node.docstring,
        "visibility": node.visibility,
        "isExported": node.is_exported,
        "isAsync": node.is_async,
        "isStatic": node.is_static,
    }


def _serialize_edge(edge: Edge) -> dict:
    return {
        "source": edge.source,
        "target": edge.target,
        "kind": edge.kind.value,
        "line": edge.line,
    }


def format_bytes(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} bytes"
    elif num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    else:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
