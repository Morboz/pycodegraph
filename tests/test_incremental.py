"""Integration tests for incremental re-indexing: index_all idempotency, apply_delta, consistency."""

from __future__ import annotations

from pathlib import Path

import pytest

from pycodegraph import CodeGraph
from pycodegraph.types import NodeKind
from tests.conftest import write_file


class TestIncrementalIndexAll:
    """index_all() handles unchanged, modified, and new files incrementally."""

    def test_second_index_all_skips_unchanged(self, create_python_project):
        """Re-running index_all on unchanged files should skip them."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        first = cg.index_all()
        assert first.files_indexed > 0

        second = cg.index_all()
        assert second.files_skipped == first.files_indexed
        assert second.nodes_created == 0
        cg.close()

    def test_modified_file_reindexed(self, create_python_project):
        """Modifying a file and re-running index_all should update the graph."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        # Modify models.py to add a new class
        write_file(root, "models.py", "class User:\n    pass\nclass Extra:\n    pass\n")
        result = cg.index_all()
        assert result.files_indexed >= 1

        nodes = cg._queries.get_nodes_by_file("models.py")
        names = {n.name for n in nodes}
        assert "Extra" in names
        cg.close()

    def test_new_file_indexed(self, create_python_project):
        """Adding a new file and re-running index_all should index it."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        initial_count = cg.get_stats()["file_count"]

        write_file(root, "new_module.py", "def brand_new(): pass\n")
        result = cg.index_all()
        assert result.files_indexed >= 1

        new_count = cg.get_stats()["file_count"]
        assert new_count > initial_count
        cg.close()

    def test_content_hash_drives_skip(self, create_python_project):
        """Touching a file without changing content should still skip it."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        # Touch the file (change mtime but not content)
        p = Path(root) / "models.py"
        content = p.read_text()
        p.write_text(content)  # Same content, new mtime

        result = cg.index_all()
        # Should still be skipped because content hash matches
        assert result.files_skipped > 0
        cg.close()


class TestApplyDeltaIncremental:
    """apply_delta() handles incremental add/modify/remove."""

    def test_apply_delta_adds_new_file(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        write_file(root, "extra.py", "def bonus(): pass\n")
        result = cg.apply_delta(changed_files=["extra.py"], removed_files=[])
        assert result.success

        nodes = cg._queries.get_nodes_by_file("extra.py")
        assert any(n.name == "bonus" for n in nodes)
        cg.close()

    def test_apply_delta_removes_deleted_file(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        result = cg.apply_delta(changed_files=[], removed_files=["utils.py"])
        assert result.success
        assert cg._queries.get_file_by_path("utils.py") is None
        assert cg._queries.get_nodes_by_file("utils.py") == []
        cg.close()

    def test_apply_delta_modifies_existing_file(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        write_file(root, "models.py", "class User:\n    pass\ndef standalone(): pass\n")
        result = cg.apply_delta(changed_files=["models.py"], removed_files=[])
        assert result.success

        nodes = cg._queries.get_nodes_by_file("models.py")
        names = {n.name for n in nodes}
        assert "standalone" in names
        # Old Admin class should be gone
        assert "Admin" not in names
        cg.close()

    def test_apply_delta_combined_add_modify_remove(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        # Add new file
        write_file(root, "extra.py", "def bonus(): pass\n")
        # Modify existing
        write_file(root, "models.py", "class User:\n    pass\n")
        # Remove
        result = cg.apply_delta(
            changed_files=["models.py", "extra.py"],
            removed_files=["utils.py"],
        )
        assert result.success
        assert cg._queries.get_file_by_path("extra.py") is not None
        assert cg._queries.get_file_by_path("utils.py") is None

        # Modified models should have User but not Admin
        nodes = cg._queries.get_nodes_by_file("models.py")
        names = {n.name for n in nodes}
        assert "User" in names
        assert "Admin" not in names
        cg.close()

    def test_apply_delta_re_resolves_cross_file_refs(self, create_python_project):
        """After apply_delta, cross-file references should be re-resolved."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        # Add a new file that uses an existing symbol
        write_file(
            root,
            "extra.py",
            "from models import User\ndef make_user(): return User('a','b')\n",
        )
        result = cg.apply_delta(changed_files=["extra.py"], removed_files=[])
        assert result.success
        assert result.refs_resolved > 0
        cg.close()


class TestIndexFileIncremental:
    """index_file() works incrementally after initial indexing."""

    def test_index_file_after_index_all(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        initial_count = cg.get_stats()["node_count"]

        write_file(root, "late.py", "def late_addition(): pass\n")
        cg.index_file("late.py")
        new_count = cg.get_stats()["node_count"]
        assert new_count > initial_count
        cg.close()

    def test_delete_file_then_reindex(self, create_python_project):
        """Delete a file, then re-create it with different content."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        # Delete models.py
        cg.delete_file("models.py")
        assert cg._queries.get_nodes_by_file("models.py") == []

        # Re-create with different content
        write_file(root, "models.py", "class NewClass:\n    pass\n")
        cg.index_file("models.py")

        nodes = cg._queries.get_nodes_by_file("models.py")
        names = {n.name for n in nodes}
        assert "NewClass" in names
        assert "User" not in names  # Old class gone
        cg.close()


class TestIncrementalConsistency:
    """Verify graph consistency after incremental operations."""

    def test_stats_consistent_after_delta(self, create_python_project):
        """Stats node_count should match actual nodes."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        write_file(root, "extra.py", "def bonus(): pass\n")
        cg.apply_delta(changed_files=["extra.py"], removed_files=[])

        stats = cg.get_stats()
        all_nodes = cg.get_all_nodes(limit=100000)
        assert stats["node_count"] == len(all_nodes)
        cg.close()

    def test_no_orphan_edges_after_delete(self, create_python_project):
        """After delete_file, no edge should reference a non-existent node."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        cg.delete_file("models.py")

        all_nodes = cg.get_all_nodes(limit=100000)
        node_ids = {n.id for n in all_nodes}

        all_edges = cg.get_all_edges(limit=200000)
        for e in all_edges:
            if e.source not in node_ids or e.target not in node_ids:
                # Edge references a deleted node — this should not happen
                pytest.fail(
                    f"Orphan edge: {e.source} -> {e.target} (kind={e.kind.value})"
                )
        cg.close()

    def test_no_duplicate_nodes_after_reindex(self, create_python_project):
        """After modifying and reindexing, each symbol should appear once per file."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        write_file(
            root, "utils.py", "def format_date(ts): pass\ndef parse_config(p): pass\n"
        )
        cg.index_file("utils.py")

        nodes = cg._queries.get_nodes_by_file("utils.py")
        # Each name should appear at most once (excluding FILE node)
        non_file = [n for n in nodes if n.kind != NodeKind.FILE]
        names = [n.name for n in non_file]
        assert len(names) == len(set(names)), f"Duplicate nodes found: {names}"
        cg.close()
