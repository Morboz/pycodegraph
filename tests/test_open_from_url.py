"""Tests for CodeGraph.open_from_url() class method."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from pycodegraph import CodeGraph


class TestOpenFromUrl:
    """Tests for the open_from_url() class method."""

    def test_open_from_url_sqlite(self, tmp_path):
        """open_from_url() can open an existing SQLite DB by URL."""
        root = str(tmp_path)
        with CodeGraph.init(root) as cg:
            db_url = cg.config.db_url  # e.g. "sqlite:///.../.codegraph/codegraph.db"
            # db_url may be None when using the default resolved path
            if db_url is None:
                from pycodegraph.config import get_db_url
                db_url = get_db_url(root, cg.config)

        cg2 = CodeGraph.open_from_url(db_url)
        try:
            stats = cg2.get_stats()
            assert isinstance(stats, dict)
        finally:
            cg2.close()

    def test_open_from_url_sets_config_db_url(self, tmp_path):
        """open_from_url() stores the provided db_url in the config."""
        root = str(tmp_path)
        with CodeGraph.init(root) as cg:
            from pycodegraph.config import get_db_url
            db_url = get_db_url(root, cg.config)

        cg2 = CodeGraph.open_from_url(db_url)
        try:
            assert cg2.config.db_url == db_url
        finally:
            cg2.close()

    def test_open_from_url_project_root_default(self, tmp_path):
        """open_from_url() with no project_root uses empty string."""
        root = str(tmp_path)
        with CodeGraph.init(root) as cg:
            from pycodegraph.config import get_db_url
            db_url = get_db_url(root, cg.config)

        cg2 = CodeGraph.open_from_url(db_url)
        try:
            assert cg2.project_root == ""
        finally:
            cg2.close()

    def test_open_from_url_custom_project_root(self, tmp_path):
        """open_from_url() uses the provided project_root."""
        root = str(tmp_path)
        with CodeGraph.init(root) as cg:
            from pycodegraph.config import get_db_url
            db_url = get_db_url(root, cg.config)

        custom_root = "/some/external/path"
        cg2 = CodeGraph.open_from_url(db_url, project_root=custom_root)
        try:
            assert cg2.project_root == custom_root
        finally:
            cg2.close()

    def test_open_from_url_supports_context_manager(self, tmp_path):
        """open_from_url() result works as a context manager."""
        root = str(tmp_path)
        with CodeGraph.init(root) as cg:
            from pycodegraph.config import get_db_url
            db_url = get_db_url(root, cg.config)

        with CodeGraph.open_from_url(db_url) as cg2:
            stats = cg2.get_stats()
            assert isinstance(stats, dict)
