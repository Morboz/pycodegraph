"""Python language extractor."""

from __future__ import annotations

import re

from tree_sitter import Node as TSNode

from ...extraction.helpers import generate_node_id, get_child_by_field, get_node_text
from ...types import InlineFact
from .base import LanguageExtractor

# =============================================================================
# Inline fact extraction (STORES_DEFAULT + IMPLEMENTS_BEHAVIOR)
# =============================================================================

_PARAMETER_NODE_KINDS = frozenset(
    {
        "default_parameter",
        "typed_default_parameter",
    }
)

# Guard patterns that belong to GUARDS_EFFECT, not IMPLEMENTS_BEHAVIOR.
_GUARD_CONDITION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bcheck_mode\b"),
    re.compile(r"\bfailed\b"),
    re.compile(r"\bchanged\b"),
    re.compile(r"\bis_changed\b"),
    re.compile(r"\bwarnings?\b"),
    re.compile(r"\bdry_run\b"),
    re.compile(r"\bdryrun\b"),
]


def _is_guard_condition(condition_text: str) -> bool:
    """Return True if the condition looks like a guard (check_mode / failed / …)
    rather than a behavior-selector condition.

    Matches are case-insensitive. An empty condition is considered a guard
    (will be skipped).
    """
    if not condition_text:
        return True
    lower = condition_text.lower()
    return any(pat.search(lower) for pat in _GUARD_CONDITION_PATTERNS)


def _get_first_call_in_block(
    block_node: TSNode, source_bytes: bytes
) -> tuple[str, int, int] | None:
    """Walk a block body to find the first ``call`` expression.

    Returns ``(call_text, line, column)`` or None when the block is empty
    or contains no call.
    """
    for i in range(block_node.named_child_count):
        stmt = block_node.named_child(i)
        if stmt is None:
            continue
        # expression_statement → call
        # (also handles assignment_statement that wraps a call via
        #  assignment → call)
        calls = _collect_calls_in_node(stmt, source_bytes)
        if calls:
            return calls[0]
    return None


def _collect_calls_in_node(
    node: TSNode, source_bytes: bytes
) -> list[tuple[str, int, int]]:
    """Collect all ``call`` expressions directly inside *node*, returning
    ``(text, line, column)`` tuples for the call function name.

    This walks one level deep from *node* to find ``call`` nodes, not
    recursively through if-statements or other compound statements
    (we only want direct children of the consequence body).
    """
    results: list[tuple[str, int, int]] = []
    for i in range(node.named_child_count):
        child = node.named_child(i)
        if child is None:
            continue
        if child.type == "call":
            func = child.child_by_field_name("function")
            if func is not None:
                call_text = get_node_text(child, source_bytes)
                results.append(
                    (
                        call_text,
                        child.start_point[0] + 1,
                        child.start_point[1] + 1,
                    )
                )
        elif child.type == "assignment":
            # x = some_func() — the call lives inside the right-hand side
            rhs = child.child_by_field_name("right")
            if rhs is not None and rhs.type == "call":
                call_text = get_node_text(rhs, source_bytes)
                results.append(
                    (
                        call_text,
                        rhs.start_point[0] + 1,
                        rhs.start_point[1] + 1,
                    )
                )
    return results


def _extract_if_branch_facts(
    if_node: TSNode,
    source_bytes: bytes,
    file_path: str,
    parent_qualified_name: str,
    parent_kind: str,
    parent_node_id: str,
    indent_level: int = 0,
) -> list[InlineFact]:
    """Extract IMPLEMENTS_BEHAVIOR InlineFacts from an if_statement subtree.

    Recurses into elif and else clauses. Guard conditions (check_mode /
    failed / changed / …) are skipped.

    ``indent_level`` is reserved for future nested-if filtering.
    """
    facts: list[InlineFact] = []

    # ── Main condition (if <cond>) ──────────────────────────────────
    cond = if_node.child_by_field_name("condition")
    cons = if_node.child_by_field_name("consequence")

    condition_text: str | None = None

    if cond is not None:
        condition_text = get_node_text(cond, source_bytes)
    elif if_node.type == "else_clause":
        condition_text = "else"
    else:
        condition_text = None

    if (
        cond is not None
        and condition_text
        and not _is_guard_condition(condition_text)
        and cons is not None
    ):
        call_info = _get_first_call_in_block(cons, source_bytes)
        if call_info is not None:
            call_text, call_line, _ = call_info
            facts.append(
                InlineFact(
                    relation_kind="implements_behavior",
                    subject_node_id=parent_node_id,
                    subject_qualified_name=parent_qualified_name,
                    subject_file_path=file_path,
                    object_literal=condition_text,
                    start_line=cond.start_point[0] + 1,
                    end_line=cond.end_point[0] + 1,
                    evidence_kind="source",
                    extraction_method="parser",
                    metadata={
                        "branch_condition": condition_text,
                        "branch_action": call_text,
                        "branch_type": "if",
                        "call_line": call_line,
                    },
                )
            )

    # ── Recurse into elif / else alternatives ─────────────────────
    # Walk named children after the consequence block. The children
    # are ordered: [condition, consequence_block, *elif_clause_or_else_clause...].
    # We can't use `is` for identity (tree-sitter returns new wrappers),
    # so we track whether we've passed the consequence block by checking
    # for the first block node after the condition.
    passed_consequence = False
    for i in range(if_node.named_child_count):
        child = if_node.named_child(i)
        if child is None:
            continue
        if child.type == "block" and not passed_consequence:
            # First block after condition → consequence body, skip
            passed_consequence = True
            continue
        if not passed_consequence:
            # Still before consequence (the condition and any unnamed children)
            continue
        # After consequence → elif_clause or else_clause
        if child.type == "elif_clause":
            # elif has a condition and consequence
            elif_cond = child.child_by_field_name("condition")
            elif_cons = child.child_by_field_name("consequence")
            if elif_cond is not None and elif_cons is not None:
                elif_cond_text = get_node_text(elif_cond, source_bytes)
                if not _is_guard_condition(elif_cond_text):
                    call_info = _get_first_call_in_block(elif_cons, source_bytes)
                    if call_info is not None:
                        call_text, call_line, _ = call_info
                        facts.append(
                            InlineFact(
                                relation_kind="implements_behavior",
                                subject_node_id=parent_node_id,
                                subject_qualified_name=parent_qualified_name,
                                subject_file_path=file_path,
                                object_literal=elif_cond_text,
                                start_line=elif_cond.start_point[0] + 1,
                                end_line=elif_cond.end_point[0] + 1,
                                evidence_kind="source",
                                extraction_method="parser",
                                metadata={
                                    "branch_condition": elif_cond_text,
                                    "branch_action": call_text,
                                    "branch_type": "elif",
                                    "call_line": call_line,
                                },
                            )
                        )
        elif child.type == "else_clause":
            # else has a body but no condition
            else_body = child.child_by_field_name("body")
            if else_body is not None:
                call_info = _get_first_call_in_block(else_body, source_bytes)
                if call_info is not None:
                    call_text, call_line, _ = call_info
                    facts.append(
                        InlineFact(
                            relation_kind="implements_behavior",
                            subject_node_id=parent_node_id,
                            subject_qualified_name=parent_qualified_name,
                            subject_file_path=file_path,
                            object_literal="else",
                            start_line=child.start_point[0] + 1,
                            end_line=child.end_point[0] + 1,
                            evidence_kind="source",
                            extraction_method="parser",
                            metadata={
                                "branch_condition": "else",
                                "branch_action": call_text,
                                "branch_type": "else",
                                "call_line": call_line,
                            },
                        )
                    )

    return facts


def _python_extract_inline_facts(
    node: TSNode, source_bytes: bytes, file_path: str
) -> list[InlineFact]:
    """Extract STORES_DEFAULT InlineFacts from Python function/method.

    Walks the function_definition AST node:
    1. Determine if this is a method (inside a class_definition) or top-level
       function via ancestor walk.
    2. For each parameter with a default value (typed_default_parameter or
       default_parameter), produce one InlineFact.
    3. Compute the correct qualified_name with parent scope prefix.

    ``source_bytes`` is the raw file content (bytes), used for extracting
    text from AST node byte ranges — consistent with other Python lang hooks
    like ``_python_get_signature``.
    """
    # ── 1. Detect enclosing class for qualified_name + kind ───────────
    is_method = False
    enclosing_class: str | None = None
    # Python grammar: decorated_definition wraps function_definition
    # when decorators are present. Walk past it to find class.
    check: TSNode | None = node
    while check is not None:
        check = check.parent
        if check is None:
            break
        if check.type == "class_definition":
            class_name_node = get_child_by_field(check, "name")
            if class_name_node is not None:
                enclosing_class = get_node_text(class_name_node, source_bytes)
                is_method = True
                break

    # ── 2. Compute qualified_name ─────────────────────────────────────
    name_node = get_child_by_field(node, "name")
    if name_node is None:
        return []
    func_name = get_node_text(name_node, source_bytes)
    qualified_name = f"{enclosing_class}::{func_name}" if is_method else func_name

    # ── 3. Infer NodeKind ─────────────────────────────────────────────
    kind = "method" if is_method else "function"

    # ── 4. Compute Node.id via generate_node_id ───────────────────────
    node_id = generate_node_id(file_path, kind, qualified_name)

    # ── 5. Walk parameters → defaults → InlineFacts ───────────────────
    params_node = get_child_by_field(node, "parameters")
    if params_node is None:
        return []
    facts: list[InlineFact] = []
    for i in range(params_node.named_child_count):
        param = params_node.named_child(i)
        if param is None:
            continue
        if param.type not in _PARAMETER_NODE_KINDS:
            continue
        # parameter name
        pname_node = get_child_by_field(param, "name")
        if pname_node is None:
            continue
        param_name = get_node_text(pname_node, source_bytes)
        # default value (None if no default — guarded by node type above)
        value_node = get_child_by_field(param, "value")
        if value_node is None:
            # typed_default_parameter may have type annotation but no
            # value if the type is annotated without a default.
            # Only default_parameter and typed_default_parameter with
            # a 'value' child produce a fact.
            continue
        default_text = get_node_text(value_node, source_bytes)
        facts.append(
            InlineFact(
                relation_kind="stores_default",
                subject_node_id=node_id,
                subject_qualified_name=qualified_name,
                subject_file_path=file_path,
                object_literal=default_text,
                start_line=value_node.start_point[0] + 1,
                end_line=value_node.end_point[0] + 1,
                evidence_kind="source",
                extraction_method="parser",
                metadata={"parameter_name": param_name},
            )
        )

    # ── 6. Walk function body for if/elif/else branches → IMPLEMENTS_BEHAVIOR ──
    body_node = get_child_by_field(node, "body")
    if body_node is not None:
        for i in range(body_node.named_child_count):
            stmt = body_node.named_child(i)
            if stmt is None:
                continue
            if stmt.type == "if_statement":
                branch_facts = _extract_if_branch_facts(
                    stmt,
                    source_bytes,
                    file_path,
                    qualified_name,
                    kind,
                    node_id,
                )
                facts.extend(branch_facts)

    return facts


# =============================================================================
# Signature extraction
# =============================================================================


def _python_get_signature(node: TSNode, source: bytes) -> str | None:
    params = get_child_by_field(node, "parameters")
    ret = get_child_by_field(node, "return_type")
    if not params:
        return None
    sig = get_node_text(params, source)
    if ret:
        sig += " -> " + get_node_text(ret, source)
    return sig


def _python_is_async(node: TSNode) -> bool:
    prev = node.prev_named_sibling
    return prev is not None and prev.type == "async"


def _python_is_static(node: TSNode, decorator_names: list[str] | None = None) -> bool:
    """Check if a method has the @staticmethod decorator.

    The decorator_names list is populated during extraction by walking
    the decorated_definition parent.
    """
    if decorator_names:
        return "staticmethod" in decorator_names
    return False


def _python_is_classmethod(
    node: TSNode, decorator_names: list[str] | None = None
) -> bool:
    """Check if a method has the @classmethod decorator."""
    if decorator_names:
        return "classmethod" in decorator_names
    return False


def _python_is_property(node: TSNode, decorator_names: list[str] | None = None) -> bool:
    """Check if a method has the @property decorator."""
    if decorator_names:
        return "property" in decorator_names
    return False


def _python_extract_import(node: TSNode, source: bytes) -> dict | None:
    if node.type == "import_from_statement":
        module = get_child_by_field(node, "module_name")
        if module:
            # Collect individual imported names (Y, Z in "from X import Y, Z")
            # for emitting per-name IMPORTS unresolved references.
            import_names: list[dict[str, str | int]] = []
            module_node = module
            for i in range(node.named_child_count):
                child = node.named_child(i)
                if not child:
                    continue
                # Skip the module_name node itself
                if child == module_node:
                    continue
                # Skip wildcard imports
                if child.type == "wildcard_import":
                    continue
                if child.type == "aliased_import":
                    # "from X import Y as Z" -> use alias "Z"
                    alias_node = get_child_by_field(child, "alias")
                    name_node = alias_node or get_child_by_field(child, "name")
                    if name_node:
                        raw = get_node_text(name_node, source)
                        local = raw.split(".")[-1] if "." in raw else raw
                        import_names.append(
                            {
                                "name": local,
                                "line": name_node.start_point[0] + 1,
                                "column": name_node.start_point[1],
                            }
                        )
                elif child.type == "dotted_name":
                    raw = get_node_text(child, source)
                    local = raw.split(".")[-1] if "." in raw else raw
                    import_names.append(
                        {
                            "name": local,
                            "line": child.start_point[0] + 1,
                            "column": child.start_point[1],
                        }
                    )

            return {
                "module_name": get_node_text(module, source),
                "signature": get_node_text(node, source),
                "import_names": import_names,
            }
    return None


PYTHON_EXTRACTOR = LanguageExtractor(
    function_types=["function_definition"],
    class_types=["class_definition"],
    method_types=["function_definition"],
    interface_types=[],
    struct_types=[],
    enum_types=[],
    type_alias_types=[],
    import_types=["import_statement", "import_from_statement"],
    call_types=["call"],
    variable_types=["assignment"],
    name_field="name",
    body_field="body",
    params_field="parameters",
    return_field="return_type",
    decorated_definition_types=["decorated_definition"],
    get_signature=_python_get_signature,
    is_async=_python_is_async,
    is_static=_python_is_static,
    is_classmethod=_python_is_classmethod,
    is_property=_python_is_property,
    extract_import=_python_extract_import,
    extract_inline_facts=_python_extract_inline_facts,
)
