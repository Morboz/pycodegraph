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
    guard_only: bool = False,
    relation_kind: str = "implements_behavior",
) -> list[InlineFact]:
    """Extract IMPLEMENTS_BEHAVIOR (or GUARDS_EFFECT) InlineFacts from an
    if_statement subtree.

    When ``guard_only=False`` (default): extracts non-guard conditions →
    IMPLEMENTS_BEHAVIOR.

    When ``guard_only=True``: extracts guard conditions (check_mode / failed /
    changed / …) → GUARDS_EFFECT.

    Recurses into elif and else clauses. An else clause is never a guard.
    """
    facts: list[InlineFact] = []

    def _should_extract(text: str | None) -> bool:
        """Determine whether this condition should be extracted based on
        guard_only mode.
        """
        if text is None:
            return False
        is_guard = _is_guard_condition(text)
        return is_guard if guard_only else (not is_guard)

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

    if cond is not None and _should_extract(condition_text) and cons is not None:
        call_info = _get_first_call_in_block(cons, source_bytes)
        if call_info is not None:
            call_text, call_line, _ = call_info
            facts.append(
                InlineFact(
                    relation_kind=relation_kind,
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
                if _should_extract(elif_cond_text):
                    call_info = _get_first_call_in_block(elif_cons, source_bytes)
                    if call_info is not None:
                        call_text, call_line, _ = call_info
                        facts.append(
                            InlineFact(
                                relation_kind=relation_kind,
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
            # else has a body but no condition; only extract in non-guard mode
            if not guard_only:
                else_body = child.child_by_field_name("body")
                if else_body is not None:
                    call_info = _get_first_call_in_block(else_body, source_bytes)
                    if call_info is not None:
                        call_text, call_line, _ = call_info
                        facts.append(
                            InlineFact(
                                relation_kind=relation_kind,
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

    # ── 6. Walk function body for if/elif/else branches → IMPLEMENTS_BEHAVIOR + GUARDS_EFFECT ──
    body_node = get_child_by_field(node, "body")
    if body_node is not None:
        for i in range(body_node.named_child_count):
            stmt = body_node.named_child(i)
            if stmt is None:
                continue
            if stmt.type == "if_statement":
                # IMPLEMENTS_BEHAVIOR — non-guard branches
                branch_facts = _extract_if_branch_facts(
                    stmt,
                    source_bytes,
                    file_path,
                    qualified_name,
                    kind,
                    node_id,
                    guard_only=False,
                    relation_kind="implements_behavior",
                )
                facts.extend(branch_facts)
                # GUARDS_EFFECT — guard branches (check_mode/failed/changed/…)
                guard_facts = _extract_if_branch_facts(
                    stmt,
                    source_bytes,
                    file_path,
                    qualified_name,
                    kind,
                    node_id,
                    guard_only=True,
                    relation_kind="guards_effect",
                )
                facts.extend(guard_facts)

    # ── 7. Walk function body for call-site param forwarding → FORWARDS_VALUE ──
    if body_node is not None:
        param_names = _get_parameter_names(params_node, source_bytes)
        forward_facts = _extract_forward_value_facts(
            body_node,
            source_bytes,
            file_path,
            qualified_name,
            node_id,
            param_names,
        )
        facts.extend(forward_facts)

    return facts


def _get_parameter_names(params_node: TSNode, source_bytes: bytes) -> set[str]:
    """Extract parameter names from a function's parameter AST node.

    Returns a set of parameter names (excluding self/cls).
    """
    names: set[str] = set()
    for i in range(params_node.named_child_count):
        param = params_node.named_child(i)
        if param is None:
            continue
        # parameter nodes: identifier, default_parameter, typed_default_parameter,
        # typed_parameter, list_splat_pattern, dictionary_splat_pattern
        name_node = get_child_by_field(param, "name")
        if name_node is None and param.type == "identifier":
            # Simple bare parameter like ``x``
            pname = get_node_text(param, source_bytes)
            if pname in ("self", "cls"):
                continue
            names.add(pname)
            continue
        if name_node is None:
            continue
        pname = get_node_text(name_node, source_bytes)
        if pname in ("self", "cls"):
            continue
        names.add(pname)
    return names


def _extract_forward_value_facts(
    body_node: TSNode,
    source_bytes: bytes,
    file_path: str,
    qualified_name: str,
    node_id: str,
    param_names: set[str],
) -> list[InlineFact]:
    """Extract FORWARDS_VALUE InlineFacts by walking function body for
    call sites where a function parameter is forwarded to a callee argument.

    Matches:
      ``helper(x)`` — positional, ``x`` is a function param
      ``helper(arg=x)`` — keyword, ``x`` is a function param

    Does NOT match transforms (``helper(x + 1)``), attribute access
    (``self.x``), or complex expressions.
    """
    facts: list[InlineFact] = []
    for i in range(body_node.named_child_count):
        stmt = body_node.named_child(i)
        if stmt is None:
            continue

        # Find call node(s) in this statement
        calls_in_stmt = _collect_calls_in_stmt(stmt, source_bytes)
        for call_node, call_text in calls_in_stmt:
            arg_list = _get_argument_list(call_node)
            if arg_list is None:
                continue

            arg_idx = 0
            for j in range(arg_list.named_child_count):
                arg = arg_list.named_child(j)
                if arg is None:
                    continue

                pname: str | None = None
                arg_type: str = "positional"
                kw_name: str | None = None

                if arg.type == "identifier":
                    pname = get_node_text(arg, source_bytes)
                elif arg.type == "keyword_argument":
                    kw_name_node = arg.child_by_field_name("name")
                    kw_val = arg.child_by_field_name("value")
                    if kw_val is not None and kw_val.type == "identifier":
                        pname = get_node_text(kw_val, source_bytes)
                        kw_name = (
                            get_node_text(kw_name_node, source_bytes)
                            if kw_name_node
                            else None
                        )
                        arg_type = "keyword"
                else:
                    arg_idx += 1
                    continue

                if pname is None or pname not in param_names:
                    arg_idx += 1
                    continue

                callee_name = call_text.split("(")[0] if call_text else ""
                obj_text = f"{callee_name}.{kw_name or arg_idx}"

                metadata: dict = {
                    "callee": callee_name,
                    "param_index": arg_idx,
                    "param_name": pname,
                    "arg_type": arg_type,
                    "call_line": call_node.start_point[0] + 1,
                }
                if kw_name is not None:
                    metadata["kw_arg_name"] = kw_name

                facts.append(
                    InlineFact(
                        relation_kind="forwards_value",
                        subject_node_id=node_id,
                        subject_qualified_name=qualified_name,
                        subject_file_path=file_path,
                        object_literal=obj_text,
                        start_line=arg.start_point[0] + 1,
                        end_line=arg.end_point[0] + 1,
                        evidence_kind="source",
                        extraction_method="parser",
                        metadata=metadata,
                    )
                )
                arg_idx += 1
    return facts


def _collect_calls_in_stmt(
    stmt: TSNode, source_bytes: bytes
) -> list[tuple[TSNode, str]]:
    """Collect call nodes from a statement, returning (call_node, text) pairs.

    Handles: ``helper(x)``, ``result = helper(x)``, and other patterns
    where a call is the main action.
    """
    results: list[tuple[TSNode, str]] = []
    if stmt.type == "expression_statement":
        for i in range(stmt.named_child_count):
            child = stmt.named_child(i)
            if child is None:
                continue
            if child.type == "call":
                text = get_node_text(child, source_bytes)
                results.append((child, text))
            elif child.type == "assignment":
                # ``result = helper(x)`` — recurse into assignment
                results.extend(_collect_calls_in_stmt(child, source_bytes))
    elif stmt.type == "assignment":
        # ``result = helper(x)`` — the call is a named child
        # (first is ``left`` identifier). Python grammar may not name this
        # field, so we scan named children for a ``call`` node.
        for i in range(stmt.named_child_count):
            child = stmt.named_child(i)
            if child is not None and child.type == "call":
                text = get_node_text(child, source_bytes)
                results.append((child, text))
    return results


def _get_argument_list(call_node: TSNode) -> TSNode | None:
    """Get the argument_list child of a call node."""
    for i in range(call_node.named_child_count):
        child = call_node.named_child(i)
        if child is not None and child.type == "argument_list":
            return child
    return None


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
