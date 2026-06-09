#!/usr/bin/env python3
"""Quick smoke-test: clean + index + explore on a target repo."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from pycodegraph import CodeGraph

REPO = Path("/Users/xx/software/mini-swe-agent/runs/data/repos/django__django")
QUERY = "QuerySet._fetch_all SQL query compiler execute"


def main():
    # 1. Clean .codegraph
    cg_dir = REPO / ".codegraph"
    if cg_dir.exists():
        print(f"Removing existing {cg_dir} ...")
        shutil.rmtree(cg_dir)
    else:
        print(f"No existing {cg_dir}, clean.")

    # 2. Index
    print(f"\nIndexing {REPO} ...")
    cg = CodeGraph.init(str(REPO))

    t0 = time.monotonic()
    result = cg.index_all()
    elapsed = (time.monotonic() - t0) * 1000

    print(f"Done in {result.duration_ms}ms (wall: {elapsed:.0f}ms)")
    print(f"  indexed:  {result.files_indexed}")
    print(f"  skipped:  {result.files_skipped}")
    print(f"  errored:  {result.files_errored}")

    stats = cg.get_stats()
    print(f"  nodes:    {stats['node_count']}")
    print(f"  edges:    {stats['edge_count']}")

    # 3. Explore
    print(f"\n{'=' * 60}")
    print(f"Explore: {QUERY}")
    print(f"{'=' * 60}\n")

    t1 = time.monotonic()
    text = cg.explore(QUERY)
    explore_ms = (time.monotonic() - t1) * 1000

    print(text)
    print(f"\n--- {explore_ms:.0f}ms, {len(text)} chars ---")

    cg.close()


if __name__ == "__main__":
    main()
