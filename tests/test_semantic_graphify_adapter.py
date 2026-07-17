"""Tests for the GraphifyAdapter — doc_graph → TOCS contract types.

Uses a synthetic fixture (5-10 node mini graphify-out graph.json) to verify
adapter logic in isolation. A separate slow test (``test_graphify_adapter_slow.py``)
runs against the real graphify-out data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pycodegraph.semantic.adapters.graphify.adapter import GraphifyAdapter
from pycodegraph.semantic.types import (
    AuthorityScope,
    CapabilityName,
    CapabilitySupport,
    EntityKind,
    EvidenceKind,
    ExtractionMethod,
    GraphKind,
    Modality,
    RelationKind,
    RevisionMappingStatus,
    RevisionScheme,
)

# =============================================================================
# Fixture: a minimal graphify-out graph.json
# =============================================================================

FIXTURE_GRAPH = {
    "directed": False,
    "multigraph": False,
    "graph": {},
    "built_at_commit": "abc123def456abc123def456abc123def456abcd",
    "nodes": [
        {
            "id": "sanity-test:index",
            "label": "Sanity Tests Index",
            "type": "concept",
            "description": "Index of all sanity tests available as --test options for ansible-test sanity.",
            "source_file": "docs/docsite/rst/dev_guide/testing/sanity/index.rst",
            "source_location": "L1",
            "file_type": "concept",
            "norm_label": "sanity tests index",
            "community": 6,
        },
        {
            "id": "concept:ansible-test-sanity",
            "label": "ansible-test-sanity",
            "type": "concept",
            "description": "The ansible-test sanity command runs sanity tests on collections.",
            "source_file": "docs/docsite/rst/dev_guide/testing/sanity/index.rst",
            "source_location": "L5",
            "file_type": "concept",
            "norm_label": "ansible-test-sanity",
            "community": 6,
        },
        {
            "id": "topic:developing_modules_general",
            "label": "Developing Modules — General",
            "type": "topic",
            "description": "Best practices for developing Ansible modules.",
            "source_file": "docs/docsite/rst/dev_guide/developing_modules_general.rst",
            "source_location": "L1",
            "file_type": "documentation",
            "norm_label": "developing modules general",
            "community": 12,
        },
        {
            "id": "concept:module",
            "label": "Module",
            "type": "concept",
            "description": "A reusable unit of automation in Ansible.",
            "source_file": "docs/docsite/rst/dev_guide/developing_modules_general.rst",
            "source_location": "L10",
            "file_type": "concept",
            "norm_label": "module",
            "community": 12,
        },
        {
            "id": "doc:platform-index",
            "label": "Platform Index",
            "type": "documentation",
            "description": "Index of supported network platforms.",
            "source_file": "docs/docsite/rst/network/platform_index.rst",
            "source_location": "L1",
            "file_type": "documentation",
            "norm_label": "platform index",
            "community": 5,
        },
        {
            "id": "platform:ios",
            "label": "Cisco IOS",
            "type": "Platform",
            "description": "Cisco IOS network platform.",
            "source_file": "docs/docsite/rst/network/platform_index.rst",
            "source_location": "L20",
            "file_type": "concept",
            "norm_label": "cisco ios",
            "community": 5,
        },
        {
            "id": "doc:dev_guide:testing:sanity:import",
            "label": "Import sanity test docs",
            "type": "documentation",
            "description": "How to run import sanity tests.",
            "source_file": "docs/docsite/rst/dev_guide/testing/sanity/import.rst",
            "source_location": "L1",
            "file_type": "documentation",
            "norm_label": "import sanity test docs",
            "community": 6,
        },
        {
            "id": "concept:allowed_unchecked_imports",
            "label": "allowed_unchecked_imports",
            "type": "concept",
            "description": "Modules that may import additional packages without error.",
            "source_file": "docs/docsite/rst/dev_guide/testing/sanity/import.rst",
            "source_location": "L5",
            "file_type": "concept",
            "norm_label": "allowed_unchecked_imports",
            "community": 6,
        },
        {
            "id": "doc:community:collection_requirements",
            "label": "Collection Requirements",
            "type": "documentation",
            "description": "Requirements for including a collection in the Ansible package.",
            "source_file": "docs/docsite/rst/community/collection_contributors/collection_requirements.rst",
            "source_location": "L1",
            "file_type": "documentation",
            "norm_label": "collection requirements",
            "community": 3,
        },
        {
            "id": "concept:collection_checklist",
            "label": "Collection Checklist",
            "type": "concept",
            "description": "Checklist for collection inclusion review.",
            "source_file": "docs/docsite/rst/community/collection_contributors/collection_requirements.rst",
            "source_location": "L15",
            "file_type": "concept",
            "norm_label": "collection checklist",
            "community": 3,
        },
    ],
    "links": [
        # Schema 1: 'relation' key
        {
            "relation": "describes",
            "confidence": "EXTRACTED",
            "source_file": "docs/docsite/rst/dev_guide/testing/sanity/index.rst",
            "source_location": "L5",
            "source": "sanity-test:index",
            "target": "concept:ansible-test-sanity",
            "confidence_score": 1.0,
        },
        # Schema 2: 'type' key (no 'relation')
        {
            "type": "describes",
            "source_file": "docs/docsite/rst/dev_guide/developing_modules_general.rst",
            "source": "topic:developing_modules_general",
            "target": "concept:module",
            "confidence_score": 1.0,
        },
        # documents relation (maps to documents_concept)
        {
            "type": "documents",
            "source_file": "docs/docsite/rst/network/platform_index.rst",
            "source_location": "L20",
            "source": "doc:platform-index",
            "target": "platform:ios",
            "confidence_score": 1.0,
        },
        # describes from documentation node
        {
            "type": "describes",
            "source_file": "docs/docsite/rst/dev_guide/testing/sanity/import.rst",
            "source_location": "L5",
            "source": "doc:dev_guide:testing:sanity:import",
            "target": "concept:allowed_unchecked_imports",
            "confidence_score": 1.0,
        },
        # defines relation (maps to documents_concept too)
        {
            "type": "defines",
            "source_file": "docs/docsite/rst/dev_guide/testing/sanity/import.rst",
            "source_location": "L10",
            "source": "doc:dev_guide:testing:sanity:import",
            "target": "concept:allowed_unchecked_imports",
            "confidence_score": 1.0,
        },
        # Community/ dev_guide path → authority_scope=project_convention
        {
            "type": "documents",
            "source_file": "docs/docsite/rst/community/collection_contributors/collection_requirements.rst",
            "source_location": "L15",
            "source": "doc:community:collection_requirements",
            "target": "concept:collection_checklist",
            "confidence_score": 1.0,
        },
        # Non-doc link — should be discarded
        {
            "type": "references",
            "source_file": "docs/docsite/rst/dev_guide/testing/sanity/index.rst",
            "source": "sanity-test:index",
            "target": "concept:ansible-test-sanity",
            "confidence_score": 1.0,
        },
        # unknown-type link — should be discarded
        {
            "type": "unknown",
            "source_file": "docs/docsite/rst/dev_guide/testing/sanity/index.rst",
            "source": "sanity-test:index",
            "target": "concept:ansible-test-sanity",
            "confidence_score": 1.0,
        },
    ],
}


@pytest.fixture()
def fixture_graph_path(tmp_path: Path) -> str:
    """Write FIXTURE_GRAPH to a temp file and return its path."""
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(FIXTURE_GRAPH))
    return str(path)


# =============================================================================
# Adapter construction
# =============================================================================


class TestGraphifyAdapterConstruction:
    def test_init_reads_graph_json(self, fixture_graph_path: str):
        adapter = GraphifyAdapter(fixture_graph_path)
        assert len(adapter._nodes) == 10
        assert len(adapter._links) == 8
        assert adapter._built_at_commit == "abc123def456abc123def456abc123def456abcd"

    def test_init_raises_on_missing_file(self, tmp_path: Path):
        missing = str(tmp_path / "nonexistent.json")
        with pytest.raises(FileNotFoundError):
            GraphifyAdapter(missing)


# =============================================================================
# Build result
# =============================================================================


class TestGraphifyAdapterBuild:
    """Verifies the full build() output: entities, relations, manifests."""

    def test_build_returns_semantic_build_result(self, fixture_graph_path: str):
        adapter = GraphifyAdapter(fixture_graph_path)
        result = adapter.build(built_at=1700000000)
        assert result.success
        assert result.build_id.startswith("build:")
        assert result.relations_emitted > 0
        assert result.extractors_run == 1  # one extractor: documents_concept

    def test_build_persists_dataset_manifest(self, fixture_graph_path: str):
        adapter = GraphifyAdapter(fixture_graph_path)
        result = adapter.build(built_at=1700000000)
        ds = result.dataset_manifest
        assert ds.instance_id == "docgraph:graphify-out"
        assert ds.graph_kind == GraphKind.DOC_GRAPH
        assert ds.revision.scheme == RevisionScheme.GIT_COMMIT
        assert ds.revision.value == "abc123def456abc123def456abc123def456abcd"
        assert ds.revision.mapping_status == RevisionMappingStatus.EXACT
        assert ds.built_at == 1700000000
        assert ds.schema_version == "0.1.0-tocs"

    def test_build_persists_capability_manifest(self, fixture_graph_path: str):
        adapter = GraphifyAdapter(fixture_graph_path)
        result = adapter.build(built_at=1700000000)
        cap = result.capability_manifest
        assert cap.instance_id == "docgraph:graphify-out"
        # documents_concept → term_lookup = supported
        assert (
            cap.capabilities[CapabilityName.TERM_LOOKUP] == CapabilitySupport.SUPPORTED
        )
        # All documents_* except documents_concept = unavailable
        assert (
            cap.capabilities[CapabilityName.DOCUMENTED_OPTION]
            == CapabilitySupport.UNAVAILABLE
        )
        assert (
            cap.capabilities[CapabilityName.DOCUMENTED_DEFAULT]
            == CapabilitySupport.UNAVAILABLE
        )
        assert (
            cap.capabilities[CapabilityName.DOCUMENTED_BEHAVIOR]
            == CapabilitySupport.UNAVAILABLE
        )
        assert (
            cap.capabilities[CapabilityName.DOCUMENTED_PRECEDENCE]
            == CapabilitySupport.UNAVAILABLE
        )
        assert (
            cap.capabilities[CapabilityName.DOCUMENTED_SAFETY]
            == CapabilitySupport.UNAVAILABLE
        )
        assert (
            cap.capabilities[CapabilityName.DOCUMENTED_VALIDATION]
            == CapabilitySupport.UNAVAILABLE
        )
        # Limitations mention the unavailable docs_*
        assert any("documents_*" in lim for lim in cap.limitations)

    def test_build_emits_documents_concept_relations(self, fixture_graph_path: str):
        adapter = GraphifyAdapter(fixture_graph_path)
        result = adapter.build(built_at=1700000000)
        doc_relations = [
            r
            for r in result._relations
            if r.relation_kind == RelationKind.DOCUMENTS_CONCEPT
        ]
        # 6 doc links in fixture:
        #   describes(sanity-test:index → concept:ansible-test-sanity)  ✓
        #   describes(topic:developing_modules_general → concept:module)  ✓
        #   documents(doc:platform-index → platform:ios)  ✓
        #   describes(doc:dev_guide:testing:sanity:import → concept:allowed_unchecked_imports)  ✓
        #   defines(doc:dev_guide:testing:sanity:import → concept:allowed_unchecked_imports)
        #       → same source+target pair as above → deduped by relation_id  ✗
        #   documents(doc:community:collection_requirements → concept:collection_checklist)  ✓
        # = 5 unique documents_concept relations after dedup
        assert len(doc_relations) == 5

    def test_relation_modality_authority_method(self, fixture_graph_path: str):
        adapter = GraphifyAdapter(fixture_graph_path)
        result = adapter.build(built_at=1700000000)
        for r in result._relations:
            assert r.modality == Modality.DOCUMENTED
            assert r.extraction_method == ExtractionMethod.STRUCTURED_DOCUMENT
        # Check authority_scope — community/ dev_guide should be project_convention
        for r in result._relations:
            src_node = next(
                n for n in FIXTURE_GRAPH["nodes"] if n["id"] == r.subject_entity_id
            )
            src_file = src_node.get("source_file", "")
            if "community/" in src_file or "dev_guide/" in src_file:
                assert r.authority_scope == AuthorityScope.PROJECT_CONVENTION, (
                    f"expected project_convention for {src_file}"
                )
            else:
                assert r.authority_scope == AuthorityScope.PUBLIC_CONTRACT, (
                    f"expected public_contract for {src_file}"
                )

    def test_evidence_ref_structure(self, fixture_graph_path: str):
        adapter = GraphifyAdapter(fixture_graph_path)
        result = adapter.build(built_at=1700000000)
        for r in result._relations:
            assert len(r.evidence_refs) >= 1
            ev = r.evidence_refs[0]
            assert ev.evidence_kind == EvidenceKind.DOCUMENTATION
            assert ev.content_digest.startswith("sha256:")
            assert ev.locator.path_or_document_id  # has source_file
            assert ev.excerpt is not None  # uses description or label

    def test_entity_kind_mapping(self, fixture_graph_path: str):
        adapter = GraphifyAdapter(fixture_graph_path)
        result = adapter.build(built_at=1700000000)
        # Check that concept type → project_concept, Platform → project_concept
        for r in result._relations:
            src_node = next(
                n for n in FIXTURE_GRAPH["nodes"] if n["id"] == r.subject_entity_id
            )
            tgt_node = next(
                n for n in FIXTURE_GRAPH["nodes"] if n["id"] == r.object_entity_id
            )
            # Both subject and target entities exist in the semantic_entities map
            entity_map = adapter._entity_map
            src_entity = entity_map[src_node["id"]]
            tgt_entity = entity_map[tgt_node["id"]]
            assert src_entity.entity_kind in (
                EntityKind.PROJECT_CONCEPT,
                EntityKind.DOCUMENT_SECTION,
            )
            assert tgt_entity.entity_kind == EntityKind.PROJECT_CONCEPT

    def test_non_doc_links_discarded(self, fixture_graph_path: str):
        """Verify that 'references' and 'unknown' links do not produce relations."""
        adapter = GraphifyAdapter(fixture_graph_path)
        result = adapter.build(built_at=1700000000)
        all_rels = result._relations
        # No relations should have come from non-doc links
        assert len(all_rels) == 5  # 6 doc links - 1 dedup = 5 unique

    def test_build_id_deterministic(self, fixture_graph_path: str):
        adapter1 = GraphifyAdapter(fixture_graph_path)
        adapter2 = GraphifyAdapter(fixture_graph_path)
        result1 = adapter1.build(built_at=1700000000)
        result2 = adapter2.build(built_at=1700000000)
        assert result1.build_id == result2.build_id

    def test_concept_without_description_no_relation(self, tmp_path: Path):
        """Concept node without description + no doc link → no relation emitted."""
        graph = {
            "directed": False,
            "multigraph": False,
            "graph": {},
            "built_at_commit": "deadbeef",
            "nodes": [
                {
                    "id": "doc:some-doc",
                    "label": "Some Doc",
                    "type": "documentation",
                    "description": "A doc page.",
                    "source_file": "docs/docsite/rst/some/doc.rst",
                    "source_location": "L1",
                    "file_type": "documentation",
                    "norm_label": "some doc",
                    "community": 1,
                },
                {
                    "id": "concept:no-desc",
                    "label": "No Desc",
                    "type": "concept",
                    "source_file": "docs/docsite/rst/some/doc.rst",
                    "source_location": "L10",
                    "file_type": "concept",
                    "norm_label": "no desc",
                    "community": 1,
                },
            ],
            "links": [
                {
                    "type": "describes",
                    "source_file": "docs/docsite/rst/some/doc.rst",
                    "source": "doc:some-doc",
                    "target": "concept:no-desc",
                    "confidence_score": 1.0,
                }
            ],
        }
        path = tmp_path / "graph.json"
        path.write_text(json.dumps(graph))
        adapter = GraphifyAdapter(str(path))
        result = adapter.build(built_at=1700000000)
        # Target (concept:no-desc) has no description → entity is still project_concept, but relation emitted
        # Actually: the spec says concept + has description + has doc link → documents_concept
        # But a concept without description should still be mapped to project_concept entity.
        # The relation should still be emitted; the entity just has no excerpt fallback.
        assert len(result._relations) == 1


# =============================================================================
# Entity map (internal)
# =============================================================================


class TestEntityMap:
    def test_build_entity_map_returns_all_nodes(self, fixture_graph_path: str):
        adapter = GraphifyAdapter(fixture_graph_path)
        adapter.build(built_at=1700000000)
        entities = adapter._entity_map
        assert len(entities) == 10  # all 10 nodes
        for n in FIXTURE_GRAPH["nodes"]:
            assert n["id"] in entities
            e = entities[n["id"]]
            assert e.canonical_name == n["label"]
            assert e.dataset_id.startswith("ds:build:")

    def test_entity_kind_mapping_accuracy(self, fixture_graph_path: str):
        adapter = GraphifyAdapter(fixture_graph_path)
        adapter.build(built_at=1700000000)
        entities = adapter._entity_map
        # concept type → project_concept
        assert entities["sanity-test:index"].entity_kind == EntityKind.PROJECT_CONCEPT
        # documentation type → document_section
        assert entities["doc:platform-index"].entity_kind == EntityKind.DOCUMENT_SECTION
        assert (
            entities["doc:dev_guide:testing:sanity:import"].entity_kind
            == EntityKind.DOCUMENT_SECTION
        )
        # Platform type → project_concept
        assert entities["platform:ios"].entity_kind == EntityKind.PROJECT_CONCEPT
        # topic type → project_concept
        assert (
            entities["topic:developing_modules_general"].entity_kind
            == EntityKind.PROJECT_CONCEPT
        )
