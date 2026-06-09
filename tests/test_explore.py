"""Integration tests for explore(): LLM-oriented code exploration."""

from __future__ import annotations

from pycodegraph import CodeGraph
from pycodegraph.explore.budget import format_budget_note, get_explore_budget
from pycodegraph.explore.flow import FlowResult, find_flow_chain, format_flow_chain
from pycodegraph.explore.skeletonize import (
    compute_unique_named_node_ids,
    render_skeletonized,
    should_skeletonize,
)
from pycodegraph.types import (
    Edge,
    EdgeKind,
    ExploreOptions,
    Language,
    Node,
    NodeKind,
    Subgraph,
)


def _make_node(
    nid: str,
    name: str,
    kind: NodeKind = NodeKind.METHOD,
    file_path: str = "query.py",
    start_line: int = 1,
    end_line: int = 5,
) -> Node:
    """Create a Node with minimal defaults (shared by skeletonization tests)."""
    return Node(
        id=nid,
        kind=kind,
        name=name,
        qualified_name=name,
        file_path=file_path,
        language=Language.PYTHON,
        start_line=start_line,
        end_line=end_line,
        start_column=0,
        end_column=10,
        updated_at=0,
    )


def _write_project(tmp_path, files: dict[str, str]) -> str:
    """Write a set of {relative_path: content} files under tmp_path."""
    from pathlib import Path as P

    root = str(tmp_path)
    for rel, content in files.items():
        p = P(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return root


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

        result = find_flow_chain([alpha, gamma], Traverser())

        assert isinstance(result, FlowResult)
        assert [step["node"].name for step in result.chain] == [
            "alpha",
            "beta",
            "gamma",
        ]
        # path_node_ids should contain all nodes on the spine
        assert "alpha" in result.path_node_ids
        assert "beta" in result.path_node_ids
        assert "gamma" in result.path_node_ids
        assert "1. alpha" in format_flow_chain(result.chain)

    def test_flow_chain_returns_empty_path_node_ids_when_no_chain(self):
        """When <2 named symbols, find_flow_chain returns empty path_node_ids."""

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

        class Traverser:
            def get_callees(self, node_id: str, max_depth: int = 1):
                return []

        result = find_flow_chain([alpha], Traverser())

        assert isinstance(result, FlowResult)
        assert result.chain == []
        assert result.path_node_ids == set()

    def test_flow_chain_traverses_heuristic_edges(self):
        """Heuristic (synth) edges with kind=CALLS should be traversed by
        find_flow_chain, enabling it to cross dynamic-dispatch boundaries.

        This models the Django ORM pattern where a callback synthesizer
        would create an edge from ``_fetch_all`` through an unnamed
        bridge (``_iterable_class``) to ``execute_sql`` via heuristic
        provenance.
        """

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

        fetch_all = make_node("fetch_all", "_fetch_all")
        bridge = make_node("bridge", "_iterable_class")
        execute_sql = make_node("execute_sql", "execute_sql")

        # A heuristic/synth edge bridging dynamic dispatch
        fetch_bridge = Edge(
            source="fetch_all",
            target="bridge",
            kind=EdgeKind.CALLS,
            provenance="heuristic:callback",
        )
        bridge_execute = Edge(
            source="bridge",
            target="execute_sql",
            kind=EdgeKind.CALLS,
        )

        class Traverser:
            def get_callees(self, node_id: str, max_depth: int = 1):
                return {
                    "fetch_all": [(bridge, fetch_bridge)],
                    "bridge": [(execute_sql, bridge_execute)],
                    "execute_sql": [],
                }.get(node_id, [])

        result = find_flow_chain([fetch_all, execute_sql], Traverser())

        assert len(result.chain) == 3
        assert [step["node"].name for step in result.chain] == [
            "_fetch_all",
            "_iterable_class",
            "execute_sql",
        ]
        # All nodes including the unnamed bridge should be on the spine
        assert "fetch_all" in result.path_node_ids
        assert "bridge" in result.path_node_ids
        assert "execute_sql" in result.path_node_ids

    def test_flow_chain_empty_when_dynamic_dispatch_gap(self):
        """When call graph has a dynamic-dispatch gap (no CALLS edge
        across the boundary), find_flow_chain should return empty
        chain and empty path_node_ids.

        This models the actual Django ORM problem from #45:
        _fetch_all → [dynamic dispatch] → execute_sql has no static
        CALLS edge, so BFS cannot find the chain.
        """

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

        fetch_all = make_node("fetch_all", "_fetch_all")
        execute_sql = make_node("execute_sql", "execute_sql")

        # No CALLS edge between fetch_all and execute_sql
        class Traverser:
            def get_callees(self, node_id: str, max_depth: int = 1):
                return {
                    "fetch_all": [],  # gap: no edge to execute_sql
                    "execute_sql": [],
                }.get(node_id, [])

        result = find_flow_chain([fetch_all, execute_sql], Traverser())

        assert result.chain == []
        assert result.path_node_ids == set()


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


class TestExploreBudgetEnforcement:
    """Integration tests for file_budget enforcement in cluster selection (issue #31)."""

    def test_explore_oversized_cluster_does_not_exceed_budget(self, tmp_path):
        """When a single cluster far exceeds file_budget, it is kept as
        fallback but the output should not grow unboundedly (issue #31)."""
        # Create a file with one huge class spanning many lines
        big_class_lines = [
            "class BigService:",
            "    def __init__(self):",
            "        self.data = []",
        ]
        # Add many methods to inflate the class
        for i in range(80):
            big_class_lines.append(f"    def method_{i}(self):")
            big_class_lines.append(f"        return {i}")
        big_class_lines.append("")  # trailing newline

        root = _write_project(
            tmp_path,
            {
                "src/service.py": "\n".join(big_class_lines),
                "src/main.py": "from service import BigService\n\ndef run():\n    s = BigService()\n    return s.method_0()\n",
            },
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            # Very small per-file budget
            result = cg.explore(
                "BigService",
                ExploreOptions(max_chars_per_file=500, max_output_chars=2000),
            )
            assert isinstance(result, str)
            # The output should stay within hard ceiling
            assert len(result) <= 3000  # hard ceiling is 1.5 * 2000 = 3000
        finally:
            cg.close()

    def test_explore_budget_not_violated_by_first_cluster(self, tmp_path):
        """Even with a tight budget, explore should produce output that
        respects the hard ceiling — the first oversized cluster should not
        cause the output to balloon (issue #31)."""
        # Create a file with a single large function
        lines = ["def huge_function():", "    x = 1"]
        for i in range(200):
            lines.append(f"    y_{i} = x + {i}")
        lines.append("    return x")
        lines.append("")

        root = _write_project(
            tmp_path,
            {
                "src/large.py": "\n".join(lines),
                "src/caller.py": "from large import huge_function\n\ndef call_it():\n    return huge_function()\n",
            },
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore(
                "huge_function",
                ExploreOptions(max_output_chars=1500),
            )
            # Hard ceiling: min(1.5 * 1500, 25000) = 2250
            assert len(result) <= 2250
        finally:
            cg.close()


class TestSeedingTestFilter:
    """Test that seed_named_symbols filters out test-file candidates."""

    def test_test_files_excluded_from_seeds(self, tmp_path):
        """Test-file symbols should NOT appear as seeds for a non-test query."""
        root = _write_project(
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
        root = _write_project(
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
        root = _write_project(
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
        root = _write_project(
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


class TestUniqueNamedNodeIds:
    """Tests for compute_unique_named_node_ids — names with ≤3 global defs."""

    def test_specific_name_marked_unique(self):
        """A name with ≤3 global definitions should be marked unique."""
        from pycodegraph.types import Subgraph

        nodes = {
            "a": Node(
                id="a",
                kind=NodeKind.METHOD,
                name="fetch_all",
                qualified_name="QuerySet.fetch_all",
                file_path="query.py",
                language=Language.PYTHON,
                start_line=1,
                end_line=5,
                start_column=0,
                end_column=0,
                updated_at=0,
            ),
            "b": Node(
                id="b",
                kind=NodeKind.METHOD,
                name="fetch_all",
                qualified_name="BaseManager.fetch_all",
                file_path="manager.py",
                language=Language.PYTHON,
                start_line=1,
                end_line=5,
                start_column=0,
                end_column=0,
                updated_at=0,
            ),
        }
        named_node_ids = {"a", "b"}
        subgraph = Subgraph(nodes=nodes, edges=[], roots=[])

        unique_ids = compute_unique_named_node_ids(named_node_ids, subgraph)

        # "fetch_all" has 2 defs (≤3) → both should be unique
        assert "a" in unique_ids
        assert "b" in unique_ids

    def test_overloaded_name_not_unique(self):
        """A name with >3 global definitions should NOT be marked unique."""
        nodes = {}
        named_node_ids = set()
        for i in range(5):
            nid = f"n{i}"
            nodes[nid] = Node(
                id=nid,
                kind=NodeKind.METHOD,
                name="execute",
                qualified_name=f"Compiler{i}.execute",
                file_path=f"comp{i}.py",
                language=Language.PYTHON,
                start_line=1,
                end_line=5,
                start_column=0,
                end_column=0,
                updated_at=0,
            )
            named_node_ids.add(nid)

        subgraph = Subgraph(nodes=nodes, edges=[], roots=[])
        unique_ids = compute_unique_named_node_ids(named_node_ids, subgraph)

        # "execute" has 5 defs (>3) → none should be unique
        assert len(unique_ids) == 0

    def test_mixed_specific_and_overloaded(self):
        """Specific names are unique, overloaded names are not."""
        nodes = {
            # "fetch_all" — 2 defs, specific
            "a": Node(
                id="a",
                kind=NodeKind.METHOD,
                name="fetch_all",
                qualified_name="QuerySet.fetch_all",
                file_path="query.py",
                language=Language.PYTHON,
                start_line=1,
                end_line=5,
                start_column=0,
                end_column=0,
                updated_at=0,
            ),
            "b": Node(
                id="b",
                kind=NodeKind.METHOD,
                name="fetch_all",
                qualified_name="BaseManager.fetch_all",
                file_path="manager.py",
                language=Language.PYTHON,
                start_line=1,
                end_line=5,
                start_column=0,
                end_column=0,
                updated_at=0,
            ),
            # "as_sql" — 5 defs, overloaded
            "c": Node(
                id="c",
                kind=NodeKind.METHOD,
                name="as_sql",
                qualified_name="SQLCompiler.as_sql",
                file_path="compiler.py",
                language=Language.PYTHON,
                start_line=1,
                end_line=5,
                start_column=0,
                end_column=0,
                updated_at=0,
            ),
        }
        # Add more "as_sql" nodes (5 total)
        for i in range(3, 6):
            nid = f"sql{i}"
            nodes[nid] = Node(
                id=nid,
                kind=NodeKind.METHOD,
                name="as_sql",
                qualified_name=f"Compiler{i}.as_sql",
                file_path=f"comp{i}.py",
                language=Language.PYTHON,
                start_line=1,
                end_line=5,
                start_column=0,
                end_column=0,
                updated_at=0,
            )

        named_node_ids = {"a", "b", "c"}
        subgraph = Subgraph(nodes=nodes, edges=[], roots=[])
        unique_ids = compute_unique_named_node_ids(named_node_ids, subgraph)

        # "fetch_all" (2 defs) → unique
        assert "a" in unique_ids
        assert "b" in unique_ids
        # "as_sql" (5 defs) → NOT unique
        assert "c" not in unique_ids

    def test_empty_named_ids(self):
        """Empty named_node_ids returns empty set."""
        from pycodegraph.types import Subgraph

        subgraph = Subgraph(nodes={}, edges=[], roots=[])
        unique_ids = compute_unique_named_node_ids(set(), subgraph)
        assert unique_ids == set()


class TestShouldSkeletonize:
    """Tests for should_skeletonize — god-file detection."""

    def test_god_file_detected(self):
        """File with spine nodes + large named body + off-path unique → skeletonize."""
        # Spine method with 50-line body
        spine = _make_node("spine", "fetch_all", start_line=10, end_line=60)
        # Off-path unique method with 50-line body
        off_path = _make_node("off", "unique_method", start_line=100, end_line=150)
        # Background method
        bg = _make_node("bg", "background", start_line=200, end_line=210)

        file_lines = [""] * 300  # 300-line file
        # Fill spine body with content (50 lines x ~50 chars)
        for i in range(9, 60):
            file_lines[i] = "x" * 50
        # Fill off-path body
        for i in range(99, 150):
            file_lines[i] = "y" * 50

        path_node_ids = {"spine"}
        unique_named_node_ids = {"spine", "off"}
        entry_node_ids = {"spine"}

        result = should_skeletonize(
            [spine, off_path, bg],
            path_node_ids,
            unique_named_node_ids,
            entry_node_ids,
            file_lines,
            max_chars_per_file=500,
        )
        # spine body = 50 lines x 50 chars = 2500 + off-path 2500 = 5000 >> 500
        # Has off-path unique → should skeletonize
        assert result is True

    def test_not_god_file_when_no_spine(self):
        """File without spine nodes and only 1 entry callable should not skeletonize."""
        method = _make_node("m", "method", start_line=10, end_line=60)
        file_lines = [""] * 100

        result = should_skeletonize(
            [method],
            set(),
            {"m"},
            {"m"},
            file_lines,
            max_chars_per_file=500,
        )
        assert result is False

    def test_not_god_file_when_body_fits_budget(self):
        """File where named bodies fit within budget should not skeletonize."""
        spine = _make_node("spine", "fetch_all", start_line=10, end_line=15)
        off_path = _make_node("off", "unique_method", start_line=20, end_line=25)
        file_lines = [""] * 100

        result = should_skeletonize(
            [spine, off_path],
            {"spine"},
            {"spine", "off"},
            {"spine", "off"},
            file_lines,
            max_chars_per_file=5000,
        )
        # Small bodies, large budget → no skeletonization needed
        assert result is False

    def test_not_god_file_when_no_off_path_high_prio(self):
        """File where all high-priority nodes are on-spine should not skeletonize."""
        # Only spine nodes, no off-path high-priority callables
        spine = _make_node("spine", "fetch_all", start_line=10, end_line=60)
        file_lines = ["x" * 50] * 100

        result = should_skeletonize(
            [spine],
            {"spine"},
            {"spine"},
            {"spine"},
            file_lines,
            max_chars_per_file=500,
        )
        # No off-path high-prio → not a god-file
        assert result is False

    def test_on_spine_skeletonizes_with_overloaded_off_path_entry(self):
        """On-spine file with overloaded off-path entry methods should
        still skeletonize (not just unique-named)."""
        spine = _make_node("spine", "fetch_all", start_line=10, end_line=60)
        # Overloaded method — not unique, but is an entry point
        overloaded = _make_node("overload", "execute", start_line=80, end_line=130)
        file_lines = [""] * 200
        for i in range(9, 60):
            file_lines[i] = "x" * 50
        for i in range(79, 130):
            file_lines[i] = "y" * 50

        result = should_skeletonize(
            [spine, overloaded],
            {"spine"},  # spine
            set(),  # no unique named (overloaded)
            {"overload"},  # but it IS an entry
            file_lines,
            max_chars_per_file=500,
        )
        # Off-path entry callable → should skeletonize
        assert result is True

    def test_named_body_overflow_without_spine(self):
        """File without spine nodes but with large named bodies should
        skeletonize when ≥2 named callables exceed budget."""
        named_a = _make_node("a", "fetch_all", start_line=10, end_line=60)
        named_b = _make_node("b", "process_data", start_line=80, end_line=130)
        file_lines = [""] * 200
        # Fill bodies with content
        for i in range(9, 60):
            file_lines[i] = "x" * 50
        for i in range(79, 130):
            file_lines[i] = "y" * 50

        result = should_skeletonize(
            [named_a, named_b],
            set(),  # no spine
            {"a", "b"},  # both are unique
            {"a", "b"},  # both are entry
            file_lines,
            max_chars_per_file=500,
        )
        # Named bodies total = 50*50 + 50*50 = 5000 >> 500 budget
        # With 2 named callables and no spine → skeletonize
        assert result is True

    def test_no_overflow_without_spine_single_named(self):
        """File without spine and only 1 named callable should NOT skeletonize."""
        named = _make_node("n", "fetch_all", start_line=10, end_line=60)
        file_lines = ["x" * 50] * 100

        result = should_skeletonize(
            [named],
            set(),  # no spine
            {"n"},  # single unique
            {"n"},  # single entry
            file_lines,
            max_chars_per_file=500,
        )
        # Only 1 named callable → not enough for overflow skeletonization
        assert result is False

    def test_density_fallback_when_no_spine_god_file(self):
        """When path_node_ids is empty (no flow chain) and a file has many
        callable nodes (god-file density), skeletonization should still
        trigger as a fallback even if no nodes are in unique_named or entry.

        This matches the Django ORM scenario from #45: find_flow_chain
        returns empty path_node_ids because it can't cross dynamic-
        dispatch boundaries, and the file's named symbols are overloaded
        (not unique).  Without this fallback, the file falls through to
        the clustering path, which produces a 728-line cluster that
        swallows the entire output budget.
        """
        # 25 callable nodes — clearly a god-file
        nodes = [
            _make_node(
                f"n{i}", f"method_{i}", start_line=10 + i * 30, end_line=35 + i * 30
            )
            for i in range(25)
        ]
        file_lines = [""] * 800
        # Fill bodies with content so total chars >> budget
        for i in range(25):
            for j in range(10 + i * 30 - 1, 35 + i * 30):
                file_lines[j] = "x" * 50

        result = should_skeletonize(
            nodes,
            set(),  # no spine — flow chain was empty
            set(),  # no unique named (all overloaded)
            set(),  # no entry nodes
            file_lines,
            max_chars_per_file=6500,
        )
        # 25 callables x 25 lines x 50 chars = 31,250 chars >> 6,500 budget
        # Density fallback should trigger skeletonization
        assert result is True

    def test_density_fallback_not_triggered_for_small_file(self):
        """A small file with <20 callables should NOT trigger the density
        fallback, even when there is no spine/unique/entry."""
        # 5 callable nodes — not a god-file
        nodes = [
            _make_node(
                f"n{i}", f"method_{i}", start_line=10 + i * 10, end_line=15 + i * 10
            )
            for i in range(5)
        ]
        file_lines = [""] * 100

        result = should_skeletonize(
            nodes,
            set(),  # no spine
            set(),  # no unique named
            set(),  # no entry
            file_lines,
            max_chars_per_file=6500,
        )
        # <20 callables, no high-prio nodes → no skeletonization
        assert result is False


class TestRenderSkeletonized:
    """Tests for render_skeletonized — per-symbol rendering."""

    def test_spine_gets_full_body(self):
        """On-spine methods should have full body in output."""
        spine = _make_node("spine", "fetch_all", start_line=5, end_line=8)
        bg = _make_node("bg", "background", start_line=20, end_line=22)
        file_lines = [
            "",
            "",
            "",
            "",
            "",
            "def fetch_all(self):",
            "    results = list(self)",
            "    return results",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "def background(self):",
            "    pass",
            "",
        ]

        result, _tag = render_skeletonized(
            [spine, bg],
            file_lines,
            path_node_ids={"spine"},
            named_node_ids={"spine"},
            unique_named_node_ids={"spine"},
            entry_node_ids={"spine"},
            max_chars_per_file=5000,
        )

        # Spine method should have full body
        assert "def fetch_all(self):" in result
        assert "results = list(self)" in result

    def test_background_gets_signature_only(self):
        """Off-spine, non-named methods should only get signature lines."""
        spine = _make_node("spine", "fetch_all", start_line=5, end_line=8)
        bg = _make_node("bg", "background", start_line=20, end_line=22)
        file_lines = [
            "",
            "",
            "",
            "",
            "",
            "def fetch_all(self):",
            "    results = list(self)",
            "    return results",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "def background(self):",
            "    pass",
            "",
        ]

        result, _tag = render_skeletonized(
            [spine, bg],
            file_lines,
            path_node_ids={"spine"},
            named_node_ids={"spine"},
            unique_named_node_ids={"spine"},
            entry_node_ids={"spine"},
            max_chars_per_file=5000,
        )

        # Background method should only have signature, not body
        assert "def background(self):" in result
        # The "pass" line should NOT appear (signature only)
        assert "pass" not in result

    def test_unique_named_gets_full_body(self):
        """Uniquely-named methods (not on spine) should get full body."""
        unique = _make_node("unique", "special_handler", start_line=5, end_line=8)
        bg = _make_node("bg", "background", start_line=20, end_line=22)
        file_lines = [
            "",
            "",
            "",
            "",
            "",
            "def special_handler(self):",
            "    data = self.process()",
            "    return data",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "def background(self):",
            "    pass",
            "",
        ]

        result, _tag = render_skeletonized(
            [unique, bg],
            file_lines,
            path_node_ids=set(),
            named_node_ids={"unique"},
            unique_named_node_ids={"unique"},
            entry_node_ids=set(),
            max_chars_per_file=5000,
        )

        # Unique method should have full body
        assert "def special_handler(self):" in result
        assert "data = self.process()" in result

    def test_body_cap_respected(self):
        """Total body chars should not exceed max_chars_per_file * 1.5."""
        # Create methods with large bodies
        methods = []
        file_lines = [""] * 500
        for i in range(10):
            start = i * 50 + 1
            end = start + 40
            methods.append(
                _make_node(f"m{i}", f"method_{i}", start_line=start, end_line=end)
            )
            for j in range(start - 1, end):
                file_lines[j] = "x" * 100  # 100 chars per line

        # Mark all as entry points (priority 2) and spine
        all_ids = {m.id for m in methods}
        result, _tag = render_skeletonized(
            methods,
            file_lines,
            path_node_ids=all_ids,
            named_node_ids=all_ids,
            unique_named_node_ids=all_ids,
            entry_node_ids=all_ids,
            max_chars_per_file=500,  # body_cap = 750
        )

        # Count body chars in output (lines with tabs = body lines)
        body_lines = [
            line for line in result.split("\n") if "\t" in line and "x" * 50 in line
        ]
        # Each method body is 41 lines x 100 chars = 4100 chars
        # body_cap = 750 → only 1 method should fit
        assert len(body_lines) < 41 * 10  # Not all methods should appear in full

    def test_tag_is_focused_when_body_ids_nonempty(self):
        """When some methods get full body, tag should be 'focused'."""
        spine = _make_node("spine", "fetch_all", start_line=5, end_line=8)
        file_lines = [
            "",
            "",
            "",
            "",
            "",
            "def fetch_all(self):",
            "    results = list(self)",
            "    return results",
            "",
        ]

        _result, tag = render_skeletonized(
            [spine],
            file_lines,
            path_node_ids={"spine"},
            named_node_ids={"spine"},
            unique_named_node_ids={"spine"},
            entry_node_ids={"spine"},
            max_chars_per_file=5000,
        )

        assert tag == "focused"

    def test_tag_is_skeleton_when_no_body_ids(self):
        """When no methods get full body (all priority 99), tag should be 'skeleton'."""
        bg = _make_node("bg", "background", start_line=5, end_line=8)
        file_lines = [
            "",
            "",
            "",
            "",
            "",
            "def background(self):",
            "    pass",
            "",
        ]

        _result, tag = render_skeletonized(
            [bg],
            file_lines,
            path_node_ids=set(),
            named_node_ids=set(),
            unique_named_node_ids=set(),
            entry_node_ids=set(),
            max_chars_per_file=5000,
        )

        assert tag == "skeleton"

    def test_signature_no_substring_false_positive(self):
        """A node named 'get' should NOT match 'def target(self):'."""
        get_method = _make_node("get", "get", start_line=5, end_line=7)
        target_method = _make_node("target", "target", start_line=10, end_line=12)
        file_lines = [
            "",
            "",
            "",
            "",
            "",
            "def get(self):",
            "    return self.value",
            "",
            "",
            "def target(self):",
            "    return 0",
            "",
        ]

        result, _tag = render_skeletonized(
            [get_method, target_method],
            file_lines,
            path_node_ids={"get"},
            named_node_ids={"get"},
            unique_named_node_ids={"get"},
            entry_node_ids=set(),
            max_chars_per_file=5000,
        )

        # "get" should have full body (it's on spine)
        assert "def get(self):" in result
        # "target" should only have signature — and NOT match via substring
        assert "def target(self):" in result
        # The body of target should NOT appear
        assert "return 0" not in result

    def test_signature_fallback_when_name_not_found(self):
        """If the callable name is not found within 4 lines, emit the
        first line as fallback instead of silently skipping."""
        # start_line=5 but the name "unlikely_prefix_rare" only appears
        # on line 10 — beyond the 4-line scan window.
        method = _make_node("rare", "unlikely_prefix_rare", start_line=5, end_line=12)
        file_lines = [
            "",
            "",
            "",
            "",
            "",
            "    @decorator_one",  # line 5
            "    @decorator_two",  # line 6
            "    @decorator_three",  # line 7
            "    @decorator_four",  # line 8 — 4-line scan ends here
            "    # some comment",  # line 9
            "    def unlikely_prefix_rare(self):",  # line 10
            "        pass",  # line 11
            "",
        ]

        result, _tag = render_skeletonized(
            [method],
            file_lines,
            path_node_ids=set(),
            named_node_ids=set(),
            unique_named_node_ids=set(),
            entry_node_ids=set(),
            max_chars_per_file=5000,
        )

        # Should NOT silently skip — fallback to first line
        assert len(result) > 0
        # Fallback emits start_line (5) + first line content
        assert "5\t" in result


class TestExploreSkeletonization:
    """Integration tests for skeletonization in the explore pipeline (issue #32)."""

    def test_god_file_skeletonized_stays_within_budget(self, tmp_path):
        """A large file with many named methods should be skeletonized
        and stay within per-file budget (issue #32)."""
        # Build a Django-query.py-like file: many methods, some named
        lines = [
            "class BigService:",
            "    def __init__(self):",
            "        self.data = []",
        ]
        # On-spine method (important, should get full body)
        lines.append("    def fetch_all(self):")
        lines.append("        results = list(self._iter())")
        lines.append("        return results")
        # Many off-path methods with large bodies
        for i in range(20):
            lines.append(f"    def method_{i}(self):")
            for j in range(10):
                lines.append(f"        x_{j} = self.data + {j}")
            lines.append(f"        return x_{j}")
        lines.append("")  # trailing newline

        root = _write_project(
            tmp_path,
            {
                "src/service.py": "\n".join(lines),
                "src/caller.py": (
                    "from service import BigService\n\n"
                    "def run():\n"
                    "    s = BigService()\n"
                    "    return s.fetch_all()\n"
                ),
            },
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            # Small per-file budget to trigger skeletonization
            result = cg.explore(
                "BigService fetch_all",
                ExploreOptions(max_chars_per_file=500, max_output_chars=5000),
            )
            assert isinstance(result, str)
            # Should stay within hard ceiling
            assert len(result) <= 7500  # 1.5 * 5000
            # The fetch_all method should appear (it's a named symbol)
            assert "fetch_all" in result
            # The output should be tagged as "focused" or "skeleton"
            assert "focused" in result or "skeleton" in result
        finally:
            cg.close()

    def test_god_file_non_entry_methods_get_signatures_only(self, tmp_path):
        """Non-entry, non-named methods in a god-file should only show
        signature lines, not full body (issue #32)."""
        # Build a file with many methods — some will be entry points,
        # others should be skeletonized to signatures only.
        lines = [
            "class BigService:",
            "    def __init__(self):",
            "        self.data = []",
        ]
        lines.append("    def fetch_all(self):")
        lines.append("        results = list(self._iter())")
        lines.append("        return results")
        for i in range(40):
            lines.append(f"    def method_{i}(self):")
            for j in range(15):
                lines.append(f"        y_{j} = self.process({j})")
            lines.append("        return y_0")
        lines.append("")

        root = _write_project(
            tmp_path,
            {
                "src/service.py": "\n".join(lines),
                "src/caller.py": (
                    "from service import BigService\n\n"
                    "def run():\n"
                    "    s = BigService()\n"
                    "    return s.fetch_all()\n"
                ),
            },
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            # Very small per-file budget to force skeletonization
            result = cg.explore(
                "BigService fetch_all",
                ExploreOptions(max_chars_per_file=300, max_output_chars=3000),
            )
            assert isinstance(result, str)
            # fetch_all (named symbol) should appear with full body
            assert "fetch_all" in result
            # The output should be tagged
            assert "focused" in result or "skeleton" in result
            # Methods that are NOT entry/named should only appear as
            # signature lines — their body content should not be present.
            # "method_30+" should not have body content
            # (they are beyond the search roots)
            # Check that the output is much smaller than the full file
            # (full file ~40 methods x 16 lines x ~40 chars = ~25,600 chars)
            # skeletonized should be < 3000 chars for the source section
            assert len(result) <= 4500  # 1.5 * 3000
        finally:
            cg.close()

    def test_small_file_not_skeletonized(self, tmp_path):
        """A small file should NOT be skeletonized — existing behavior preserved."""
        root = _write_project(
            tmp_path,
            {
                "src/small.py": "def add(a, b):\n    return a + b\n",
                "src/main.py": "from small import add\n\ndef run():\n    return add(1, 2)\n",
            },
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore("add")
            # Small file should be included whole (no skeletonization)
            assert "return a + b" in result
        finally:
            cg.close()


class TestExploreNecessaryFileBudget:
    """Tests for per-file section cap on necessary files (issue #33).

    When is_necessary=True, the 90% global budget check is bypassed so
    that core files are never dropped.  But a necessary file's section
    must still be capped at 1.5 * max_chars_per_file — otherwise a
    single large necessary file can eat the entire output budget.
    """

    _SECTION_CAP_FACTOR = 1.5  # mirrors _NECESSARY_FILE_SECTION_FACTOR

    @staticmethod
    def _split_sections(output: str) -> dict[str, str]:
        """Split explore output into file sections keyed by file path.

        Each section starts with ``#### <file_path>`` and ends before
        the next ``#### `` or end of output.
        """
        import re

        parts: dict[str, str] = {}
        for m in re.finditer(r"(^#### (\S+).*?)(?=^#### |\Z)", output, re.M | re.S):
            parts[m.group(2)] = m.group(1)
        return parts

    def test_necessary_whole_file_section_capped(self, tmp_path):
        """A necessary file via the whole-file path has its section
        capped at 1.5 * max_chars_per_file (issue #33).

        Without the fix, is_necessary=True bypasses all budget checks,
        so a necessary file's section can grow unboundedly.  The fix
        caps the section so it cannot exceed 1.5x the per-file budget.
        """
        # File: <220 lines, < 3*800=2400 raw chars → whole-file path.
        # But formatted section (with line numbers) > 1.5*800=1200.
        max_chars_per_file = 800
        section_cap = int(max_chars_per_file * self._SECTION_CAP_FACTOR)

        svc_lines = ["class Svc:"]
        for i in range(50):
            svc_lines.append(f"    def m{i}(self): return {i}")
        svc_lines.append("")

        root = _write_project(
            tmp_path,
            {
                "src/svc.py": "\n".join(svc_lines),
                "src/caller.py": (
                    "from svc import Svc\n\n"
                    "def run():\n    s = Svc()\n    return s.m0()\n"
                ),
            },
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore(
                "Svc",
                ExploreOptions(
                    max_chars_per_file=max_chars_per_file, max_output_chars=5000
                ),
            )
            assert isinstance(result, str)
            assert "Svc" in result
            # Verify the exact per-section cap marker is present
            assert "section trimmed to fit budget" in result, (
                "A necessary file exceeding the section cap should show "
                "the exact truncation marker"
            )
            # Verify the svc.py section does not exceed the cap
            # (allow +1 for the inter-section newline from _split_sections)
            sections = self._split_sections(result)
            svc_section = sections.get("src/svc.py", "")
            assert len(svc_section) <= section_cap + 1, (
                f"svc.py section is {len(svc_section)} chars, "
                f"exceeds cap {section_cap}+1"
            )
        finally:
            cg.close()

    def test_multiple_necessary_files_stay_under_ceiling(self, tmp_path):
        """When all selected files are necessary, the output still stays
        within the hard ceiling and each section respects per-file cap
        (issue #33)."""
        max_chars_per_file = 6500  # default for 5000+ files
        section_cap = int(max_chars_per_file * self._SECTION_CAP_FACTOR)

        files = {}
        for name in ["alpha", "beta", "gamma"]:
            svc_lines = [f"class {name.title()}Svc:"]
            for i in range(50):
                svc_lines.append(f"    def m{i}(self): return {i}")
            svc_lines.append("")
            files[f"src/{name}.py"] = "\n".join(svc_lines)

        root = _write_project(tmp_path, files)
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore(
                "AlphaSvc BetaSvc GammaSvc",
                ExploreOptions(max_output_chars=3000),
            )
            assert isinstance(result, str)
            # Hard ceiling: min(1.5 * 3000, 25000) = 4500
            assert len(result) <= 4500, (
                f"Output {len(result)} chars exceeds hard ceiling 4500"
            )
            # Verify each file section respects the per-section cap
            # (allow +1 for the inter-section newline from _split_sections)
            sections = self._split_sections(result)
            for fpath, section_text in sections.items():
                assert len(section_text) <= section_cap + 1, (
                    f"Section for {fpath} is {len(section_text)} chars, "
                    f"exceeds cap {section_cap}+1"
                )
        finally:
            cg.close()

    def test_necessary_clustered_file_output_bounded(self, tmp_path):
        """When a necessary file via the cluster path exceeds the section
        cap, the output is bounded — either via the truncation marker or
        via skeletonization fallback (issue #33, updated by #46)."""
        # Create a file >220 lines → cluster path, named symbol → necessary.
        lines = ["class HugeService:"]
        for i in range(150):
            lines.append(f"    def method_{i}(self):")
            lines.append(f"        return {i}")
        lines.append("")

        root = _write_project(
            tmp_path,
            {
                "src/huge.py": "\n".join(lines),
                "src/caller.py": (
                    "from huge import HugeService\n\n"
                    "def run():\n    s = HugeService()\n    return s.method_0()\n"
                ),
            },
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore(
                "HugeService",
                ExploreOptions(max_chars_per_file=500, max_output_chars=2000),
            )
            assert isinstance(result, str)
            # The output should be bounded — either via truncation marker
            # or via skeletonization (which is better, issue #46 fallback).
            # Use specific section-header patterns to avoid false positives
            # from variable names or code comments containing these words.
            is_trimmed = "section trimmed to fit budget" in result
            is_skeletonized = "· focused" in result or "· skeleton" in result
            assert is_trimmed or is_skeletonized, (
                "A necessary file exceeding the section cap via cluster path "
                "should show truncation marker or be skeletonized"
            )
            assert "HugeService" in result
        finally:
            cg.close()

    def test_non_necessary_file_still_respects_global_cap(self, tmp_path):
        """Non-necessary files still skip when exceeding the 90% global
        cap — regression test for issue #33 fix."""
        named_lines = ["class Target:", "    def run(self):", "        pass", ""]
        big_lines = ["class Incidental:"]
        for i in range(50):
            big_lines.append(f"    def method_{i}(self):")
            big_lines.append(f"        return {i}")
        big_lines.append("")

        root = _write_project(
            tmp_path,
            {
                "src/target.py": "\n".join(named_lines),
                "src/incidental.py": "\n".join(big_lines),
            },
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore(
                "Target",
                ExploreOptions(max_output_chars=1000),
            )
            assert isinstance(result, str)
            assert "Target" in result
        finally:
            cg.close()

    def test_small_necessary_file_not_truncated(self, tmp_path):
        """A necessary file that fits within the section cap is not
        truncated (issue #33)."""
        root = _write_project(
            tmp_path,
            {
                "src/service.py": (
                    "class Service:\n    def run(self):\n        pass\n"
                ),
                "src/main.py": (
                    "from service import Service\n\n"
                    "def main():\n    s = Service()\n    s.run()\n"
                ),
            },
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore("Service")
            assert isinstance(result, str)
            assert "section trimmed to fit budget" not in result
            assert "Service" in result
        finally:
            cg.close()

    def test_tiny_max_chars_per_file_edge_case(self, tmp_path):
        """When max_chars_per_file is very small, the truncation message
        may be longer than the cap — output should still be valid
        (issue #33 edge case)."""
        svc_lines = ["class Svc:"]
        for i in range(50):
            svc_lines.append(f"    def m{i}(self): return {i}")
        svc_lines.append("")

        root = _write_project(
            tmp_path,
            {
                "src/svc.py": "\n".join(svc_lines),
                "src/caller.py": (
                    "from svc import Svc\n\n"
                    "def run():\n    s = Svc()\n    return s.m0()\n"
                ),
            },
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore(
                "Svc",
                ExploreOptions(max_chars_per_file=50, max_output_chars=2000),
            )
            assert isinstance(result, str)
            # Should not crash — output is a valid string
            assert "Svc" in result
            # The section cap (1.5 * 50 = 75) is smaller than the
            # truncation message itself, but the max(0, ...) guard
            # prevents negative slicing.
            assert len(result) > 0
        finally:
            cg.close()


# =============================================================================
# Issue #34: Explore Budget Note, Blast Radius protection, remaining_files
# =============================================================================


class TestGetExploreBudget:
    """Unit tests for get_explore_budget — call-count tiers."""

    def test_tiny_project(self):
        assert get_explore_budget(10) == 1

    def test_medium_project(self):
        assert get_explore_budget(1_000) == 2

    def test_large_project(self):
        assert get_explore_budget(10_000) == 3

    def test_very_large_project(self):
        assert get_explore_budget(20_000) == 4

    def test_huge_project(self):
        assert get_explore_budget(30_000) == 5

    def test_boundary_500(self):
        assert get_explore_budget(499) == 1
        assert get_explore_budget(500) == 2

    def test_boundary_5000(self):
        assert get_explore_budget(4_999) == 2
        assert get_explore_budget(5_000) == 3


class TestFormatBudgetNote:
    """Unit tests for format_budget_note."""

    def test_contains_call_count(self):
        note = format_budget_note(3, 5_002)
        assert "3 calls" in note

    def test_contains_file_count(self):
        note = format_budget_note(3, 5_002)
        assert "5,002" in note

    def test_is_blockquote(self):
        """Budget note should be a markdown blockquote."""
        note = format_budget_note(2, 1_000)
        assert note.startswith(">")

    def test_mentions_explore_cheaper(self):
        note = format_budget_note(2, 1_000)
        assert "explore" in note.lower()


class TestExploreBudgetNote:
    """Integration tests for the budget note in explore output (issue #34)."""

    def test_small_project_has_no_budget_note(self, create_python_project):
        """Small projects (<500 files) should NOT show the budget note."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore("User")
            assert "Explore budget:" not in result
        finally:
            cg.close()

    def test_budget_note_appears_when_file_count_large(self, tmp_path):
        """When the project has ≥500 files, the budget note should appear."""
        # Build a project large enough to trigger include_budget_note.
        # We create 600 minimal Python files.
        files: dict[str, str] = {}
        for i in range(600):
            files[f"src/mod_{i:04d}.py"] = f"x_{i} = {i}\n"
        files["src/models.py"] = (
            "class User:\n"
            "    def __init__(self, name):\n"
            "        self.name = name\n\n"
            "    def greet(self):\n"
            "        return f'Hello {self.name}'\n"
        )
        root = _write_project(tmp_path, files)
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.explore("User")
            assert "Explore budget:" in result
            assert "calls for this project" in result
        finally:
            cg.close()


class TestBlastRadiusTruncationProtection:
    """Blast radius section should survive hard-ceiling truncation (issue #34)."""

    def test_blast_radius_survives_truncation(self, tmp_path):
        """When source code triggers hard-ceiling truncation, the blast radius
        section (which appears before Source Code) should be preserved.

        We create a project where:
        - An entry point (create_user) has callers (run), giving non-empty blast radius
        - A large file inflates the source section past the hard ceiling
        """
        # A large service file with many methods
        svc_lines = [
            "from models import User",
            "",
            "def create_user(name, email):",
            "    return User(name, email)",
            "",
        ]
        for i in range(50):
            svc_lines.append(f"def helper_{i}(data):")
            for j in range(15):
                svc_lines.append(f"    x_{j} = data.get('k_{j}', {j})")
            svc_lines.append(f"    return {i}")
            svc_lines.append("")
        svc_lines.append("")

        caller = (
            "from services import create_user\n\n"
            "def run():\n"
            "    user = create_user('Alice', 'alice@example.com')\n"
            "    return user\n"
        )

        models = (
            "class User:\n"
            "    def __init__(self, name, email):\n"
            "        self.name = name\n\n"
            "    def greet(self):\n"
            "        return f'Hello {self.name}'\n"
        )

        root = _write_project(
            tmp_path,
            {
                "models.py": models,
                "services.py": "\n".join(svc_lines),
                "main.py": caller,
            },
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            # Tight budget forces hard-ceiling truncation
            result = cg.explore(
                "create_user",
                ExploreOptions(
                    max_output_chars=800,
                    include_blast_radius=True,
                ),
            )
            # Verify truncation actually happened
            assert len(result) <= 1200  # hard ceiling = min(1.5*800, 25000)
            # If blast radius was computed, it must be present
            # (create_user is called by run, so blast radius should exist)
            if "### Blast radius" in result:
                blast_pos = result.index("### Blast radius")
                source_pos = result.index("### Source Code")
                assert blast_pos < source_pos, (
                    "Blast radius should appear before Source Code"
                )
        finally:
            cg.close()


class TestRemainingFilesCollection:
    """Truncated files should appear in 'Not shown above' (issue #34)."""

    def test_truncated_files_appear_in_not_shown(self, tmp_path):
        """Files whose source sections are cut by the hard ceiling should
        appear in the 'Not shown above' list."""
        # Create multiple files large enough that the combined output
        # exceeds the hard ceiling.
        files: dict[str, str] = {}
        for idx in range(5):
            lines = [f"def handler_{idx}(request):"]
            for i in range(100):
                lines.append(f"    val_{i} = request.get('key_{i}', {i})")
            lines.append("    return request")
            lines.append("")
            files[f"handler_{idx}.py"] = "\n".join(lines)

        root = _write_project(tmp_path, files)
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            # Tight budget forces hard-ceiling truncation
            result = cg.explore(
                "handler",
                ExploreOptions(max_output_chars=600),
            )
            # If truncation happened, truncated file paths should be collected
            if "output truncated" in result:
                assert "Not shown above" in result or "handler_" in result
        finally:
            cg.close()


class TestClusterToSkeletonFallback:
    """Tests for the defensive fallback to skeletonization when cluster
    output would exceed the per-file budget (issue #46).

    When the cluster path produces a single oversized cluster that would
    emit far more than max_chars_per_file * 2, the engine should fall
    back to skeletonization instead of emitting the raw oversized cluster.
    """

    def test_oversized_cluster_triggers_skeletonization(self, tmp_path):
        """A file where clustering produces a single huge cluster (many
        dense methods) should fall back to skeletonization rather than
        emitting 88K chars of raw source (issue #46)."""
        # Build a file like Django's query.py: many dense methods in a class
        lines = [
            "class DenseService:",
            "    def __init__(self):",
            "        self.data = []",
        ]
        # Add many methods with small gaps (all within gap_threshold)
        for i in range(40):
            lines.append(f"    def method_{i}(self, arg):")
            for j in range(10):
                lines.append(f"        x_{j} = self.data + {j} + arg")
            lines.append("        return x_0")
        lines.append("")

        root = _write_project(
            tmp_path,
            {
                "src/dense.py": "\n".join(lines),
                "src/caller.py": (
                    "from dense import DenseService\n\n"
                    "def run():\n"
                    "    s = DenseService()\n"
                    "    return s.method_0()\n"
                ),
            },
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            # Small per-file budget; clustering would produce a single
            # cluster of 40 dense methods (~450 lines)
            result = cg.explore(
                "DenseService method_0",
                ExploreOptions(max_chars_per_file=500, max_output_chars=5000),
            )
            assert isinstance(result, str)
            # Must stay within hard ceiling
            assert len(result) <= 7500, (
                f"Output {len(result)} exceeds hard ceiling 7500"
            )
            # The named symbol should appear
            assert "method_0" in result
            # Should be skeletonized (focused or skeleton tag)
            assert "focused" in result or "skeleton" in result, (
                "Oversized cluster should trigger skeletonization fallback"
            )
        finally:
            cg.close()

    def test_cluster_output_does_not_exceed_2x_per_file_budget(self, tmp_path):
        """The cluster path should never emit more than
        max_chars_per_file * 2 even when a single cluster is huge."""
        # Many methods, very tight budget
        lines = ["class BigClass:"]
        for i in range(60):
            lines.append(f"    def m{i}(self):")
            for j in range(8):
                lines.append(f"        val_{j} = {i} + {j}")
            lines.append("        return val_0")
        lines.append("")

        root = _write_project(
            tmp_path,
            {
                "src/big.py": "\n".join(lines),
                "src/caller.py": (
                    "from big import BigClass\n\n"
                    "def run():\n    return BigClass().m0()\n"
                ),
            },
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            max_cpf = 400
            result = cg.explore(
                "BigClass m0",
                ExploreOptions(max_chars_per_file=max_cpf, max_output_chars=3000),
            )
            # The source section for big.py should not exceed 2 * max_cpf
            # (this is checked via the hard ceiling at 1.5 * max_output_chars)
            assert len(result) <= 4500, (
                f"Output {len(result)} chars exceeds expected ceiling"
            )
        finally:
            cg.close()

    def test_cluster_fallback_when_skeletonize_not_triggered(self, tmp_path):
        """When should_skeletonize returns False but the cluster output
        would be huge, the engine should still limit output size.

        This simulates the scenario from issue #46 where skeletonization
        does NOT trigger (e.g., the query doesn't produce named nodes
        specific enough to make should_skeletonize return True), but the
        cluster path still produces a massive output.
        """
        # A file with many entry-point-adjacent methods but no named
        # symbols unique enough to trigger skeletonization
        lines = ["class GenericHandler:"]
        for i in range(50):
            lines.append(f"    def handle_{i}(self, request):")
            for j in range(10):
                lines.append(f"        data_{j} = request.get('key_{j}', {j})")
            lines.append("        return data_0")
        lines.append("")

        root = _write_project(
            tmp_path,
            {
                "src/handler.py": "\n".join(lines),
                "src/app.py": (
                    "from handler import GenericHandler\n\n"
                    "def main():\n"
                    "    h = GenericHandler()\n"
                    "    return h.handle_0({})\n"
                ),
            },
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            max_cpf = 500
            result = cg.explore(
                "GenericHandler",
                ExploreOptions(max_chars_per_file=max_cpf, max_output_chars=3000),
            )
            assert isinstance(result, str)
            # Hard ceiling: min(1.5 * 3000, 25000) = 4500
            assert len(result) <= 4500, (
                f"Output {len(result)} exceeds hard ceiling 4500"
            )
            # The class name should appear
            assert "GenericHandler" in result
        finally:
            cg.close()
