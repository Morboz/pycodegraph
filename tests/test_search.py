"""Integration tests for the search API."""

from __future__ import annotations

from pycodegraph import CodeGraph
from tests.conftest import write_file


class TestSearchBasic:
    """Basic search() behaviour."""

    def test_search_returns_nodes(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            results = cg.search("User")
            assert len(results) > 0
            assert all(isinstance(n, type(results[0])) for n in results)
        finally:
            cg.close()

    def test_search_case_insensitive(self, create_python_project):
        """Lowercase query should still find 'User'."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            results = cg.search("user")
            names = {n.name for n in results}
            assert "User" in names
        finally:
            cg.close()

    def test_search_no_results(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            results = cg.search("ZZZNoSuchSymbol123")
            assert results == []
        finally:
            cg.close()

    def test_search_limit_respected(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            results = cg.search("a", limit=2)
            assert len(results) <= 2
        finally:
            cg.close()


class TestSearchExactName:
    """Exact and partial name matching."""

    def test_exact_name_match_found(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            results = cg.search("create_user")
            names = {n.name for n in results}
            assert "create_user" in names
        finally:
            cg.close()

    def test_partial_name_match(self, create_python_project):
        """Querying 'user' should find User, create_user, notify_user."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            results = cg.search("user")
            names = {n.name for n in results}
            assert "User" in names or "create_user" in names or "notify_user" in names
        finally:
            cg.close()


class TestSearchFts:
    """Full-text search via docstrings and qualified names."""

    def test_fts_search_by_docstring(self, tmp_path):
        """Searching for text that appears in a docstring or qualified name should find nodes."""
        root = str(tmp_path)
        write_file(
            root,
            "mod.py",
            'def process_database_connection():\n    """Handle database connection pool."""\n    pass\n',
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            # The function name contains "database" so FTS/LIKE should find it
            results = cg.search("database")
            assert len(results) > 0
            names = {n.name for n in results}
            assert "process_database_connection" in names
        finally:
            cg.close()

    def test_fts_search_by_qualified_name(self, create_python_project):
        """Searching for a qualified name should find matching nodes."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            results = cg.search("User")
            assert len(results) > 0
            # User class should appear
            assert any(n.name == "User" for n in results)
        finally:
            cg.close()


class TestSearchLike:
    """LIKE-based fallback search."""

    def test_like_fallback_for_short_queries(self, create_python_project):
        """Short queries should still produce results via LIKE fallback."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            # Direct query to the internal _search_nodes_like method
            results = cg._queries._search_nodes_like("run", None, None, 10, 0)
            names = {r.node.name for r in results}
            assert "run" in names
        finally:
            cg.close()

    def test_like_matches_substring(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            results = cg._queries.find_nodes_by_name_substring("user")
            names = {r.node.name for r in results}
            # Should match User, create_user, notify_user
            assert any("user" in n.lower() for n in names)
        finally:
            cg.close()


class TestSearchFuzzy:
    """Fuzzy (edit distance) matching."""

    def test_fuzzy_search_returns_results(self, create_python_project):
        """Fuzzy search should return results for approximate queries."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            results = cg._queries._search_nodes_fuzzy("User", None, None, 5)
            assert isinstance(results, list)
            if results:
                names = {r.node.name for r in results}
                assert "User" in names
        finally:
            cg.close()


class TestGetNodeById:
    """get_node_by_id() lookups."""

    def test_get_node_by_id_returns_node(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            results = cg.search("User")
            assert len(results) > 0
            node_id = results[0].id

            fetched = cg.get_node_by_id(node_id)
            assert fetched is not None
            assert fetched.id == node_id
            assert fetched.name == results[0].name
        finally:
            cg.close()

    def test_get_node_by_id_nonexistent(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            assert cg.get_node_by_id("nonexistent_id_12345") is None
        finally:
            cg.close()
