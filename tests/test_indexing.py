"""Integration tests for the indexing pipeline: index_all, index_file, delete_file, apply_delta."""

from __future__ import annotations

from pycodegraph import CodeGraph
from pycodegraph.types import IndexResult
from tests.conftest import write_file


class TestIndexAll:
    """index_all() indexes every file in the project."""

    def test_returns_success(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        result = cg.index_all()
        assert result.success is True
        cg.close()

    def test_counts_files(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        result = cg.index_all()
        assert result.files_indexed >= 4  # 4 Python files
        cg.close()

    def test_creates_nodes(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        result = cg.index_all()
        assert result.nodes_created > 0
        cg.close()

    def test_creates_edges(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        result = cg.index_all()
        assert result.edges_created > 0
        cg.close()

    def test_resolves_refs(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        result = cg.index_all()
        assert result.refs_resolved > 0
        cg.close()

    def test_idempotent_unchanged(self, create_python_project):
        """Second index_all() with unchanged files should skip them all."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        first = cg.index_all()
        assert first.files_indexed > 0

        second = cg.index_all()
        assert second.files_skipped > 0
        assert second.nodes_created == 0
        cg.close()

    def test_progress_callback(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        phases = []

        def on_progress(phase, cur, total, f="", **kw):
            phases.append(phase)

        cg.index_all(on_progress)
        assert len(phases) > 0
        # At least scanning and parsing phases should appear
        assert "scanning" in phases or "parsing" in phases
        cg.close()

    def test_empty_project(self, tmp_path):
        """index_all() on an empty directory succeeds with 0 files."""
        root = str(tmp_path)
        cg = CodeGraph.init(root)
        result = cg.index_all()
        assert result.files_indexed == 0
        assert result.success is True
        cg.close()

    def test_returns_index_result(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        result = cg.index_all()
        assert isinstance(result, IndexResult)
        cg.close()


class TestIndexFile:
    """index_file() indexes a single relative path."""

    def test_creates_nodes(self, empty_codegraph, tmp_path):
        root = str(tmp_path)
        write_file(root, "mod.py", "def hello(): pass\n")
        empty_codegraph.index_file("mod.py")
        nodes = empty_codegraph._queries.get_nodes_by_file("mod.py")
        # At least a FILE node + the function node
        assert len(nodes) >= 2

    def test_unchanged_skips(self, empty_codegraph, tmp_path):
        root = str(tmp_path)
        write_file(root, "mod.py", "def hello(): pass\n")
        empty_codegraph.index_file("mod.py")
        count_before = empty_codegraph.get_stats()["node_count"]

        empty_codegraph.index_file("mod.py")
        count_after = empty_codegraph.get_stats()["node_count"]
        assert count_after == count_before

    def test_changed_reindexes(self, empty_codegraph, tmp_path):
        root = str(tmp_path)
        write_file(root, "mod.py", "def hello(): pass\n")
        empty_codegraph.index_file("mod.py")

        # Modify the file to add a second function
        write_file(root, "mod.py", "def hello(): pass\ndef world(): pass\n")
        empty_codegraph.index_file("mod.py")

        nodes = empty_codegraph._queries.get_nodes_by_file("mod.py")
        names = {n.name for n in nodes}
        assert "world" in names

    def test_nonexistent_returns_result(self, empty_codegraph):
        """index_file() on a nonexistent file returns an ExtractionResult (may be None or have errors)."""
        result = empty_codegraph.index_file("does_not_exist.py")
        # The result may be None (file not found) or have errors
        assert result is None or len(result.errors) > 0


class TestDeleteFile:
    """delete_file() removes a file and all its associated data."""

    def test_removes_nodes(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        # Verify data exists before deletion
        assert cg._queries.get_nodes_by_file("models.py")

        cg.delete_file("models.py")
        assert cg._queries.get_nodes_by_file("models.py") == []
        cg.close()

    def test_removes_edges(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        nodes_before = cg._queries.get_nodes_by_file("models.py")
        node_ids = {n.id for n in nodes_before}

        cg.delete_file("models.py")

        # No edge should reference a deleted node
        for nid in node_ids:
            assert cg._queries.get_outgoing_edges(nid) == []
            assert cg._queries.get_incoming_edges(nid) == []
        cg.close()

    def test_removes_file_record(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        cg.delete_file("models.py")
        assert cg._queries.get_file_by_path("models.py") is None
        cg.close()

    def test_nonexistent_noop(self, create_python_project):
        """Deleting a file that was never indexed should not raise."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        cg.delete_file("nonexistent.py")  # Should not raise
        cg.close()


class TestApplyDelta:
    """apply_delta() handles incremental changes."""

    def test_changed_files(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        # Modify models.py to add a new function
        write_file(root, "models.py", "class User:\n    pass\ndef extra(): pass\n")

        result = cg.apply_delta(changed_files=["models.py"], removed_files=[])
        assert result.success
        assert result.files_indexed == 1
        # The new function should be in the graph
        nodes = cg._queries.get_nodes_by_file("models.py")
        names = {n.name for n in nodes}
        assert "extra" in names
        cg.close()

    def test_removed_files(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        result = cg.apply_delta(changed_files=[], removed_files=["utils.py"])
        assert result.success
        assert cg._queries.get_file_by_path("utils.py") is None
        assert cg._queries.get_nodes_by_file("utils.py") == []
        cg.close()

    def test_adds_and_removes(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        # Add a new file
        write_file(root, "extra.py", "def bonus(): pass\n")
        # Modify models.py
        write_file(root, "models.py", "class User:\n    pass\n")

        result = cg.apply_delta(
            changed_files=["models.py", "extra.py"],
            removed_files=["utils.py"],
        )
        assert result.success
        assert cg._queries.get_file_by_path("extra.py") is not None
        assert cg._queries.get_file_by_path("utils.py") is None
        cg.close()

    def test_resolves_refs(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        # Add a new file that imports from existing code
        write_file(
            root,
            "extra.py",
            "from models import User\ndef make(): return User('a','b')\n",
        )

        result = cg.apply_delta(changed_files=["extra.py"], removed_files=[])
        assert result.success
        assert result.refs_resolved > 0
        cg.close()

    def test_fatal_error_prevents_resolution(self, tmp_path):
        """apply_delta with an unreadable file should not run resolution."""
        root = str(tmp_path)
        write_file(root, "good.py", "def ok(): pass\n")
        cg = CodeGraph.init(root)
        cg.index_all()

        result = cg.apply_delta(changed_files=["nonexistent_file.py"], removed_files=[])
        assert not result.success
        assert len(result.errors) > 0
        cg.close()

    def test_returns_index_result(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        result = cg.apply_delta(changed_files=[], removed_files=[])
        assert isinstance(result, IndexResult)
        assert result.success
        cg.close()
