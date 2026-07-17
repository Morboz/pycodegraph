"""Query handler for the semantic evidence layer.

Reads persisted :class:`SemanticRelation` rows (written by
:meth:`SemanticLayerBuilder.build`) and answers
:class:`SemanticGraphQuery` requests with :class:`SemanticGraphQueryResult`.

Implements the contract from section 11 of the TOCS spec:
- One query asks for one relation (QUERY-001).
- Subject resolution: the query's ``subject.name`` is a retrieval handle,
  not an assertion — we resolve it to one or more entity IDs (by node name
  here) and report the *observed* subject in each observation (QUERY-002).
- Missing relations stay explicit (QUERY-003): no lexical broadening.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .store import read_latest_dataset_manifests, read_relations
from .types import (
    CapabilityName,
    CapabilitySupport,
    GraphDatasetManifest,
    GraphKind,
    QueryDiagnostics,
    QueryStatus,
    SemanticGraphQuery,
    SemanticGraphQueryResult,
    SemanticRelation,
)

if TYPE_CHECKING:
    from ..db.queries import QueryBuilder


@runtime_checkable
class _CodeGraphLike(Protocol):
    """Structural type for what :class:`SemanticGraphQueryHandler` needs.

    Breaking the ``semantic → codegraph`` import cycle (import-linter
    ``pycodegraph/C1``). The handler only needs the QueryBuilder; callers pass
    a :class:`pycodegraph.CodeGraph` instance, which satisfies this protocol.
    """

    @property
    def _queries(self) -> QueryBuilder: ...


# Map each RelationKind to the capability it exercises (section 5.5).
# Used to populate SemanticGraphQueryResult.capability.
_RELATION_TO_CAPABILITY: dict = {
    # Filled below after RelationKind import to avoid circular-ish lookup.
}


def _build_capability_map() -> dict:
    from .types import RelationKind

    return {
        RelationKind.RESOLVES_SYMBOL: CapabilityName.SYMBOL_LOOKUP,
        RelationKind.OWNS_CONTROL: CapabilityName.SIGNATURE_PARAMETER,
        RelationKind.STORES_DEFAULT: CapabilityName.SIGNATURE_PARAMETER,
        RelationKind.READS_DEFAULT: CapabilityName.SOURCE_SLICE,
        RelationKind.EXPOSES_PUBLIC_SURFACE: CapabilityName.SIGNATURE_PARAMETER,
        RelationKind.CALLS: CapabilityName.CALL_TOPOLOGY,
        RelationKind.FORWARDS_VALUE: CapabilityName.VALUE_FORWARDING,
        RelationKind.TRANSFORMS_VALUE: CapabilityName.VALUE_FORWARDING,
        RelationKind.CONSUMES_VALUE: CapabilityName.VALUE_FORWARDING,
        RelationKind.IMPLEMENTS_BEHAVIOR: CapabilityName.CONDITION_BEHAVIOR,
        RelationKind.PRESERVES_BEHAVIOR: CapabilityName.CONDITION_BEHAVIOR,
        RelationKind.ENFORCES_PRECEDENCE: CapabilityName.PRECEDENCE,
        RelationKind.SELECTS_COMMAND: CapabilityName.COMMAND_SEMANTICS,
        RelationKind.GUARDS_EFFECT: CapabilityName.EFFECT_GUARD,
        RelationKind.CONSUMES_RETURN: CapabilityName.RETURN_CONSUMERS,
        RelationKind.TESTS_SCENARIO: CapabilityName.TEST_SCENARIO_RELATION,
        RelationKind.DOCUMENTS_CONCEPT: CapabilityName.TERM_LOOKUP,
        RelationKind.DOCUMENTS_OPTION: CapabilityName.DOCUMENTED_OPTION,
        RelationKind.DOCUMENTS_DEFAULT: CapabilityName.DOCUMENTED_DEFAULT,
        RelationKind.DOCUMENTS_BEHAVIOR: CapabilityName.DOCUMENTED_BEHAVIOR,
        RelationKind.DOCUMENTS_PRECEDENCE: CapabilityName.DOCUMENTED_PRECEDENCE,
        RelationKind.DOCUMENTS_SAFETY: CapabilityName.DOCUMENTED_SAFETY,
        RelationKind.DOCUMENTS_VALIDATION: CapabilityName.DOCUMENTED_VALIDATION,
    }


class SemanticGraphQueryHandler:
    """Answers SemanticGraphQuery requests against the persisted semantic layer.

    Stateless across calls — reads from the DB each time. Construct with a
    :class:`CodeGraph` (for its QueryBuilder connection) and call
    :meth:`query`.
    """

    def __init__(self, codegraph: _CodeGraphLike) -> None:
        self._cg = codegraph
        self._queries = codegraph._queries
        self._capability_map = _build_capability_map()

    def query(self, q: SemanticGraphQuery) -> SemanticGraphQueryResult:
        """Execute one semantic query against the persisted layer.

        Cross-graph composition (XG-001~008, issue #107): fans out across
        every dataset manifest in the DB. For each dataset whose capability
        manifest declares the requested relation's capability as
        ``SUPPORTED`` or ``PARTIAL``, reads matching relations and
        accumulates observations. Observations from different datasets are
        NOT deduplicated — overlapping or contradictory evidence stays as
        separate observations with their own ``dataset_id`` provenance
        (XG-007, XG-008).
        """
        conn = self._queries.connection
        datasets = read_latest_dataset_manifests(conn)
        if not datasets:
            return self._failure(
                q, QueryStatus.SOURCE_UNAVAILABLE, "no semantic layer built yet"
            )

        capability = self._capability_map.get(q.expected_relation)
        if capability is None:
            return self._failure(
                q,
                QueryStatus.QUERY_NOT_SUPPORTED,
                f"no capability mapped for {q.expected_relation!r}",
            )

        # Subject resolution: name → entity IDs. A short name may match many
        # nodes (same name in different scopes) — each becomes an observation
        # with its own observed subject (QUERY-002).
        candidate_ids = self._resolve_subject(q)
        examined_entities = len(candidate_ids)

        # Fan out across every dataset. Check capability support if the
        # capability manifest is addressable (the manifest's capabilities_ref
        # may not match the dataset's lookup key in all builds — fall back to
        # attempting a query regardless when that happens, rather than treating
        # an addressability issue as "capability unavailable").
        contributing_datasets: list[GraphDatasetManifest] = []
        all_observations: list[SemanticRelation] = []
        any_supported = False

        from .store import read_capability_manifest

        for ds in datasets:
            cap_manifest = read_capability_manifest(conn, ds.capabilities_ref)
            if cap_manifest is not None:
                support = cap_manifest.capabilities.get(capability)
                if support is not None and support == CapabilitySupport.UNAVAILABLE:
                    continue
                any_supported = True
            else:
                # Capability manifest not addressable by capabilities_ref —
                # attempt the query anyway (best-effort).
                any_supported = True

            # DocGraph dataset: subject_entity_id is the source_file path or
            # graphify-out node id, not resolvable via the CodeGraph `nodes`
            # table. Drop the subject filter for DocGraph datasets so the
            # query returns all matching-kind relations from that dataset;
            # the caller can filter by excerpt/object_entity_id downstream.
            # XG-005: a concept and a code symbol remain separate.
            if ds.graph_kind == GraphKind.DOC_GRAPH:
                ds_relations = read_relations(
                    conn,
                    relation_kind=q.expected_relation,
                    subject_entity_ids=None,
                    dataset_ids=[f"ds:{ds.build_id}"],
                )
            else:
                if not candidate_ids:
                    continue
                ds_relations = read_relations(
                    conn,
                    relation_kind=q.expected_relation,
                    subject_entity_ids=candidate_ids,
                    dataset_ids=[f"ds:{ds.build_id}"],
                )

            if ds_relations:
                contributing_datasets.append(ds)
                all_observations.extend(ds_relations)

        if not any_supported:
            return self._build_result(
                q,
                QueryStatus.QUERY_NOT_SUPPORTED,
                capability=capability,
                served_datasets=datasets,
                observations=[],
                examined_entities=examined_entities,
                examined_relations=0,
                limitations=[
                    f"capability {capability.value} is unavailable in all "
                    f"{len(datasets)} persisted dataset(s)"
                ],
            )

        if not all_observations:
            return self._build_result(
                q,
                QueryStatus.NO_MATCHING_EVIDENCE,
                capability=capability,
                served_datasets=datasets,
                observations=[],
                examined_entities=examined_entities,
                examined_relations=0,
            )

        return self._build_result(
            q,
            QueryStatus.SUCCEEDED,
            capability=capability,
            served_datasets=contributing_datasets,
            observations=all_observations,
            examined_entities=examined_entities,
            examined_relations=len(all_observations),
        )

    def _build_result(
        self,
        q: SemanticGraphQuery,
        status: QueryStatus,
        *,
        capability: CapabilityName,
        served_datasets: list,
        observations: list[SemanticRelation],
        examined_entities: int,
        examined_relations: int,
        limitations: list[str] | None = None,
    ) -> SemanticGraphQueryResult:
        """Construct a SemanticGraphQueryResult with both served_dataset and
        served_datasets populated (XG-001~008 backward compat)."""
        # served_dataset (legacy) = first served_dataset, or a placeholder.
        if served_datasets:
            primary = served_datasets[0]
        else:
            primary = self._placeholder_dataset(q)

        return SemanticGraphQueryResult(
            status=status,
            served_dataset=primary,
            capability=capability,
            observations=observations,
            diagnostics=QueryDiagnostics(
                examined_entities=examined_entities,
                examined_relations=examined_relations,
                limitations=limitations or [],
            ),
            served_datasets=served_datasets,
        )

    def _placeholder_dataset(self, q: SemanticGraphQuery):
        """Fabricate a minimal dataset manifest when no real one is available."""
        from .types import (
            DatasetRevision,
            GraphDatasetManifest,
            GraphKind,
            RevisionMappingStatus,
            RevisionScheme,
        )

        return GraphDatasetManifest(
            instance_id="unbuilt",
            graph_kind=GraphKind.CODE_GRAPH,
            repository_id=q.repository_id,
            revision=DatasetRevision(
                scheme=RevisionScheme.GIT_COMMIT,
                value=q.requested_revision,
                mapping_status=RevisionMappingStatus.UNKNOWN,
            ),
            build_id="unbuilt",
            built_at=0,
            schema_version="0",
            extractor_versions={},
            capabilities_ref="unbuilt",
        )

    # ------------------------------------------------------------------
    # Subject resolution
    # ------------------------------------------------------------------

    def _resolve_subject(self, q: SemanticGraphQuery) -> list[str]:
        """Resolve subject.name (+ aliases) to entity (node) IDs.

        Uses the raw nodes table — the semantic layer's entity_id mirrors
        node.id (see extractors/_common.entity_for_node). kind_hint and
        scope_hint narrow the candidate set when provided.
        """
        from ..types import NodeKind

        names = {q.subject.name, *q.subject.aliases}
        candidate_ids: list[str] = []
        for name in names:
            for node in self._queries.get_nodes_by_name(name):
                if self._matches_hints(node, q, NodeKind):
                    candidate_ids.append(node.id)
        # Dedupe preserving order (deterministic — QUERY-004).
        seen: set[str] = set()
        unique: list[str] = []
        for cid in candidate_ids:
            if cid not in seen:
                seen.add(cid)
                unique.append(cid)
        return unique

    def _matches_hints(self, node, q: SemanticGraphQuery, NodeKind) -> bool:
        """Apply kind_hint and scope_hint filters if present."""
        if (
            q.subject.kind_hint is not None
            and node.kind
            not in _ENTITY_KIND_TO_NODE_KINDS.get(q.subject.kind_hint, set())
        ):
            # Map EntityKind back to the NodeKind values that map to it.
            return False
        if q.subject.scope_hint is not None:
            # scope_hint matches if the node's qualified_name starts with it
            # (scope is the parent chain) or equals it.
            return bool(
                node.qualified_name == q.subject.scope_hint
                or node.qualified_name.startswith(q.subject.scope_hint + "::")
                or _node_scope(node) == q.subject.scope_hint
            )
        return True

    # ------------------------------------------------------------------
    # Failure helper
    # ------------------------------------------------------------------

    def _failure(
        self,
        q: SemanticGraphQuery,
        status: QueryStatus,
        reason: str,
    ) -> SemanticGraphQueryResult:
        from .store import read_latest_dataset_manifest

        dataset = read_latest_dataset_manifest(self._queries.connection)
        if dataset is None:
            # Truly nothing built — fabricate a minimal placeholder so the
            # result type is satisfied. Real callers should build first.
            from .types import (
                DatasetRevision,
                GraphDatasetManifest,
                GraphKind,
                RevisionMappingStatus,
                RevisionScheme,
            )

            dataset = GraphDatasetManifest(
                instance_id="unbuilt",
                graph_kind=GraphKind.CODE_GRAPH,
                repository_id=q.repository_id,
                revision=DatasetRevision(
                    scheme=RevisionScheme.GIT_COMMIT,
                    value=q.requested_revision,
                    mapping_status=RevisionMappingStatus.UNKNOWN,
                ),
                build_id="unbuilt",
                built_at=0,
                schema_version="0",
                extractor_versions={},
                capabilities_ref="unbuilt",
            )
        capability = self._capability_map.get(
            q.expected_relation, CapabilityName.TERM_LOOKUP
        )
        return SemanticGraphQueryResult(
            status=status,
            served_dataset=dataset,
            capability=capability,
            observations=[],
            diagnostics=QueryDiagnostics(limitations=[reason]),
            served_datasets=[dataset] if dataset.instance_id != "unbuilt" else [],
        )


def _node_scope(node) -> str | None:
    if "::" in node.qualified_name:
        return node.qualified_name.rsplit("::", 1)[0]
    return None


def read_latest_dataset_manifest_safely(conn):
    """Wrapper that imports lazily to avoid a circular import at module load."""
    from .store import read_latest_dataset_manifest

    return read_latest_dataset_manifest(conn)


# EntityKind → set of NodeKind values that map to it (for subject filtering).
# Built once at module load from the extractors' canonical mapping.
from ..types import NodeKind as _NK  # noqa: E402
from .extractors._common import _NODE_KIND_TO_ENTITY_KIND  # noqa: E402
from .types import EntityKind as _EntityKind  # noqa: E402

_ENTITY_KIND_TO_NODE_KINDS: dict[_EntityKind, set[_NK]] = {}
for _nk_str, _ek in _NODE_KIND_TO_ENTITY_KIND.items():
    _nk_enum = next((nk for nk in _NK if nk.value == _nk_str), None)
    if _nk_enum is not None:
        _ENTITY_KIND_TO_NODE_KINDS.setdefault(_ek, set()).add(_nk_enum)
