"""FORWARDS_VALUE (inter-procedural) extractor — cross-function parameter
forwarding.

Association logic (issue #120, keyword-arg support in #121):
1. Read CALLS edges
2. For each CALLS edge, look up caller parameter names (from signature)
3. Read caller source file, ast.parse to extract positional AND keyword args
   at the call site
4. For each arg whose value is a simple identifier matching a caller param:
   → FORWARDS_VALUE(call_site, callee_param)
5. Positional arg N maps to callee param N; keyword arg `kw=x` maps to the
   callee param named `kw`.

Distinct from intra-procedural FORWARDS_VALUE (#118, InlineFact hook):
- #118 subject = function, object = callee.N
- #120/#121 subject = call_site (caller::L{line}), object = callee.param_name
- metadata forwards_type: "intra" vs "inter"; arg_type: "positional" | "keyword"
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

_EXTRACTOR_VERSION = "xg-120-1"


def _file_provider_source(builder: _BuilderLike, file_path: str) -> str | None:
    """Read source file content via builder.file_provider, if available.

    Returns None when the provider is not configured or the file cannot
    be read — extractor skips that call edge gracefully.
    """
    fp = builder.file_provider
    if fp is None:
        return None
    try:
        data = fp.read_file(file_path)  # type: ignore[attr-defined]
        if data is None:
            return None
        return str(data)
    except Exception:
        return None


def _parse_simple_params(signature: str) -> list[str]:
    """Extract all parameter names from a Python signature string.

    Handles: ``(x, y: str = 5)``, ``(self, x, *args, **kwargs)``.
    Skips self/cls.
    """

    sig = signature.strip().strip("()")
    if not sig:
        return []
    params: list[str] = []
    for part in sig.split(","):
        part = part.strip()
        if not part:
            continue
        # Strip type annotation after ":" and default after "="
        name = part.split(":")[0].split("=")[0].strip().lstrip("*").strip()
        if not name or name in ("self", "cls"):
            continue
        params.append(name)
    return params


def _count_call_args(
    caller_source: str, line: int
) -> list[tuple[str, str | int, str | None]] | None:
    """Parse caller source to find the call at *line* and return argument
    info for each positional and keyword argument.

    Each entry is ``(arg_type, key, value_identifier)`` where:
    - positional: ``("positional", idx, identifier_name_or_None)``
    - keyword: ``("keyword", kw_name, identifier_name_or_None)``

    ``value_identifier`` is the text when the arg value is a simple
    identifier (``x``), or None when it is a complex expression (``x + 1``).

    Returns None when parsing fails or no call found at that line.
    Returns empty list for a call with no args.
    """
    try:
        tree = ast.parse(caller_source)
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.lineno == line:
            results: list[tuple[str, str | int, str | None]] = []
            for idx, arg in enumerate(node.args):
                if isinstance(arg, ast.Name):
                    results.append(("positional", idx, arg.id))
                else:
                    results.append(("positional", idx, None))
            for kw in node.keywords:
                # kw.arg is None for **kwargs splat — skip (can't map to a param)
                if kw.arg is None:
                    continue
                if isinstance(kw.value, ast.Name):
                    results.append(("keyword", kw.arg, kw.value.id))
                else:
                    results.append(("keyword", kw.arg, None))
            return results
    return None


def extract_forwards_value(
    builder: _BuilderLike,
) -> list[SemanticRelation]:
    """Emit one FORWARDS_VALUE relation per forwarded parameter at a call
    site where a caller parameter is passed (positionally or by keyword) to a
    callee.

    Requires ``builder.file_provider`` to be set. When absent, returns [].
    """
    queries = builder.queries()
    ds_id = builder.dataset_id()
    repo_id = builder.repository_id()
    rev = builder.revision_value()

    # 1. Read all CALLS edges
    call_edges = queries.get_all_edges(limit=200_000)
    calls = [e for e in call_edges if e.kind == EdgeKind.CALLS]

    relations: list[SemanticRelation] = []
    seen: set[str] = set()

    for edge in calls:
        caller_id = edge.source
        callee_id = edge.target
        caller = queries.get_node_by_id(caller_id)
        callee = queries.get_node_by_id(callee_id)
        if caller is None or callee is None:
            continue

        # 2. Get caller parameter names from signature
        caller_params = _parse_simple_params(caller.signature or "")
        if not caller_params:
            continue

        call_line = edge.line
        if call_line is None:
            continue

        # 3. Read caller source and find call args
        caller_source = _file_provider_source(builder, caller.file_path)
        if caller_source is None:
            continue

        args_info = _count_call_args(caller_source, call_line)
        if args_info is None:
            continue

        # 4. Get callee parameter names for mapping position -> name
        callee_params = _parse_simple_params(callee.signature or "")

        # 5. For each arg that is a simple identifier matching a caller
        #    parameter name, emit FORWARDS_VALUE.
        for arg_type, key, arg_name in args_info:
            if arg_name is None:
                # Complex expression — not forwarding
                continue
            if arg_name not in caller_params:
                # Not a caller parameter — local var, global, etc.
                continue

            # Map to callee param name
            callee_param_name: str | None = None
            if arg_type == "positional":
                # arg N -> callee param N
                idx = key if isinstance(key, int) else 0
                if callee_params and idx < len(callee_params):
                    callee_param_name = callee_params[idx]
                else:
                    callee_param_name = f"_{idx}"  # fallback
            else:  # keyword
                # kw=identifier -> callee param named `key`. If the callee
                # has no such param (e.g. **kwargs), use the kw name as a
                # best-effort label so the relation is still emitted.
                callee_param_name = key if isinstance(key, str) else ""

            # subject = call site (synthetic ID, same pattern as READS_DEFAULT)
            subject_id = f"{caller.qualified_name}::L{call_line}"

            # object = callee param reference
            obj_text = f"{callee.qualified_name}.{callee_param_name}"

            rid = _mkr_id(
                RelationKind.FORWARDS_VALUE.value,
                subject_id,
                None,
                obj_text,
                ds_id,
            )
            if rid in seen:
                continue
            seen.add(rid)

            condition: dict = {
                "forwards_type": "inter",
                "caller_param": arg_name,
                "callee_param": callee_param_name,
                "arg_type": arg_type,
                "caller_node_id": caller_id,
                "callee_node_id": callee_id,
            }
            if arg_type == "positional":
                idx = key if isinstance(key, int) else 0
                condition["arg_index"] = idx
            else:
                condition["kw_arg_name"] = key if isinstance(key, str) else ""

            relations.append(
                SemanticRelation(
                    relation_id=rid,
                    subject_entity_id=subject_id,
                    relation_kind=RelationKind.FORWARDS_VALUE,
                    authority_scope=AuthorityScope.IMPLEMENTATION_TOPOLOGY,
                    modality=Modality.OBSERVED,
                    extraction_method=ExtractionMethod.STATIC_ANALYSIS,
                    extractor_version=_EXTRACTOR_VERSION,
                    dataset_id=ds_id,
                    evidence_refs=[
                        EvidenceRef(
                            evidence_ref_id=f"ev:{rid[4:]}_fw",
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
                    condition_expression=condition,
                    confidence=1.0,
                )
            )

    return relations
