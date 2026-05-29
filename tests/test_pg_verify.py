"""Verify PostgreSQL query results are actually correct (not just exception-free).

Requires a running PG instance. Skips all tests if unavailable.

Usage:
    FORMSY_PG_DSN="host=localhost port=5433 dbname=ai user=admin password=admin" \
        pytest tests/test_pg_verify.py
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from sqlalchemy import text  # noqa: E402

from pycodegraph import CodeGraph  # noqa: E402
from pycodegraph.types import (  # noqa: E402
    Edge,
    EdgeKind,
    Language,
    Node,
    NodeKind,
    SearchOptions,
)

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


TEST_DB = "codegraph_verify_test"
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

    tmp_root = tempfile.mkdtemp(prefix="cg_pg_verify_")
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

    cg.index_all(lambda *a, **kw: None)
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
# Tests
# ---------------------------------------------------------------------------


@pg_available
class TestStatsConsistency:
    def test_counts_positive(self, codegraph):
        stats = codegraph.get_stats()
        assert stats["node_count"] > 0
        assert stats["edge_count"] > 0
        assert stats["file_count"] > 0

    def test_get_all_nodes_count_matches_stats(self, codegraph):
        stats = codegraph.get_stats()
        all_nodes = codegraph.get_all_nodes(limit=100000)
        assert len(all_nodes) == stats["node_count"]

    def test_get_all_edges_count_matches_stats(self, codegraph):
        stats = codegraph.get_stats()
        all_edges = codegraph.get_all_edges(limit=200000)
        assert len(all_edges) == stats["edge_count"]


@pg_available
class TestNodeFieldCorrectness:
    def test_codegraph_node_found(self, codegraph):
        q = codegraph._queries
        cg_nodes = q.get_nodes_by_name("CodeGraph")
        assert len(cg_nodes) > 0

    def test_codegraph_node_fields(self, codegraph):
        q = codegraph._queries
        node = q.get_nodes_by_name("CodeGraph")[0]
        assert node.kind == NodeKind.CLASS
        assert "codegraph.py" in node.file_path
        assert node.language == Language.PYTHON
        assert node.start_line > 0 and node.end_line >= node.start_line

    def test_qualified_name_roundtrip(self, codegraph):
        q = codegraph._queries
        node = q.get_nodes_by_name("CodeGraph")[0]
        qn_nodes = q.get_nodes_by_qualified_name(node.qualified_name)
        assert len(qn_nodes) > 0
        assert qn_nodes[0].name == node.name
        assert qn_nodes[0].kind == node.kind


@pg_available
class TestGetNodesByFile:
    def test_returns_nodes(self, codegraph):
        q = codegraph._queries
        file_nodes = q.get_nodes_by_file("pycodegraph/db/queries.py")
        assert len(file_nodes) > 0

    def test_all_nodes_have_correct_file_path(self, codegraph):
        q = codegraph._queries
        file_nodes = q.get_nodes_by_file("pycodegraph/db/queries.py")
        assert all(n.file_path == "pycodegraph/db/queries.py" for n in file_nodes)

    def test_file_contains_querybuilder(self, codegraph):
        q = codegraph._queries
        file_nodes = q.get_nodes_by_file("pycodegraph/db/queries.py")
        names = {n.name for n in file_nodes}
        assert "QueryBuilder" in names

    def test_nodes_sorted_by_start_line(self, codegraph):
        q = codegraph._queries
        file_nodes = q.get_nodes_by_file("pycodegraph/db/queries.py")
        assert file_nodes == sorted(file_nodes, key=lambda n: n.start_line)


@pg_available
class TestGetNodesByKind:
    def test_class_kind(self, codegraph):
        q = codegraph._queries
        classes = q.get_nodes_by_kind(NodeKind.CLASS)
        assert len(classes) > 0
        assert all(n.kind == NodeKind.CLASS for n in classes)
        assert "CodeGraph" in {n.name for n in classes}

    def test_function_kind(self, codegraph):
        q = codegraph._queries
        functions = q.get_nodes_by_kind(NodeKind.FUNCTION)
        assert len(functions) > 0
        assert all(n.kind == NodeKind.FUNCTION for n in functions)

    def test_method_kind(self, codegraph):
        q = codegraph._queries
        methods = q.get_nodes_by_kind(NodeKind.METHOD)
        assert len(methods) > 0
        assert all(n.kind == NodeKind.METHOD for n in methods)


@pg_available
class TestGetNodeByIdRoundtrip:
    def test_roundtrip(self, codegraph):
        q = codegraph._queries
        node = q.get_nodes_by_name("CodeGraph")[0]
        fetched = q.get_node_by_id(node.id)
        assert fetched is not None
        assert fetched.id == node.id
        assert fetched.name == node.name
        assert fetched.kind == node.kind
        assert fetched.file_path == node.file_path
        assert fetched.qualified_name == node.qualified_name


@pg_available
class TestEdgeQueries:
    def test_callers_of_insert_nodes(self, codegraph):
        q = codegraph._queries
        insert_nodes = q.get_nodes_by_name("insert_nodes")
        if not insert_nodes:
            pytest.skip("insert_nodes not found")
        callers = q.get_callers(insert_nodes[0].id)
        assert len(callers) > 0
        assert all(e.kind == EdgeKind.CALLS for e in callers)
        assert all(e.target == insert_nodes[0].id for e in callers)

    def test_callees_returns_list(self, codegraph):
        q = codegraph._queries
        insert_nodes = q.get_nodes_by_name("insert_nodes")
        if not insert_nodes:
            pytest.skip("insert_nodes not found")
        assert isinstance(q.get_callees(insert_nodes[0].id), list)

    def test_edge_fields_valid(self, codegraph):
        all_edges = codegraph.get_all_edges(limit=10)
        if not all_edges:
            pytest.skip("no edges")
        e = all_edges[0]
        assert bool(e.source)
        assert bool(e.target)
        assert isinstance(e.kind, EdgeKind)


@pg_available
class TestFindEdgesBetweenNodes:
    def test_connected_pair(self, codegraph):
        q = codegraph._queries
        insert_nodes = q.get_nodes_by_name("insert_nodes")
        if not insert_nodes:
            pytest.skip("insert_nodes not found")
        callers = q.get_callers(insert_nodes[0].id)
        if not callers:
            pytest.skip("no callers")
        caller_node = q.get_node_by_id(callers[0].source)
        if not caller_node:
            pytest.skip("caller not found")
        pair_ids = [insert_nodes[0].id, caller_node.id]
        between = q.find_edges_between_nodes(pair_ids)
        assert len(between) > 0
        assert all(e.source in pair_ids and e.target in pair_ids for e in between)

    def test_with_kind_filter(self, codegraph):
        q = codegraph._queries
        insert_nodes = q.get_nodes_by_name("insert_nodes")
        if not insert_nodes:
            pytest.skip("insert_nodes not found")
        callers = q.get_callers(insert_nodes[0].id)
        if not callers:
            pytest.skip("no callers")
        caller_node = q.get_node_by_id(callers[0].source)
        pair_ids = [insert_nodes[0].id, caller_node.id] if caller_node else []
        result = q.find_edges_between_nodes(pair_ids, ["calls"])
        assert isinstance(result, list)

    def test_empty_list(self, codegraph):
        q = codegraph._queries
        assert q.find_edges_between_nodes([]) == []


@pg_available
class TestOutgoingIncomingEdges:
    def test_outgoing_edges_source(self, codegraph):
        q = codegraph._queries
        node = q.get_nodes_by_name("CodeGraph")[0]
        outgoing = q.get_outgoing_edges(node.id)
        if outgoing:
            assert all(e.source == node.id for e in outgoing)

    def test_outgoing_edges_kind_filter(self, codegraph):
        q = codegraph._queries
        node = q.get_nodes_by_name("CodeGraph")[0]
        contains_edges = q.get_outgoing_edges(node.id, ["contains"])
        if contains_edges:
            assert all(e.kind == EdgeKind.CONTAINS for e in contains_edges)

    def test_incoming_edges_returns_list(self, codegraph):
        q = codegraph._queries
        node = q.get_nodes_by_name("CodeGraph")[0]
        assert isinstance(q.get_incoming_edges(node.id), list)


@pg_available
class TestSearchCorrectness:
    def test_fts_search(self, codegraph):
        q = codegraph._queries
        results = q.search_nodes("QueryBuilder")
        assert len(results) > 0
        names = {r.node.name for r in results}
        assert "QueryBuilder" in names
        assert all(r.score > 0 for r in results)

    def test_like_fallback(self, codegraph):
        q = codegraph._queries
        results = q._search_nodes_like("insert", None, None, 10, 0)
        assert len(results) > 0
        names = {r.node.name for r in results}
        assert any("insert" in n.lower() for n in names)

    def test_exact_name_search(self, codegraph):
        q = codegraph._queries
        results = q.find_nodes_by_exact_name(["QueryBuilder"])
        assert len(results) > 0
        assert all(r.node.name == "QueryBuilder" for r in results)

    def test_substring_search(self, codegraph):
        q = codegraph._queries
        results = q.find_nodes_by_name_substring("Graph")
        assert len(results) > 0
        assert any("Graph" in r.node.name for r in results)

    def test_kind_filter(self, codegraph):
        q = codegraph._queries
        results = q.search_nodes(
            "QueryBuilder", SearchOptions(kinds=[NodeKind.CLASS], limit=5)
        )
        assert len(results) > 0
        assert all(r.node.kind == NodeKind.CLASS for r in results)

    def test_language_filter(self, codegraph):
        q = codegraph._queries
        results = q.search_nodes(
            "function", SearchOptions(languages=[Language.PYTHON], limit=5)
        )
        assert isinstance(results, list)


@pg_available
class TestFileOperations:
    def test_get_file_by_path(self, codegraph):
        q = codegraph._queries
        f = q.get_file_by_path("pycodegraph/db/queries.py")
        assert f is not None
        assert f.path == "pycodegraph/db/queries.py"
        assert f.language == Language.PYTHON
        assert bool(f.content_hash)
        assert f.size > 0
        assert f.node_count > 0

    def test_get_all_files(self, codegraph):
        q = codegraph._queries
        all_files = q.get_all_files()
        assert len(all_files) > 0
        assert all(f.path.endswith(".py") for f in all_files)

    def test_file_paths_match_files_count(self, codegraph):
        q = codegraph._queries
        assert len(q.get_all_file_paths()) == len(q.get_all_files())

    def test_node_names_are_distinct(self, codegraph):
        q = codegraph._queries
        names = q.get_all_node_names()
        assert len(names) == len(set(names))


@pg_available
class TestGraphTraversalCorrectness:
    def test_callers_deep_superset(self, codegraph):
        q = codegraph._queries
        insert_nodes = q.get_nodes_by_name("insert_nodes")
        if not insert_nodes:
            pytest.skip("insert_nodes not found")
        shallow = q.get_callers(insert_nodes[0].id)
        deep = codegraph.get_callers_deep(insert_nodes[0].id, max_depth=2)
        assert len(deep) >= len(shallow)

    def test_callees_deep_tuples(self, codegraph):
        q = codegraph._queries
        insert_nodes = q.get_nodes_by_name("insert_nodes")
        if not insert_nodes:
            pytest.skip("insert_nodes not found")
        deep = codegraph.get_callees_deep(insert_nodes[0].id, max_depth=2)
        if deep:
            assert all(
                isinstance(item, tuple)
                and len(item) == 2
                and isinstance(item[0], Node)
                and isinstance(item[1], Edge)
                for item in deep
            )

    def test_call_graph(self, codegraph):
        q = codegraph._queries
        insert_nodes = q.get_nodes_by_name("insert_nodes")
        if not insert_nodes:
            pytest.skip("insert_nodes not found")
        sg = codegraph.get_call_graph(insert_nodes[0].id, depth=2)
        assert len(sg.nodes) > 0
        assert insert_nodes[0].id in sg.nodes
        assert insert_nodes[0].id in sg.roots

    def test_type_hierarchy(self, codegraph):
        q = codegraph._queries
        node = q.get_nodes_by_name("CodeGraph")[0]
        sg = codegraph.get_type_hierarchy(node.id)
        assert isinstance(sg.nodes, dict)

    def test_find_usages(self, codegraph):
        q = codegraph._queries
        insert_nodes = q.get_nodes_by_name("insert_nodes")
        if not insert_nodes:
            pytest.skip("insert_nodes not found")
        usages = codegraph.find_usages(insert_nodes[0].id)
        if usages:
            assert all(item[1].target == insert_nodes[0].id for item in usages)

    def test_impact_radius(self, codegraph):
        q = codegraph._queries
        insert_nodes = q.get_nodes_by_name("insert_nodes")
        if not insert_nodes:
            pytest.skip("insert_nodes not found")
        sg = codegraph.get_impact_radius(insert_nodes[0].id, max_depth=2)
        assert len(sg.nodes) > 0


@pg_available
class TestContextBuilding:
    def test_get_context(self, codegraph):
        q = codegraph._queries
        insert_nodes = q.get_nodes_by_name("insert_nodes")
        if not insert_nodes:
            pytest.skip("insert_nodes not found")
        ctx = codegraph.get_context(insert_nodes[0].id)
        assert ctx.focal is not None
        assert ctx.focal.id == insert_nodes[0].id


@pg_available
class TestFileDependencies:
    def test_dependencies(self, codegraph):
        deps = codegraph.get_file_dependencies("pycodegraph/codegraph.py")
        assert isinstance(deps, list)
        if deps:
            assert all(d != "pycodegraph/codegraph.py" for d in deps)

    def test_dependents(self, codegraph):
        deps = codegraph.get_file_dependents("pycodegraph/codegraph.py")
        assert isinstance(deps, list)


@pg_available
class TestUnresolvedReferences:
    def test_count_non_negative(self, codegraph):
        q = codegraph._queries
        assert q.get_unresolved_refs_count() >= 0


@pg_available
class TestCaseInsensitiveLookup:
    def test_get_nodes_by_lower_name(self, codegraph):
        q = codegraph._queries
        results = q.get_nodes_by_lower_name("codegraph")
        assert len(results) > 0
        assert any(n.name == "CodeGraph" for n in results)


@pg_available
class TestPgSpecificFts:
    def test_tsvector_column_works(self, codegraph):
        q = codegraph._queries
        rows = q._conn.execute(
            text(
                "SELECT id, name FROM nodes "
                "WHERE fts @@ plainto_tsquery('simple', 'QueryBuilder') "
                "ORDER BY ts_rank(fts, plainto_tsquery('simple', 'QueryBuilder')) DESC "
                "LIMIT 50"
            )
        ).fetchall()
        assert len(rows) > 0
        fts_names = [r[1] for r in rows]
        assert "QueryBuilder" in fts_names

    def test_gin_index_exists(self, codegraph):
        q = codegraph._queries
        idx = q._conn.execute(
            text(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'nodes' AND indexname = 'idx_nodes_fts'"
            )
        ).fetchone()
        assert idx is not None

    def test_trgm_index_exists(self, codegraph):
        q = codegraph._queries
        idx = q._conn.execute(
            text(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'nodes' AND indexname = 'idx_nodes_name_trgm'"
            )
        ).fetchone()
        assert idx is not None


@pg_available
class TestFileNodesHaveEdges:
    def test_queries_py_nodes_have_edges(self, codegraph):
        q = codegraph._queries
        file_nodes = q.get_nodes_by_file("pycodegraph/db/queries.py")
        qb_node_ids = {n.id for n in file_nodes}
        has_edges = False
        for nid in list(qb_node_ids)[:10]:
            if q.get_outgoing_edges(nid) or q.get_incoming_edges(nid):
                has_edges = True
                break
        assert has_edges
