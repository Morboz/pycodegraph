"""Test Analysis stage — creates TESTS Edges linking test functions to
production symbols they directly exercise."""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..db.queries import QueryBuilder
from ..types import Edge, EdgeKind, Node
from . import is_test_node

logger = logging.getLogger(__name__)

# Edge kinds that a test function can use to exercise production code.
_RELEVANT_EDGE_KINDS = frozenset(
    {EdgeKind.CALLS, EdgeKind.INSTANTIATES, EdgeKind.REFERENCES}
)


class TestAnalyzer:
    """Reads the persisted graph and creates TESTS Edges.

    Runs as the third stage of the indexing pipeline, after Resolution.
    """

    def __init__(self, queries: QueryBuilder):
        self._queries = queries

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_and_persist(
        self,
        on_progress: Callable | None = None,
    ) -> dict:
        """Run test analysis and persist TESTS edges.

        Returns a stats dict with ``edges_created``.
        """
        # 1. Load all nodes
        all_nodes_list = self._queries.get_all_nodes()
        all_nodes = {n.id: n for n in all_nodes_list}

        # 2. Identify test nodes
        test_node_ids = {nid for nid, n in all_nodes.items() if is_test_node(n)}
        if not test_node_ids:
            return {"edges_created": 0}

        # 3. Build file-level import index: test_file -> set of imported file paths
        test_file_imports: dict[str, set[str]] = {}
        _build_import_index(self._queries, test_node_ids, all_nodes, test_file_imports)

        # 4. Delete old TEST edges first (idempotent re-run)
        self._queries.delete_edges_by_provenance_prefix("test-analysis")

        # 5. For each test node, find outgoing edges and create TESTS edges
        tests_edges: list[Edge] = []
        seen_pairs: set[tuple[str, str]] = set()

        total = len(test_node_ids)
        for i, test_id in enumerate(sorted(test_node_ids)):
            if on_progress and i % 100 == 0:
                on_progress("test-analysis", i, total, "")

            test_node = all_nodes[test_id]
            imported_files = test_file_imports.get(test_node.file_path, set())
            if not imported_files:
                continue

            outgoing = self._queries.get_outgoing_edges(
                test_id,
                kinds=[k.value for k in _RELEVANT_EDGE_KINDS],
            )

            for edge in outgoing:
                target_node = all_nodes.get(edge.target)
                if target_node is None:
                    continue

                # Only create TESTS edge if target's file is imported by the test
                if target_node.file_path not in imported_files:
                    continue

                # Deduplicate
                pair = (test_id, edge.target)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)

                tests_edges.append(
                    Edge(
                        source=test_id,
                        target=edge.target,
                        kind=EdgeKind.TESTS,
                        provenance="test-analysis",
                    )
                )

        if tests_edges:
            self._queries.insert_edges(tests_edges)

        return {"edges_created": len(tests_edges)}


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _build_import_index(
    queries: QueryBuilder,
    test_node_ids: set[str],
    all_nodes: dict[str, Node],
    out: dict[str, set[str]],
) -> None:
    """Populate *out* with ``{test_file_path: {imported_file_path}}``.

    For each test file, collects all IMPORTS edges originating from any
    node in that file, then maps them to the file paths of the imported
    targets.  Files that are themselves test files are excluded (test
    helpers / fixtures don't count as production targets).
    """
    from ..search.query_utils import is_test_file as _is_test_file

    # Group test node IDs by their file_path
    test_files: dict[str, set[str]] = {}
    for nid in test_node_ids:
        node = all_nodes.get(nid)
        if node:
            test_files.setdefault(node.file_path, set()).add(nid)

    for test_file, _test_ids in test_files.items():
        # Get all nodes in this test file
        file_nodes = queries.get_nodes_by_file(test_file)
        file_node_ids = {n.id for n in file_nodes}

        imported_files: set[str] = set()
        for fnid in file_node_ids:
            imports = queries.get_outgoing_edges(fnid, kinds=[EdgeKind.IMPORTS.value])
            for imp_edge in imports:
                target = all_nodes.get(imp_edge.target)
                if target is None:
                    continue
                # Exclude test files as targets
                if _is_test_file(target.file_path):
                    continue
                imported_files.add(target.file_path)

        out[test_file] = imported_files
