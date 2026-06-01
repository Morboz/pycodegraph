"""Integration tests for CodeGraph lifecycle: init, open, close, context manager."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pycodegraph import CodeGraph
from pycodegraph.config import CODEGRAPH_DIR, get_db_url


class TestInit:
    """CodeGraph.init() creates the expected on-disk layout."""

    def test_init_creates_codegraph_dir(self, tmp_path):
        root = str(tmp_path)
        CodeGraph.init(root)
        assert (Path(root) / CODEGRAPH_DIR).is_dir()

    def test_init_creates_config_json(self, tmp_path):
        root = str(tmp_path)
        CodeGraph.init(root)
        config_path = Path(root) / CODEGRAPH_DIR / "config.json"
        assert config_path.exists()
        data = json.loads(config_path.read_text())
        assert data["version"] == 1

    def test_init_creates_sqlite_db(self, tmp_path):
        root = str(tmp_path)
        CodeGraph.init(root)
        db_path = Path(root) / CODEGRAPH_DIR / "codegraph.db"
        assert db_path.exists()

    def test_init_creates_gitignore(self, tmp_path):
        root = str(tmp_path)
        CodeGraph.init(root)
        gi = Path(root) / CODEGRAPH_DIR / ".gitignore"
        assert gi.exists()
        assert "*.db" in gi.read_text()

    def test_init_raises_on_reinit(self, tmp_path):
        root = str(tmp_path)
        CodeGraph.init(root)
        with pytest.raises(FileExistsError, match="already initialized"):
            CodeGraph.init(root)

    def test_init_with_config_overrides(self, tmp_path):
        root = str(tmp_path)
        cg = CodeGraph.init(root, config_overrides={"max_file_size": 512})
        assert cg.config.max_file_size == 512
        cg.close()

    def test_init_project_root_is_resolved(self, tmp_path):
        root = str(tmp_path)
        cg = CodeGraph.init(root)
        assert cg.project_root == str(Path(root).resolve())
        cg.close()


class TestOpen:
    """CodeGraph.open() reopens an existing project."""

    def test_open_after_init(self, tmp_path):
        root = str(tmp_path)
        cg_init = CodeGraph.init(root)
        project_root = cg_init.project_root
        cg_init.close()

        cg_open = CodeGraph.open(root)
        assert cg_open.project_root == project_root
        cg_open.close()

    def test_open_fails_without_init(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            CodeGraph.open(str(tmp_path))

    def test_open_preserves_indexed_data(self, create_python_project, tmp_path):
        root = create_python_project()
        cg_init = CodeGraph.init(root)
        result = cg_init.index_all()
        assert result.nodes_created > 0
        node_count = cg_init.get_stats()["node_count"]
        cg_init.close()

        cg_open = CodeGraph.open(root)
        assert cg_open.get_stats()["node_count"] == node_count
        cg_open.close()

    def test_open_reads_config(self, tmp_path):
        root = str(tmp_path)
        cg = CodeGraph.init(root, config_overrides={"max_file_size": 2048})
        cg.close()

        cg2 = CodeGraph.open(root)
        assert cg2.config.max_file_size == 2048
        cg2.close()


class TestClose:
    """CodeGraph.close() is safe to call multiple times."""

    def test_close_is_idempotent(self, tmp_path):
        root = str(tmp_path)
        cg = CodeGraph.init(root)
        cg.close()
        cg.close()  # Should not raise

    def test_operations_after_close_raise(self, tmp_path):
        root = str(tmp_path)
        cg = CodeGraph.init(root)
        cg.close()
        with pytest.raises(Exception):  # noqa: B017
            cg.get_stats()


class TestContextManager:
    """CodeGraph works as a context manager."""

    def test_init_as_context_manager(self, tmp_path):
        root = str(tmp_path)
        with CodeGraph.init(root) as cg:
            stats = cg.get_stats()
            assert isinstance(stats, dict)

    def test_context_manager_closes_on_exit(self, tmp_path):
        root = str(tmp_path)
        cg = CodeGraph.init(root)
        cg.__enter__()
        cg.__exit__(None, None, None)
        with pytest.raises(Exception):  # noqa: B017
            cg.get_stats()

    def test_open_as_context_manager(self, create_python_project):
        root = create_python_project()
        CodeGraph.init(root).close()

        with CodeGraph.open(root) as cg:
            stats = cg.get_stats()
            assert isinstance(stats, dict)

    def test_context_manager_closes_on_exception(self, tmp_path):
        root = str(tmp_path)
        cg_ref = None
        with pytest.raises(ValueError, match="boom"), CodeGraph.init(root) as cg:
            cg_ref = cg
            raise ValueError("boom")
        # After the with block, operations should fail (connection closed)
        with pytest.raises(Exception):  # noqa: B017
            cg_ref.get_stats()


class TestOpenFromUrl:
    """CodeGraph.open_from_url() connects by explicit URL."""

    def test_open_from_url_sqlite(self, tmp_path):
        root = str(tmp_path)
        with CodeGraph.init(root) as cg:
            db_url = cg.config.db_url
            if db_url is None:
                db_url = get_db_url(root, cg.config)

        cg2 = CodeGraph.open_from_url(db_url)
        try:
            stats = cg2.get_stats()
            assert isinstance(stats, dict)
        finally:
            cg2.close()

    def test_open_from_url_missing_db_raises(self):
        with pytest.raises(FileNotFoundError):
            CodeGraph.open_from_url("sqlite:///nonexistent_path/codegraph.db")

    def test_open_from_url_project_root(self, tmp_path):
        root = str(tmp_path)
        with CodeGraph.init(root) as cg:
            db_url = cg.config.db_url or get_db_url(root, cg.config)

        custom_root = "/some/external/path"
        cg2 = CodeGraph.open_from_url(db_url, project_root=custom_root)
        try:
            assert cg2.project_root == custom_root
        finally:
            cg2.close()
