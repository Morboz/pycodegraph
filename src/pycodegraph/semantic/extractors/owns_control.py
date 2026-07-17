"""OWNS_CONTROL extractor — owner FUNCTION/METHOD/CLASS → control (parameter).

A parameter/option is "owned" by the function/method/class that declares it
in its signature. Since pycodegraph does not extract PARAMETER nodes as
independent entities, we parse parameter names from the ``Node.signature``
string and emit one relation per parameter with a ``literal_object`` holding
the parameter name.

Section 6.1: ``owns_control`` — signature, structured option definition, or
explicit documentation. Here we use the signature path.
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

_EXTRACTOR_VERSION = "0.1.0"


def extract_owns_control(builder: _BuilderLike) -> list[SemanticRelation]:
    """Emit one OWNS_CONTROL relation per (owner, parameter-name) pair."""
    queries = builder.queries()
    dataset_id = builder.dataset_id()
    repository_id = builder.repository_id()
    revision = builder.revision_value()

    # Collect all owner-typed nodes (FUNCTION, METHOD, CLASS).
    owner_nodes: list = []
    for kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS):
        owner_nodes.extend(queries.get_nodes_by_kind(kind))
    # Dedupe by id.
    seen_ids: set[str] = set()
    owners = []
    for o in owner_nodes:
        if o.id not in seen_ids:
            seen_ids.add(o.id)
            owners.append(o)

    relations: list[SemanticRelation] = []
    seen_rel: set[str] = set()
    for owner in owners:
        params = _parse_parameters(owner.signature or "")
        for pname in params:
            rid = relation_id(
                RelationKind.OWNS_CONTROL.value,
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
                    relation_kind=RelationKind.OWNS_CONTROL,
                    authority_scope=AuthorityScope.IMPLEMENTATION_TOPOLOGY,
                    modality=Modality.OBSERVED,
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


def _parse_parameters(signature: str) -> list[str]:
    """Extract parameter names from a Python signature string.

    Handles: ``(name, email)``, ``(name: str, email: str = None)``,
    ``(self, name, *args, **kwargs)``, ``(a, /, b, *, c)``.
    Skips ``self`` and ``cls`` (implicit instance/class references).
    """
    sig = signature.strip().strip("()")
    if not sig:
        return []
    params: list[str] = []
    for part in sig.split(","):
        part = part.strip()
        if not part:
            continue
        # Strip leading ``*`` / ``/`` and annotation after ``:``
        name = part.split(":")[0].strip().lstrip("*").strip()
        if not name or name in ("self", "cls"):
            continue
        params.append(name)
    return params
