"""Cross-graph alias relations (XG-004, issue #110).

DocGraph entities (e.g. ``concept:ansible-test``) and CodeGraph symbols
(e.g. ``module::requests.api:FunctionDef:get``) cannot be joined by string
equality — their ``entity_id`` formats are different. XG-004 requires an
**explicit, evidence-backed** relation to connect them, so that subject
resolution can expand a query across both graphs.

This module houses:

* :class:`CrossGraphAliasBuilder` — a post-build step that reads a mapping
  config (YAML) and writes ``CROSS_GRAPH_ALIAS`` :class:`SemanticRelation`
  rows into ``semantic_relations``. Run **after** both the CodeGraph and
  DocGraph builds have completed, so both endpoints are resolvable.
* :func:`read_cross_graph_aliases` — store-side helper called by
  :class:`pycodegraph.semantic.query.SemanticGraphQueryHandler._resolve_subject`
  to expand candidate subject IDs by one alias hop (bidirectional).

Design choices (grilling session on issue #110):

* Alias is its own ``RelationKind`` (not a reuse of ``RESOLVES_SYMBOL`` —
  that one is CodeGraph-internal concept→symbol, while CROSS_GRAPH_ALIAS
  spans datasets).
* ``max_hops=1``: aliases are not transitive. No cycle risk.
* Bidirectional expansion at query time — a query for a CodeGraph symbol
  resolves its DocGraph aliases too, and vice versa.
* YAML config uses ``qualified_name`` as the CodeGraph-side key (human-
  readable, stable across rebuilds), not ``entity_id`` (a hash).
* Evidence is **mandatory** per alias entry. An alias without evidence is
  just a string-equality join — exactly what XG-004 forbids.
* Validation is warn-and-skip: a YAML entry whose doc/code entity is not
  found in ``semantic_entities`` logs a warning and is dropped, not fatal.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Connection, or_, select

from ..db.tables import semantic_entities, semantic_relations
from .store import write_relations
from .types import (
    AuthorityScope,
    EvidenceKind,
    EvidenceRef,
    ExtractionMethod,
    Modality,
    RelationKind,
    SemanticRelation,
    SourceLocator,
)

_LOGGER = logging.getLogger(__name__)

#: Default authority scope for a cross-graph alias. The alias is a project
#: convention — it asserts "the doc team says these two refer to the same
#: thing" — not a public contract obligation (section 2.4 of the spec).
_ALIAS_AUTHORITY_SCOPE = AuthorityScope.PROJECT_CONVENTION

#: Aliases are documented statements (an ``.rst`` ``:ref:`` or a maintainer
#: note in the YAML), observed in the project's documentation corpus.
_ALIAS_MODALITY = Modality.DOCUMENTED

#: MVP evidence tier is tier 1 — explicit ``:ref:`` cross-references in
#: documentation. The YAML carries the doc location; we don't (yet)
#: re-extract it from the source RST.
_ALIAS_EVIDENCE_KIND = EvidenceKind.DOCUMENTATION

#: Tier-2 (static YAML config) is human-curated. Tier-1 ``:ref:`` targets
#: are also captured via the YAML in MVP, so the extraction method stays
#: HUMAN_CURATED until an automatic ``:ref:`` extractor exists.
_ALIAS_EXTRACTION_METHOD = ExtractionMethod.HUMAN_CURATED

_ALIAS_EXTRACTOR_VERSION = "xg-004-mvp-1"


# =============================================================================
# YAML config schema
# =============================================================================


def _load_alias_config(config_path: str | Path) -> list[dict[str, Any]]:
    """Load and validate the alias mapping YAML.

    Returns the list of alias entries. Each entry is a dict with keys:
    ``doc_entity_id``, ``code_qualified_name``, ``evidence``.

    Raises ``ValueError`` if the YAML is structurally malformed (missing
    required keys, wrong types). Individual entries that reference unknown
    entities are NOT rejected here — that's a build-time warn-and-skip.
    """
    path = Path(config_path)
    if not path.exists():
        # No config file is a valid state — zero aliases.
        return []
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{config_path}: expected a YAML mapping at top level")
    entries = data.get("aliases", [])
    if not isinstance(entries, list):
        raise ValueError(f"{config_path}: 'aliases' must be a list")
    cleaned: list[dict[str, Any]] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"{config_path}: aliases[{i}] must be a mapping")
        for required in ("doc_entity_id", "code_qualified_name", "evidence"):
            if required not in entry:
                raise ValueError(
                    f"{config_path}: aliases[{i}] missing required key {required!r}"
                )
        ev = entry["evidence"]
        if not isinstance(ev, dict) or "path_or_document_id" not in ev:
            raise ValueError(
                f"{config_path}: aliases[{i}].evidence must be a mapping "
                "with at least 'path_or_document_id'"
            )
        cleaned.append(entry)
    return cleaned


# =============================================================================
# CrossGraphAliasBuilder
# =============================================================================


class CrossGraphAliasBuilder:
    """Post-build step: write CROSS_GRAPH_ALIAS relations from a YAML config.

    Run **after** both the CodeGraph and DocGraph semantic layers are built
    into the shared DB. The builder resolves each alias entry's endpoints
    against the persisted ``semantic_entities`` rows, then writes a
    :class:`SemanticRelation` of kind ``CROSS_GRAPH_ALIAS`` for each
    resolvable entry.

    Entries whose ``doc_entity_id`` or ``code_qualified_name`` cannot be
    resolved are skipped with a warning — they do not abort the build
    (grilling decision Q8: warn-and-skip on unknown entities).

    The resulting relations live in their own dataset (``ds:cross-graph-aliases``)
    so that consumers can identify them by ``dataset_id`` and so the query
    fan-out in :class:`SemanticGraphQueryHandler` picks them up alongside
    the CodeGraph and DocGraph datasets.
    """

    #: Synthetic dataset_id for alias relations. They are not produced by
    #: either graph's build, but by this post-build step — so they get
    #: their own dataset tag for provenance.
    ALIAS_DATASET_ID = "ds:cross-graph-aliases"

    def __init__(
        self,
        conn: Connection,
        config_path: str | Path,
        repository_id: str,
        revision: str,
    ) -> None:
        self._conn = conn
        self._config_path = config_path
        self._repository_id = repository_id
        self._revision = revision

    def build(self) -> int:
        """Read the config and write alias relations.

        Returns the number of alias relations written. Idempotent on
        relation_id (re-running with the same config replaces prior rows).
        """
        entries = _load_alias_config(self._config_path)
        if not entries:
            return 0

        # Index existing entities for resolution.
        # doc_entity_id → entity_id (1:1 expected; DocGraph entities use
        # graphify-out node ids directly).
        # code_qualified_name → entity_id (CodeGraph entities are keyed by
        # qualified_name in the YAML because entity_id is a hash).
        doc_index = _index_entities_by_field(self._conn, "entity_id")
        code_index = _index_entities_by_field(self._conn, "qualified_name")

        relations: list[SemanticRelation] = []
        for entry in entries:
            doc_id = entry["doc_entity_id"]
            code_qname = entry["code_qualified_name"]
            doc_entity = doc_index.get(doc_id)
            if doc_entity is None:
                _LOGGER.warning(
                    "XG-004 alias: doc entity %r not found in semantic_entities; "
                    "skipping (entry: code=%r)",
                    doc_id,
                    code_qname,
                )
                continue
            code_entity = code_index.get(code_qname)
            if code_entity is None:
                _LOGGER.warning(
                    "XG-004 alias: code entity with qualified_name %r not found "
                    "in semantic_entities; skipping (entry: doc=%r)",
                    code_qname,
                    doc_id,
                )
                continue
            relations.append(
                self._build_relation(doc_entity, code_entity, entry["evidence"])
            )

        if relations:
            write_relations(self._conn, relations)
        return len(relations)

    # ------------------------------------------------------------------
    # Relation construction
    # ------------------------------------------------------------------

    def _build_relation(
        self,
        doc_entity: dict[str, Any],
        code_entity: dict[str, Any],
        evidence_cfg: dict[str, Any],
    ) -> SemanticRelation:
        """Construct one CROSS_GRAPH_ALIAS SemanticRelation.

        Direction: DocGraph entity (subject) → CodeGraph entity (object).
        Bidirectional expansion is handled at query time by
        :func:`read_cross_graph_aliases`, which checks both endpoints.
        """
        subject_id = doc_entity["entity_id"]
        object_id = code_entity["entity_id"]
        rel_id = _alias_relation_id(subject_id, object_id)
        evidence = self._build_evidence(rel_id, evidence_cfg)
        return SemanticRelation(
            relation_id=rel_id,
            subject_entity_id=subject_id,
            relation_kind=RelationKind.CROSS_GRAPH_ALIAS,
            authority_scope=_ALIAS_AUTHORITY_SCOPE,
            modality=_ALIAS_MODALITY,
            extraction_method=_ALIAS_EXTRACTION_METHOD,
            extractor_version=_ALIAS_EXTRACTOR_VERSION,
            dataset_id=self.ALIAS_DATASET_ID,
            evidence_refs=[evidence],
            object_entity_id=object_id,
        )

    def _build_evidence(
        self, relation_id: str, evidence_cfg: dict[str, Any]
    ) -> EvidenceRef:
        """Build a DOCUMENTATION EvidenceRef from the YAML evidence block."""
        path_or_doc_id = evidence_cfg["path_or_document_id"]
        start_line = evidence_cfg.get("start_line")
        end_line = evidence_cfg.get("end_line")
        symbol_or_section = evidence_cfg.get("symbol_or_section")
        excerpt = evidence_cfg.get("excerpt")
        ev_id = _alias_evidence_id(relation_id, path_or_doc_id, start_line)
        digest = _alias_content_digest(path_or_doc_id, start_line, end_line)
        return EvidenceRef(
            evidence_ref_id=ev_id,
            evidence_kind=_ALIAS_EVIDENCE_KIND,
            repository_id=self._repository_id,
            revision=self._revision,
            locator=SourceLocator(
                path_or_document_id=path_or_doc_id,
                start_line=start_line,
                end_line=end_line,
                symbol_or_section=symbol_or_section,
                graph_node_ids=[],  # aliases reference docs, not raw nodes
            ),
            content_digest=digest,
            dataset_id=self.ALIAS_DATASET_ID,
            excerpt=excerpt,
        )


# =============================================================================
# Store helper — read side
# =============================================================================


def read_cross_graph_aliases(
    conn: Connection, entity_ids: list[str]
) -> dict[str, list[str]]:
    """One-hop bidirectional alias expansion.

    Given a set of entity IDs, return a mapping of input entity_id → list
    of aliased entity IDs reachable in one CROSS_GRAPH_ALIAS hop (in
    either direction). The input IDs themselves are not included in their
    own value lists.

    Used by :class:`SemanticGraphQueryHandler._resolve_subject` to expand
    candidate subject IDs across graphs (XG-004). ``max_hops=1`` — no
    transitive aliasing.
    """
    if not entity_ids:
        return {}
    rows = list(
        conn.execute(
            select(
                semantic_relations.c.subject_entity_id,
                semantic_relations.c.object_entity_id,
            )
            .where(
                semantic_relations.c.relation_kind
                == RelationKind.CROSS_GRAPH_ALIAS.value
            )
            .where(
                or_(
                    semantic_relations.c.subject_entity_id.in_(entity_ids),
                    semantic_relations.c.object_entity_id.in_(entity_ids),
                )
            )
            .order_by(semantic_relations.c.relation_id)
        ).fetchall()
    )
    out: dict[str, list[str]] = {eid: [] for eid in entity_ids}
    for row in rows:
        s, o = row.subject_entity_id, row.object_entity_id
        if s in out and o is not None:
            out[s].append(o)
        if o in out:
            out[o].append(s)
    return out


# =============================================================================
# Internal helpers
# =============================================================================


def _index_entities_by_field(conn: Connection, field: str) -> dict[str, dict[str, Any]]:
    """Build a {field_value → row_dict} index over semantic_entities.

    ``field`` is one of the column names on ``semantic_entities``. Used
    for resolving YAML alias entries by either entity_id (DocGraph side)
    or qualified_name (CodeGraph side).
    """
    rows = list(
        conn.execute(
            select(
                semantic_entities.c.entity_id,
                semantic_entities.c.qualified_name,
                semantic_entities.c.dataset_id,
                semantic_entities.c.entity_kind,
            )
        ).fetchall()
    )
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = getattr(row, field)
        if key is None:
            continue
        out[key] = {
            "entity_id": row.entity_id,
            "qualified_name": row.qualified_name,
            "dataset_id": row.dataset_id,
            "entity_kind": row.entity_kind,
        }
    return out


def _alias_relation_id(subject_id: str, object_id: str) -> str:
    """Deterministic relation_id for a CROSS_GRAPH_ALIAS row.

    Idempotent on (subject, object) so re-running the builder with the
    same config replaces rather than duplicates (matches the pattern in
    :func:`pycodegraph.semantic.extractors._common.relation_id`).
    """
    raw = f"{CrossGraphAliasBuilder.ALIAS_DATASET_ID}|cross_graph_alias|{subject_id}|{object_id}"
    return "rel:" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _alias_evidence_id(
    relation_id: str, path_or_doc_id: str, start_line: int | None
) -> str:
    raw = f"{relation_id}|doc|{path_or_doc_id}|{start_line}"
    return "ev:" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _alias_content_digest(
    path_or_doc_id: str, start_line: int | None, end_line: int | None
) -> str:
    """Deterministic digest of the evidence location.

    No file is read at this layer (the YAML is the source of truth for
    MVP), so the digest is over the locator itself. This still gives
    downstream consumers a tamper-evident handle on the evidence.
    """
    raw = f"{path_or_doc_id}|{start_line}|{end_line}"
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:16]
