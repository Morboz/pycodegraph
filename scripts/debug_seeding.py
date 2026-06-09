#!/usr/bin/env python3
"""Debug script: print seeding and file-ranking intermediate results."""

from __future__ import annotations

from pathlib import Path

from pycodegraph import CodeGraph
from pycodegraph.explore.seeding import seed_named_symbols
from pycodegraph.search.query_utils import extract_symbols_from_query

REPO = Path("/Users/xx/software/mini-swe-agent/runs/data/repos/django__django")
QUERY = "QuerySet._fetch_all SQL query compiler execute"


def main():
    # Open existing index (no re-index)
    cg = CodeGraph.open(str(REPO))

    # 1. Show extracted tokens
    tokens = extract_symbols_from_query(QUERY)
    type_tokens = {t.lower() for t in tokens if t[0].isupper() and len(t) >= 4}
    print(f"Query: {QUERY}")
    print(f"Extracted tokens: {tokens}")
    print(f"Type tokens (PascalCase disambiguators): {type_tokens}")
    print()

    # 2. Show seeding results
    seeds = seed_named_symbols(QUERY, cg._searcher)
    print(f"Seeds ({len(seeds)}):")
    for node, boost in seeds:
        is_test = "test" in node.file_path.lower()
        print(
            f"  boost={boost:5.1f}  test={is_test}  kind={node.kind.value:10s}  "
            f"qname={node.qualified_name}  file={node.file_path}:{node.start_line}"
        )
    print()

    # 3. Show file-level seed distribution
    from collections import defaultdict

    file_boosts: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for node, boost in seeds:
        file_boosts[node.file_path].append((node.qualified_name, boost))

    print("File-level seed aggregation:")
    for fp, items in sorted(
        file_boosts.items(), key=lambda x: -sum(b for _, b in x[1])
    ):
        total = sum(b for _, b in items)
        is_test = "test" in fp.lower()
        print(
            f"  {'[TEST]' if is_test else '     '} total_boost={total:6.1f}  "
            f"seeds={len(items)}  file={fp}"
        )
        for qname, boost in items:
            print(f"    {boost:5.1f}  {qname}")

    cg.close()


if __name__ == "__main__":
    main()
