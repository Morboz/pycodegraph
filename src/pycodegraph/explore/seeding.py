"""Named-symbol seeding — resolve query tokens to code symbols."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..search.query_utils import (
    extract_symbols_from_query,
    is_test_file,
    normalize_name_token,
)
from ..types import Node, NodeKind, SearchOptions

if TYPE_CHECKING:
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

# Overloaded-name disambiguation caps (matching TS CodeGraph behaviour)
_DISAMBIGUATED_CAP = 4  # max picks after co-naming disambiguation
_GENERIC_FALLBACK_CAP = 1  # max picks when no disambiguation context matches


def seed_named_symbols(
    query: str,
    searcher: NodeSearcher,
    max_seeds: int = 20,
) -> list[tuple[Node, float]]:
    """Resolve query tokens to code symbols with score boosts.

    Returns a list of (Node, score_boost) tuples.  Symbols that the
    agent explicitly named get a high boost; overloaded names get a
    lower boost.

    **Seeding strategy (aligned with TS CodeGraph):**

    1. Extract symbol-like tokens from the query.
    2. For each token, look up all definitions by exact name.
    3. **Filter out test-file candidates** unless the query itself
       contains "test"/"spec" — test files rarely contain the answer
       the agent is looking for.
    4. Specific names (≤3 defs) keep all candidates at high boost.
    5. Overloaded names (>3 defs) use **co-naming disambiguation**:
       only keep defs whose class/file is also named in the query
       (PascalCase tokens serve as type hints).  Capped at
       ``_DISAMBIGUATED_CAP`` picks.
    6. If disambiguation yields nothing, fall back to the single
       most-substantive non-test definition (``_GENERIC_FALLBACK_CAP``).
    """
    tokens = extract_symbols_from_query(query)
    if not tokens:
        return []

    # PascalCase tokens serve as type/file disambiguators
    project_name_tokens = searcher.project_name_tokens
    type_tokens = {
        t.lower()
        for t in tokens
        if t[0].isupper()
        and len(t) >= 4
        and normalize_name_token(t) not in project_name_tokens
    }

    # Whether the query explicitly asks about tests — if so, keep
    # test-file candidates in the pool (matching TS CodeGraph).
    query_lower = query.lower()
    is_test_query = "test" in query_lower or "spec" in query_lower

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

        # ── Filter out test-file candidates (TS CodeGraph: isTestPath) ───
        # Test files rarely contain the answer; their presence inflates
        # heuristic scores and pollutes file ranking.  Skip them unless
        # the agent explicitly asks about tests.
        if not is_test_query:
            candidates = [r for r in candidates if not is_test_file(r.node.file_path)]

        # Sort: larger body first (skip stubs).  When is_test_query=True,
        # test files remain in the pool — sort them to the back so that
        # fallback picks prefer production implementations.
        # Key: (is_test 0/1, body_lines) — non-test & large-body first.
        candidates.sort(
            key=lambda r: (
                1 if is_test_file(r.node.file_path) else 0,
                -(r.node.end_line - r.node.start_line),
            ),
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
            if picks:
                # Disambiguation matched — cap to avoid flooding
                picks = picks[:_DISAMBIGUATED_CAP]
            else:
                # No disambiguation match — fall back to the single
                # most-substantive definition (non-test, largest body)
                picks = candidates[:_GENERIC_FALLBACK_CAP]
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
