"""Build pipeline for the semantic evidence layer.

Produces :class:`GraphDatasetManifest`, :class:`GraphCapabilityManifest`, and
typed :class:`SemanticRelation` rows by running relation-specific extractors
over the raw graph built by :class:`pycodegraph.extraction.ExtractionOrchestrator`.

This is the offline "relation-specific extraction" + "capability measurement"
+ "dataset manifest publication" stages from section 12.1 of the TOCS
contract. It is intentionally **not** wired into :meth:`CodeGraph.index_all`:
callers opt in via :meth:`CodeGraph.build_semantic_layer` so existing users
are unaffected while the contract layer matures.

Current state: skeleton only. ``_RelationExtractor`` callables return empty
lists; ``_CapabilityMeasurer`` returns ``UNAVAILABLE`` for every capability.
The shape of the pipeline is what is being reviewed — the real extraction
logic is filled in incrementally per relation kind.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeAlias

from ..types import InlineFact, Node
from .types import (
    AuthorityScope,
    CapabilityName,
    CapabilitySupport,
    DatasetRevision,
    EntityKind,
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
    SemanticRelation,
    SourceLocator,
)

if TYPE_CHECKING:
    from ..db.queries import QueryBuilder


# =============================================================================
# Build result
# =============================================================================


@dataclass
class SemanticBuildResult:
    """Outcome of one :meth:`build_semantic_layer` run.

    Mirrors the shape of :class:`pycodegraph.types.IndexResult`: counts plus
    a success flag. The manifests themselves are persisted to the graph and
    also returned for the caller's convenience (and for any acceptance query
    suite that wants to inspect them without re-reading the DB).
    """

    success: bool
    build_id: str
    dataset_manifest: GraphDatasetManifest
    capability_manifest: GraphCapabilityManifest
    relations_emitted: int = 0
    extractors_run: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0


# =============================================================================
# Relation extractor registry
# =============================================================================


_ExtractorFn: TypeAlias = Callable[["SemanticLayerBuilder"], list[SemanticRelation]]


@dataclass
class _RegisteredExtractor:
    """One typed-relation extractor entry in the registry."""

    relation_kind: RelationKind
    capability: CapabilityName
    authority_scope: AuthorityScope
    extraction_method: ExtractionMethod
    extractor_version: str
    fn: _ExtractorFn


class SemanticLayerBuilder:
    """Runs relation-specific extractors over a CodeGraph DB and publishes
    the semantic evidence layer (manifest + typed relations).

    Lifecycle: construct once per build, call :meth:`build` once. The builder
    is single-use — extractor state accumulates during the run.
    """

    def __init__(
        self,
        queries: QueryBuilder,
        *,
        repository_id: str,
        revision_value: str,
        instance_id: str = "default",
        revision_scheme: RevisionScheme = RevisionScheme.GIT_COMMIT,
        source_revision: str | None = None,
        revision_mapping_status: RevisionMappingStatus = RevisionMappingStatus.EXACT,
        file_provider: Any = None,
    ) -> None:
        self._queries = queries
        self._repository_id = repository_id
        self._revision_value = revision_value
        self._instance_id = instance_id
        self._revision_scheme = revision_scheme
        self._source_revision = source_revision
        self._revision_mapping_status = revision_mapping_status
        # Optional FileProvider (issue #116 READS_DEFAULT extractor reads
        # caller source to count positional args at the call site).
        self._file_provider = file_provider

        self._registry: list[_RegisteredExtractor] = []
        self._build_id: str | None = None
        self._register_default_extractors()

    @property
    def file_provider(self) -> Any:
        """Optional FileProvider for source reads (None when not configured)."""
        return self._file_provider

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    def register_extractor(
        self,
        relation_kind: RelationKind,
        capability: CapabilityName,
        authority_scope: AuthorityScope,
        extraction_method: ExtractionMethod,
        extractor_version: str,
        fn: _ExtractorFn,
    ) -> None:
        """Register an extractor for one typed relation.

        Multiple extractors may target the same ``relation_kind`` (e.g. one
        for signatures, one for structured option schemas). They are run in
        registration order; their outputs are concatenated.
        """
        self._registry.append(
            _RegisteredExtractor(
                relation_kind=relation_kind,
                capability=capability,
                authority_scope=authority_scope,
                extraction_method=extraction_method,
                extractor_version=extractor_version,
                fn=fn,
            )
        )

    def _register_default_extractors(self) -> None:
        """Register extractors for every P1 relation.

        Three relations have real extractors backed by the raw graph:
        CALLS (← EdgeKind.CALLS), OWNS_CONTROL (← PARAMETER + CONTAINS),
        EXPOSES_PUBLIC_SURFACE (← public owner + PARAMETER). The rest remain
        stubs returning ``[]`` — present so capability measurement reflects
        "the extractor ran but produced nothing" rather than "no extractor
        exists". Real extraction logic replaces these one relation at a time.
        """
        empty: _ExtractorFn = lambda _self: []  # noqa: E731
        # --- Real extractors (raw-graph-backed) ---
        from .extractors import (
            extract_calls,
            extract_exposes_public_surface,
            extract_forwards_value,
            extract_owns_control,
            extract_reads_default,
        )

        self.register_extractor(
            RelationKind.CALLS,
            CapabilityName.CALL_TOPOLOGY,
            AuthorityScope.IMPLEMENTATION_TOPOLOGY,
            ExtractionMethod.STATIC_ANALYSIS,
            "0.1.0",
            extract_calls,
        )
        self.register_extractor(
            RelationKind.OWNS_CONTROL,
            CapabilityName.SIGNATURE_PARAMETER,
            AuthorityScope.IMPLEMENTATION_TOPOLOGY,
            ExtractionMethod.STATIC_ANALYSIS,
            "0.1.0",
            extract_owns_control,
        )
        self.register_extractor(
            RelationKind.EXPOSES_PUBLIC_SURFACE,
            CapabilityName.SIGNATURE_PARAMETER,
            AuthorityScope.PUBLIC_CONTRACT,
            ExtractionMethod.STATIC_ANALYSIS,
            "0.1.0",
            extract_exposes_public_surface,
        )
        # --- Stub extractors (P1 relations not yet implemented) ---
        self.register_extractor(
            RelationKind.RESOLVES_SYMBOL,
            CapabilityName.SYMBOL_LOOKUP,
            AuthorityScope.IMPLEMENTATION_TOPOLOGY,
            ExtractionMethod.STATIC_ANALYSIS,
            "0.0.1",
            empty,
        )
        self.register_extractor(
            RelationKind.STORES_DEFAULT,
            CapabilityName.SIGNATURE_PARAMETER,
            AuthorityScope.IMPLEMENTATION_TOPOLOGY,
            ExtractionMethod.PARSER,
            "inline-xg-114-1",
            # STORES_DEFAULT is produced by the InlineFact pipeline via the
            # Python extract_inline_facts hook (issue #115). The registered
            # extractor is a no-op here — the real data arrives via
            # build_semantic_layer(inline_facts=...).
            empty,
        )
        self.register_extractor(
            RelationKind.READS_DEFAULT,
            CapabilityName.SOURCE_SLICE,
            AuthorityScope.IMPLEMENTATION_TOPOLOGY,
            ExtractionMethod.STATIC_ANALYSIS,
            "xg-116-1",
            extract_reads_default,
        )
        self.register_extractor(
            RelationKind.FORWARDS_VALUE,
            CapabilityName.VALUE_FORWARDING,
            AuthorityScope.IMPLEMENTATION_TOPOLOGY,
            ExtractionMethod.PARSER,
            "inline-xg-118-1",
            # FORWARDS_VALUE (intra-procedural) is produced by the InlineFact
            # pipeline via the Python extract_inline_facts hook (issue #118).
            # The registered extractor is a no-op here — the real data for
            # intra-proc arrives via build_semantic_layer(inline_facts=...).
            empty,
        )
        self.register_extractor(
            RelationKind.FORWARDS_VALUE,
            CapabilityName.VALUE_FORWARDING,
            AuthorityScope.IMPLEMENTATION_TOPOLOGY,
            ExtractionMethod.STATIC_ANALYSIS,
            "xg-120-1",
            # FORWARDS_VALUE (inter-procedural) is produced by a registered
            # extractor that walks CALLS edges + caller source to find
            # cross-function parameter forwarding (issue #120).
            extract_forwards_value,
        )
        self.register_extractor(
            RelationKind.IMPLEMENTS_BEHAVIOR,
            CapabilityName.CONDITION_BEHAVIOR,
            AuthorityScope.IMPLEMENTATION_TOPOLOGY,
            ExtractionMethod.PARSER,
            "inline-xg-117-1",
            # IMPLEMENTS_BEHAVIOR is produced by the InlineFact pipeline
            # via the Python extract_inline_facts hook (issue #117).
            # The registered extractor is a no-op here — the real data
            # arrives via build_semantic_layer(inline_facts=...).
            empty,
        )
        self.register_extractor(
            RelationKind.GUARDS_EFFECT,
            CapabilityName.EFFECT_GUARD,
            AuthorityScope.IMPLEMENTATION_TOPOLOGY,
            ExtractionMethod.PARSER,
            "inline-xg-119-1",
            # GUARDS_EFFECT is produced by the InlineFact pipeline via the
            # Python extract_inline_facts hook (issue #119). The registered
            # extractor is a no-op here — the real data arrives via
            # build_semantic_layer(inline_facts=...).
            empty,
        )
        self.register_extractor(
            RelationKind.TESTS_SCENARIO,
            CapabilityName.TEST_SCENARIO_RELATION,
            AuthorityScope.OBSERVABLE_COMPATIBILITY,
            ExtractionMethod.STATIC_ANALYSIS,
            "0.0.1",
            empty,
        )

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(
        self,
        built_at: int,
        inline_facts: list[InlineFact] | None = None,
    ) -> SemanticBuildResult:
        """Run all registered extractors and publish the semantic layer.

        ``built_at`` is supplied by the caller (epoch seconds) so that
        :class:`GraphDatasetManifest.built_at` stays deterministic from the
        builder's perspective — the caller controls the timestamp source
        (e.g. a build system clock), not this library.

        ``inline_facts`` (issue #114, optional) are typed facts collected
        during Tree-sitter traversal by a ``LanguageExtractor.extract_inline_facts``
        hook. They are flushed to :class:`SemanticRelation` rows **before**
        registered extractors run, so that :meth:`_measure_capabilities` sees
        all relation kinds.

        Persists relations + manifests to the semantic tables via
        :mod:`pycodegraph.semantic.store` so the query handler can read them
        back in a later call (or a different process).
        """
        from .extractors._common import relation_id as _mk_relation_id
        from .store import (
            write_capability_manifest,
            write_dataset_manifest,
            write_relations,
        )

        start = _monotonic_ms()
        errors: list[str] = []
        all_relations: list[SemanticRelation] = []
        extractors_run = 0

        # Extractor versions are known up-front from the registry; build_id
        # depends on them + repository + revision + timestamp, so we can
        # compute it before running extractors. That lets extractors reference
        # the dataset_id (derived from build_id) in their output.
        extractor_versions: dict[str, str] = {
            f"{e.relation_kind.value}:{e.extraction_method.value}": e.extractor_version
            for e in self._registry
        }
        build_id = _compute_build_id(
            repository_id=self._repository_id,
            revision_value=self._revision_value,
            extractor_versions=extractor_versions,
            built_at=built_at,
        )
        self._build_id = build_id

        # Issue #114: flush inline_facts before registered extractors, so
        # _measure_capabilities sees all relation kinds (QUERY DESIGN).
        # Extractor versions are mapped per relation-kind via a lookup since
        # different InlineFact types (STORES_DEFAULT, IMPLEMENTS_BEHAVIOR, …)
        # may have distinct version strings.
        _INLINE_FACT_VERSIONS: dict[str, str] = {
            "stores_default": "inline-xg-114-1",
            "implements_behavior": "inline-xg-117-1",
            "forwards_value": "inline-xg-118-1",
            "guards_effect": "inline-xg-119-1",
        }
        if inline_facts:
            ds_id = self.dataset_id()
            repo_id = self._repository_id
            rev = self._revision_value
            for fact in inline_facts:
                subject_id = _inline_subject_id(fact)
                lit_obj = fact.object_literal
                obj_id = fact.object_node_id
                # InlineFact stores kind/method strings loosely at the
                # extraction layer (avoiding semantic-types import there);
                # resolve to the SemanticRelation enums for storage.
                rk = RelationKind(fact.relation_kind)
                em = ExtractionMethod(
                    fact.extraction_method or ExtractionMethod.PARSER.value
                )
                ek = EvidenceKind(fact.evidence_kind or EvidenceKind.SOURCE.value)
                rid = _mk_relation_id(rk.value, subject_id, obj_id, lit_obj, ds_id)
                all_relations.append(
                    SemanticRelation(
                        relation_id=rid,
                        subject_entity_id=subject_id,
                        relation_kind=rk,
                        authority_scope=AuthorityScope.IMPLEMENTATION_TOPOLOGY,
                        modality=Modality.OBSERVED,
                        extraction_method=em,
                        extractor_version=_INLINE_FACT_VERSIONS.get(
                            fact.relation_kind, "inline-xg-114-1"
                        ),
                        dataset_id=ds_id,
                        evidence_refs=[
                            EvidenceRef(
                                evidence_ref_id=f"ev:{rid[4:]}_inline",
                                evidence_kind=ek,
                                repository_id=repo_id,
                                revision=rev,
                                locator=SourceLocator(
                                    path_or_document_id=fact.subject_file_path,
                                    start_line=fact.start_line,
                                    end_line=fact.end_line,
                                    symbol_or_section=fact.subject_qualified_name,
                                ),
                                content_digest=_inline_digest(fact, ds_id),
                                dataset_id=ds_id,
                            )
                        ],
                        object_entity_id=obj_id,
                        literal_object=lit_obj,
                        # InlineFact.metadata is preserved in condition_expression
                        # (JSON) so downstream extractors can read it back. Used by
                        # READS_DEFAULT to find the parameter_name of STORES_DEFAULT
                        # relations (issue #116).
                        condition_expression=dict(fact.metadata)
                        if fact.metadata
                        else None,
                        confidence=1.0,
                    )
                )

        for entry in self._registry:
            extractor_key = (
                f"{entry.relation_kind.value}:{entry.extraction_method.value}"
            )
            try:
                relations = entry.fn(self)
            except Exception as exc:
                errors.append(f"extractor {extractor_key} failed: {exc!r}")
                continue
            extractors_run += 1
            all_relations.extend(relations)

        dataset_manifest = GraphDatasetManifest(
            instance_id=self._instance_id,
            graph_kind=GraphKind.CODE_GRAPH,
            repository_id=self._repository_id,
            revision=DatasetRevision(
                scheme=self._revision_scheme,
                value=self._revision_value,
                mapping_status=self._revision_mapping_status,
                source_revision=self._source_revision,
            ),
            build_id=build_id,
            built_at=built_at,
            schema_version=_SCHEMA_VERSION,
            extractor_versions=extractor_versions,
            capabilities_ref=f"{build_id}:capabilities",
        )

        capability_manifest = self._measure_capabilities(all_relations)

        # Persist (decision A: dedicated semantic_* tables).
        conn = self._queries.connection
        # Connection is already in autobegin mode — no explicit begin() needed.
        write_dataset_manifest(conn, dataset_manifest)
        write_capability_manifest(conn, capability_manifest)
        write_relations(conn, all_relations)

        # Persist entities (XG-003: independent semantic_entities table).
        entities = self._collect_entities(all_relations)
        from .store import write_entities as _write_entities

        _write_entities(conn, entities)

        duration_ms = _monotonic_ms() - start
        return SemanticBuildResult(
            success=not errors,
            build_id=build_id,
            dataset_manifest=dataset_manifest,
            capability_manifest=capability_manifest,
            relations_emitted=len(all_relations),
            extractors_run=extractors_run,
            errors=errors,
            duration_ms=duration_ms,
        )

    # ------------------------------------------------------------------
    # Capability measurement (BUILD-004)
    # ------------------------------------------------------------------

    def _measure_capabilities(
        self, relations: list[SemanticRelation]
    ) -> GraphCapabilityManifest:
        """Measure support for each capability by counting emitted relations.

        BUILD-004: capability status is measured by acceptance queries, not a
        manually optimistic declaration. This skeleton measures by relation
        count — a capability is ``supported`` if at least one relation with
        provenance was emitted for it this build, ``unavailable`` otherwise.

        ``partial`` is not produced by this skeleton; real extractors that
        cover some languages but not others will set ``partial`` with a
        machine-readable limitation (COMMON-019).
        """
        produced: set[CapabilityName] = {
            self._registry_entry_for(r.relation_kind).capability
            for r in relations
            if r.evidence_refs  # COMMON-013: relation needs evidence to count
        }

        capabilities: dict[CapabilityName, CapabilitySupport] = {}
        for cap in CapabilityName:
            if cap in produced:
                capabilities[cap] = CapabilitySupport.SUPPORTED
            else:
                capabilities[cap] = CapabilitySupport.UNAVAILABLE
        return GraphCapabilityManifest(
            instance_id=self._instance_id,
            schema_version=_SCHEMA_VERSION,
            capabilities=capabilities,
            limitations=[],
        )

    def _registry_entry_for(self, relation_kind: RelationKind) -> _RegisteredExtractor:
        for entry in self._registry:
            if entry.relation_kind == relation_kind:
                return entry
        raise KeyError(f"no extractor registered for relation {relation_kind!r}")

    # ------------------------------------------------------------------
    # Entity collection (XG-003)
    # ------------------------------------------------------------------

    def _collect_entities(
        self, relations: list[SemanticRelation]
    ) -> list[SemanticEntity]:
        """Collect all entities referenced by this build.

        Sources:
        1. All raw graph nodes mapped via ``entity_for_node``.
        2. Any ``subject_entity_id`` or ``object_entity_id`` from relations
           that don't correspond to a raw node (future-proofing).

        Entities are deduplicated by ``entity_id``.
        """
        from .extractors._common import entity_for_node

        seen: set[str] = set()
        result: list[SemanticEntity] = []

        # 1. Map all raw graph nodes.
        for node in self.iter_nodes():
            entity = entity_for_node(node, self.dataset_id(), self._repository_id)
            if entity is not None and entity.entity_id not in seen:
                seen.add(entity.entity_id)
                result.append(entity)

        # 2. Any relation subject/object IDs not yet covered.
        for rel in relations:
            for eid in (rel.subject_entity_id, rel.object_entity_id):
                if eid is not None and eid not in seen:
                    # Create a minimal placeholder entity so the ID is
                    # resolvable by the query handler.
                    seen.add(eid)
                    result.append(
                        SemanticEntity(
                            entity_id=eid,
                            repository_id=self._repository_id,
                            entity_kind=EntityKind.DOCUMENT_SECTION,
                            canonical_name=eid,
                            dataset_id=self.dataset_id(),
                        )
                    )

        return result

    # ------------------------------------------------------------------
    # Helpers exposed to extractor callables
    # ------------------------------------------------------------------

    def queries(self) -> QueryBuilder:
        """The raw-graph QueryBuilder — extractors read nodes/edges from it."""
        return self._queries

    def iter_nodes(self, limit: int = 50000) -> list[Node]:
        """All indexed nodes — the raw graph the extractors run over."""
        return self._queries.get_all_nodes(limit=limit)

    def repository_id(self) -> str:
        return self._repository_id

    def revision_value(self) -> str:
        return self._revision_value

    def dataset_id(self) -> str:
        """Stable dataset_id for the current build.

        Derived from build_id (set during :meth:`build`), so extractors can
        stamp it onto every SemanticRelation and EvidenceRef they emit.
        """
        if self._build_id is None:
            raise RuntimeError("dataset_id() called before build()")
        return f"ds:{self._build_id}"


# =============================================================================
# Build ID + schema version (COMMON-004, BUILD-005)
# =============================================================================


_SCHEMA_VERSION = "0.1.0-tocs"


def _compute_build_id(
    *,
    repository_id: str,
    revision_value: str,
    extractor_versions: dict[str, str],
    built_at: int,
) -> str:
    """Deterministic build_id from inputs (COMMON-004: rebuild → new id).

    Two builds with identical repository + revision + extractor versions +
    timestamp produce the same id. Changing any of those produces a different
    id. A rebuild at a later timestamp produces a new id, which is the
    normal rebuild path satisfying COMMON-004.
    """
    payload = "|".join(
        [
            repository_id,
            revision_value,
            _stable_version_map(extractor_versions),
            str(built_at),
        ]
    )
    return "build:" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def _stable_version_map(versions: dict[str, str]) -> str:
    return ",".join(f"{k}={v}" for k, v in sorted(versions.items()))


def _monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


# =============================================================================
# InlineFact helpers (issue #114)
# =============================================================================


def _inline_subject_id(fact: InlineFact) -> str:
    """Compute a stable subject_entity_id from an InlineFact.

    When the subject is a CodeGraph Node (subject_node_id is set), use it
    directly. For call-site / other non-node subjects, synthesize an ID
    from qualified_name and line.
    """
    if fact.subject_node_id:
        return fact.subject_node_id
    if fact.start_line > 0:
        return f"{fact.subject_qualified_name}::L{fact.start_line}"
    return fact.subject_qualified_name


def _inline_digest(fact: InlineFact, dataset_id: str) -> str:
    import hashlib as _hl

    raw = f"{dataset_id}|{fact.subject_file_path}|{fact.start_line}|{fact.end_line}|{fact.object_literal}"
    return "sha256:" + _hl.sha256(raw.encode()).hexdigest()[:16]
