"""TOCS semantic evidence contract — provider-neutral types for the
CodeGraph/DocGraph query interface.

Implements the contract in
``TOCS_CodeGraph_DocGraph_Offline_Evidence_Enhancement_Requirements_v0.1.md``
(sections 2, 5, 6, 11, 13). This is the public boundary between the offline
graph (this project) and the online symbolic-grounding stage (FormSy).

These types are deliberately distinct from :mod:`pycodegraph.types`
(Node/Edge/DataflowEdge): those are backend-specific raw graph facts, these
are provider-neutral evidence. The mapping between the two is an
implementation concern, not part of the contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# =============================================================================
# Enumerations (sections 2, 5)
# =============================================================================


class GraphKind(StrEnum):
    """Section 5.1 — kind of graph dataset."""

    CODE_GRAPH = "code_graph"
    DOC_GRAPH = "doc_graph"


class EntityKind(StrEnum):
    """Section 5.2 — semantic entity kinds.

    Distinct from :class:`pycodegraph.types.NodeKind`: the contract layer adds
    option/command/effect/behavior/scenario/project_concept kinds that the raw
    graph does not store as first-class nodes, and omits backend-only kinds
    (file, struct, trait, protocol, …). The mapping is an adapter concern.
    """

    PROJECT_CONCEPT = "project_concept"
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    PARAMETER = "parameter"
    OPTION = "option"
    FIELD = "field"
    HEADER = "header"
    COMMAND = "command"
    EFFECT = "effect"
    BEHAVIOR = "behavior"
    SCENARIO = "scenario"
    TEST = "test"
    DOCUMENT_SECTION = "document_section"


class AliasKind(StrEnum):
    """Section 5.2 — alias classification."""

    EXACT = "exact"
    SHORT_NAME = "short_name"
    DOCUMENTATION_TERM = "documentation_term"
    NORMALIZED_TERM = "normalized_term"


class RelationKind(StrEnum):
    """Section 6 — canonical relation vocabulary (23 relations).

    Grouped by section: subject/interface (6.1), propagation (6.2),
    behavior/compatibility (6.3), validation/documentation (6.4).
    """

    # 6.1 Subject & Interface
    RESOLVES_SYMBOL = "resolves_symbol"
    OWNS_CONTROL = "owns_control"
    STORES_DEFAULT = "stores_default"
    READS_DEFAULT = "reads_default"
    EXPOSES_PUBLIC_SURFACE = "exposes_public_surface"
    # 6.2 Propagation
    CALLS = "calls"
    FORWARDS_VALUE = "forwards_value"
    TRANSFORMS_VALUE = "transforms_value"
    CONSUMES_VALUE = "consumes_value"
    # 6.3 Behavior & Compatibility
    IMPLEMENTS_BEHAVIOR = "implements_behavior"
    PRESERVES_BEHAVIOR = "preserves_behavior"
    ENFORCES_PRECEDENCE = "enforces_precedence"
    SELECTS_COMMAND = "selects_command"
    GUARDS_EFFECT = "guards_effect"
    CONSUMES_RETURN = "consumes_return"
    # 6.4 Validation & Documentation
    TESTS_SCENARIO = "tests_scenario"
    DOCUMENTS_CONCEPT = "documents_concept"
    DOCUMENTS_OPTION = "documents_option"
    DOCUMENTS_DEFAULT = "documents_default"
    DOCUMENTS_BEHAVIOR = "documents_behavior"
    DOCUMENTS_PRECEDENCE = "documents_precedence"
    DOCUMENTS_SAFETY = "documents_safety"
    DOCUMENTS_VALIDATION = "documents_validation"


class Modality(StrEnum):
    """Section 5.3 — relation modality."""

    OBSERVED = "observed"
    DOCUMENTED = "documented"
    REQUIRED = "required"
    PROHIBITED = "prohibited"


class AuthorityScope(StrEnum):
    """Section 2.4 — what a source can establish.

    A graph hit must not be promoted outside its authority (section 3.3:
    runtime contract must not promote structural evidence to a public
    obligation).
    """

    IMPLEMENTATION_TOPOLOGY = "implementation_topology"
    OBSERVABLE_COMPATIBILITY = "observable_compatibility"
    PUBLIC_CONTRACT = "public_contract"
    PROJECT_CONVENTION = "project_convention"
    EXTERNAL_PROTOCOL = "external_protocol"


class EvidenceKind(StrEnum):
    """Section 5.4 — kind of evidence backing a relation."""

    SOURCE = "source"
    TEST = "test"
    DOCUMENTATION = "documentation"
    CLAIM = "claim"


class ExtractionMethod(StrEnum):
    """Section 5.3 — how a relation was extracted."""

    PARSER = "parser"
    STATIC_ANALYSIS = "static_analysis"
    STRUCTURED_DOCUMENT = "structured_document"
    MODEL_EXTRACTION = "model_extraction"
    HUMAN_CURATED = "human_curated"


class RevisionScheme(StrEnum):
    """Section 5.1 — revision identifier scheme."""

    GIT_COMMIT = "git_commit"
    RELEASE = "release"
    DOCUMENT_SNAPSHOT = "document_snapshot"


class RevisionMappingStatus(StrEnum):
    """Section 5.1 — alignment between graph revision and source revision."""

    EXACT = "exact"
    VERIFIED_MAPPING = "verified_mapping"
    UNKNOWN = "unknown"


class CapabilitySupport(StrEnum):
    """Section 5.5 — support level for a capability."""

    SUPPORTED = "supported"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class CapabilityName(StrEnum):
    """Section 5.5 — the 18 capability keys a manifest must declare."""

    TERM_LOOKUP = "term_lookup"
    SYMBOL_LOOKUP = "symbol_lookup"
    SOURCE_SLICE = "source_slice"
    SIGNATURE_PARAMETER = "signature_parameter"
    CALL_TOPOLOGY = "call_topology"
    VALUE_FORWARDING = "value_forwarding"
    CONDITION_BEHAVIOR = "condition_behavior"
    COMMAND_SEMANTICS = "command_semantics"
    EFFECT_GUARD = "effect_guard"
    PRECEDENCE = "precedence"
    RETURN_CONSUMERS = "return_consumers"
    TEST_SCENARIO_RELATION = "test_scenario_relation"
    DOCUMENTED_OPTION = "documented_option"
    DOCUMENTED_DEFAULT = "documented_default"
    DOCUMENTED_BEHAVIOR = "documented_behavior"
    DOCUMENTED_PRECEDENCE = "documented_precedence"
    DOCUMENTED_SAFETY = "documented_safety"
    DOCUMENTED_VALIDATION = "documented_validation"


class QueryStatus(StrEnum):
    """Section 2.5 / 11 — terminal status of a semantic query."""

    SUCCEEDED = "succeeded"
    NO_MATCHING_EVIDENCE = "no_matching_evidence"
    QUERY_NOT_SUPPORTED = "query_not_supported"
    SCHEMA_CAPABILITY_MISSING = "schema_capability_missing"
    REVISION_MISMATCH = "revision_mismatch"
    SOURCE_UNAVAILABLE = "source_unavailable"
    BUDGET_EXHAUSTED = "budget_exhausted"


class EvidenceStrength(StrEnum):
    """Section 2.3 — what a result proves.

    Assigned by the online directness gate after receiving observations, not
    by the graph. Shared here so both sides use one vocabulary.
    """

    DIRECT = "direct"
    STRUCTURAL = "structural"
    CONTEXTUAL = "contextual"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"


class CoverageGapReason(StrEnum):
    """Section 13 — reason an online coverage gap was recorded."""

    SOURCE_UNAVAILABLE = "source_unavailable"
    REVISION_MISMATCH = "revision_mismatch"
    BUDGET_EXHAUSTED = "budget_exhausted"
    QUERY_NOT_SUPPORTED = "query_not_supported"
    SCHEMA_CAPABILITY_MISSING = "schema_capability_missing"
    NO_MATCHING_EVIDENCE = "no_matching_evidence"
    STRUCTURAL_ONLY = "structural_only"
    CONTEXTUAL_ONLY = "contextual_only"


# =============================================================================
# Dataset identity (sections 5.1, 5.5)
# =============================================================================


@dataclass
class DatasetRevision:
    """Section 5.1 — revision block of a GraphDatasetManifest.

    COMMON-002: git revisions must use a full commit id when available.
    COMMON-003: a document snapshot without verified source alignment must
    not claim ``exact``.
    """

    scheme: RevisionScheme
    value: str
    mapping_status: RevisionMappingStatus
    source_revision: str | None = None


@dataclass
class GraphDatasetManifest:
    """Section 5.1 — versioned identity of one graph build.

    COMMON-001: every query result must identify its dataset and build.
    COMMON-004: a rebuild must produce a new immutable ``build_id``.
    """

    instance_id: str
    graph_kind: GraphKind
    repository_id: str
    revision: DatasetRevision
    build_id: str
    built_at: int  # epoch seconds; builds are deterministic, callers stamp
    schema_version: str
    extractor_versions: dict[str, str]
    capabilities_ref: str


@dataclass
class GraphCapabilityManifest:
    """Section 5.5 — truthful declaration of which semantic queries a graph
    instance can answer.

    COMMON-017: ``supported`` requires the graph to return the typed relation
    with provenance. COMMON-018: broad full-text claims do not satisfy a
    capability. COMMON-019: ``partial`` must carry a machine-readable or
    documented limitation.
    """

    instance_id: str
    schema_version: str
    capabilities: dict[CapabilityName, CapabilitySupport]
    limitations: list[str] = field(default_factory=list)


# =============================================================================
# Source locator (section 5.4) — shared by SemanticEntity and EvidenceRef
# =============================================================================


@dataclass
class SourceLocator:
    """Section 5.4 — location of a piece of evidence.

    COMMON-014: direct source relations require a source span (start/end line)
    or exact graph nodes that resolve to a span. COMMON-016: documentation
    evidence requires document identity, section, and source snapshot.
    """

    path_or_document_id: str
    start_line: int | None = None
    end_line: int | None = None
    symbol_or_section: str | None = None
    graph_node_ids: list[str] = field(default_factory=list)


# =============================================================================
# Semantic entities and aliases (section 5.2)
# =============================================================================


@dataclass
class SemanticEntityAlias:
    """Section 5.2 — one scoped alias on a SemanticEntity.

    COMMON-005: aliases must be scoped; a short name must not globally merge
    unrelated symbols.
    """

    value: str
    alias_kind: AliasKind
    scope: str | None = None


@dataclass
class SemanticEntity:
    """Section 5.2 — a stable thing that may be the subject or object of a
    relation.

    COMMON-006: a project_concept must remain distinct from a concrete source
    symbol until an evidence-backed relation connects them. COMMON-007:
    parameters and options must identify their owner (via ``scope``).
    COMMON-008: scenarios must have stable identities based on conditions and
    expected outcome, not only test names.
    """

    entity_id: str
    repository_id: str
    entity_kind: EntityKind
    canonical_name: str
    dataset_id: str
    qualified_name: str | None = None
    language: str | None = None
    scope: str | None = None
    aliases: list[SemanticEntityAlias] = field(default_factory=list)
    source_locator: SourceLocator | None = None


# =============================================================================
# Evidence references (section 5.4)
# =============================================================================


@dataclass
class EvidenceRef:
    """Section 5.4 — provenance for one relation.

    COMMON-013: every relation must have at least one evidence reference.
    COMMON-015: direct test coverage requires the test and scenario mapping,
    not only a test file path.
    """

    evidence_ref_id: str
    evidence_kind: EvidenceKind
    repository_id: str
    revision: str
    locator: SourceLocator
    content_digest: str
    dataset_id: str
    excerpt: str | None = None


# =============================================================================
# Semantic relations (section 5.3)
# =============================================================================


@dataclass
class SemanticRelation:
    """Section 5.3 — a typed, directional statement connecting a subject to
    an object or outcome.

    COMMON-009: direction must be explicit and stable. COMMON-010: scenario-
    dependent relations must carry a scenario or condition; an unconditional
    relation must not be emitted from one branch. COMMON-011: confidence must
    never replace source provenance or relation matching. COMMON-012: a
    semantic claim without a matching typed relation is contextual only.

    ``object_entity_id`` and ``literal_object`` are mutually exclusive: use the
    former when the object is another entity, the latter when it is a typed
    literal (e.g. ``True``, ``"create-chain"``).

    Deviation from 5.3 storage shape: the spec stores ``evidence_refs`` as
    identifiers with a separate join table (section 15.1). Here we embed the
    full :class:`EvidenceRef` objects so each observation is self-contained
    for the consumer (section 11 bundles "SemanticRelation plus EvidenceRef").
    Storage may still normalize via IDs + join table.
    """

    relation_id: str
    subject_entity_id: str
    relation_kind: RelationKind
    authority_scope: AuthorityScope
    modality: Modality
    extraction_method: ExtractionMethod
    extractor_version: str
    dataset_id: str
    evidence_refs: list[EvidenceRef]
    object_entity_id: str | None = None
    literal_object: object | None = None
    scenario_id: str | None = None
    condition_expression: dict[str, Any] | None = None
    confidence: float | None = None


# =============================================================================
# Query interface (section 11)
# =============================================================================


@dataclass
class QuerySubject:
    """Section 11 / 2.2 — the entity whose property or relationship the
    question asks about.

    ``name`` and ``aliases`` are retrieval handles, not assertions: the graph
    resolves them to one or more :class:`SemanticEntity` instances internally
    and reports the *observed* subject in each :class:`SemanticRelation`
    (QUERY-002). A short name must not be treated as globally unique
    (COMMON-005). One subject may resolve to many entities (same name in
    different scopes) — each becomes a separate observation.
    """

    name: str
    kind_hint: EntityKind | None = None
    scope_hint: str | None = None
    aliases: list[str] = field(default_factory=list)


@dataclass
class QueryScenario:
    """Section 11 — scenario binding for a query."""

    conditions: list[str] = field(default_factory=list)
    expected_outcome: str | None = None


@dataclass
class QueryLimits:
    """Section 11 — bounded query budget.

    Defaults are library choices, not spec values.
    """

    max_results: int = 50
    max_hops: int = 3


@dataclass
class SemanticGraphQuery:
    """Section 11 — one bounded semantic request.

    QUERY-001: a query asks for **one** primary semantic relation
    (``expected_relation``). One query → one relation → zero or more
    observations.
    """

    repository_id: str
    requested_revision: str
    subject: QuerySubject
    expected_relation: RelationKind
    authority_scope: AuthorityScope
    object_hint: str | None = None
    scenario: QueryScenario | None = None
    limits: QueryLimits = field(default_factory=QueryLimits)


@dataclass
class QueryDiagnostics:
    """Section 11 — bounded diagnostics for a query result.

    QUERY-005: diagnostics must be bounded and must not expose an entire raw
    graph payload to the agent.
    """

    examined_entities: int = 0
    examined_relations: int = 0
    limitations: list[str] = field(default_factory=list)


@dataclass
class SemanticGraphQueryResult:
    """Section 11 — normalized result for one semantic query.

    QUERY-002: results must retain the observed subject (in each
    :class:`SemanticRelation.subject_entity_id`), not the requested subject.
    QUERY-003: providers must not broaden a missing relation into an unrelated
    lexical result and call it success. QUERY-004: result order must be
    deterministic for the same dataset and request.
    """

    status: QueryStatus
    served_dataset: GraphDatasetManifest
    capability: CapabilityName
    observations: list[SemanticRelation] = field(default_factory=list)
    diagnostics: QueryDiagnostics = field(default_factory=QueryDiagnostics)


# =============================================================================
# Coverage feedback (section 13)
#
# Recorded by the online layer, consumed by the offline team in aggregate.
# Runtime traces do not automatically write graph facts.
# =============================================================================


@dataclass
class EvidenceCoverageGap:
    """Section 13 — a gap recorded by the online layer for offline triage.

    ``modeling_hint`` is an extractor/schema category, never a patch target.
    """

    semantic_key: str
    required_fact_kind: str
    expected_relation: RelationKind
    expected_authority_scope: AuthorityScope
    source_instance_id: str
    gap_reason: CoverageGapReason
    observed_capability: str
    modeling_hint: str | None = None
