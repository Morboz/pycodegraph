"""Tests for file-level source clustering — envelope node filtering."""

from __future__ import annotations

from pycodegraph.explore.clustering import cluster_nodes_in_file
from pycodegraph.types import Language, Node, NodeKind


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

    def test_safety_fallback_when_all_envelopes(self):
        """If filtering removes ALL nodes, original list is used."""
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

        # Safety fallback: should still produce a cluster with the class
        assert len(clusters) == 1
        assert big_class in clusters[0].symbols

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

    def test_all_envelope_kinds_recognized(self):
        """Each kind in _ENVELOPE_KINDS should be filterable."""
        from pycodegraph.explore.clustering import _ENVELOPE_KINDS

        # Verify the constant contains the expected kinds
        assert NodeKind.FILE in _ENVELOPE_KINDS
        assert NodeKind.MODULE in _ENVELOPE_KINDS
        assert NodeKind.CLASS in _ENVELOPE_KINDS
        assert NodeKind.STRUCT in _ENVELOPE_KINDS
        assert NodeKind.INTERFACE in _ENVELOPE_KINDS
        assert NodeKind.ENUM in _ENVELOPE_KINDS
        assert NodeKind.NAMESPACE in _ENVELOPE_KINDS
        assert NodeKind.PROTOCOL in _ENVELOPE_KINDS
        assert NodeKind.TRAIT in _ENVELOPE_KINDS
        assert NodeKind.COMPONENT in _ENVELOPE_KINDS
        # Non-envelope kinds should NOT be present
        assert NodeKind.FUNCTION not in _ENVELOPE_KINDS
        assert NodeKind.METHOD not in _ENVELOPE_KINDS
        assert NodeKind.FIELD not in _ENVELOPE_KINDS

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

        # Without filtering: one giant cluster (5-490) because the class
        # covers 5-450 and merges everything.
        # With filtering: the class is removed; methods cluster based on
        # their own line ranges and the gap_threshold.
        # filter(30-80) and exclude(100-140) are within gap=15 of each
        # other (80+15=95 >= 100), so they merge.
        # annotate(200-260) is far from exclude(100-140): 140+15=155 < 200.
        # values(400-430) is far from annotate(200-260): 260+15=275 < 400.
        # helper(470-490) is far from values(400-430): 430+15=445 < 470.
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
