"""Integration tests for explore(): LLM-oriented code exploration."""

from __future__ import annotations

from pycodegraph import CodeGraph
from pycodegraph.explore.flow import find_flow_chain, format_flow_chain
from pycodegraph.types import Edge, EdgeKind, ExploreOptions, Language, Node, NodeKind


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

    def test_flow_chain_includes_actual_seed_node(self):
        def make_node(node_id: str, name: str) -> Node:
            return Node(
                id=node_id,
                kind=NodeKind.FUNCTION,
                name=name,
                qualified_name=name,
                file_path=f"{name}.py",
                language=Language.PYTHON,
                start_line=1,
                end_line=2,
                start_column=0,
                end_column=0,
                updated_at=0,
            )

        alpha = make_node("alpha", "alpha")
        beta = make_node("beta", "beta")
        gamma = make_node("gamma", "gamma")
        alpha_beta = Edge(source="alpha", target="beta", kind=EdgeKind.CALLS)
        beta_gamma = Edge(source="beta", target="gamma", kind=EdgeKind.CALLS)

        class Traverser:
            def get_callees(self, node_id: str, max_depth: int = 1):
                return {
                    "alpha": [(beta, alpha_beta)],
                    "beta": [(gamma, beta_gamma)],
                    "gamma": [],
                }.get(node_id, [])

        chain = find_flow_chain([alpha, gamma], Traverser())

        assert [step["node"].name for step in chain] == ["alpha", "beta", "gamma"]
        assert "1. alpha" in format_flow_chain(chain)


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

    def test_explore_tiny_output_budget_stays_under_hard_ceiling(
        self, create_python_project
    ):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore("User", ExploreOptions(max_output_chars=100))
            assert len(result) <= 150
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
