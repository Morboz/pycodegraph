"""Unit tests for _prepare_for_storage — extraction filtering and ref patching."""

from __future__ import annotations

from pycodegraph.extraction.orchestrator import _prepare_for_storage, _PreparedStorage
from pycodegraph.types import (
    Edge,
    EdgeKind,
    ExtractionResult,
    Language,
    Node,
    NodeKind,
    UnresolvedReference,
)


def _node(
    id: str,
    name: str = "n",
    kind: NodeKind = NodeKind.FUNCTION,
    file_path: str = "a.py",
    language: Language = Language.PYTHON,
) -> Node:
    return Node(
        id=id,
        kind=kind,
        name=name,
        qualified_name=f"mod.{name}",
        file_path=file_path,
        language=language,
        start_line=1,
        end_line=1,
        start_column=0,
        end_column=1,
        updated_at=0,
    )


def _edge(source: str, target: str, kind: EdgeKind = EdgeKind.CALLS) -> Edge:
    return Edge(source=source, target=target, kind=kind)


def _ref(
    from_node_id: str, reference_name: str = "x", **overrides
) -> UnresolvedReference:
    defaults = dict(
        from_node_id=from_node_id,
        reference_name=reference_name,
        reference_kind=EdgeKind.CALLS,
        line=1,
        column=0,
    )
    defaults.update(overrides)
    return UnresolvedReference(**defaults)


class TestNodeFiltering:
    """Only nodes with non-empty id, kind, name, file_path are kept."""

    def test_keeps_valid_nodes(self):
        n = _node("a")
        result = _prepare_for_storage(
            ExtractionResult(nodes=[n]), "f.py", Language.PYTHON
        )
        assert result.valid_nodes == [n]

    def test_filters_node_missing_id(self):
        n = _node("")
        result = _prepare_for_storage(
            ExtractionResult(nodes=[n]), "f.py", Language.PYTHON
        )
        assert result.valid_nodes == []

    def test_filters_node_missing_name(self):
        n = _node("a", name="")
        result = _prepare_for_storage(
            ExtractionResult(nodes=[n]), "f.py", Language.PYTHON
        )
        assert result.valid_nodes == []

    def test_filters_node_missing_kind(self):
        n = _node("a")
        n.kind = None  # type: ignore[assignment]
        result = _prepare_for_storage(
            ExtractionResult(nodes=[n]), "f.py", Language.PYTHON
        )
        assert result.valid_nodes == []

    def test_filters_node_missing_file_path(self):
        n = _node("a", file_path="")
        result = _prepare_for_storage(
            ExtractionResult(nodes=[n]), "f.py", Language.PYTHON
        )
        assert result.valid_nodes == []

    def test_mixed_valid_and_invalid_nodes(self):
        good = _node("a")
        bad = _node("")
        result = _prepare_for_storage(
            ExtractionResult(nodes=[good, bad]), "f.py", Language.PYTHON
        )
        assert result.valid_nodes == [good]


class TestEdgeFiltering:
    """Only edges whose source and target are both in the valid-node set are kept."""

    def test_keeps_edge_between_valid_nodes(self):
        n1, n2 = _node("a"), _node("b")
        e = _edge("a", "b")
        result = _prepare_for_storage(
            ExtractionResult(nodes=[n1, n2], edges=[e]), "f.py", Language.PYTHON
        )
        assert result.valid_edges == [e]

    def test_filters_edge_with_missing_source(self):
        n = _node("b")
        e = _edge("a", "b")  # "a" is not a valid node
        result = _prepare_for_storage(
            ExtractionResult(nodes=[n], edges=[e]), "f.py", Language.PYTHON
        )
        assert result.valid_edges == []

    def test_filters_edge_with_missing_target(self):
        n = _node("a")
        e = _edge("a", "b")  # "b" is not a valid node
        result = _prepare_for_storage(
            ExtractionResult(nodes=[n], edges=[e]), "f.py", Language.PYTHON
        )
        assert result.valid_edges == []

    def test_returns_empty_when_no_edges(self):
        n = _node("a")
        result = _prepare_for_storage(
            ExtractionResult(nodes=[n]), "f.py", Language.PYTHON
        )
        assert result.valid_edges == []


class TestRefFiltering:
    """Only refs whose from_node_id is in the valid-node set are kept."""

    def test_keeps_ref_from_valid_node(self):
        n = _node("a")
        r = _ref("a")
        result = _prepare_for_storage(
            ExtractionResult(nodes=[n], unresolved_references=[r]),
            "f.py",
            Language.PYTHON,
        )
        assert len(result.patched_refs) == 1

    def test_filters_ref_from_invalid_node(self):
        r = _ref("orphan")  # no valid node with id "orphan"
        result = _prepare_for_storage(
            ExtractionResult(nodes=[], unresolved_references=[r]),
            "f.py",
            Language.PYTHON,
        )
        assert result.patched_refs == []

    def test_returns_empty_when_no_refs(self):
        n = _node("a")
        result = _prepare_for_storage(
            ExtractionResult(nodes=[n]), "f.py", Language.PYTHON
        )
        assert result.patched_refs == []


class TestRefFilePathPatch:
    """Missing file_path on a ref is backfilled from rel_path."""

    def test_uses_rel_path_when_ref_file_path_empty(self):
        n = _node("a")
        r = _ref("a", file_path="")
        result = _prepare_for_storage(
            ExtractionResult(nodes=[n], unresolved_references=[r]),
            "src/mod.py",
            Language.PYTHON,
        )
        assert result.patched_refs[0].file_path == "src/mod.py"

    def test_preserves_existing_file_path(self):
        n = _node("a")
        r = _ref("a", file_path="other.py")
        result = _prepare_for_storage(
            ExtractionResult(nodes=[n], unresolved_references=[r]),
            "src/mod.py",
            Language.PYTHON,
        )
        assert result.patched_refs[0].file_path == "other.py"


class TestRefLanguagePatch:
    """Missing or 'unknown' language on a ref is backfilled from the file's Language."""

    def test_uses_file_language_when_ref_language_unknown(self):
        n = _node("a")
        r = _ref("a", language="unknown")
        result = _prepare_for_storage(
            ExtractionResult(nodes=[n], unresolved_references=[r]),
            "f.py",
            Language.PYTHON,
        )
        assert result.patched_refs[0].language == "python"

    def test_uses_file_language_when_ref_language_empty(self):
        n = _node("a")
        r = _ref("a", language="")
        result = _prepare_for_storage(
            ExtractionResult(nodes=[n], unresolved_references=[r]),
            "f.rs",
            Language.RUST,
        )
        assert result.patched_refs[0].language == "rust"

    def test_preserves_valid_ref_language(self):
        n = _node("a")
        r = _ref("a", language="python")
        result = _prepare_for_storage(
            ExtractionResult(nodes=[n], unresolved_references=[r]),
            "f.rs",
            Language.RUST,
        )
        assert result.patched_refs[0].language == "python"


class TestReturnType:
    """_prepare_for_storage returns a _PreparedStorage."""

    def test_returns_prepared_storage(self):
        result = _prepare_for_storage(ExtractionResult(), "f.py", Language.PYTHON)
        assert isinstance(result, _PreparedStorage)

    def test_empty_result_yields_empty_collections(self):
        result = _prepare_for_storage(ExtractionResult(), "f.py", Language.PYTHON)
        assert result.valid_nodes == []
        assert result.valid_edges == []
        assert result.patched_refs == []
