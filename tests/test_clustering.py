"""Tests for file-level source clustering — envelope node filtering."""

from __future__ import annotations

from pycodegraph.explore.clustering import cluster_nodes_in_file
from pycodegraph.types import CONTAINER_KINDS, Language, Node, NodeKind


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
