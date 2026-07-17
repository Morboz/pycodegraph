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
import logging
from pathlib import Path

import pytest

from pycodegraph import CodeGraph
from pycodegraph.semantic import (
    AuthorityScope,
    CrossGraphAliasBuilder,
    GraphKind,
    QueryStatus,
    QuerySubject,
    RelationKind,
    SemanticGraphQuery,
)
from pycodegraph.semantic.adapters.graphify import GraphifyAdapter
from pycodegraph.semantic.alias import read_cross_graph_aliases
from pycodegraph.semantic.query import SemanticGraphQueryHandler
from pycodegraph.semantic.store import (
    read_entities,
    read_entities_by_name,
    read_latest_dataset_manifests,
    read_relations,
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


# =============================================================================
# Cross-graph aliases (XG-004, issue #110)
#
# Evidence-backed explicit relations between DocGraph concepts and CodeGraph
# symbols. Alias expansion is bidirectional and one-hop (max_hops=1).
# =============================================================================


def _write_alias_config(tmp_path: Path, aliases: list[dict]) -> str:
    """Write a YAML alias config and return its path."""
    import yaml as _yaml

    path = tmp_path / "cross_graph_aliases.yaml"
    with path.open("w") as f:
        _yaml.safe_dump({"aliases": aliases}, f, sort_keys=False)
    return str(path)


@pytest.fixture()
def cross_graph_with_aliases(cross_graph_codegraph, tmp_path, request):
    """A shared CodeGraph+DocGraph DB and a path to an alias config.

    The alias config (parametrized via ``request.param``) is written to a
    temp file, but :class:`CrossGraphAliasBuilder` is **not** run here —
    tests run it themselves so they can capture warning logs.

    Returns a tuple ``(codegraph, config_path)``.
    """
    aliases = getattr(request, "param", [])
    config_path = _write_alias_config(tmp_path, aliases)
    return cross_graph_codegraph, config_path


def _run_alias_builder(cg, config_path):
    """Helper: run CrossGraphAliasBuilder against a shared DB connection."""
    conn = cg._queries.connection
    builder = CrossGraphAliasBuilder(
        conn=conn,
        config_path=config_path,
        repository_id="test/repo",
        revision="abc123",
    )
    builder.build()


class TestCrossGraphAliasBuild:
    """Builder behavior: relation writing + validation (warn-and-skip)."""

    @pytest.mark.parametrize(
        "cross_graph_with_aliases",
        [
            [
                {
                    "doc_entity_id": "concept:ansible-test-sanity",
                    "code_qualified_name": "run",
                    "evidence": {
                        "path_or_document_id": "docs/docsite/rst/dev_guide/testing/sanity/index.rst",
                        "start_line": 5,
                        "end_line": 5,
                        "excerpt": ":ref:`ansible-test <run>`",
                    },
                }
            ]
        ],
        indirect=True,
    )
    def test_alias_relation_written_with_evidence(self, cross_graph_with_aliases):
        """A valid alias entry produces one CROSS_GRAPH_ALIAS relation."""
        cg, config_path = cross_graph_with_aliases
        _run_alias_builder(cg, config_path)
        conn = cg._queries.connection
        rels = read_relations(conn, relation_kind=RelationKind.CROSS_GRAPH_ALIAS)
        assert len(rels) == 1
        rel = rels[0]
        assert rel.subject_entity_id == "concept:ansible-test-sanity"
        assert rel.object_entity_id is not None
        assert rel.relation_kind == RelationKind.CROSS_GRAPH_ALIAS
        # Evidence is mandatory (XG-004 core requirement).
        assert len(rel.evidence_refs) == 1
        ev = rel.evidence_refs[0]
        assert ev.locator.path_or_document_id == (
            "docs/docsite/rst/dev_guide/testing/sanity/index.rst"
        )
        assert ev.locator.start_line == 5

    @pytest.mark.parametrize(
        "cross_graph_with_aliases",
        [
            [
                {
                    "doc_entity_id": "concept:does-not-exist",
                    "code_qualified_name": "run",
                    "evidence": {
                        "path_or_document_id": "docs/x.rst",
                    },
                }
            ]
        ],
        indirect=True,
    )
    def test_warns_on_unknown_doc_entity(self, cross_graph_with_aliases, caplog):
        """Unknown doc_entity_id → warn-and-skip, no relation written."""
        cg, config_path = cross_graph_with_aliases
        with caplog.at_level(logging.WARNING, logger="pycodegraph.semantic.alias"):
            _run_alias_builder(cg, config_path)
        conn = cg._queries.connection
        rels = read_relations(conn, relation_kind=RelationKind.CROSS_GRAPH_ALIAS)
        assert rels == []
        assert any(
            "doc entity" in r.getMessage() and "not found" in r.getMessage()
            for r in caplog.records
        )

    @pytest.mark.parametrize(
        "cross_graph_with_aliases",
        [
            [
                {
                    "doc_entity_id": "concept:ansible-test-sanity",
                    "code_qualified_name": "nonexistent_symbol",
                    "evidence": {
                        "path_or_document_id": "docs/x.rst",
                    },
                }
            ]
        ],
        indirect=True,
    )
    def test_warns_on_unknown_code_entity(self, cross_graph_with_aliases, caplog):
        """Unknown code_qualified_name → warn-and-skip, no relation written."""
        cg, config_path = cross_graph_with_aliases
        with caplog.at_level(logging.WARNING, logger="pycodegraph.semantic.alias"):
            _run_alias_builder(cg, config_path)
        conn = cg._queries.connection
        rels = read_relations(conn, relation_kind=RelationKind.CROSS_GRAPH_ALIAS)
        assert rels == []
        assert any(
            "code entity" in r.getMessage() and "not found" in r.getMessage()
            for r in caplog.records
        )

    def test_missing_config_file_is_noop(self, cross_graph_codegraph, tmp_path):
        """A non-existent config path is a no-op (zero aliases written)."""
        conn = cross_graph_codegraph._queries.connection
        builder = CrossGraphAliasBuilder(
            conn=conn,
            config_path=str(tmp_path / "does-not-exist.yaml"),
            repository_id="test/repo",
            revision="abc123",
        )
        count = builder.build()
        assert count == 0
        assert read_relations(conn, relation_kind=RelationKind.CROSS_GRAPH_ALIAS) == []


class TestCrossGraphAliasQueryExpansion:
    """Query-time subject expansion via CROSS_GRAPH_ALIAS."""

    @pytest.mark.parametrize(
        "cross_graph_with_aliases",
        [
            [
                {
                    "doc_entity_id": "concept:ansible-test-sanity",
                    # ``call_it`` is the *subject* of the CALLS relation
                    # (call_it calls run). Aliasing the doc concept to
                    # ``call_it`` lets a CALLS query by the doc name find
                    # that observation via alias expansion.
                    "code_qualified_name": "call_it",
                    "evidence": {
                        "path_or_document_id": "docs/docsite/rst/dev_guide/testing/sanity/index.rst",
                        "start_line": 5,
                        "excerpt": ":ref:`ansible-test <call_it>`",
                    },
                }
            ]
        ],
        indirect=True,
    )
    def test_doc_to_code_expansion(self, cross_graph_with_aliases):
        """Querying by DocGraph concept name resolves the aliased CodeGraph
        symbol's CALLS relations.

        Without alias expansion, querying "ansible-test-sanity" for CALLS
        would return NO_MATCHING_EVIDENCE (the concept is a DocGraph
        entity with no code calls). With XG-004 expansion, the subject is
        expanded to include the CodeGraph ``call_it`` entity, so its
        CALLS relations are returned.
        """
        cg, config_path = cross_graph_with_aliases
        _run_alias_builder(cg, config_path)
        handler = SemanticGraphQueryHandler(cg)
        result = handler.query(
            SemanticGraphQuery(
                repository_id="test/repo",
                requested_revision="abc123",
                subject=QuerySubject(name="ansible-test-sanity"),
                expected_relation=RelationKind.CALLS,
                authority_scope=AuthorityScope.IMPLEMENTATION_TOPOLOGY,
            )
        )
        assert result.status == QueryStatus.SUCCEEDED
        # call_it calls run → at least one CALLS observation.
        assert len(result.observations) >= 1
        assert all(o.relation_kind == RelationKind.CALLS for o in result.observations)

    @pytest.mark.parametrize(
        "cross_graph_with_aliases",
        [
            [
                {
                    # ``sanity-test:index`` is the *subject* of the
                    # DOCUMENTS_CONCEPT relation in the fixture (it
                    # documents concept:ansible-test-sanity). Aliasing
                    # the CodeGraph ``call_it`` to ``sanity-test:index``
                    # lets a DOCUMENTS_CONCEPT query by the code name
                    # find that observation.
                    "doc_entity_id": "sanity-test:index",
                    "code_qualified_name": "call_it",
                    "evidence": {
                        "path_or_document_id": "docs/docsite/rst/dev_guide/testing/sanity/index.rst",
                        "start_line": 5,
                        "excerpt": ":ref:`sanity-index <call_it>`",
                    },
                }
            ]
        ],
        indirect=True,
    )
    def test_code_to_doc_expansion(self, cross_graph_with_aliases):
        """Querying by CodeGraph symbol name resolves the aliased DocGraph
        entity's DOCUMENTS_CONCEPT relations.

        Without alias expansion, querying "call_it" for DOCUMENTS_CONCEPT
        returns NO_MATCHING_EVIDENCE (the symbol is CodeGraph-only). With
        XG-004 expansion, the subject is expanded to include the
        DocGraph ``sanity-test:index`` entity, which is the subject of a
        DOCUMENTS_CONCEPT relation — so the query succeeds.
        """
        cg, config_path = cross_graph_with_aliases
        _run_alias_builder(cg, config_path)
        handler = SemanticGraphQueryHandler(cg)
        result = handler.query(
            SemanticGraphQuery(
                repository_id="test/repo",
                requested_revision="abc123",
                subject=QuerySubject(name="call_it"),
                expected_relation=RelationKind.DOCUMENTS_CONCEPT,
                authority_scope=AuthorityScope.PUBLIC_CONTRACT,
            )
        )
        assert result.status == QueryStatus.SUCCEEDED
        assert len(result.observations) >= 1
        assert all(
            o.relation_kind == RelationKind.DOCUMENTS_CONCEPT
            for o in result.observations
        )

    @pytest.mark.parametrize(
        "cross_graph_with_aliases",
        [[]],
        indirect=True,
    )
    def test_no_alias_no_false_positive(self, cross_graph_with_aliases):
        """With no aliases configured, querying a doc concept for CALLS
        returns NO_MATCHING_EVIDENCE (no spurious cross-graph match)."""
        cg, config_path = cross_graph_with_aliases
        _run_alias_builder(cg, config_path)
        handler = SemanticGraphQueryHandler(cg)
        result = handler.query(
            SemanticGraphQuery(
                repository_id="test/repo",
                requested_revision="abc123",
                subject=QuerySubject(name="ansible-test-sanity"),
                expected_relation=RelationKind.CALLS,
                authority_scope=AuthorityScope.IMPLEMENTATION_TOPOLOGY,
            )
        )
        assert result.status == QueryStatus.NO_MATCHING_EVIDENCE
        assert result.observations == []

    @pytest.mark.parametrize(
        "cross_graph_with_aliases",
        [
            [
                {
                    "doc_entity_id": "concept:ansible-test-sanity",
                    "code_qualified_name": "run",
                    "evidence": {
                        "path_or_document_id": "docs/a.rst",
                        "start_line": 1,
                    },
                },
                # A second alias from the same code symbol to a different
                # doc concept — verifies multi-alias expansion works.
                {
                    "doc_entity_id": "concept:module",
                    "code_qualified_name": "run",
                    "evidence": {
                        "path_or_document_id": "docs/b.rst",
                        "start_line": 1,
                    },
                },
            ]
        ],
        indirect=True,
    )
    def test_multiple_aliases_expand(self, cross_graph_with_aliases):
        """A CodeGraph symbol with two doc aliases expands to both."""
        cg, config_path = cross_graph_with_aliases
        _run_alias_builder(cg, config_path)
        conn = cg._queries.connection
        code_ents = read_entities_by_name(conn, "run")
        code_ids = [
            e.entity_id for e in code_ents if e.entity_kind == EntityKind.FUNCTION
        ]
        assert code_ids, "fixture should have a 'run' function entity"
        alias_map = read_cross_graph_aliases(conn, code_ids)
        expanded: set[str] = set()
        for ids in alias_map.values():
            expanded.update(ids)
        assert "concept:ansible-test-sanity" in expanded
        assert "concept:module" in expanded


class TestCrossGraphAliasMaxHops:
    """``max_hops=1`` enforcement — no transitive aliasing."""

    @pytest.mark.parametrize(
        "cross_graph_with_aliases",
        [
            [
                # alias: concept:ansible-test-sanity → run (code)
                {
                    "doc_entity_id": "concept:ansible-test-sanity",
                    "code_qualified_name": "run",
                    "evidence": {
                        "path_or_document_id": "docs/a.rst",
                        "start_line": 1,
                    },
                },
                # alias: call_it (code) → concept:module (doc)
                {
                    "doc_entity_id": "concept:module",
                    "code_qualified_name": "call_it",
                    "evidence": {
                        "path_or_document_id": "docs/b.rst",
                        "start_line": 1,
                    },
                },
            ]
        ],
        indirect=True,
    )
    def test_no_transitive_expansion(self, cross_graph_with_aliases):
        """Aliases are NOT transitive — A→B and C→D do not produce A→D.

        Querying ``run`` (which is aliased to concept:ansible-test-sanity)
        should NOT transitively reach ``call_it`` via concept:module —
        even though concept:module is aliased to call_it. The alias graph
        is flat, not navigated.
        """
        cg, config_path = cross_graph_with_aliases
        _run_alias_builder(cg, config_path)
        conn = cg._queries.connection
        code_ents = read_entities_by_name(conn, "run")
        code_ids = [
            e.entity_id for e in code_ents if e.entity_kind == EntityKind.FUNCTION
        ]
        assert code_ids
        alias_map = read_cross_graph_aliases(conn, code_ids)
        expanded: set[str] = set()
        for ids in alias_map.values():
            expanded.update(ids)
        # Direct alias: concept:ansible-test-sanity should be in expanded.
        assert "concept:ansible-test-sanity" in expanded
        # Transitive reach: concept:module and call_it should NOT be in
        # expanded — they're only reachable via a second alias hop.
        assert "concept:module" not in expanded
        assert "call_it" not in expanded
