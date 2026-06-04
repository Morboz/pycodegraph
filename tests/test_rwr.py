"""Tests for RWR (Personalized PageRank) graph ranking."""

from __future__ import annotations

import pytest

from pycodegraph.explore.rwr import aggregate_to_file_level, compute_rwr
from pycodegraph.types import Edge, EdgeKind, Language, Node, NodeKind


def _make_node(
    nid: str,
    name: str,
    file_path: str = "a.py",
    kind: NodeKind = NodeKind.FUNCTION,
) -> Node:
    return Node(
        id=nid,
        kind=kind,
        name=name,
        qualified_name=name,
        file_path=file_path,
        language=Language.PYTHON,
        start_line=1,
        end_line=5,
        start_column=0,
        end_column=10,
        updated_at=0,
    )


def _make_edge(src: str, tgt: str, kind: EdgeKind = EdgeKind.CALLS) -> Edge:
    return Edge(source=src, target=tgt, kind=kind)


class TestComputeRWR:
    """Unit tests for the RWR algorithm with synthetic graphs."""

    def test_single_seed_gets_highest_score(self):
        """Seed node and its direct neighbor should outrank distant nodes."""
        ids = ["a", "b", "c"]
        edges = [
            _make_edge("a", "b"),
            _make_edge("b", "c"),
        ]
        scores = compute_rwr({"a": 1.0}, edges, ids)
        # Seed and its neighbor should both outrank the far node
        assert scores["a"] > scores["c"]
        assert scores["b"] > scores["c"]

    def test_connected_nodes_score_higher_than_isolated(self):
        """Nodes connected to seeds should score higher than isolated ones."""
        ids = ["seed", "connected", "isolated"]
        edges = [_make_edge("seed", "connected")]
        scores = compute_rwr({"seed": 1.0}, edges, ids)
        assert scores["connected"] > scores["isolated"]

    def test_symmetric_graph_equal_seeds(self):
        """Two seeds with equal weight in a symmetric graph should have similar scores."""
        ids = ["a", "b"]
        edges = [_make_edge("a", "b")]
        scores = compute_rwr({"a": 1.0, "b": 1.0}, edges, ids)
        # Both seeds get personalization mass, scores should be close
        assert abs(scores["a"] - scores["b"]) < 0.05

    def test_empty_graph(self):
        """Empty graph should return empty dict."""
        assert compute_rwr({}, [], []) == {}

    def test_single_node(self):
        """Single node with no edges should still get score."""
        scores = compute_rwr({"a": 1.0}, [], ["a"])
        assert scores["a"] > 0

    def test_two_seeds_different_weights(self):
        """Higher-weighted seed should get more mass."""
        ids = ["a", "b", "c"]
        edges = [_make_edge("a", "c"), _make_edge("b", "c")]
        scores = compute_rwr({"a": 2.0, "b": 1.0}, edges, ids)
        assert scores["a"] > scores["b"]

    def test_ignores_non_rank_edges(self):
        """Contains edges should not affect ranking."""
        ids = ["a", "b"]
        # CONTAINS edges should be ignored by RWR
        edges = [_make_edge("a", "b", EdgeKind.CONTAINS)]
        scores = compute_rwr({"a": 1.0}, edges, ids)
        # Without structural edges, b gets no diffusion from a
        assert scores.get("b", 0.0) < scores["a"]

    def test_multi_hop_diffusion(self):
        """Score should diffuse across multiple hops, decreasing with distance."""
        ids = ["seed", "hop1", "hop2", "hop3"]
        edges = [
            _make_edge("seed", "hop1"),
            _make_edge("hop1", "hop2"),
            _make_edge("hop2", "hop3"),
        ]
        scores = compute_rwr({"seed": 1.0}, edges, ids)
        assert scores["seed"] > scores["hop1"]
        assert scores["hop1"] > scores["hop2"]
        assert scores["hop2"] > scores["hop3"]


class TestAggregateToFileLevel:
    """Tests for RWR score aggregation to file level."""

    def test_aggregates_by_file(self):
        nodes = {
            "a": _make_node("a", "fn1", "file1.py"),
            "b": _make_node("b", "fn2", "file1.py"),
            "c": _make_node("c", "fn3", "file2.py"),
        }
        scores = {"a": 0.3, "b": 0.2, "c": 0.5}
        file_scores = aggregate_to_file_level(scores, nodes)
        assert file_scores["file1.py"] == pytest.approx(0.5)
        assert file_scores["file2.py"] == pytest.approx(0.5)

    def test_skips_import_nodes(self):
        nodes = {
            "a": _make_node("a", "fn1", "file1.py"),
            "b": _make_node("b", "import_os", "file1.py", NodeKind.IMPORT),
        }
        scores = {"a": 0.6, "b": 0.4}
        file_scores = aggregate_to_file_level(scores, nodes)
        # Import node should be skipped
        assert file_scores["file1.py"] == pytest.approx(0.6)

    def test_empty_input(self):
        assert aggregate_to_file_level({}, {}) == {}
