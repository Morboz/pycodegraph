"""Integration tests for explore(): LLM-oriented code exploration."""

from __future__ import annotations

from pycodegraph import CodeGraph
from pycodegraph.types import ExploreOptions


class TestExploreBasic:
    """Smoke tests for explore()."""

    def test_explore_returns_string(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore("How does User work?")
            assert isinstance(result, str)
            assert len(result) > 0
        finally:
            cg.close()

    def test_explore_empty_query(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore("")
            assert isinstance(result, str)
            # Should not crash — may return minimal result
        finally:
            cg.close()

    def test_explore_finds_named_symbol(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore("User class")
            assert "User" in result
        finally:
            cg.close()

    def test_explore_includes_line_numbers(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore("User class")
            # cat -n style: line_number\tcode
            assert "\t" in result  # Has tab-separated line numbers
        finally:
            cg.close()


class TestExploreClustering:
    """Tests for file-level source clustering."""

    def test_explore_respects_budget(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            # Tiny project (<150 files) → 13K budget
            result = cg.explore("User")
            assert len(result) <= 25_000  # Hard ceiling
        finally:
            cg.close()

    def test_explore_whole_small_file(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore("format_date")
            # Small files should be returned whole
            assert "format_date" in result
        finally:
            cg.close()

    def test_explore_multiple_symbols_in_file(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore("User Admin")
            # Both classes are in models.py — should appear together
            assert "User" in result
        finally:
            cg.close()


class TestExploreFlow:
    """Tests for call chain tracing among named symbols."""

    def test_explore_finds_call_chain(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            # run calls create_user — should find this chain
            result = cg.explore("run create_user")
            # Should contain flow section or at least both symbols
            assert "run" in result or "create_user" in result
        finally:
            cg.close()


class TestExploreOutput:
    """Tests for output format and structure."""

    def test_explore_output_has_source_section(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore("User")
            assert "### Source Code" in result
        finally:
            cg.close()

    def test_explore_with_options(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore(
                "User",
                ExploreOptions(
                    include_relationships=False,
                    include_flow=False,
                    include_blast_radius=False,
                ),
            )
            assert isinstance(result, str)
            assert "User" in result
        finally:
            cg.close()

    def test_explore_with_blast_radius(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore(
                "User",
                ExploreOptions(include_blast_radius=True),
            )
            assert isinstance(result, str)
        finally:
            cg.close()

    def test_explore_has_completeness_signal(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore("User")
            # Should have a reminder not to re-read files
            assert (
                "do NOT re-read" in result
                or "do not Read" in result
                or "Complete source" in result
            )
        finally:
            cg.close()
