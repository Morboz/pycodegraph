"""Example: Using CodeGraph Python to index a project.

Install dependencies:
    pip install tree-sitter tree-sitter-python tree-sitter-typescript \
                tree-sitter-javascript tree-sitter-go tree-sitter-rust \
                tree-sitter-java tree-sitter-c tree-sitter-cpp

Usage:
    python -m pycodegraph.example /path/to/project
"""

from __future__ import annotations

import sys
from pycodegraph import CodeGraph


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m pycodegraph.example <project_path>")
        sys.exit(1)

    project_path = sys.argv[1]

    # Initialize
    cg = CodeGraph.init(project_path)

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
