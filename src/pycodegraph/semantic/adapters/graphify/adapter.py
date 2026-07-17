"""GraphifyAdapter — graphify-out ``graph.json`` → TOCS contract types.

Reads a networkx JSON graph produced by the ``graphify`` skill (over the
Ansible documentation), then emits:

- :class:`SemanticEntity` for every node (one-to-one mapping via
  :mod:`._mapping`).
- :class:`SemanticRelation` of kind :data:`RelationKind.DOCUMENTS_CONCEPT`
  for every ``documents``/``describes``/``defines``/``explains`` link whose
  source and target nodes both exist in the graph.
- :class:`EvidenceRef` per relation, anchored at the link's ``source_file``
  + ``source_location``.
- :class:`GraphDatasetManifest` with ``instance_id="docgraph:graphify-out"``,
  ``graph_kind=doc_graph``, ``revision`` from ``built_at_commit``.
- :class:`GraphCapabilityManifest` declaring ``term_lookup`` supported and
  the six other ``documented_*`` capabilities unavailable.

The adapter does **not** depend on the original ``.rst`` files —
``content_digest`` is sha256 of the source node id, and ``excerpt`` is the
source node's ``description`` (or ``label`` fallback). This keeps the adapter
self-contained (decision 5, issue #100).
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection

from ....db.tables import metadata as db_metadata
from ...extractor import _SCHEMA_VERSION, SemanticBuildResult, _compute_build_id
from ...store import (
    write_capability_manifest,
    write_dataset_manifest,
    write_relations,
)
from ...types import (
    AliasKind,
    AuthorityScope,
    CapabilityName,
    CapabilitySupport,
    DatasetRevision,
    EvidenceKind,
    EvidenceRef,
    ExtractionMethod,
    GraphCapabilityManifest,
    GraphDatasetManifest,
    GraphKind,
    Modality,
    RelationKind,
    RevisionMappingStatus,
    RevisionScheme,
    SemanticEntity,
    SemanticEntityAlias,
    SemanticRelation,
    SourceLocator,
)
from ._mapping import (
    DOCUMENTS_CONCEPT_RELATIONS,
    entity_kind_for_node_type,
)

# =============================================================================
# Adapter constants (decisions 3, 4, 6, 7 — issue #100)
# =============================================================================

#: Decision 6 — instance_id and graph_kind for the dataset manifest.
_INSTANCE_ID = "docgraph:graphify-out"
_GRAPH_KIND = GraphKind.DOC_GRAPH

#: Decision 4 — DocGraph relations are ``documented`` (from docs, not source).
_MODALITY = Modality.DOCUMENTED

#: Decision 4 — DocGraph uses ``structured_document`` extraction method.
_EXTRACTION_METHOD = ExtractionMethod.STRUCTURED_DOCUMENT

#: Extractor version stamp (mirrors CodeGraph extractors' "0.1.0" style).
_EXTRACTOR_VERSION = "0.1.0"

#: Decision 3 — source files under these path fragments are project_convention
#: (maintainer docs, not public API contract).
_PROJECT_CONVENTION_PATH_FRAGMENTS = ("dev_guide/", "community/")

#: Decision 7 — capability → documents_* relation mapping. The adapter only
#: produces ``documents_concept`` → ``term_lookup``. All other documented_*
#: capabilities are declared ``unavailable`` with a limitation note.
_DOCUMENTS_CAPABILITY_TO_RELATION: dict[CapabilityName, RelationKind] = {
    CapabilityName.TERM_LOOKUP: RelationKind.DOCUMENTS_CONCEPT,
    CapabilityName.DOCUMENTED_OPTION: RelationKind.DOCUMENTS_OPTION,
    CapabilityName.DOCUMENTED_DEFAULT: RelationKind.DOCUMENTS_DEFAULT,
    CapabilityName.DOCUMENTED_BEHAVIOR: RelationKind.DOCUMENTS_BEHAVIOR,
    CapabilityName.DOCUMENTED_PRECEDENCE: RelationKind.DOCUMENTS_PRECEDENCE,
    CapabilityName.DOCUMENTED_SAFETY: RelationKind.DOCUMENTS_SAFETY,
    CapabilityName.DOCUMENTED_VALIDATION: RelationKind.DOCUMENTS_VALIDATION,
}


# =============================================================================
# GraphifyAdapter
# =============================================================================


class GraphifyAdapter:
    """Adapter that converts a graphify-out ``graph.json`` into TOCS contract types.

    Lifecycle: construct once with the path to ``graph.json``, call
    :meth:`build` once. The adapter is single-use — entity map and relation
    list accumulate during the build.

    The adapter creates its own in-memory SQLite DB for persistence (so it
    doesn't require a CodeGraph instance). Tests can access the connection
    via :attr:`_db_conn` to read back the persisted rows.
    """

    def __init__(self, graph_json_path: str | Path) -> None:
        path = Path(graph_json_path)
        if not path.exists():
            raise FileNotFoundError(f"graph.json not found: {path}")
        self._graph_json_path = path
        self._raw: dict[str, Any] = json.loads(path.read_text())

        self._nodes: list[dict[str, Any]] = self._raw.get("nodes", [])
        self._links: list[dict[str, Any]] = self._raw.get("links", [])
        self._built_at_commit: str | None = self._raw.get("built_at_commit")

        # Index nodes by id for O(1) link resolution.
        self._nodes_by_id: dict[str, dict[str, Any]] = {
            n["id"]: n for n in self._nodes if "id" in n
        }

        # Build-time state — populated by build().
        self._build_id: str | None = None
        self._dataset_id: str | None = None
        self._entity_map: dict[str, SemanticEntity] = {}

        # Lazy-init the DB connection (only when build() is called).
        self._db_conn: Connection | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, built_at: int) -> SemanticBuildResult:
        """Run the adapter and persist the semantic layer.

        ``built_at`` is supplied by the caller (epoch seconds) so the manifest
        timestamp is deterministic from the adapter's perspective.

        Persists manifest + capability + relations to an in-memory SQLite DB
        accessible via :attr:`_db_conn`. Returns a
        :class:`SemanticBuildResult` mirroring
        :meth:`pycodegraph.semantic.SemanticLayerBuilder.build`.
        """
        start = _monotonic_ms()
        errors: list[str] = []

        # Compute build_id deterministically (decision 6 — reuse CodeGraph
        # _compute_build_id). Extractor versions is a single entry for the
        # graphify adapter.
        extractor_versions: dict[str, str] = {
            f"{RelationKind.DOCUMENTS_CONCEPT.value}:{_EXTRACTION_METHOD.value}": _EXTRACTOR_VERSION
        }
        build_id = _compute_build_id(
            repository_id=_repository_id_from_commit(self._built_at_commit),
            revision_value=self._built_at_commit or "unknown",
            extractor_versions=extractor_versions,
            built_at=built_at,
        )
        self._build_id = build_id
        self._dataset_id = f"ds:{build_id}"

        # Build entity map (one SemanticEntity per node).
        self._entity_map = self._build_entity_map()

        # Extract DOCUMENTS_CONCEPT relations.
        relations = self._extract_documents_concept_relations()

        # Build manifests.
        dataset_manifest = self._build_dataset_manifest(
            build_id=build_id,
            built_at=built_at,
            extractor_versions=extractor_versions,
        )
        capability_manifest = self._build_capability_manifest(relations)

        # Persist to an in-memory SQLite DB.
        self._init_db()
        assert self._db_conn is not None
        write_dataset_manifest(self._db_conn, dataset_manifest)
        # Capability manifest's instance_id must match the dataset's so the
        # query handler can find it via read_latest_dataset_manifest().
        write_capability_manifest(self._db_conn, capability_manifest)
        write_relations(self._db_conn, relations)
        self._db_conn.commit()

        duration_ms = _monotonic_ms() - start

        # Stash relations on the result for test access — the persisted copy
        # is in the DB, but tests want to inspect them without re-reading.
        result = SemanticBuildResult(
            success=not errors,
            build_id=build_id,
            dataset_manifest=dataset_manifest,
            capability_manifest=capability_manifest,
            relations_emitted=len(relations),
            extractors_run=1,
            errors=errors,
            duration_ms=duration_ms,
        )
        # Attach relations for test inspection (not part of the dataclass).
        result._relations = relations  # type: ignore[attr-defined]
        return result

    # ------------------------------------------------------------------
    # Entity construction
    # ------------------------------------------------------------------

    def _build_entity_map(self) -> dict[str, SemanticEntity]:
        """Build a SemanticEntity for every graphify-out node.

        Decision 1 (issue #100): node.type → EntityKind via
        :mod:`._mapping`. Each entity's ``entity_id`` is the graphify-out
        node id (already deterministic). ``canonical_name`` is the node's
        ``label``.
        """
        assert self._dataset_id is not None
        repository_id = _repository_id_from_commit(self._built_at_commit)
        result: dict[str, SemanticEntity] = {}
        for node in self._nodes:
            node_id = node.get("id")
            if node_id is None:
                continue
            entity_kind = entity_kind_for_node_type(node.get("type"))
            label = node.get("label") or node_id
            norm_label = node.get("norm_label")
            source_file = node.get("source_file") or node.get("file") or ""
            source_loc = node.get("source_location")
            start_line, end_line = _parse_source_location(source_loc)

            aliases: list[SemanticEntityAlias] = []
            if norm_label and norm_label != label:
                aliases.append(
                    SemanticEntityAlias(
                        value=norm_label,
                        alias_kind=AliasKind.NORMALIZED_TERM,
                    )
                )

            entity = SemanticEntity(
                entity_id=node_id,
                repository_id=repository_id,
                entity_kind=entity_kind,
                canonical_name=label,
                dataset_id=self._dataset_id,
                qualified_name=node_id,
                language="rst",  # DocGraph nodes are documentation, not code
                scope=_derive_scope(node_id),
                aliases=aliases,
                source_locator=SourceLocator(
                    path_or_document_id=source_file,
                    start_line=start_line,
                    end_line=end_line,
                    symbol_or_section=label,
                    graph_node_ids=[node_id],
                ),
            )
            result[node_id] = entity
        return result

    # ------------------------------------------------------------------
    # Relation extraction
    # ------------------------------------------------------------------

    def _extract_documents_concept_relations(self) -> list[SemanticRelation]:
        """Emit one DOCUMENTS_CONCEPT relation per qualifying link.

        Decision 2 (issue #100): only ``documents``/``describes``/``defines``/
        ``explains`` links produce a relation. Both source and target nodes
        must exist in the entity map. The relation's subject is the source
        node (a documentation/topic entity), object is the target node (a
        concept entity).
        """
        assert self._build_id is not None
        assert self._dataset_id is not None
        relations: list[SemanticRelation] = []
        seen_relation_ids: set[str] = set()

        for link in self._links:
            rel_str = _link_relation(link)
            if rel_str not in DOCUMENTS_CONCEPT_RELATIONS:
                continue  # dropped — see _mapping.DOCUMENTS_CONCEPT_RELATIONS

            source_id = link.get("source")
            target_id = link.get("target")
            if source_id is None or target_id is None:
                continue
            if source_id not in self._entity_map or target_id not in self._entity_map:
                continue

            source_file = link.get("source_file") or _node_source_file(
                self._nodes_by_id.get(source_id, {})
            )
            source_loc = link.get("source_location") or _node_source_location(
                self._nodes_by_id.get(source_id, {})
            )
            start_line, end_line = _parse_source_location(source_loc)
            authority_scope = _authority_scope_for_path(source_file)

            source_node = self._nodes_by_id[source_id]
            content_digest = _content_digest(source_node)
            excerpt = source_node.get("description") or source_node.get("label")

            relation_id = _relation_id(
                dataset_id=self._dataset_id,
                relation_kind=RelationKind.DOCUMENTS_CONCEPT.value,
                subject_id=source_id,
                object_id=target_id,
            )
            # Dedupe by relation_id — graphify-out may have parallel edges
            # between the same pair; the contract relation is the same.
            if relation_id in seen_relation_ids:
                continue
            seen_relation_ids.add(relation_id)

            evidence_ref = EvidenceRef(
                evidence_ref_id=_evidence_ref_id(
                    self._dataset_id, source_id, relation_id
                ),
                evidence_kind=EvidenceKind.DOCUMENTATION,
                repository_id=_repository_id_from_commit(self._built_at_commit),
                revision=self._built_at_commit or "unknown",
                locator=SourceLocator(
                    path_or_document_id=source_file,
                    start_line=start_line,
                    end_line=end_line,
                    symbol_or_section=source_node.get("label"),
                    graph_node_ids=[source_id, target_id],
                ),
                content_digest=content_digest,
                dataset_id=self._dataset_id,
                excerpt=excerpt,
            )

            relations.append(
                SemanticRelation(
                    relation_id=relation_id,
                    subject_entity_id=source_id,
                    relation_kind=RelationKind.DOCUMENTS_CONCEPT,
                    authority_scope=authority_scope,
                    modality=_MODALITY,
                    extraction_method=_EXTRACTION_METHOD,
                    extractor_version=_EXTRACTOR_VERSION,
                    dataset_id=self._dataset_id,
                    evidence_refs=[evidence_ref],
                    object_entity_id=target_id,
                    literal_object=None,
                )
            )

        return relations

    # ------------------------------------------------------------------
    # Manifest construction
    # ------------------------------------------------------------------

    def _build_dataset_manifest(
        self,
        *,
        build_id: str,
        built_at: int,
        extractor_versions: dict[str, str],
    ) -> GraphDatasetManifest:
        """Build the GraphDatasetManifest (decision 6 — issue #100)."""
        return GraphDatasetManifest(
            instance_id=_INSTANCE_ID,
            graph_kind=_GRAPH_KIND,
            repository_id=_repository_id_from_commit(self._built_at_commit),
            revision=DatasetRevision(
                scheme=RevisionScheme.GIT_COMMIT,
                value=self._built_at_commit or "unknown",
                mapping_status=RevisionMappingStatus.EXACT,
                source_revision=None,
            ),
            build_id=build_id,
            built_at=built_at,
            schema_version=_SCHEMA_VERSION,
            extractor_versions=extractor_versions,
            capabilities_ref=f"{build_id}:capabilities",
        )

    def _build_capability_manifest(
        self, relations: list[SemanticRelation]
    ) -> GraphCapabilityManifest:
        """Build the GraphCapabilityManifest (decision 7 — issue #100).

        ``term_lookup`` (← ``documents_concept``) is ``supported`` if at
        least one relation was emitted. All other ``documented_*``
        capabilities are ``unavailable`` with a limitation explaining the
        missing granularity.
        """
        has_documents_concept = any(
            r.relation_kind == RelationKind.DOCUMENTS_CONCEPT
            and r.evidence_refs  # COMMON-013
            for r in relations
        )

        capabilities: dict[CapabilityName, CapabilitySupport] = {}
        limitations: list[str] = []

        # documents_concept → term_lookup (supported if we have relations)
        if has_documents_concept:
            capabilities[CapabilityName.TERM_LOOKUP] = CapabilitySupport.SUPPORTED
        else:
            capabilities[CapabilityName.TERM_LOOKUP] = CapabilitySupport.UNAVAILABLE
            limitations.append(
                "term_lookup: no documents_concept relations emitted "
                "(graphify-out produced no documents/describes/defines/explains links)."
            )

        # Other documents_* — always unavailable (decision B, issue #100).
        unavailable_caps = [
            CapabilityName.DOCUMENTED_OPTION,
            CapabilityName.DOCUMENTED_DEFAULT,
            CapabilityName.DOCUMENTED_BEHAVIOR,
            CapabilityName.DOCUMENTED_PRECEDENCE,
            CapabilityName.DOCUMENTED_SAFETY,
            CapabilityName.DOCUMENTED_VALIDATION,
        ]
        for cap in unavailable_caps:
            capabilities[cap] = CapabilitySupport.UNAVAILABLE
        limitations.append(
            "documents_* (option/default/behavior/precedence/safety/validation) "
            "are unavailable: graphify-out only emits coarse documents/describes/"
            "defines/explains links. Fine-grained extraction from .rst requires "
            "a follow-up ticket (see issue #100, decision B)."
        )

        # CodeGraph-only capabilities — DocGraph doesn't produce them.
        code_only_caps = [
            CapabilityName.SYMBOL_LOOKUP,
            CapabilityName.SOURCE_SLICE,
            CapabilityName.SIGNATURE_PARAMETER,
            CapabilityName.CALL_TOPOLOGY,
            CapabilityName.VALUE_FORWARDING,
            CapabilityName.CONDITION_BEHAVIOR,
            CapabilityName.COMMAND_SEMANTICS,
            CapabilityName.EFFECT_GUARD,
            CapabilityName.PRECEDENCE,
            CapabilityName.RETURN_CONSUMERS,
            CapabilityName.TEST_SCENARIO_RELATION,
        ]
        for cap in code_only_caps:
            capabilities[cap] = CapabilitySupport.UNAVAILABLE

        # Override instance_id to match the dataset manifest (the capability
        # manifest is keyed by capabilities_ref=instance_id in the store).
        return GraphCapabilityManifest(
            instance_id=_INSTANCE_ID,
            schema_version=_SCHEMA_VERSION,
            capabilities=capabilities,
            limitations=limitations,
        )

    # ------------------------------------------------------------------
    # DB setup
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """Create an in-memory SQLite DB with the semantic_* tables."""
        if self._db_conn is not None:
            return
        engine = create_engine("sqlite:///:memory:", future=True)
        db_metadata.create_all(engine)
        self._db_conn = engine.connect()


# =============================================================================
# Helpers
# =============================================================================


def _link_relation(link: dict[str, Any]) -> str | None:
    """Extract the relation type from a link, handling both schema variants.

    graphify-out emits two link schemas:
    - Schema 1: ``{"relation": "documents", ...}``
    - Schema 2: ``{"type": "documents", ...}`` (with ``from``/``to`` keys)

    Returns the relation string (or None if neither key is present).
    """
    return link.get("relation") or link.get("type")


def _parse_source_location(loc: str | None) -> tuple[int | None, int | None]:
    """Parse a graphify-out ``source_location`` like ``"L23"`` → ``(23, 23)``.

    Returns ``(None, None)`` when the location is missing or unparseable.
    The format observed in real data is ``"L<n>"`` for a single line; we
    treat start_line == end_line for that case.
    """
    if not loc:
        return (None, None)
    s = loc.strip()
    if not s.startswith("L"):
        return (None, None)
    digits = s[1:]
    if not digits.isdigit():
        return (None, None)
    line = int(digits)
    return (line, line)


def _authority_scope_for_path(source_file: str) -> AuthorityScope:
    """Decision 3 (issue #100): ``public_contract`` by default.

    Source files under ``dev_guide/`` or ``community/`` are maintainer
    documentation — they describe project conventions, not public API
    contract. Downgrade to ``project_convention``.
    """
    if not source_file:
        return AuthorityScope.PUBLIC_CONTRACT
    for frag in _PROJECT_CONVENTION_PATH_FRAGMENTS:
        if frag in source_file:
            return AuthorityScope.PROJECT_CONVENTION
    return AuthorityScope.PUBLIC_CONTRACT


def _content_digest(source_node: dict[str, Any]) -> str:
    """Decision 5 (issue #100): sha256 of the source node's ``id`` field.

    The adapter does NOT read the original .rst files — ``content_digest``
    is derived from the graphify-out node id. This keeps the adapter
    self-contained (no dependency on the source repo).
    """
    node_id = source_node.get("id", "")
    return "sha256:" + hashlib.sha256(node_id.encode()).hexdigest()[:16]


def _evidence_ref_id(dataset_id: str, source_node_id: str, relation_id: str) -> str:
    """Deterministic evidence_ref_id (mirrors CodeGraph extractors/_common.py)."""
    raw = f"{dataset_id}|{source_node_id}|documentation|{relation_id}"
    return "ev:" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _relation_id(
    *,
    dataset_id: str,
    relation_kind: str,
    subject_id: str,
    object_id: str,
) -> str:
    """Deterministic relation_id (mirrors CodeGraph extractors/_common.py)."""
    raw = f"{dataset_id}|{relation_kind}|{subject_id}|{object_id}"
    return "rel:" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _repository_id_from_commit(commit: str | None) -> str:
    """Repository id derived from the build commit.

    For the DocGraph adapter, we use ``ansible-documentation`` as the
    repository id (the source repo of the documentation). The commit
    hash is preserved in ``revision.value``.
    """
    return "ansible-documentation"


def _node_source_file(node: dict[str, Any]) -> str:
    """Best-effort source_file from a node (link may not carry one)."""
    return node.get("source_file") or node.get("file") or ""


def _node_source_location(node: dict[str, Any]) -> str | None:
    """Best-effort source_location from a node (link may not carry one)."""
    return node.get("source_location")


def _derive_scope(node_id: str) -> str | None:
    """Scope = the namespace prefix of the node id (before ``:`` or ``-``).

    graphify-out node ids look like ``doc:dev_guide:testing:sanity:import``
    or ``concept:ansible-test-sanity``. The leading token (``doc``,
    ``concept``, ``topic``) is a useful scope hint for alias
    disambiguation (COMMON-005).
    """
    if ":" in node_id:
        return node_id.split(":", 1)[0]
    return None


def _monotonic_ms() -> int:
    return int(time.monotonic() * 1000)
