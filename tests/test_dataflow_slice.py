"""Tests for the Dataflow Slice consumer API — issue #88.

Exercises ``CodeGraph.get_dataflow_slice`` through the public interface.
Dataflow endpoints are ``(file_path, line range)`` triples, so these tests
insert storage-layer :class:`DataflowEdge` rows directly (same pattern as
``test_dataflow_queries.py``) rather than running the indexing pipeline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pycodegraph import CodeGraph
from pycodegraph.types import DataflowEdge, DataflowSlice


@pytest.fixture()
def empty_codegraph(tmp_path):
    """A CodeGraph initialised on an empty directory (no source files)."""
    root = str(tmp_path)
    cg = CodeGraph.init(root)
    yield cg
    cg.close()


def _edge(file_path="models.py", function_id="models.py::fn", **overrides):
    """Build a storage-layer DataflowEdge with sane defaults + overrides."""
    base = dict(
        file_path=file_path,
        source_start_line=10,
        source_end_line=10,
        target_start_line=20,
        target_end_line=20,
        variable="a",
        function_id=function_id,
        provenance="dataflow:models.py",
    )
    base.update(overrides)
    return DataflowEdge(**base)


class TestEmptyResult:
    def test_no_edges_at_seed_returns_empty_slice(self, empty_codegraph):
        """A seed line with no dataflow edges yields an empty slice, seed None."""
        cg = empty_codegraph
        result = cg.get_dataflow_slice("models.py", 11)

        assert isinstance(result, DataflowSlice)
        assert result.statements == []
        assert result.edges == []
        assert result.seed is None


def _coords(statements):
    """Set of (file_path, start_line, end_line) identifying each statement."""
    return {(s.file_path, s.start_line, s.end_line) for s in statements}


def _vars(edges):
    """Set of variable names carried by each slice edge."""
    return {e.variable for e in edges}


class _ChainFixture:
    """Inserts a known def→use chain in models.py::fn:

    S10 --a--> S20          (E1)
    S12 --b--> S20          (E2)
    S20 --c--> S25          (E3)

    Statements: S10, S12, S20, S25 (single-line spans).
    """

    @staticmethod
    def insert(cg):
        cg._queries.insert_dataflow_edges(
            [
                _edge(
                    variable="a",
                    source_start_line=10,
                    source_end_line=10,
                    target_start_line=20,
                    target_end_line=20,
                ),
                _edge(
                    variable="b",
                    source_start_line=12,
                    source_end_line=12,
                    target_start_line=20,
                    target_end_line=20,
                ),
                _edge(
                    variable="c",
                    source_start_line=20,
                    source_end_line=20,
                    target_start_line=25,
                    target_end_line=25,
                ),
            ]
        )


class TestForwardSlice:
    def test_follows_outgoing_edges_only(self, empty_codegraph):
        """Forward slice from S20 reaches S25 but not the backward S10/S12."""
        cg = empty_codegraph
        _ChainFixture.insert(cg)

        result = cg.get_dataflow_slice("models.py", 20, direction="forward")

        assert _coords(result.statements) == {
            ("models.py", 20, 20),
            ("models.py", 25, 25),
        }
        assert _vars(result.edges) == {"c"}  # only the S20→S25 edge
        # seed is the statement containing the requested line
        assert result.seed is not None
        assert (
            result.seed.file_path,
            result.seed.start_line,
            result.seed.end_line,
        ) == (
            "models.py",
            20,
            20,
        )


class TestBackwardSlice:
    def test_follows_incoming_edges_only(self, empty_codegraph):
        """Backward slice from S20 reaches S10/S12 but not the forward S25."""
        cg = empty_codegraph
        _ChainFixture.insert(cg)

        result = cg.get_dataflow_slice("models.py", 20, direction="backward")

        assert _coords(result.statements) == {
            ("models.py", 10, 10),
            ("models.py", 12, 12),
            ("models.py", 20, 20),
        }
        assert _vars(result.edges) == {"a", "b"}  # the two def→S20 edges


class TestBothDirections:
    def test_traverses_forward_and_backward(self, empty_codegraph):
        """direction="both" reaches the whole chain S10/S12/S20/S25."""
        cg = empty_codegraph
        _ChainFixture.insert(cg)

        result = cg.get_dataflow_slice("models.py", 20, direction="both")

        assert _coords(result.statements) == {
            ("models.py", 10, 10),
            ("models.py", 12, 12),
            ("models.py", 20, 20),
            ("models.py", 25, 25),
        }
        assert _vars(result.edges) == {"a", "b", "c"}


class TestVariableFilter:
    def test_restricts_traversal_to_one_variable(self, empty_codegraph):
        """variable='a' keeps only the a-edge, so S12 and S25 are not reached.

        Without the filter, line 20 both-reaches all four statements.
        """
        cg = empty_codegraph
        _ChainFixture.insert(cg)

        result = cg.get_dataflow_slice("models.py", 20, variable="a")

        assert _coords(result.statements) == {
            ("models.py", 10, 10),
            ("models.py", 20, 20),
        }
        assert _vars(result.edges) == {"a"}

    def test_filter_with_no_matching_edges_at_seed_is_empty(self, empty_codegraph):
        """A variable that never touches the seed line yields an empty slice."""
        cg = empty_codegraph
        _ChainFixture.insert(cg)

        result = cg.get_dataflow_slice("models.py", 20, variable="zzz")

        assert result.statements == []
        assert result.edges == []
        assert result.seed is None


class _DepthChainFixture:
    """A 3-hop forward chain in models.py::depth_fn: S10 → S15 → S20 → S25."""

    @staticmethod
    def insert(cg):
        cg._queries.insert_dataflow_edges(
            [
                _edge(
                    variable="x",
                    function_id="models.py::depth_fn",
                    source_start_line=10,
                    source_end_line=10,
                    target_start_line=15,
                    target_end_line=15,
                ),
                _edge(
                    variable="y",
                    function_id="models.py::depth_fn",
                    source_start_line=15,
                    source_end_line=15,
                    target_start_line=20,
                    target_end_line=20,
                ),
                _edge(
                    variable="z",
                    function_id="models.py::depth_fn",
                    source_start_line=20,
                    source_end_line=20,
                    target_start_line=25,
                    target_end_line=25,
                ),
            ]
        )


class TestMaxDepth:
    def test_depth_zero_returns_only_seed(self, empty_codegraph):
        """max_depth=0 performs no hops — just the seed statement."""
        cg = empty_codegraph
        _DepthChainFixture.insert(cg)

        result = cg.get_dataflow_slice(
            "models.py", 10, direction="forward", max_depth=0
        )

        assert _coords(result.statements) == {("models.py", 10, 10)}
        assert result.edges == []

    def test_depth_two_bounds_at_two_hops(self, empty_codegraph):
        """max_depth=2 reaches S10/S15/S20 but not S25 (3 hops away)."""
        cg = empty_codegraph
        _DepthChainFixture.insert(cg)

        result = cg.get_dataflow_slice(
            "models.py", 10, direction="forward", max_depth=2
        )

        assert _coords(result.statements) == {
            ("models.py", 10, 10),
            ("models.py", 15, 15),
            ("models.py", 20, 20),
        }
        assert _vars(result.edges) == {"x", "y"}


class TestSourceText:
    def test_statements_carry_source_text_from_file(self, tmp_path, empty_codegraph):
        """source_text is read from the real file by (start_line, end_line).

        Edge source spans lines 2-3 (multi-line), target line 5. Seeding at
        line 5 backward-reaches the 2-3 statement, whose text is two lines.
        """
        cg = empty_codegraph
        (Path(tmp_path) / "models.py").write_text(
            "\n".join(f"L{n}" for n in range(1, 7))
        )
        cg._queries.insert_dataflow_edges(
            [
                _edge(
                    variable="v",
                    source_start_line=2,
                    source_end_line=3,
                    target_start_line=5,
                    target_end_line=5,
                )
            ]
        )

        result = cg.get_dataflow_slice("models.py", 5, direction="backward")

        by_coord = {(s.start_line, s.end_line): s for s in result.statements}
        assert by_coord[(2, 3)].source_text == "L2\nL3"
        assert by_coord[(5, 5)].source_text == "L5"
        # the seed (line 5) carries its text too
        assert result.seed is not None
        assert result.seed.source_text == "L5"

    def test_missing_file_leaves_source_text_none(self, empty_codegraph):
        """No file on disk → source_text stays None (graceful, no error)."""
        cg = empty_codegraph
        cg._queries.insert_dataflow_edges(
            [
                _edge(
                    variable="v",
                    source_start_line=2,
                    source_end_line=2,
                    target_start_line=5,
                    target_end_line=5,
                )
            ]
        )

        result = cg.get_dataflow_slice("models.py", 5, direction="backward")

        assert all(s.source_text is None for s in result.statements)
