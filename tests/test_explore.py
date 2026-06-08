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


class TestSeedingTestFilter:
    """Test that seed_named_symbols filters out test-file candidates."""

    def _write_project(self, tmp_path, files: dict[str, str]) -> str:
        """Write a set of {relative_path: content} files under tmp_path."""
        from pathlib import Path as P

        root = str(tmp_path)
        for rel, content in files.items():
            p = P(root) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        return root

    def test_test_files_excluded_from_seeds(self, tmp_path):
        """Test-file symbols should NOT appear as seeds for a non-test query."""
        root = self._write_project(
            tmp_path,
            {
                "src/calculator.py": """\
class Calculator:
    def add(self, a, b):
        return a + b

    def compute(self, x):
        return self.add(x, 1)
""",
                "tests/test_calculator.py": """\
class TestCalculator:
    def compute(self):
        return 42

    def add(self):
        pass
""",
            },
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            from pycodegraph.explore.seeding import seed_named_symbols

            seeds = seed_named_symbols("Calculator compute", cg._searcher)
            # No seed should come from a test file
            test_seeds = [s for s in seeds if "test" in s[0].file_path.lower()]
            assert len(test_seeds) == 0, (
                f"Test-file seeds should be filtered out, got: {test_seeds}"
            )
        finally:
            cg.close()

    def test_test_files_kept_for_test_query(self, tmp_path):
        """Test-file symbols SHOULD appear when query mentions 'test'."""
        root = self._write_project(
            tmp_path,
            {
                "src/calculator.py": """\
class Calculator:
    def add(self, a, b):
        return a + b
""",
                "tests/test_calculator.py": """\
class TestCalculator:
    def test_add(self):
        pass
""",
            },
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            from pycodegraph.explore.seeding import seed_named_symbols

            seeds = seed_named_symbols("test_add test", cg._searcher)
            test_seeds = [s for s in seeds if "test" in s[0].file_path.lower()]
            assert len(test_seeds) > 0, (
                "Test-file seeds should be kept when query contains 'test'"
            )
        finally:
            cg.close()

    def test_overloaded_name_limited_to_one_fallback(self, tmp_path):
        """Overloaded names (>3 defs) without disambiguation should
        produce at most 1 seed (the most substantive non-test one)."""
        root = self._write_project(
            tmp_path,
            {
                "src/a.py": """\
def process():
    pass
""",
                "src/b.py": """\
def process():
    pass
""",
                "src/c.py": """\
def process():
    pass
""",
                "src/d.py": """\
def process():
    pass
""",
            },
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            from pycodegraph.explore.seeding import seed_named_symbols

            seeds = seed_named_symbols("process", cg._searcher)
            process_seeds = [(n, b) for n, b in seeds if n.name == "process"]
            assert len(process_seeds) <= 1, (
                f"Overloaded name should produce at most 1 seed, got {len(process_seeds)}: "
                f"{[(n.qualified_name, n.file_path) for n, _ in process_seeds]}"
            )
        finally:
            cg.close()


class TestIsTestFile:
    """Tests for is_test_file() — covers PR #37 review findings."""

    def test_no_false_positive_on_test_substring(self):
        """Filenames containing 'test' as substring should NOT be test files."""
        from pycodegraph.search.query_utils import is_test_file

        for path in [
            "src/latest.py",
            "src/contest.py",
            "src/protest.py",
            "src/attest.py",
            "src/detest.py",
            "src/intest.py",
        ]:
            assert not is_test_file(path), f"False positive: {path}"

    def test_camelcase_suffix_case_sensitive(self):
        """CamelCase suffixes (Test, Spec, etc.) must be capital-led."""
        from pycodegraph.search.query_utils import is_test_file

        # Should match — capital-led suffix
        assert is_test_file("src/UserTest.java")
        assert is_test_file("src/IntegrationSpec.scala")
        assert is_test_file("src/HandlerTester.java")
        assert is_test_file("src/BarTestCase.java")
        assert is_test_file("src/FooTests.scala")
        assert is_test_file("src/FooSpecs.scala")

    def test_testlib_and_testing_dirs(self):
        """testlib/ and testing/ directories should be detected."""
        from pycodegraph.search.query_utils import is_test_file

        assert is_test_file("testlib/helpers.py")
        assert is_test_file("testing/conftest.py")
        assert is_test_file("src/testlib/fixtures.py")
        assert is_test_file("project/testing/utils.py")

    def test_sort_prefers_non_test_when_test_query(self, tmp_path):
        """When is_test_query=True, non-test candidates should still
        sort before test candidates (production impl > test stub)."""
        root = TestSeedingTestFilter()._write_project(
            tmp_path,
            {
                "src/calculator.py": """\
class Calculator:
    def compute(self, x):
        return x + 1
""",
                "tests/test_calculator.py": """\
class TestCalculator:
    def compute(self):
        return 42
""",
            },
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            from pycodegraph.explore.seeding import seed_named_symbols

            seeds = seed_named_symbols("compute test", cg._searcher)
            # Both should appear (test query), but production impl should
            # come first in the seed list
            seed_files = [s[0].file_path for s in seeds if s[0].name == "compute"]
            if len(seed_files) >= 2:
                assert "test" not in seed_files[0].lower(), (
                    f"Production 'compute' should sort before test version, "
                    f"got order: {seed_files}"
                )
        finally:
            cg.close()
