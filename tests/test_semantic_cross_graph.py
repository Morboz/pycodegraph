"""Cross-graph composition tests (XG-001~008, issue #107).

Verifies that CodeGraph and DocGraph can coexist in the same DB and that
``SemanticGraphQueryHandler`` fans out across both, returning observations
from each with provenance preserved.

XG-003 (issue #109): entities are persisted to the ``semantic_entities``
table, so subject resolution works for both CodeGraph and DocGraph entities
without the previous DocGraph subject-filter hack.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pycodegraph import CodeGraph
from pycodegraph.semantic import (
    AuthorityScope,
    GraphKind,
    QueryStatus,
    QuerySubject,
    RelationKind,
    SemanticGraphQuery,
)
from pycodegraph.semantic.adapters.graphify import GraphifyAdapter
from pycodegraph.semantic.query import SemanticGraphQueryHandler
from pycodegraph.semantic.store import (
    read_entities,
    read_entities_by_name,
    read_latest_dataset_manifests,
)
from pycodegraph.semantic.types import (
    EntityKind,
)
from tests.conftest import write_file

# Reuse the FIXTURE_GRAPH from the adapter test module — a synthetic
# graphify-out graph.json with documentation/concept nodes + doc links.
from tests.test_semantic_graphify_adapter import FIXTURE_GRAPH

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture()
def shared_graph_path(tmp_path: Path) -> str:
    """Write FIXTURE_GRAPH to a temp file and return its path."""
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(FIXTURE_GRAPH))
    return str(path)


@pytest.fixture()
def cross_graph_codegraph(shared_graph_path: str, tmp_path: Path):
    """A CodeGraph with both CodeGraph and DocGraph datasets in one DB.

    Builds a tiny Python project's CodeGraph semantic layer, then runs
    ``GraphifyAdapter`` against ``shared_graph_path`` writing into the same
    DB connection. Both datasets end up addressable from one query handler.
    """
    write_file(
        str(tmp_path),
        "src/mod.py",
        "def run(x: int = 5) -> int:\n    return x + 1\n\n"
        "def call_it() -> int:\n    return run(42)\n",
    )

    cg = CodeGraph.init(str(tmp_path))
    cg.index_all()
    cg.build_semantic_layer(
        repository_id="test/repo",
        revision_value="abc123",
        built_at=1700000000,
    )

    # Build DocGraph into the same DB.
    adapter = GraphifyAdapter(shared_graph_path, db_conn=cg._queries.connection)
    adapter.build(built_at=1700000001)

    yield cg
    cg.close()


# =============================================================================
# Both datasets in one DB
# =============================================================================


class TestBothGraphsShareDB:
    def test_two_datasets_persisted(self, cross_graph_codegraph):
        """Both CodeGraph and DocGraph manifests exist in the same DB."""
        conn = cross_graph_codegraph._queries.connection
        datasets = read_latest_dataset_manifests(conn)
        assert len(datasets) >= 2
        graph_kinds = {ds.graph_kind for ds in datasets}
        assert GraphKind.CODE_GRAPH in graph_kinds
        assert GraphKind.DOC_GRAPH in graph_kinds


# =============================================================================
# Entity persistence (XG-003)
# =============================================================================


class TestEntityPersistence:
    def test_docgraph_entities_persisted(self, cross_graph_codegraph):
        """DocGraph entities are written to the semantic_entities table."""
        conn = cross_graph_codegraph._queries.connection
        datasets = read_latest_dataset_manifests(conn)
        doc_datasets = [d for d in datasets if d.graph_kind == GraphKind.DOC_GRAPH]
        assert len(doc_datasets) >= 1
        entities = read_entities(conn, [f"ds:{d.build_id}" for d in doc_datasets])
        assert len(entities) >= 5
        entity_ids = {e.entity_id for e in entities}
        assert "concept:ansible-test-sanity" in entity_ids
        assert "sanity-test:index" in entity_ids

    def test_docgraph_entity_resolvable_by_name(self, cross_graph_codegraph):
        """DocGraph entities are resolvable via canonical_name."""
        conn = cross_graph_codegraph._queries.connection
        matches = read_entities_by_name(conn, "ansible-test-sanity")
        assert len(matches) >= 1
        assert matches[0].entity_id == "concept:ansible-test-sanity"
        assert matches[0].entity_kind == EntityKind.PROJECT_CONCEPT

    def test_codegraph_entities_persisted(self, cross_graph_codegraph):
        """CodeGraph entities are written to the semantic_entities table."""
        conn = cross_graph_codegraph._queries.connection
        datasets = read_latest_dataset_manifests(conn)
        code_datasets = [d for d in datasets if d.graph_kind == GraphKind.CODE_GRAPH]
        assert len(code_datasets) >= 1
        entities = read_entities(conn, [f"ds:{d.build_id}" for d in code_datasets])
        assert len(entities) >= 1
        entity_names = {e.canonical_name for e in entities}
        assert "run" in entity_names or "call_it" in entity_names

    def test_docgraph_source_file_entities_persisted(self, cross_graph_codegraph):
        """Source_file-based subjects (documents_*) get DOCUMENT_SECTION entities.

        Entities like ``docs/docsite/rst/dev_guide/testing/sanity/index.rst``
        are written so subject resolution can find them by canonical_name
        (the file basename).
        """
        conn = cross_graph_codegraph._queries.connection
        datasets = read_latest_dataset_manifests(conn)
        doc_datasets = [d for d in datasets if d.graph_kind == GraphKind.DOC_GRAPH]
        entities = read_entities(conn, [f"ds:{d.build_id}" for d in doc_datasets])
        # Some source_file paths should appear as DOCUMENT_SECTION entities.
        doc_sections = {
            e.entity_id
            for e in entities
            if e.entity_kind == EntityKind.DOCUMENT_SECTION
        }
        assert len(doc_sections) > 0


# =============================================================================
# Query fan-out (XG-003)
# =============================================================================


class TestCrossGraphQueryFanOut:
    def test_query_docgraph_by_doc_entity_name(self, cross_graph_codegraph):
        """A DOCUMENTS_CONCEPT query by document entity name resolves correctly.

        DOCUMENTS_CONCEPT's subject is the doc entity (e.g. ``sanity-test:index``
        with canonical_name ``Sanity Tests Index``), not the concept. Querying
        by the doc entity name returns the right observations.
        """
        handler = SemanticGraphQueryHandler(cross_graph_codegraph)
        result = handler.query(
            SemanticGraphQuery(
                repository_id="test/repo",
                requested_revision="abc123",
                subject=QuerySubject(name="Sanity Tests Index"),
                expected_relation=RelationKind.DOCUMENTS_CONCEPT,
                authority_scope=AuthorityScope.PUBLIC_CONTRACT,
            )
        )
        assert result.status == QueryStatus.SUCCEEDED
        assert len(result.observations) >= 1
        docgraph_observations = [
            o
            for o in result.observations
            if o.relation_kind == RelationKind.DOCUMENTS_CONCEPT
        ]
        assert len(docgraph_observations) >= 1
        # Every observation carries the DocGraph dataset_id provenance.
        docgraph_dataset_ids = {o.dataset_id for o in docgraph_observations}
        assert all(d.startswith("ds:") for d in docgraph_dataset_ids)

    def test_query_docgraph_by_concept_name_returns_no_matching_evidence(
        self, cross_graph_codegraph
    ):
        """Querying by concept name returns NO_MATCHING_EVIDENCE.

        DOCUMENTS_CONCEPT's subject is the doc entity, not the concept. A
        query for ``ansible-test-sanity`` (a concept entity) correctly finds
        no relations where that concept is the subject. This is XG-003
        correctness: the old code path that dropped the subject filter for
        DocGraph datasets would have incorrectly returned all relations.
        """
        handler = SemanticGraphQueryHandler(cross_graph_codegraph)
        result = handler.query(
            SemanticGraphQuery(
                repository_id="test/repo",
                requested_revision="abc123",
                subject=QuerySubject(name="ansible-test-sanity"),
                expected_relation=RelationKind.DOCUMENTS_CONCEPT,
                authority_scope=AuthorityScope.PUBLIC_CONTRACT,
            )
        )
        # ansible-test-sanity is a concept (object of DOCUMENTS_CONCEPT), not
        # a subject — so no matching evidence for it as a subject.
        assert result.status == QueryStatus.NO_MATCHING_EVIDENCE
        assert result.observations == []

    def test_served_datasets_lists_contributing_datasets(self, cross_graph_codegraph):
        """``served_datasets`` is populated for cross-graph queries."""
        handler = SemanticGraphQueryHandler(cross_graph_codegraph)
        result = handler.query(
            SemanticGraphQuery(
                repository_id="test/repo",
                requested_revision="abc123",
                subject=QuerySubject(name="Sanity Tests Index"),
                expected_relation=RelationKind.DOCUMENTS_CONCEPT,
                authority_scope=AuthorityScope.PUBLIC_CONTRACT,
            )
        )
        assert result.status == QueryStatus.SUCCEEDED
        assert len(result.served_datasets) >= 1
        # At least one served_dataset must be DOC_GRAPH.
        kinds = {ds.graph_kind for ds in result.served_datasets}
        assert GraphKind.DOC_GRAPH in kinds


# =============================================================================
# Backward compatibility
# =============================================================================


class TestBackwardCompat:
    def test_served_dataset_still_populated(self, cross_graph_codegraph):
        """The legacy single ``served_dataset`` field is still set."""
        handler = SemanticGraphQueryHandler(cross_graph_codegraph)
        result = handler.query(
            SemanticGraphQuery(
                repository_id="test/repo",
                requested_revision="abc123",
                subject=QuerySubject(name="call_it"),
                expected_relation=RelationKind.CALLS,
                authority_scope=AuthorityScope.IMPLEMENTATION_TOPOLOGY,
            )
        )
        # SUCCEEDED requires CALLS observations from CodeGraph.
        assert result.status == QueryStatus.SUCCEEDED
        assert result.served_dataset is not None
        assert result.served_dataset.graph_kind == GraphKind.CODE_GRAPH
        # served_datasets[0] should match served_dataset for back-compat.
        assert result.served_datasets[0].build_id == result.served_dataset.build_id


# =============================================================================
# Provenance (XG-007)
# =============================================================================


class TestProvenancePreserved:
    def test_each_observation_carries_its_dataset_id(self, cross_graph_codegraph):
        """Observations retain their originating dataset_id (XG-007)."""
        handler = SemanticGraphQueryHandler(cross_graph_codegraph)
        result = handler.query(
            SemanticGraphQuery(
                repository_id="test/repo",
                requested_revision="abc123",
                subject=QuerySubject(name="Sanity Tests Index"),
                expected_relation=RelationKind.DOCUMENTS_CONCEPT,
                authority_scope=AuthorityScope.PUBLIC_CONTRACT,
            )
        )
        assert result.status == QueryStatus.SUCCEEDED
        for obs in result.observations:
            # dataset_id is non-empty and prefixed with "ds:".
            assert obs.dataset_id.startswith("ds:")
            # Each observation's evidence_refs share its dataset_id.
            for ev in obs.evidence_refs:
                assert ev.dataset_id == obs.dataset_id


# =============================================================================
# No fixed winner (XG-006)
# =============================================================================


class TestNoFixedWinner:
    def test_docgraph_observations_not_dropped_when_codegraph_present(
        self, cross_graph_codegraph
    ):
        """DocGraph observations appear even though a CodeGraph dataset exists.

        XG-006: the handler must not encode a permanent CodeGraph-first or
        DocGraph-first winner. With both datasets in the DB, a
        DOCUMENTS_CONCEPT query (DocGraph-only capability) returns
        observations from the DocGraph dataset — they are not dropped in
        favor of CodeGraph.
        """
        handler = SemanticGraphQueryHandler(cross_graph_codegraph)
        result = handler.query(
            SemanticGraphQuery(
                repository_id="test/repo",
                requested_revision="abc123",
                subject=QuerySubject(name="Sanity Tests Index"),
                expected_relation=RelationKind.DOCUMENTS_CONCEPT,
                authority_scope=AuthorityScope.PUBLIC_CONTRACT,
            )
        )
        assert result.status == QueryStatus.SUCCEEDED
        assert any(
            o.relation_kind == RelationKind.DOCUMENTS_CONCEPT
            for o in result.observations
        )
