"""EXPOSES_PUBLIC_SURFACE extractor — public FUNCTION/METHOD → PARAMETER.

Like OWNS_CONTROL but restricted to *public* owners: a public entry point
exposes its parameters as part of the public surface. "Public" is
language-dependent:

- Python: name does not start with ``_`` (convention; tree-sitter doesn't
  mark visibility for Python).
- Other languages: defer to Node.is_exported / Node.visibility when the
  extractor populates them; otherwise fall back to "not obviously private"
  (name not starting with ``_``).

Since pycodegraph does not extract PARAMETER nodes, we parse parameter names
from ``Node.signature``, same as OWNS_CONTROL.

Section 6.1: ``exposes_public_surface`` — public signature, module schema,
or API documentation. Here we use the public-signature path.
"""

from __future__ import annotations

from ...types import NodeKind
from ..types import (
    AuthorityScope,
    ExtractionMethod,
    Modality,
    RelationKind,
    SemanticRelation,
)
from ._common import _BuilderLike, relation_id, source_evidence
from .owns_control import _parse_parameters

_EXTRACTOR_VERSION = "0.1.0"


def extract_exposes_public_surface(
    builder: _BuilderLike,
) -> list[SemanticRelation]:
    """Emit EXPOSES_PUBLIC_SURFACE for each (public owner, param-name) pair."""
    queries = builder.queries()
    dataset_id = builder.dataset_id()
    repository_id = builder.repository_id()
    revision = builder.revision_value()

    owner_nodes: list = []
    for kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS):
        owner_nodes.extend(queries.get_nodes_by_kind(kind))
    seen_ids: set[str] = set()
    owners = []
    for o in owner_nodes:
        if o.id not in seen_ids:
            seen_ids.add(o.id)
            owners.append(o)

    relations: list[SemanticRelation] = []
    seen_rel: set[str] = set()
    for owner in owners:
        if not _is_public(owner):
            continue
        params = _parse_parameters(owner.signature or "")
        for pname in params:
            rid = relation_id(
                RelationKind.EXPOSES_PUBLIC_SURFACE.value,
                owner.id,
                None,
                pname,
                dataset_id,
            )
            if rid in seen_rel:
                continue
            seen_rel.add(rid)
            relations.append(
                SemanticRelation(
                    relation_id=rid,
                    subject_entity_id=owner.id,
                    relation_kind=RelationKind.EXPOSES_PUBLIC_SURFACE,
                    authority_scope=AuthorityScope.PUBLIC_CONTRACT,
                    modality=Modality.DOCUMENTED,
                    extraction_method=ExtractionMethod.STATIC_ANALYSIS,
                    extractor_version=_EXTRACTOR_VERSION,
                    dataset_id=dataset_id,
                    evidence_refs=[
                        source_evidence(
                            owner,
                            queries,
                            repository_id,
                            revision,
                            dataset_id,
                            relation_id_hint=rid,
                        )
                    ],
                    literal_object=pname,
                )
            )
    return relations


def _is_public(owner) -> bool:
    """Is *owner* a public entry point?

    Python convention: names not starting with ``_`` are public. For other
    languages, trust the extractor's is_exported/visibility if set.
    """
    if owner.is_exported:
        return True
    if owner.visibility in ("private", "protected"):
        return owner.visibility == "protected"
    return not owner.name.startswith("_")
