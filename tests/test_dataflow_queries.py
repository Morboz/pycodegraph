"""Tests for the dataflow_edges table and QueryBuilder dataflow methods.

Covers issue #86 — DB-layer infrastructure for the Dataflow Analysis stage.
Dataflow endpoints are (file_path, line range) triples, not Node IDs, so these
tests exercise the dataflow table directly through cg._queries rather than via
the indexing pipeline.
"""

from __future__ import annotations

import pytest

from pycodegraph import CodeGraph
from pycodegraph.types import DataflowEdge, EdgeKind


@pytest.fixture()
def empty_codegraph(tmp_path):
    """A CodeGraph initialised on an empty directory (no source files)."""
    root = str(tmp_path)
    cg = CodeGraph.init(root)
    yield cg
    cg.close()


def _edge(file_path="models.py", function_id="models.py::fn", **overrides):
    """Build a DataflowEdge with sane defaults, applying any overrides."""
    base = dict(
        file_path=file_path,
        source_start_line=10,
        source_end_line=12,
        target_start_line=20,
        target_end_line=22,
        variable="user",
        function_id=function_id,
        provenance="dataflow:models.py",
    )
    base.update(overrides)
    return DataflowEdge(**base)


class TestDataflowRoundTrip:
    def test_insert_and_get_by_function(self, empty_codegraph):
        """Inserted dataflow edges round-trip with all fields preserved."""
        cg = empty_codegraph
        edges = [
            _edge(variable="user", provenance="dataflow:models.py"),
            _edge(
                variable="count",
                source_start_line=30,
                source_end_line=30,
                target_start_line=40,
                target_end_line=41,
                provenance="dataflow:models.py",
            ),
        ]
        cg._queries.insert_dataflow_edges(edges)

        result = cg._queries.get_dataflow_edges_by_function("models.py::fn")

        assert len(result) == 2
        by_var = {e.variable: e for e in result}
        assert set(by_var) == {"user", "count"}

        user = by_var["user"]
        assert user.file_path == "models.py"
        assert user.source_start_line == 10
        assert user.source_end_line == 12
        assert user.target_start_line == 20
        assert user.target_end_line == 22
        assert user.function_id == "models.py::fn"
        assert user.provenance == "dataflow:models.py"


class TestEdgeKindDataflow:
    def test_dataflow_enum_value_exists(self):
        """EdgeKind.DATAFLOW is the semantic marker for dataflow edges.

        It is metadata only — dataflow edges are never stored in the ``edges``
        table, so this value must exist but is not used in edge rows.
        """
        assert EdgeKind.DATAFLOW.value == "dataflow"


class TestGetDataflowByStatement:
    """get_dataflow_edges_by_statement matches a line in either span."""

    def setup_one_edge(self, cg):
        """One edge: source spans lines 10-12, target spans lines 20-22."""
        cg._queries.insert_dataflow_edges(
            [_edge(source_start_line=10, source_end_line=12,
                   target_start_line=20, target_end_line=22,
                   variable="user")]
        )

    def test_line_inside_source_span(self, empty_codegraph):
        cg = empty_codegraph
        self.setup_one_edge(cg)
        result = cg._queries.get_dataflow_edges_by_statement("models.py", 11)
        assert len(result) == 1
        assert result[0].variable == "user"

    def test_line_inside_target_span(self, empty_codegraph):
        cg = empty_codegraph
        self.setup_one_edge(cg)
        result = cg._queries.get_dataflow_edges_by_statement("models.py", 21)
        assert len(result) == 1
        assert result[0].variable == "user"

    def test_line_outside_both_spans(self, empty_codegraph):
        cg = empty_codegraph
        self.setup_one_edge(cg)
        # 15 sits between the two spans — not contained by either.
        assert cg._queries.get_dataflow_edges_by_statement("models.py", 15) == []

    def test_span_boundaries_included(self, empty_codegraph):
        cg = empty_codegraph
        self.setup_one_edge(cg)
        for line in (10, 12, 20, 22):
            assert len(cg._queries.get_dataflow_edges_by_statement("models.py", line)) == 1

    def test_scoped_to_file(self, empty_codegraph):
        cg = empty_codegraph
        self.setup_one_edge(cg)
        # Same line range, different file — must not match.
        assert cg._queries.get_dataflow_edges_by_statement("other.py", 11) == []


class TestDeleteDataflowEdgesByFile:
    def test_removes_only_named_file(self, empty_codegraph):
        cg = empty_codegraph
        cg._queries.insert_dataflow_edges(
            [
                _edge(file_path="models.py", variable="a"),
                _edge(file_path="models.py", variable="b"),
                _edge(file_path="services.py", variable="c"),
            ]
        )

        cg._queries.delete_dataflow_edges_by_file("models.py")

        # All edges share function_id default "models.py::fn"; after deleting
        # models.py's edges only the services.py edge should remain.
        remaining = cg._queries.get_dataflow_edges_by_function("models.py::fn")
        assert len(remaining) == 1
        assert remaining[0].file_path == "services.py"
        assert remaining[0].variable == "c"

    def test_no_error_on_missing_file(self, empty_codegraph):
        cg = empty_codegraph
        # Deleting a file that never had edges is a no-op, not an error.
        cg._queries.delete_dataflow_edges_by_file("ghost.py")


class TestDeleteDataflowEdgesByProvenancePrefix:
    def test_removes_matching_prefix_only(self, empty_codegraph):
        cg = empty_codegraph
        cg._queries.insert_dataflow_edges(
            [
                _edge(variable="a", provenance="dataflow:models.py"),
                _edge(variable="b", provenance="dataflow:models.py"),
                _edge(variable="c", provenance="dataflow:services.py"),
                _edge(variable="d", provenance="other:models.py"),
            ]
        )

        deleted = cg._queries.delete_dataflow_edges_by_provenance_prefix(
            "dataflow:models.py"
        )

        assert deleted == 2
        leftover = cg._queries.get_dataflow_edges_by_function("models.py::fn")
        assert {e.variable for e in leftover} == {"c", "d"}

    def test_idempotent_rerun(self, empty_codegraph):
        cg = empty_codegraph
        cg._queries.insert_dataflow_edges(
            [_edge(variable="a", provenance="dataflow:models.py")]
        )

        first = cg._queries.delete_dataflow_edges_by_provenance_prefix(
            "dataflow:models.py"
        )
        second = cg._queries.delete_dataflow_edges_by_provenance_prefix(
            "dataflow:models.py"
        )

        assert first == 1
        assert second == 0  # nothing left to delete


class TestDeleteFileCleansDataflow:
    """delete_file / delete_files_batch must drop a file's dataflow edges too,
    so they never outlive the file they describe."""

    def test_delete_file_removes_dataflow_edges(self, create_python_project):
        from pycodegraph import CodeGraph

        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        cg._queries.insert_dataflow_edges(
            [
                _edge(file_path="models.py", function_id="models.py::User"),
                _edge(file_path="services.py", function_id="services.py::create_user"),
            ]
        )

        cg.delete_file("models.py")

        assert (
            cg._queries.get_dataflow_edges_by_function("models.py::User") == []
        )
        # The untouched file's edges survive.
        assert (
            len(cg._queries.get_dataflow_edges_by_function("services.py::create_user"))
            == 1
        )
        cg.close()

    def test_delete_files_batch_removes_dataflow_edges(self, create_python_project):
        from pycodegraph import CodeGraph

        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        cg._queries.insert_dataflow_edges(
            [
                _edge(file_path="models.py", function_id="models.py::User"),
                _edge(file_path="utils.py", function_id="utils.py::format_date"),
                _edge(file_path="services.py", function_id="services.py::create_user"),
            ]
        )

        cg._queries.delete_files_batch(["models.py", "utils.py"])

        assert (
            cg._queries.get_dataflow_edges_by_function("models.py::User") == []
        )
        assert (
            cg._queries.get_dataflow_edges_by_function("utils.py::format_date") == []
        )
        assert (
            len(cg._queries.get_dataflow_edges_by_function("services.py::create_user"))
            == 1
        )
        cg.close()
