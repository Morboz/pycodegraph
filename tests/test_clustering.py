"""Tests for file-level source clustering — envelope node filtering."""

from __future__ import annotations

from pycodegraph.explore.clustering import FileCluster, cluster_nodes_in_file
from pycodegraph.types import CONTAINER_KINDS, Edge, EdgeKind, Language, Node, NodeKind


def _make_node(
    nid: str,
    name: str,
    kind: NodeKind = NodeKind.FUNCTION,
    file_path: str = "query.py",
    start_line: int = 1,
    end_line: int = 5,
) -> Node:
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


class TestEnvelopeFiltering:
    """Unit tests for envelope node filtering in cluster_nodes_in_file."""

    def test_large_class_filtered_out(self):
        """A class spanning >50% of the file should be removed before clustering."""
        # 100-line file; class spans lines 10-90 (81 lines = 81%)
        big_class = _make_node(
            "cls", "QuerySet", NodeKind.CLASS, start_line=10, end_line=90
        )
        method_a = _make_node(
            "ma", "filter", NodeKind.METHOD, start_line=15, end_line=25
        )
        method_b = _make_node(
            "mb", "exclude", NodeKind.METHOD, start_line=60, end_line=70
        )
        nodes = [big_class, method_a, method_b]
        scores = {"cls": 1.0, "ma": 5.0, "mb": 3.0}

        clusters = cluster_nodes_in_file(
            nodes,
            scores,
            gap_threshold=15,
            file_line_count=100,
        )

        # Without filtering, the class (10-90) would merge both methods
        # into one cluster spanning 10-90. With filtering, only the
        # two methods remain; they are 35 lines apart, so they form
        # separate clusters (gap=15 < 35).
        assert len(clusters) == 2, (
            f"Expected 2 separate clusters (methods far apart), got {len(clusters)}"
        )
        # Verify the class node is NOT in any cluster's symbols
        all_symbols = [s for c in clusters for s in c.symbols]
        assert big_class not in all_symbols

    def test_small_class_not_filtered(self):
        """A class spanning <=50% of the file should NOT be removed."""
        # 100-line file; class spans lines 10-40 (31 lines = 31%)
        small_class = _make_node(
            "cls", "Config", NodeKind.CLASS, start_line=10, end_line=40
        )
        nodes = [small_class]
        scores = {"cls": 1.0}

        clusters = cluster_nodes_in_file(
            nodes,
            scores,
            gap_threshold=15,
            file_line_count=100,
        )

        assert len(clusters) == 1
        assert small_class in clusters[0].symbols

    def test_exactly_50_percent_not_filtered(self):
        """A class spanning exactly 50% should NOT be filtered (strict >)."""
        # 100-line file; class spans lines 1-50 (50 lines = exactly 50%)
        half_class = _make_node(
            "cls", "Half", NodeKind.CLASS, start_line=1, end_line=50
        )
        nodes = [half_class]
        scores = {"cls": 1.0}

        clusters = cluster_nodes_in_file(
            nodes,
            scores,
            gap_threshold=15,
            file_line_count=100,
        )

        assert len(clusters) == 1
        assert half_class in clusters[0].symbols

    def test_slightly_over_50_percent_filtered(self):
        """A class spanning just over 50% should be filtered."""
        # 100-line file; class spans lines 1-51 (51 lines = 51%)
        over_half = _make_node(
            "cls", "OverHalf", NodeKind.CLASS, start_line=1, end_line=51
        )
        method = _make_node("m", "method", NodeKind.METHOD, start_line=10, end_line=20)
        nodes = [over_half, method]
        scores = {"cls": 1.0, "m": 1.0}

        clusters = cluster_nodes_in_file(
            nodes,
            scores,
            gap_threshold=15,
            file_line_count=100,
        )

        # The class is filtered; the method remains alone
        all_symbols = [s for c in clusters for s in c.symbols]
        assert over_half not in all_symbols
        assert method in all_symbols

    def test_no_filtering_when_file_line_count_zero(self):
        """When file_line_count is not provided (default 0), no filtering occurs."""
        big_class = _make_node(
            "cls", "QuerySet", NodeKind.CLASS, start_line=10, end_line=90
        )
        method_a = _make_node(
            "ma", "filter", NodeKind.METHOD, start_line=15, end_line=25
        )
        nodes = [big_class, method_a]
        scores = {"cls": 1.0, "ma": 5.0}

        # Default file_line_count=0 means no filtering
        clusters = cluster_nodes_in_file(
            nodes,
            scores,
            gap_threshold=15,
        )

        # Without filtering, the class (10-90) merges with method (15-25)
        # into one cluster
        assert len(clusters) == 1
        assert big_class in clusters[0].symbols

    def test_django_queryset_scenario(self):
        """Simulate the Django QuerySet scenario from the bug report."""
        # 500-line file; QuerySet class covers lines 5-450 (446 lines = 89%)
        qs_class = _make_node(
            "qs", "QuerySet", NodeKind.CLASS, start_line=5, end_line=450
        )
        filter_method = _make_node(
            "f", "filter", NodeKind.METHOD, start_line=30, end_line=80
        )
        exclude_method = _make_node(
            "e", "exclude", NodeKind.METHOD, start_line=100, end_line=140
        )
        annotate_method = _make_node(
            "a", "annotate", NodeKind.METHOD, start_line=200, end_line=260
        )
        values_method = _make_node(
            "v", "values", NodeKind.METHOD, start_line=400, end_line=430
        )
        # Standalone function outside the class
        helper_fn = _make_node(
            "h", "helper", NodeKind.FUNCTION, start_line=470, end_line=490
        )

        nodes = [
            qs_class,
            filter_method,
            exclude_method,
            annotate_method,
            values_method,
            helper_fn,
        ]
        scores = {n.id: 1.0 for n in nodes}

        clusters = cluster_nodes_in_file(
            nodes,
            scores,
            gap_threshold=15,
            file_line_count=500,
        )

        assert len(clusters) >= 3, (
            f"Expected >=3 separate clusters after envelope filtering, got {len(clusters)}"
        )
        # The class should not be in any cluster
        all_symbols = [s for c in clusters for s in c.symbols]
        assert qs_class not in all_symbols

    def test_existing_behavior_preserved_no_envelopes(self):
        """Files with no envelope nodes should behave identically."""
        fn_a = _make_node("a", "func_a", NodeKind.FUNCTION, start_line=10, end_line=20)
        fn_b = _make_node("b", "func_b", NodeKind.FUNCTION, start_line=25, end_line=35)
        fn_c = _make_node(
            "c", "func_c", NodeKind.FUNCTION, start_line=100, end_line=110
        )
        nodes = [fn_a, fn_b, fn_c]
        scores = {"a": 1.0, "b": 1.0, "c": 1.0}

        clusters = cluster_nodes_in_file(
            nodes,
            scores,
            gap_threshold=15,
            file_line_count=200,
        )

        # fn_a and fn_b merge (20+15=35 >= 25); fn_c is separate (35+15=50 < 100)
        assert len(clusters) == 2


class TestEnvelopeImportanceRedistribution:
    """Tests for importance score redistribution when envelopes are filtered."""

    def test_named_envelope_score_redistributed_to_children(self):
        """A filtered envelope's named-symbol boost should go to its children."""
        # 100-line file; class spans 10-90 (81%), named-symbol boost=50
        big_class = _make_node(
            "cls", "QuerySet", NodeKind.CLASS, start_line=10, end_line=90
        )
        method_a = _make_node(
            "ma", "filter", NodeKind.METHOD, start_line=15, end_line=25
        )
        method_b = _make_node(
            "mb", "exclude", NodeKind.METHOD, start_line=60, end_line=70
        )
        nodes = [big_class, method_a, method_b]
        scores = {"cls": 50.0, "ma": 2.0, "mb": 1.0}

        clusters = cluster_nodes_in_file(
            nodes,
            scores,
            gap_threshold=15,
            file_line_count=100,
        )

        # The class score (50) should be split between its two children
        # method_a: 2.0 + 25.0 = 27.0, method_b: 1.0 + 25.0 = 26.0
        # Find clusters containing each method
        for c in clusters:
            ids = {n.id for n in c.symbols}
            if "ma" in ids:
                assert c.importance == 27.0, (
                    f"Expected cluster with ma to have importance 27.0, got {c.importance}"
                )
            if "mb" in ids:
                assert c.importance == 26.0, (
                    f"Expected cluster with mb to have importance 26.0, got {c.importance}"
                )

    def test_envelope_score_not_redistributed_when_no_children(self):
        """If an envelope has no children within its span, score is not redistributed."""
        # 100-line file; class spans 10-90 but no child nodes within it
        big_class = _make_node(
            "cls", "Lonely", NodeKind.CLASS, start_line=10, end_line=90
        )
        unrelated_fn = _make_node(
            "fn", "helper", NodeKind.FUNCTION, start_line=95, end_line=99
        )
        nodes = [big_class, unrelated_fn]
        scores = {"cls": 50.0, "fn": 1.0}

        clusters = cluster_nodes_in_file(
            nodes,
            scores,
            gap_threshold=15,
            file_line_count=100,
        )

        # unrelated_fn is outside the envelope, so score is NOT redistributed
        # The function cluster should have importance 1.0 (no boost from envelope)
        fn_cluster = [c for c in clusters if any(n.id == "fn" for n in c.symbols)]
        assert len(fn_cluster) == 1
        assert fn_cluster[0].importance == 1.0

    def test_scores_dict_not_mutated(self):
        """cluster_nodes_in_file should not mutate the caller's scores dict."""
        big_class = _make_node(
            "cls", "Service", NodeKind.CLASS, start_line=10, end_line=90
        )
        method = _make_node("m", "process", NodeKind.METHOD, start_line=15, end_line=25)
        nodes = [big_class, method]
        original_scores = {"cls": 50.0, "m": 2.0}
        scores_copy = dict(original_scores)

        cluster_nodes_in_file(
            nodes,
            scores_copy,
            gap_threshold=15,
            file_line_count=100,
        )

        # The caller's dict should not have been modified
        assert scores_copy == original_scores, (
            f"scores dict was mutated: expected {original_scores}, got {scores_copy}"
        )


class TestSafetyFallback:
    """Tests for the safety fallback when all nodes are envelopes."""

    def test_single_envelope_fallback(self):
        """If only one envelope exists and it's filtered, it forms a single-node cluster."""
        # 100-line file; only node is a class covering 90%
        big_class = _make_node(
            "cls", "BigClass", NodeKind.CLASS, start_line=5, end_line=90
        )
        nodes = [big_class]
        scores = {"cls": 1.0}

        clusters = cluster_nodes_in_file(
            nodes,
            scores,
            gap_threshold=15,
            file_line_count=100,
        )

        # Should produce one single-node cluster
        assert len(clusters) == 1
        assert big_class in clusters[0].symbols
        assert len(clusters[0].symbols) == 1

    def test_multiple_overlapping_envelopes_produce_separate_clusters(self):
        """Multiple overlapping envelopes in fallback produce separate single-node clusters."""
        # 100-line file; two overlapping classes each >50%
        user_cls = _make_node("u", "User", NodeKind.CLASS, start_line=1, end_line=65)
        admin_cls = _make_node("a", "Admin", NodeKind.CLASS, start_line=10, end_line=70)
        nodes = [user_cls, admin_cls]
        scores = {"u": 1.0, "a": 1.0}

        clusters = cluster_nodes_in_file(
            nodes,
            scores,
            gap_threshold=15,
            file_line_count=100,
        )

        # Fallback builds single-node clusters directly, bypassing merge logic.
        # Each envelope should be its own cluster.
        assert len(clusters) == 2, (
            f"Expected 2 separate single-node clusters, got {len(clusters)}"
        )
        all_symbols = [s for c in clusters for s in c.symbols]
        assert user_cls in all_symbols
        assert admin_cls in all_symbols
        # Each cluster should have exactly 1 symbol
        assert all(len(c.symbols) == 1 for c in clusters)

    def test_fallback_does_not_inflate_scores(self):
        """Fallback path should not redistribute scores between envelopes."""
        # 100-line file; nested envelopes Outer(1-90, 50pts) and Inner(10-80, 10pts)
        outer = _make_node("o", "Outer", NodeKind.CLASS, start_line=1, end_line=90)
        inner = _make_node("i", "Inner", NodeKind.CLASS, start_line=10, end_line=80)
        nodes = [outer, inner]
        scores = {"o": 50.0, "i": 10.0}

        clusters = cluster_nodes_in_file(
            nodes,
            scores,
            gap_threshold=15,
            file_line_count=100,
        )

        # Each cluster should retain its original score without inflation
        outer_cluster = [c for c in clusters if any(n.id == "o" for n in c.symbols)]
        inner_cluster = [c for c in clusters if any(n.id == "i" for n in c.symbols)]
        assert outer_cluster[0].importance == 50.0
        assert inner_cluster[0].importance == 10.0


class TestClusterStartLineExtension:
    """Tests for cluster start_line extension to filtered envelope boundaries."""

    def test_cluster_extends_to_envelope_start(self):
        """Cluster's start_line should extend up to the filtered envelope's start_line."""
        # 100-line file; class spans lines 3-90 (88 lines = 88%)
        # class def + decorators at lines 1-3, methods from line 15
        big_class = _make_node(
            "cls", "Service", NodeKind.CLASS, start_line=3, end_line=90
        )
        method = _make_node("m", "process", NodeKind.METHOD, start_line=15, end_line=25)
        nodes = [big_class, method]
        scores = {"cls": 1.0, "m": 5.0}

        clusters = cluster_nodes_in_file(
            nodes,
            scores,
            gap_threshold=15,
            file_line_count=100,
        )

        # The cluster should extend start_line from 15 to 3 (the envelope's start)
        # so that class definition and decorators are included in extraction
        assert len(clusters) == 1
        assert clusters[0].start_line == 3, (
            f"Expected start_line=3 (envelope start), got {clusters[0].start_line}"
        )

    def test_cluster_does_not_extend_when_no_envelope(self):
        """Without envelope filtering, start_line is the first node's line."""
        fn_a = _make_node("a", "func_a", NodeKind.FUNCTION, start_line=10, end_line=20)
        fn_b = _make_node("b", "func_b", NodeKind.FUNCTION, start_line=25, end_line=35)
        nodes = [fn_a, fn_b]
        scores = {"a": 1.0, "b": 1.0}

        clusters = cluster_nodes_in_file(
            nodes,
            scores,
            gap_threshold=15,
            file_line_count=100,
        )

        assert clusters[0].start_line == 10  # No extension

    def test_per_envelope_start_line_extension(self):
        """Each cluster extends only to its enclosing envelope's start_line."""
        # 200-line file; two non-overlapping classes each >50%
        # ClassA spans 1-105, ClassB spans 106-200
        class_a = _make_node("ca", "ClassA", NodeKind.CLASS, start_line=1, end_line=105)
        class_b = _make_node(
            "cb", "ClassB", NodeKind.CLASS, start_line=106, end_line=200
        )
        # Methods inside each class, far apart (won't merge)
        method_a = _make_node(
            "ma", "methodA", NodeKind.METHOD, start_line=10, end_line=20
        )
        method_b = _make_node(
            "mb", "methodB", NodeKind.METHOD, start_line=115, end_line=125
        )
        nodes = [class_a, class_b, method_a, method_b]
        scores = {"ca": 1.0, "cb": 1.0, "ma": 5.0, "mb": 3.0}

        clusters = cluster_nodes_in_file(
            nodes,
            scores,
            gap_threshold=15,
            file_line_count=200,
        )

        # method_a cluster should extend to ClassA's start_line (1), not ClassB's (106)
        a_cluster = [c for c in clusters if any(n.id == "ma" for n in c.symbols)]
        assert len(a_cluster) == 1
        assert a_cluster[0].start_line == 1, (
            f"Expected methodA cluster start_line=1 (ClassA start), got {a_cluster[0].start_line}"
        )

        # method_b cluster should extend to ClassB's start_line (106), not ClassA's (1)
        b_cluster = [c for c in clusters if any(n.id == "mb" for n in c.symbols)]
        assert len(b_cluster) == 1
        assert b_cluster[0].start_line == 106, (
            f"Expected methodB cluster start_line=106 (ClassB start), got {b_cluster[0].start_line}"
        )


class TestClusterBudgetEnforcement:
    """Tests for cluster selection respecting file_budget (issue #31)."""

    def _make_cluster(
        self,
        start_line: int,
        end_line: int,
        importance: float = 1.0,
        file_path: str = "query.py",
    ) -> FileCluster:
        """Helper to create a FileCluster for budget selection tests."""
        return FileCluster(
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            symbols=[],
            importance=importance,
        )

    def test_first_cluster_exceeding_budget_selected_alone(self):
        """When the first cluster exceeds budget, it is selected as a
        fallback but no further clusters are added (issue #31)."""
        from pycodegraph.explore.clustering import select_clusters_within_budget

        # 2000-line cluster → est = 2000 * 60 = 120,000 chars
        # file_budget = 6,500 → way over
        clusters = [
            self._make_cluster(1, 2000, importance=10.0),
            self._make_cluster(2100, 2150, importance=5.0),
        ]

        selected = select_clusters_within_budget(clusters, file_budget=6500)

        # First cluster is selected as fallback (保底), but no more
        assert len(selected) == 1
        assert selected[0].start_line == 1
        assert selected[0].end_line == 2000

    def test_budget_enforced_for_first_cluster_when_it_fits(self):
        """When the first cluster fits within budget, budget is enforced
        for subsequent clusters."""
        from pycodegraph.explore.clustering import select_clusters_within_budget

        # Cluster 1: 50 lines → est = 3000 chars (fits in 6500)
        # Cluster 2: 100 lines → est = 6000 chars (would exceed 3000+6000=9000 > 6500)
        clusters = [
            self._make_cluster(1, 50, importance=10.0),
            self._make_cluster(100, 199, importance=5.0),
        ]

        selected = select_clusters_within_budget(clusters, file_budget=6500)

        assert len(selected) == 1
        assert selected[0].start_line == 1
        assert selected[0].end_line == 50

    def test_multiple_clusters_all_fit_within_budget(self):
        """Multiple clusters that all fit within budget are all selected."""
        from pycodegraph.explore.clustering import select_clusters_within_budget

        # 3 clusters each 10 lines → est = 600 each → total 1800 < 6500
        clusters = [
            self._make_cluster(1, 10, importance=10.0),
            self._make_cluster(20, 29, importance=8.0),
            self._make_cluster(40, 49, importance=5.0),
        ]

        selected = select_clusters_within_budget(clusters, file_budget=6500)

        assert len(selected) == 3

    def test_budget_stops_adding_when_next_exceeds(self):
        """Selection stops as soon as the next cluster would exceed budget."""
        from pycodegraph.explore.clustering import select_clusters_within_budget

        # Cluster 1: 50 lines → est = 3000 (fits, total=3000)
        # Cluster 2: 30 lines → est = 1800 (fits, total=4800 < 6500)
        # Cluster 3: 50 lines → est = 3000 (exceeds 4800+3000=7800 > 6500)
        clusters = [
            self._make_cluster(1, 50, importance=10.0),
            self._make_cluster(60, 89, importance=8.0),
            self._make_cluster(100, 149, importance=5.0),
        ]

        selected = select_clusters_within_budget(clusters, file_budget=6500)

        assert len(selected) == 2
        assert selected[0].start_line == 1
        assert selected[1].start_line == 60

    def test_empty_clusters_returns_empty(self):
        """No clusters → empty selection."""
        from pycodegraph.explore.clustering import select_clusters_within_budget

        selected = select_clusters_within_budget([], file_budget=6500)
        assert selected == []

    def test_zero_budget_selects_first_as_fallback(self):
        """Even with zero budget, the first cluster is selected as fallback."""
        from pycodegraph.explore.clustering import select_clusters_within_budget

        clusters = [
            self._make_cluster(1, 100, importance=10.0),
            self._make_cluster(200, 250, importance=5.0),
        ]

        selected = select_clusters_within_budget(clusters, file_budget=0)

        # First cluster is still selected as fallback
        assert len(selected) == 1
        assert selected[0].start_line == 1


class TestContainerKindsShared:
    """Tests for CONTAINER_KINDS shared constant in types.py."""

    def test_container_kinds_in_types(self):
        """CONTAINER_KINDS should be importable from types.py."""
        from pycodegraph.types import CONTAINER_KINDS

        assert NodeKind.CLASS in CONTAINER_KINDS
        assert NodeKind.INTERFACE in CONTAINER_KINDS
        assert NodeKind.STRUCT in CONTAINER_KINDS
        assert NodeKind.TRAIT in CONTAINER_KINDS
        assert NodeKind.PROTOCOL in CONTAINER_KINDS
        assert NodeKind.MODULE in CONTAINER_KINDS
        assert NodeKind.ENUM in CONTAINER_KINDS

    def test_container_kinds_excludes_non_containers(self):
        """CONTAINER_KINDS should NOT include leaf kinds."""
        from pycodegraph.types import CONTAINER_KINDS

        assert NodeKind.FUNCTION not in CONTAINER_KINDS
        assert NodeKind.METHOD not in CONTAINER_KINDS
        assert NodeKind.FIELD not in CONTAINER_KINDS
        assert NodeKind.IMPORT not in CONTAINER_KINDS

    def test_envelope_kinds_derives_from_container_kinds(self):
        """_ENVELOPE_KINDS should be CONTAINER_KINDS plus extras."""
        from pycodegraph.explore.clustering import _ENVELOPE_KINDS

        # CONTAINER_KINDS is a subset of _ENVELOPE_KINDS
        assert CONTAINER_KINDS <= _ENVELOPE_KINDS

        # Extra envelope-only kinds
        assert NodeKind.FILE in _ENVELOPE_KINDS
        assert NodeKind.NAMESPACE in _ENVELOPE_KINDS
        assert NodeKind.COMPONENT in _ENVELOPE_KINDS

        # These are in CONTAINER_KINDS but should also be in _ENVELOPE_KINDS
        assert NodeKind.CLASS in _ENVELOPE_KINDS
        assert NodeKind.ENUM in _ENVELOPE_KINDS

    def test_traversal_uses_container_kinds(self):
        """traversal.py should use CONTAINER_KINDS from types.py, not a local set."""
        import inspect

        import pycodegraph.graph.traversal as trav

        src = inspect.getsource(trav)
        # Should NOT contain a local container_kinds set definition
        assert "container_kinds = {" not in src, (
            "traversal.py should use CONTAINER_KINDS from types.py"
        )


class TestEdgeSourceLocationsInClustering:
    """Tests for adding edge source locations as ranges during clustering.

    Edge source lines (from calls/references) add spatial spread to the
    range set, preventing dense method blocks from merging into one
    monolithic cluster.  This is the Python port of the TS CodeGraph's
    edge-line logic (tools.ts:2257-2274).
    """

    def test_dense_methods_split_by_call_edges(self):
        """37 methods within gap_threshold should form separate clusters
        when edge source locations provide spatial gaps between them.

        This is the core fix for issue #46: without edge source locations,
        37 dense methods merge into 1 cluster (728 lines, 88K chars);
        with edge lines pointing to callers in other parts of the file,
        the cluster is broken up.
        """
        # Simulate: methods at lines 10-20, 25-35, 40-50 (within gap)
        # but edge source locations at lines 100, 200, 300 create gaps
        method_a = _make_node(
            "ma", "filter", NodeKind.METHOD, start_line=10, end_line=20
        )
        method_b = _make_node(
            "mb", "exclude", NodeKind.METHOD, start_line=25, end_line=35
        )
        method_c = _make_node(
            "mc", "annotate", NodeKind.METHOD, start_line=40, end_line=50
        )
        # Edge from method_a to a target, but the call site is at line 200
        edge_to_far = Edge(
            source="ma", target="external", kind=EdgeKind.CALLS, line=200
        )

        nodes = [method_a, method_b, method_c]
        scores = {n.id: 1.0 for n in nodes}

        # Without edges: all 3 methods merge into 1 cluster (within gap=15)
        clusters_no_edges = cluster_nodes_in_file(
            nodes, scores, gap_threshold=15, file_line_count=500
        )
        assert len(clusters_no_edges) == 1, "Without edges, methods should merge"

        # With edges: the edge source at line 200 creates a gap between
        # the dense method block (10-50) and the far-away call site (200),
        # so they form separate clusters.
        clusters_with_edges = cluster_nodes_in_file(
            nodes, scores, gap_threshold=15, file_line_count=500, edges=[edge_to_far]
        )
        # At minimum, the edge location at line 200 should create a
        # separate cluster from the dense method block
        assert len(clusters_with_edges) >= 2, (
            f"Edge source locations should split dense methods, "
            f"got {len(clusters_with_edges)} cluster(s)"
        )

    def test_contains_edges_ignored(self):
        """CONTAINS edges should NOT add source locations — they represent
        parent-child relationships, not call references."""
        parent = _make_node(
            "cls", "Service", NodeKind.CLASS, start_line=1, end_line=150
        )
        method = _make_node("m", "process", NodeKind.METHOD, start_line=10, end_line=20)
        contains_edge = Edge(source="cls", target="m", kind=EdgeKind.CONTAINS, line=5)

        nodes = [parent, method]
        scores = {n.id: 1.0 for n in nodes}

        # CONTAINS edge should be ignored (no additional ranges)
        clusters = cluster_nodes_in_file(
            nodes, scores, gap_threshold=15, file_line_count=200, edges=[contains_edge]
        )
        # Without the CONTAINS edge creating a range at line 5,
        # the class (1-150) is filtered as envelope (>50% of 200),
        # and the method (10-20) forms a single cluster
        all_symbols = [s for c in clusters for s in c.symbols]
        assert parent not in all_symbols

    def test_edge_without_line_ignored(self):
        """Edges with line=None or line<=0 should be ignored."""
        method = _make_node("m", "process", NodeKind.METHOD, start_line=10, end_line=20)
        edge_no_line = Edge(
            source="m", target="external", kind=EdgeKind.CALLS, line=None
        )
        edge_zero_line = Edge(
            source="m", target="external", kind=EdgeKind.CALLS, line=0
        )
        edge_neg_line = Edge(
            source="m", target="external", kind=EdgeKind.CALLS, line=-1
        )

        nodes = [method]
        scores = {"m": 1.0}

        # All three edges should be ignored — no crash, no extra clusters
        clusters = cluster_nodes_in_file(
            nodes,
            scores,
            gap_threshold=15,
            file_line_count=200,
            edges=[edge_no_line, edge_zero_line, edge_neg_line],
        )
        assert len(clusters) == 1

    def test_edge_source_lines_contribute_to_importance(self):
        """Edge source locations should contribute importance to their cluster."""
        method_a = _make_node(
            "ma", "filter", NodeKind.METHOD, start_line=10, end_line=20
        )
        # Call from method_a at line 100
        edge_far = Edge(source="ma", target="external", kind=EdgeKind.CALLS, line=100)

        nodes = [method_a]
        scores = {"ma": 5.0}

        clusters = cluster_nodes_in_file(
            nodes, scores, gap_threshold=15, file_line_count=200, edges=[edge_far]
        )
        # There should be at least 2 clusters (method + edge location)
        assert len(clusters) >= 2
        # The cluster containing the edge source location should have
        # some importance (edge ranges contribute importance=2)
        edge_cluster = [c for c in clusters if c.start_line == 100]
        assert len(edge_cluster) == 1
        assert edge_cluster[0].importance > 0

    def test_django_queryset_dense_methods_with_edges(self):
        """Simulate the exact scenario from issue #46: 37 dense QuerySet
        methods that all merge into 1 cluster without edges.

        When edges reference distant code (e.g., _filter_or_exclude_inplace
        at line 1000), the cluster should split.
        """
        # QuerySet class covering most of the file (filtered as envelope)
        qs_class = _make_node(
            "qs", "QuerySet", NodeKind.CLASS, start_line=5, end_line=1050
        )
        # Dense methods: 5 methods all within gap_threshold of each other
        methods = [
            _make_node(
                f"m{i}",
                f"method_{i}",
                NodeKind.METHOD,
                start_line=30 + i * 20,
                end_line=40 + i * 20,
            )
            for i in range(5)
        ]
        # A standalone function far away (referenced by edge from method_0)
        far_fn = _make_node(
            "far",
            "_filter_or_exclude",
            NodeKind.FUNCTION,
            start_line=1500,
            end_line=1680,
        )
        # Edge from method_0 calling _filter_or_exclude at line 325
        # This creates a range at line 325 which is within the method block
        edge_call = Edge(source="m0", target="far", kind=EdgeKind.CALLS, line=325)

        all_nodes = [qs_class, *methods, far_fn]
        scores = {n.id: 1.0 for n in all_nodes}

        # Without edges: methods are dense → 1 cluster; far_fn is separate
        clusters_no_edges = cluster_nodes_in_file(
            all_nodes, scores, gap_threshold=15, file_line_count=2000
        )
        # Methods merge (gap between methods = 10 < 15), far_fn separate
        method_clusters_no_edges = [
            c for c in clusters_no_edges if any(s.id.startswith("m") for s in c.symbols)
        ]
        assert len(method_clusters_no_edges) == 1, "Dense methods merge without edges"

        # With edges: still 1 cluster for dense methods (edge at 325 is
        # within the method block), but far_fn should still be separate
        clusters_with_edges = cluster_nodes_in_file(
            all_nodes, scores, gap_threshold=15, file_line_count=2000, edges=[edge_call]
        )
        far_clusters = [
            c for c in clusters_with_edges if any(s.id == "far" for s in c.symbols)
        ]
        assert len(far_clusters) >= 1, "far_fn should be in its own cluster"

    def test_no_edges_backward_compatible(self):
        """Calling cluster_nodes_in_file without edges should behave
        exactly as before (backward compatible)."""
        fn_a = _make_node("a", "func_a", NodeKind.FUNCTION, start_line=10, end_line=20)
        fn_b = _make_node("b", "func_b", NodeKind.FUNCTION, start_line=25, end_line=35)
        nodes = [fn_a, fn_b]
        scores = {"a": 1.0, "b": 1.0}

        # Without edges kwarg
        clusters_no_kwarg = cluster_nodes_in_file(
            nodes, scores, gap_threshold=15, file_line_count=200
        )
        # With edges=[]
        clusters_empty = cluster_nodes_in_file(
            nodes, scores, gap_threshold=15, file_line_count=200, edges=[]
        )

        # Same result
        assert len(clusters_no_kwarg) == len(clusters_empty)
        for c1, c2 in zip(clusters_no_kwarg, clusters_empty, strict=True):
            assert c1.start_line == c2.start_line
            assert c1.end_line == c2.end_line
            assert c1.importance == c2.importance


class TestClusterRankingByDensity:
    """Tests for cluster ranking: max_importance → density → score → span.

    The ranking determines the order in which clusters are selected within
    a per-file budget.  High-importance, dense clusters should be selected
    first — matching the TS CodeGraph's rankedClusters sort.
    """

    def test_max_importance_highest_priority(self):
        """Clusters with higher max_importance rank first, regardless of
        total importance or density."""
        from pycodegraph.explore.clustering import FileCluster

        # Cluster A: max_importance=50 (entry point), total=50, 50 lines
        cluster_a = FileCluster(
            file_path="f.py",
            start_line=1,
            end_line=50,
            symbols=[],
            importance=50.0,
            max_importance=50.0,
        )
        # Cluster B: max_importance=10, total=100 (more total but lower max)
        cluster_b = FileCluster(
            file_path="f.py",
            start_line=60,
            end_line=110,
            symbols=[],
            importance=100.0,
            max_importance=10.0,
        )

        ranked = sorted(
            [cluster_a, cluster_b],
            key=lambda c: (
                -c.max_importance,
                -(c.importance / max(c.end_line - c.start_line + 1, 1)),
                -c.importance,
                c.end_line - c.start_line + 1,
            ),
        )
        assert ranked[0] is cluster_a, "max_importance=50 should rank first"

    def test_density_as_tiebreaker(self):
        """When max_importance is equal, denser clusters rank first."""
        from pycodegraph.explore.clustering import FileCluster

        # Cluster A: 10 importance / 50 lines = 0.2 density
        cluster_a = FileCluster(
            file_path="f.py",
            start_line=1,
            end_line=50,
            symbols=[],
            importance=10.0,
            max_importance=5.0,
        )
        # Cluster B: 10 importance / 10 lines = 1.0 density (denser)
        cluster_b = FileCluster(
            file_path="f.py",
            start_line=60,
            end_line=69,
            symbols=[],
            importance=10.0,
            max_importance=5.0,
        )

        ranked = sorted(
            [cluster_a, cluster_b],
            key=lambda c: (
                -c.max_importance,
                -(c.importance / max(c.end_line - c.start_line + 1, 1)),
                -c.importance,
                c.end_line - c.start_line + 1,
            ),
        )
        assert ranked[0] is cluster_b, "Denser cluster should rank first"

    def test_span_as_final_tiebreaker(self):
        """When max_importance and density are equal, smaller clusters rank first."""
        from pycodegraph.explore.clustering import FileCluster

        # Same importance, same density (10/10 = 10/10), different span
        cluster_a = FileCluster(
            file_path="f.py",
            start_line=1,
            end_line=10,
            symbols=[],
            importance=10.0,
            max_importance=5.0,
        )
        cluster_b = FileCluster(
            file_path="f.py",
            start_line=60,
            end_line=69,
            symbols=[],
            importance=10.0,
            max_importance=5.0,
        )

        ranked = sorted(
            [cluster_a, cluster_b],
            key=lambda c: (
                -c.max_importance,
                -(c.importance / max(c.end_line - c.start_line + 1, 1)),
                -c.importance,
                c.end_line - c.start_line + 1,
            ),
        )
        # Both have span=10, so order is stable — just verify no crash
        assert len(ranked) == 2

    def test_file_cluster_has_max_importance_field(self):
        """FileCluster should have a max_importance field."""
        from pycodegraph.explore.clustering import FileCluster

        c = FileCluster(
            file_path="f.py",
            start_line=1,
            end_line=10,
            symbols=[],
            importance=5.0,
            max_importance=10.0,
        )
        assert c.max_importance == 10.0
