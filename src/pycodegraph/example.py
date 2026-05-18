"""Example: Using CodeGraph Python to index a project.

Install dependencies:
    pip install tree-sitter tree-sitter-python tree-sitter-typescript \
                tree-sitter-javascript tree-sitter-go tree-sitter-rust \
                tree-sitter-java tree-sitter-c tree-sitter-cpp

    # For PostgreSQL (psycopg3):
    pip install "psycopg[binary]>=3.0"

Usage:
    # SQLite (default)
    python -m pycodegraph.example /path/to/project

    # PostgreSQL
    python -m pycodegraph.example /path/to/project --db postgresql+psycopg://user:pass@localhost:5432/mydb
"""

from __future__ import annotations

import sys
from pycodegraph import CodeGraph


def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: python -m pycodegraph.example <project_path> [--db <db_url>]")
        sys.exit(1)

    project_path = args[0]

    db_url = None
    if "--db" in args:
        idx = args.index("--db")
        if idx + 1 >= len(args):
            print("Error: --db requires a value")
            sys.exit(1)
        db_url = args[idx + 1]

    config_overrides = {"db_url": db_url} if db_url else None

    # Initialize
    cg = CodeGraph.init(project_path, config_overrides=config_overrides)

    # Index all files
    def on_progress(phase, current, total, current_file=""):
        if phase == "scanning":
            print(f"\r  Scanning: {current} files...", end="", flush=True)
        elif phase == "parsing":
            pct = (current / total * 100) if total > 0 else 0
            print(f"\r  Parsing: {current}/{total} ({pct:.0f}%) - {current_file[:60]:<60}", end="", flush=True)

    print(f"Indexing {project_path}...")
    result = cg.index_all(on_progress)
    print()

    # Print results
    stats = cg.get_stats()
    print(f"\nDone in {result.duration_ms}ms:")
    print(f"  Files indexed:  {result.files_indexed}")
    print(f"  Files skipped:  {result.files_skipped}")
    print(f"  Files errored:  {result.files_errored}")
    print(f"  Nodes created:  {stats['node_count']}")
    print(f"  Edges created:  {stats['edge_count']}")

    # Example queries
    if stats["node_count"] > 0:
        print("\nSample nodes:")
        nodes = cg.search("class", limit=5)
        for node in nodes:
            print(f"  [{node.kind.value}] {node.qualified_name} @ {node.file_path}:{node.start_line}")

    cg.close()


if __name__ == "__main__":
    main()
