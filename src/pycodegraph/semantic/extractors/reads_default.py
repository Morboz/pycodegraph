"""READS_DEFAULT extractor — call site uses callee's parameter default.

Association logic (issue #116):
1. Read CALLS edges (existing edges table data)
2. For each CALLS edge, look up callee's STORES_DEFAULT relations
3. Parse parameter count from callee Node.signature
4. Read caller source file, count actual arguments at the call site
5. Missing parameter positions -> READS_DEFAULT(call_site, default_value)
"""

from __future__ import annotations

import ast
import re

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

_EXTRACTOR_VERSION = "xg-116-1"

# Parameter kinds that carry a default value in tree-sitter grammar.
_DEFAULT_PARAM_KINDS = frozenset({"default_parameter", "typed_default_parameter"})


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


def _count_args_at_line(caller_source: str, line: int) -> tuple[int, set[str]] | None:
    """Count positional arguments and keyword argument names at a specific
    source line.

    Uses ``ast.parse`` on the caller source, walks syntax tree to find a
    Call node at the given line, then returns ``(pos_count, kw_names)``.
    Returns ``None`` when parsing fails or no call is found at that line.
    """
    try:
        tree = ast.parse(caller_source)
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.lineno == line:
            # ast.lineno is 1-based, same as Edge.line
            pos_count = len(node.args)
            kw_names: set[str] = set()
            for kw in node.keywords:
                if kw.arg is not None:
                    kw_names.add(kw.arg)
            return (pos_count, kw_names)
    return None


def _parse_signature_defaults(
    signature: str,
) -> list[dict[str, str]]:
    """Parse parameter defaults from a Python signature string.

    Returns ordered list of dicts:
    ``[{"name": "x", "default": "5"}, {"name": "y", "default": "'hello'"}]``

    Only parameters with explicit defaults are returned.
    """
    sig = signature.strip().strip("()")
    if not sig:
        return []
    results: list[dict[str, str]] = []
    for part in sig.split(","):
        part = part.strip()
        if not part:
            continue
        # Match: ``name: type = value`` or ``name = value``
        m = re.match(
            r"""(\w+)            # parameter name
                (?:\s*:\s*[^,=]+)?  # optional type annotation
                \s*=\s*            # equals sign
                (.+)               # default value
            """,
            part,
            re.VERBOSE,
        )
        if m:
            results.append({"name": m.group(1), "default": m.group(2).strip()})
    return results


def extract_reads_default(
    builder: _BuilderLike,
) -> list[SemanticRelation]:
    """Emit one READS_DEFAULT relation per default-value parameter at a call
    site where the caller did NOT pass that argument (default used at runtime).

    Requires ``builder.file_provider`` to be set (a LocalFileProvider or
    equivalent). When absent, the extractor returns [] gracefully.
    """
    queries = builder.queries()
    ds_id = builder.dataset_id()
    repo_id = builder.repository_id()
    rev = builder.revision_value()

    # 1. Read all CALLS edges
    call_edges = queries.get_all_edges(limit=200_000)
    calls = [e for e in call_edges if e.kind == EdgeKind.CALLS]

    # 2. Build callee_id → [STORES_DEFAULT parameter_names]
    #    Also collect callee_id → STORES_DEFAULT relations for
    #    default-value text lookup.
    from ..store import read_relations
    from ._common import relation_id as _mkr_id

    conn = queries.connection

    # We need STORES_DEFAULT from the current build. read_relations reads
    # the DB which already has them (inline_facts were flushed before this
    # registered extractor runs).
    stores_defaults = read_relations(
        conn,
        relation_kind=RelationKind.STORES_DEFAULT,
        dataset_ids=[f"ds:{builder.dataset_id()}"],
    )

    # callee_node_id → list of {param_name, default_text}
    callee_defaults: dict[str, list[dict[str, str]]] = {}
    for rel in stores_defaults:
        callee_id = rel.object_entity_id
        if callee_id is None:
            # object is a literal string, not a node — we can match on it
            # but need the callee node id.  Actually STORES_DEFAULT
            # subject is the function/node, object is a literal. So
            # subject_entity_id is the callee node.
            pass
        pname = None
        if rel.condition_expression:
            pname = rel.condition_expression.get("parameter_name")
        default_text = rel.literal_object
        if pname is None or default_text is None:
            continue
        callee_node_id = rel.subject_entity_id
        callee_defaults.setdefault(callee_node_id, []).append(
            {"name": pname, "default": str(default_text)}
        )

    # Also try parsing from callee signature for fallback
    callee_sig_defaults: dict[str, list[dict[str, str]]] = {}

    relations: list[SemanticRelation] = []
    seen: set[str] = set()

    for edge in calls:
        caller_id = edge.source
        callee_id = edge.target
        caller = queries.get_node_by_id(caller_id)
        callee = queries.get_node_by_id(callee_id)
        if caller is None or callee is None:
            continue

        # 3. Get callee's defaults (try STORES_DEFAULT first)
        defaults = callee_defaults.get(callee_id)
        if not defaults:
            # Fallback: parse from callee Node.signature
            if callee_id not in callee_sig_defaults:
                sig_params = _parse_signature_defaults(callee.signature or "")
                callee_sig_defaults[callee_id] = sig_params
            defaults = callee_sig_defaults.get(callee_id, [])

        if not defaults:
            continue

        # 4. Read caller source to count positional args
        call_line = edge.line
        if call_line is None:
            continue
        caller_source = _file_provider_source(builder, caller.file_path)
        if caller_source is None:
            continue
        args_info = _count_args_at_line(caller_source, call_line)
        if args_info is None:
            continue
        pos_count, kw_names = args_info

        # 5. Count total non-implicit (self/cls) parameters
        #    Use signature parsing for simplicity
        sig_all_defaults = _parse_signature_defaults(callee.signature or "")
        all_params = _parse_simple_params(callee.signature or "")
        total_params = len(all_params)
        required_params = total_params - len(sig_all_defaults)

        # Parameters that have defaults but are passed positionally OR
        # via keyword are NOT reads_default (caller explicitly passed them).
        # Parameters with defaults after pos_count (positional) and NOT
        # in kw_names (keyword) → uses default.
        used_default_start = max(required_params, pos_count)
        for i, d in enumerate(sig_all_defaults):
            idx = required_params + i
            if d["name"] in kw_names:
                # caller explicitly passed this parameter as a keyword arg
                continue
            if idx >= used_default_start:
                # This parameter was NOT passed → uses default
                subject_id = f"{caller.qualified_name}::L{call_line}"
                # relation_id uses literal_object (not object_entity_id) for
                # the hash because two READS_DEFAULT relations at the same
                # call_site → same callee but different defaults must hash
                # differently. Passing object_entity_id=None makes the
                # relation_id helper include literal_object in the hash.
                rid = _mkr_id(
                    RelationKind.READS_DEFAULT.value,
                    subject_id,
                    None,
                    d["default"],
                    ds_id,
                )
                if rid in seen:
                    continue
                seen.add(rid)
                relations.append(
                    SemanticRelation(
                        relation_id=rid,
                        subject_entity_id=subject_id,
                        relation_kind=RelationKind.READS_DEFAULT,
                        authority_scope=AuthorityScope.IMPLEMENTATION_TOPOLOGY,
                        modality=Modality.OBSERVED,
                        extraction_method=ExtractionMethod.STATIC_ANALYSIS,
                        extractor_version=_EXTRACTOR_VERSION,
                        dataset_id=ds_id,
                        evidence_refs=[
                            EvidenceRef(
                                evidence_ref_id=f"ev:{rid[4:]}_rd",
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
                        literal_object=d["default"],
                        condition_expression={
                            "parameter_name": d["name"],
                            "callee_node_id": callee_id,
                        },
                        confidence=1.0,
                    )
                )

    return relations


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
