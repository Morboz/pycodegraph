"""Shared helper functions for tree-sitter extraction."""

from __future__ import annotations

import hashlib

from tree_sitter import Node as TSNode

from ..types import NodeKind


def generate_node_id(file_path: str, kind: NodeKind | str, qualified_name: str) -> str:
    """Generate a deterministic node ID from file path, kind, and qualified name.

    Identity follows the symbol's semantic position (which file, which kind,
    which qualified name), not its source line. Adding a comment or blank line
    above the symbol shifts its line but leaves its ID stable; renaming the
    symbol or moving it to another file changes the ID, as it should.

    ``qualified_name`` carries the enclosing-scope chain (e.g.
    ``Request::open``), so two same-named symbols in different scopes hash
    to different IDs. ``file_path`` disambiguates same-named symbols in
    different files. Together they satisfy COMMON-005 (a short name must not
    globally merge unrelated symbols) and BUILD-005 (deterministic IDs).

    Callers must compute ``qualified_name`` before calling — passing a bare
    ``name`` would lose scope and risk cross-scope collisions.
    """
    kind_str = kind.value if isinstance(kind, NodeKind) else kind
    raw = f"{file_path}:{kind_str}:{qualified_name}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:32]
    return f"{kind_str}:{h}"


def get_node_text(node: TSNode, source: bytes) -> str:
    """Extract text from a syntax node."""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def get_child_by_field(node: TSNode, field_name: str) -> TSNode | None:
    """Find a child node by field name."""
    return node.child_by_field_name(field_name)


def get_preceding_docstring(node: TSNode, source: bytes) -> str | None:
    """Get docstring/comment preceding a node."""
    sibling = node.prev_named_sibling
    comments: list[str] = []

    while sibling:
        if sibling.type in (
            "comment",
            "line_comment",
            "block_comment",
            "documentation_comment",
        ):
            text = get_node_text(sibling, source)
            comments.insert(0, text)
            sibling = sibling.prev_named_sibling
        else:
            break

    if not comments:
        return None

    import re

    result = []
    for c in comments:
        c = re.sub(r"^/\*\*?|\*/$", "", c)
        c = re.sub(r"^//\s?", "", c, flags=re.MULTILINE)
        c = re.sub(r"^\s*\*\s?", "", c, flags=re.MULTILINE)
        result.append(c.strip())

    return "\n".join(result).strip() or None
