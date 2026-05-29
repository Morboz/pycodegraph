"""PostgreSQL query validation for CodeGraph.

Tests every query path against PG to catch dialect-specific issues.

Requires a running PG instance. Skips all tests if unavailable.

Usage:
    FORMSY_PG_DSN="host=localhost port=5433 dbname=ai user=admin password=admin" \
        pytest tests/test_pg_queries.py
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import psycopg
import pytest

from pycodegraph import CodeGraph
from pycodegraph.types import Language, NodeKind

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PG_DSN = os.environ.get(
    "FORMSY_PG_DSN",
    "host=localhost port=5433 dbname=ai user=admin password=admin",
)
PROJECT_SRC = str(Path(__file__).parent.parent / "src")


def _build_sa_url(dsn: str, dbname: str) -> str:
    parts: dict[str, str] = {}
    for token in dsn.split():
        k, _, v = token.partition("=")
        parts[k] = v
    host = parts.get("host", "localhost")
    port = parts.get("port", "5432")
    user = parts.get("user", "postgres")
    password = parts.get("password", "")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{dbname}"


TEST_DB = "codegraph_query_test"
TEST_DB_URL = _build_sa_url(PG_DSN, TEST_DB)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _pg_available() -> bool:
    try:
        with psycopg.connect(PG_DSN, autocommit=True) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


pg_available = pytest.mark.skipif(
    not _pg_available(), reason="PostgreSQL not available"
)


@pytest.fixture(scope="module")
def codegraph():
    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
        conn.execute(f"CREATE DATABASE {TEST_DB}")

    tmp_root = tempfile.mkdtemp(prefix="cg_pg_test_")
    cg = CodeGraph.init(
        tmp_root,
        config_overrides={
            "db_url": TEST_DB_URL,
            "root_dir": PROJECT_SRC,
            "include": ["**/*.py"],
        },
    )
    cg._project_root = PROJECT_SRC
    cg._orchestrator.root_dir = PROJECT_SRC

    cg.index_all(lambda phase, cur, total, f="", **_: None)
    stats = cg.get_stats()
    assert stats["node_count"] > 0, f"No nodes indexed: {stats}"

    yield cg

    cg.close()
    shutil.rmtree(tmp_root, ignore_errors=True)
    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            [TEST_DB],
        )
        conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")


# ---------------------------------------------------------------------------
# Node Query Operations
# ---------------------------------------------------------------------------


@pg_available
class TestNodeQueries:
    def test_get_node_by_id(self, codegraph):
        all_nodes = codegraph.get_all_nodes(limit=100)
        node = codegraph.get_node_by_id(all_nodes[0].id)
        assert node is not None

    def test_search_text(self, codegraph):
        results = codegraph.search("QueryBuilder", limit=5)
        assert len(results) > 0

    def test_search_kind_filter(self, codegraph):
        results = codegraph.search("class", limit=5)
        assert isinstance(results, list)

    def test_get_callers(self, codegraph):
        func_nodes = [
            n
            for n in codegraph.get_all_nodes(limit=100)
            if n.kind in (NodeKind.FUNCTION, NodeKind.METHOD)
        ]
        if func_nodes:
            edges = codegraph.get_callers(func_nodes[0].id)
            assert isinstance(edges, list)

    def test_get_callees(self, codegraph):
        func_nodes = [
            n
            for n in codegraph.get_all_nodes(limit=100)
            if n.kind in (NodeKind.FUNCTION, NodeKind.METHOD)
        ]
        if func_nodes:
            edges = codegraph.get_callees(func_nodes[0].id)
            assert isinstance(edges, list)

    def test_get_all_nodes(self, codegraph):
        nodes = codegraph.get_all_nodes(limit=10)
        assert len(nodes) > 0

    def test_get_all_edges(self, codegraph):
        edges = codegraph.get_all_edges(limit=10)
        assert isinstance(edges, list)

    def test_get_stats(self, codegraph):
        stats = codegraph.get_stats()
        assert stats["node_count"] > 0
        assert stats["edge_count"] > 0


# ---------------------------------------------------------------------------
# Graph Traversal Operations
# ---------------------------------------------------------------------------


@pg_available
class TestGraphTraversal:
    def test_get_context(self, codegraph):
        func_nodes = [
            n
            for n in codegraph.get_all_nodes(limit=100)
            if n.kind in (NodeKind.FUNCTION, NodeKind.METHOD)
        ]
        if func_nodes:
            ctx = codegraph.get_context(func_nodes[0].id)
            assert ctx.focal is not None

    def test_get_callers_deep(self, codegraph):
        func_nodes = [
            n
            for n in codegraph.get_all_nodes(limit=100)
            if n.kind in (NodeKind.FUNCTION, NodeKind.METHOD)
        ]
        if func_nodes:
            result = codegraph.get_callers_deep(func_nodes[0].id, max_depth=2)
            assert isinstance(result, list)

    def test_get_callees_deep(self, codegraph):
        func_nodes = [
            n
            for n in codegraph.get_all_nodes(limit=100)
            if n.kind in (NodeKind.FUNCTION, NodeKind.METHOD)
        ]
        if func_nodes:
            result = codegraph.get_callees_deep(func_nodes[0].id, max_depth=2)
            assert isinstance(result, list)

    def test_get_call_graph(self, codegraph):
        func_nodes = [
            n
            for n in codegraph.get_all_nodes(limit=100)
            if n.kind in (NodeKind.FUNCTION, NodeKind.METHOD)
        ]
        if func_nodes:
            sg = codegraph.get_call_graph(func_nodes[0].id, depth=2)
            assert len(sg.nodes) > 0

    def test_get_type_hierarchy(self, codegraph):
        class_nodes = [
            n for n in codegraph.get_all_nodes(limit=100) if n.kind == NodeKind.CLASS
        ]
        if class_nodes:
            sg = codegraph.get_type_hierarchy(class_nodes[0].id)
            assert isinstance(sg.nodes, dict)

    def test_find_usages(self, codegraph):
        func_nodes = [
            n
            for n in codegraph.get_all_nodes(limit=100)
            if n.kind in (NodeKind.FUNCTION, NodeKind.METHOD)
        ]
        if func_nodes:
            result = codegraph.find_usages(func_nodes[0].id)
            assert isinstance(result, list)

    def test_get_impact_radius(self, codegraph):
        func_nodes = [
            n
            for n in codegraph.get_all_nodes(limit=100)
            if n.kind in (NodeKind.FUNCTION, NodeKind.METHOD)
        ]
        if func_nodes:
            sg = codegraph.get_impact_radius(func_nodes[0].id, max_depth=2)
            assert len(sg.nodes) > 0


# ---------------------------------------------------------------------------
# File Dependency Operations
# ---------------------------------------------------------------------------


@pg_available
class TestFileDependencies:
    def test_get_file_dependencies(self, codegraph):
        node = codegraph.get_all_nodes(limit=1)[0]
        deps = codegraph.get_file_dependencies(node.file_path)
        assert isinstance(deps, list)

    def test_get_file_dependents(self, codegraph):
        node = codegraph.get_all_nodes(limit=1)[0]
        deps = codegraph.get_file_dependents(node.file_path)
        assert isinstance(deps, list)


# ---------------------------------------------------------------------------
# QueryBuilder Direct Operations
# ---------------------------------------------------------------------------


@pg_available
class TestQueryBuilderDirect:
    def test_get_nodes_by_name(self, codegraph):
        node = codegraph.get_all_nodes(limit=1)[0]
        results = codegraph._queries.get_nodes_by_name(node.name)
        assert isinstance(results, list)

    def test_get_nodes_by_qualified_name(self, codegraph):
        node = codegraph.get_all_nodes(limit=1)[0]
        results = codegraph._queries.get_nodes_by_qualified_name(node.qualified_name)
        assert isinstance(results, list)

    def test_get_nodes_by_lower_name(self, codegraph):
        node = codegraph.get_all_nodes(limit=1)[0]
        results = codegraph._queries.get_nodes_by_lower_name(node.name.lower())
        assert isinstance(results, list)

    def test_get_nodes_by_file(self, codegraph):
        node = codegraph.get_all_nodes(limit=1)[0]
        results = codegraph._queries.get_nodes_by_file(node.file_path)
        assert isinstance(results, list)

    def test_get_nodes_by_kind(self, codegraph):
        results = codegraph._queries.get_nodes_by_kind(NodeKind.CLASS)
        assert isinstance(results, list)

    def test_get_outgoing_edges(self, codegraph):
        node = codegraph.get_all_nodes(limit=1)[0]
        results = codegraph._queries.get_outgoing_edges(node.id)
        assert isinstance(results, list)

    def test_get_incoming_edges(self, codegraph):
        node = codegraph.get_all_nodes(limit=1)[0]
        results = codegraph._queries.get_incoming_edges(node.id)
        assert isinstance(results, list)

    def test_get_outgoing_edges_with_kind_filter(self, codegraph):
        func_nodes = [
            n
            for n in codegraph.get_all_nodes(limit=100)
            if n.kind in (NodeKind.FUNCTION, NodeKind.METHOD)
        ]
        if func_nodes:
            results = codegraph._queries.get_outgoing_edges(func_nodes[0].id, ["calls"])
            assert isinstance(results, list)

    def test_get_incoming_edges_with_kind_filter(self, codegraph):
        func_nodes = [
            n
            for n in codegraph.get_all_nodes(limit=100)
            if n.kind in (NodeKind.FUNCTION, NodeKind.METHOD)
        ]
        if func_nodes:
            results = codegraph._queries.get_incoming_edges(func_nodes[0].id, ["calls"])
            assert isinstance(results, list)

    def test_find_edges_between_nodes(self, codegraph):
        node_ids = [n.id for n in codegraph.get_all_nodes(limit=10)]
        results = codegraph._queries.find_edges_between_nodes(node_ids)
        assert isinstance(results, list)

    def test_find_edges_between_nodes_with_kind_filter(self, codegraph):
        node_ids = [n.id for n in codegraph.get_all_nodes(limit=10)]
        results = codegraph._queries.find_edges_between_nodes(node_ids, ["calls"])
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Search Operations
# ---------------------------------------------------------------------------


@pg_available
class TestSearchOperations:
    def test_search_nodes_fts(self, codegraph):
        results = codegraph._queries.search_nodes("CodeGraph")
        assert len(results) > 0

    def test_search_nodes_with_kind_filter(self, codegraph):
        from pycodegraph.types import SearchOptions

        results = codegraph._queries.search_nodes(
            "init", SearchOptions(kinds=[NodeKind.METHOD], limit=5)
        )
        assert isinstance(results, list)

    def test_find_nodes_by_exact_name(self, codegraph):
        results = codegraph._queries.find_nodes_by_exact_name(["QueryBuilder"])
        assert len(results) > 0

    def test_find_nodes_by_name_substring(self, codegraph):
        results = codegraph._queries.find_nodes_by_name_substring("insert")
        assert len(results) > 0

    def test_search_nodes_like_fallback(self, codegraph):
        results = codegraph._queries._search_nodes_like("ZzzNotExist", None, None, 5, 0)
        assert isinstance(results, list)

    def test_search_nodes_like_with_match(self, codegraph):
        results = codegraph._queries._search_nodes_like("Graph", None, None, 5, 0)
        assert len(results) > 0

    def test_search_nodes_fuzzy(self, codegraph):
        results = codegraph._queries._search_nodes_fuzzy("QueriBilder", None, None, 5)
        assert isinstance(results, list)

    def test_search_all_by_filters(self, codegraph):
        results = codegraph._queries._search_all_by_filters(["class"], None, 5)
        assert isinstance(results, list)

    def test_search_nodes_with_language_filter(self, codegraph):
        from pycodegraph.types import SearchOptions

        results = codegraph._queries.search_nodes(
            "function", SearchOptions(languages=[Language.PYTHON], limit=5)
        )
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# File Operations
# ---------------------------------------------------------------------------


@pg_available
class TestFileOperations:
    def test_get_file_by_path(self, codegraph):
        node = codegraph.get_all_nodes(limit=1)[0]
        f = codegraph._queries.get_file_by_path(node.file_path)
        assert f is not None

    def test_get_all_files(self, codegraph):
        results = codegraph._queries.get_all_files()
        assert len(results) > 0

    def test_get_all_file_paths(self, codegraph):
        results = codegraph._queries.get_all_file_paths()
        assert len(results) > 0

    def test_get_all_node_names(self, codegraph):
        results = codegraph._queries.get_all_node_names()
        assert len(results) > 0


# ---------------------------------------------------------------------------
# Unresolved Reference Operations
# ---------------------------------------------------------------------------


@pg_available
class TestUnresolvedRefs:
    def test_get_unresolved_refs_count(self, codegraph):
        count = codegraph._queries.get_unresolved_refs_count()
        assert count >= 0

    def test_get_all_unresolved_refs(self, codegraph):
        results = codegraph._queries.get_all_unresolved_refs(limit=5)
        assert isinstance(results, list)

    def test_get_unresolved_refs_batch(self, codegraph):
        results = codegraph._queries.get_unresolved_refs_batch(offset=0, limit=5)
        assert isinstance(results, list)
