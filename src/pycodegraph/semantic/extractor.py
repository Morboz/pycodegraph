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
from typing import TYPE_CHECKING, TypeAlias

from ..types import Node
from .types import (
    AuthorityScope,
    CapabilityName,
    CapabilitySupport,
    DatasetRevision,
    EvidenceRef,  # noqa: F401  — re-exported for extractor authors
    ExtractionMethod,
    GraphCapabilityManifest,
    GraphDatasetManifest,
    GraphKind,
    Modality,  # noqa: F401  — re-exported for extractor authors
    RelationKind,
    RevisionMappingStatus,
    RevisionScheme,
    SemanticRelation,
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
    ) -> None:
        self._queries = queries
        self._repository_id = repository_id
        self._revision_value = revision_value
        self._instance_id = instance_id
        self._revision_scheme = revision_scheme
        self._source_revision = source_revision
        self._revision_mapping_status = revision_mapping_status

        self._registry: list[_RegisteredExtractor] = []
        self._register_default_extractors()

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
        """Register the empty stub extractors for every P1 relation.

        Each returns ``[]`` — present so the pipeline shape is exercisable
        end-to-end and so capability measurement reflects "the extractor ran
        but produced nothing" rather than "no extractor exists". Real
        extraction logic replaces these one relation at a time.
        """
        empty: _ExtractorFn = lambda _self: []  # noqa: E731
        # P1 relations (section 16 Priority 1)
        self.register_extractor(
            RelationKind.RESOLVES_SYMBOL,
            CapabilityName.SYMBOL_LOOKUP,
            AuthorityScope.IMPLEMENTATION_TOPOLOGY,
            ExtractionMethod.STATIC_ANALYSIS,
            "0.0.1",
            empty,
        )
        self.register_extractor(
            RelationKind.OWNS_CONTROL,
            CapabilityName.SIGNATURE_PARAMETER,
            AuthorityScope.IMPLEMENTATION_TOPOLOGY,
            ExtractionMethod.STATIC_ANALYSIS,
            "0.0.1",
            empty,
        )
        self.register_extractor(
            RelationKind.STORES_DEFAULT,
            CapabilityName.SIGNATURE_PARAMETER,
            AuthorityScope.IMPLEMENTATION_TOPOLOGY,
            ExtractionMethod.STATIC_ANALYSIS,
            "0.0.1",
            empty,
        )
        self.register_extractor(
            RelationKind.FORWARDS_VALUE,
            CapabilityName.VALUE_FORWARDING,
            AuthorityScope.IMPLEMENTATION_TOPOLOGY,
            ExtractionMethod.STATIC_ANALYSIS,
            "0.0.1",
            empty,
        )
        self.register_extractor(
            RelationKind.IMPLEMENTS_BEHAVIOR,
            CapabilityName.CONDITION_BEHAVIOR,
            AuthorityScope.IMPLEMENTATION_TOPOLOGY,
            ExtractionMethod.STATIC_ANALYSIS,
            "0.0.1",
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

    def build(self, built_at: int) -> SemanticBuildResult:
        """Run all registered extractors and publish the semantic layer.

        ``built_at`` is supplied by the caller (epoch seconds) so that
        :class:`GraphDatasetManifest.built_at` stays deterministic from the
        builder's perspective — the caller controls the timestamp source
        (e.g. a build system clock), not this library.
        """
        start = _monotonic_ms()
        errors: list[str] = []
        all_relations: list[SemanticRelation] = []
        extractors_run = 0

        # Extractor versions accumulate as extractors run — the manifest
        # reflects exactly what contributed to this build.
        extractor_versions: dict[str, str] = {}

        for entry in self._registry:
            extractor_key = (
                f"{entry.relation_kind.value}:{entry.extraction_method.value}"
            )
            extractor_versions[extractor_key] = entry.extractor_version
            try:
                relations = entry.fn(self)
            except Exception as exc:
                errors.append(f"extractor {extractor_key} failed: {exc!r}")
                continue
            extractors_run += 1
            all_relations.extend(relations)

        build_id = _compute_build_id(
            repository_id=self._repository_id,
            revision_value=self._revision_value,
            extractor_versions=extractor_versions,
            built_at=built_at,
        )

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
    # Helpers exposed to extractor callables
    # ------------------------------------------------------------------

    def iter_nodes(self, limit: int = 50000) -> list[Node]:
        """All indexed nodes — the raw graph the extractors run over."""
        return self._queries.get_all_nodes(limit=limit)

    def repository_id(self) -> str:
        return self._repository_id

    def dataset_id(self, build_id: str) -> str:
        """Stable dataset_id derived from build_id — used in SemanticRelation
        and EvidenceRef to tie them to this build."""
        return f"ds:{build_id}"


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
