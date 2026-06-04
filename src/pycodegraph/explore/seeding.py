"""Named-symbol seeding — resolve query tokens to code symbols."""

from __future__ import annotations

from ..search.query_utils import extract_symbols_from_query
from ..types import Node, NodeKind, SearchOptions

if __name__ == "__type_checking__":
    from ..search.searcher import NodeSearcher

# Node kinds that are callable / high-value for explore
_CALLABLE_KINDS: list[NodeKind] = [
    NodeKind.FUNCTION,
    NodeKind.METHOD,
    NodeKind.CLASS,
    NodeKind.INTERFACE,
    NodeKind.TYPE_ALIAS,
    NodeKind.STRUCT,
    NodeKind.TRAIT,
    NodeKind.COMPONENT,
    NodeKind.ROUTE,
    NodeKind.VARIABLE,
    NodeKind.CONSTANT,
    NodeKind.ENUM,
    NodeKind.MODULE,
    NodeKind.NAMESPACE,
    NodeKind.PROTOCOL,
]

# Score boosts for named symbols
_SPECIFIC_NAME_BOOST = 50.0  # ≤3 defs → likely what the agent meant
_GENERIC_NAME_BOOST = 20.0  # >3 defs → overloaded / ambiguous


def seed_named_symbols(
    query: str,
    searcher: NodeSearcher,
    max_seeds: int = 20,
) -> list[tuple[Node, float]]:
    """Resolve query tokens to code symbols with score boosts.

    Returns a list of (Node, score_boost) tuples.  Symbols that the
    agent explicitly named get a high boost; overloaded names get a
    lower boost.
    """
    tokens = extract_symbols_from_query(query)
    if not tokens:
        return []

    # PascalCase tokens serve as type/file disambiguators
    type_tokens = {t.lower() for t in tokens if t[0].isupper() and len(t) >= 4}

    named: list[tuple[Node, float]] = []
    seen_ids: set[str] = set()

    for token in tokens:
        candidates = searcher.find_nodes_by_exact_name(
            [token],
            options=SearchOptions(
                kinds=_CALLABLE_KINDS,
                limit=max(10, max_seeds * 2),
            ),
        )

        # Sort: non-test, larger body first (skip stubs)
        candidates.sort(
            key=lambda r: (
                0 if "test" not in r.node.file_path.lower() else 1,
                r.node.end_line - r.node.start_line,
            ),
            reverse=True,
        )

        is_specific = len(candidates) <= 3

        if is_specific:
            # Specific name — keep all defs with high boost
            picks = candidates
            boost = _SPECIFIC_NAME_BOOST
        else:
            # Overloaded name — disambiguate by co-naming (PascalCase
            # tokens hint at which class/file the agent means)
            picks = [r for r in candidates if _in_named_context(r.node, type_tokens)]
            if not picks:
                picks = candidates[:1]
            boost = _GENERIC_NAME_BOOST

        for r in picks:
            if r.node.id not in seen_ids:
                seen_ids.add(r.node.id)
                named.append((r.node, boost))

        if len(named) >= max_seeds:
            break

    return named


def _in_named_context(node: Node, type_tokens: set[str]) -> bool:
    """Check whether a node lives in a class/file the query also names."""
    if not type_tokens:
        return False
    file_lower = node.file_path.lower()
    qname_lower = node.qualified_name.lower()
    return any(t in file_lower or t in qname_lower for t in type_tokens)
