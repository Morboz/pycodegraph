"""CONSUMES_RETURN extractor — caller uses callee's return value.

Association logic (issue #125):
1. Read CALLS edges
2. For each CALLS edge, read caller source, ast.parse at call line
3. If the call is assigned to a variable (e.g. ``resp = fetch_url(...)``),
   emit CONSUMES_RETURN(call_site, callee)
4. When the call result is not captured (bare ``fetch_url(...)``),
   no relation is emitted — the return value is NOT consumed.

Same registered extractor pattern as FORWARDS_VALUE (#120/#121).
"""

from __future__ import annotations

import ast

from ...types import EdgeKind
from ..types import (
    AuthorityScope,
    EvidenceKind,
    EvidenceRef,
    ExtractionMethod,
    Modality,
    RelationKind,
    SemanticRelation,
    SourceLocator,
)
from ._common import _BuilderLike
from ._common import relation_id as _mkr_id

_EXTRACTOR_VERSION = "xg-125-1"


def _is_call_assigned(caller_source: str, line: int) -> str | None:
    """Check if any Call at *line* is assigned to a variable.

    Returns the variable name if assigned (``resp``), or None when the
    call result is not captured (bare ``fetch_url(...)``).
    """
    try:
        tree = ast.parse(caller_source)
    except SyntaxError:
        return None

    # Walk Assign nodes: if the value is a Call at our target line,
    # that's an assigned call
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and node.value.lineno == line
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    return target.id
                if isinstance(target, ast.Attribute):
                    return target.attr
    return None


def extract_consumes_return(
    builder: _BuilderLike,
) -> list[SemanticRelation]:
    """Emit one CONSUMES_RETURN relation per call site where the caller
    captures the callee's return value.

    Requires ``builder.file_provider`` to be set. When absent, returns [].
    """
    queries = builder.queries()
    ds_id = builder.dataset_id()
    repo_id = builder.repository_id()
    rev = builder.revision_value()

    # 1. Read all CALLS edges
    call_edges = queries.get_all_edges(limit=200_000)
    calls = [e for e in call_edges if e.kind == EdgeKind.CALLS]

    # Helper: file_provider_source (same pattern as forwards_value_inter)
    fp = builder.file_provider

    def _read(fp, path):
        if fp is None:
            return None
        try:
            d = fp.read_file(path)
            return str(d) if d is not None else None
        except Exception:
            return None

    relations: list[SemanticRelation] = []
    seen: set[str] = set()

    for edge in calls:
        caller_id = edge.source
        callee_id = edge.target
        caller = queries.get_node_by_id(caller_id)
        callee = queries.get_node_by_id(callee_id)
        if caller is None or callee is None:
            continue

        call_line = edge.line
        if call_line is None:
            continue

        caller_source = _read(fp, caller.file_path)
        if caller_source is None:
            continue

        var_name = _is_call_assigned(caller_source, call_line)
        if var_name is None:
            # Call result not captured — not consumed
            continue

        subject_id = f"{caller.qualified_name}::L{call_line}"
        obj_text = callee.qualified_name

        rid = _mkr_id(
            RelationKind.CONSUMES_RETURN.value,
            subject_id,
            None,
            obj_text,
            ds_id,
        )
        if rid in seen:
            continue
        seen.add(rid)

        relations.append(
            SemanticRelation(
                relation_id=rid,
                subject_entity_id=subject_id,
                relation_kind=RelationKind.CONSUMES_RETURN,
                authority_scope=AuthorityScope.IMPLEMENTATION_TOPOLOGY,
                modality=Modality.OBSERVED,
                extraction_method=ExtractionMethod.STATIC_ANALYSIS,
                extractor_version=_EXTRACTOR_VERSION,
                dataset_id=ds_id,
                evidence_refs=[
                    EvidenceRef(
                        evidence_ref_id=f"ev:{rid[4:]}_cr",
                        evidence_kind=EvidenceKind.SOURCE,
                        repository_id=repo_id,
                        revision=rev,
                        locator=SourceLocator(
                            path_or_document_id=caller.file_path,
                            start_line=call_line,
                            end_line=call_line,
                            symbol_or_section=caller.qualified_name,
                        ),
                        content_digest=f"sha256:{caller.file_path}:{call_line}",
                        dataset_id=ds_id,
                    )
                ],
                object_entity_id=callee_id,
                literal_object=obj_text,
                condition_expression={
                    "variable_name": var_name,
                    "caller_node_id": caller_id,
                    "callee_node_id": callee_id,
                },
                confidence=1.0,
            )
        )

    return relations
