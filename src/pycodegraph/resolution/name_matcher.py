"""Name-based matching strategies for reference resolution."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..types import Node, NodeKind
from ._types import ResolvedRef, UnresolvedRef

if TYPE_CHECKING:
    from ._context import ResolutionContext


def match_by_file_path(
    ref: UnresolvedRef, context: ResolutionContext
) -> ResolvedRef | None:
    """Match path-like references (e.g., 'snippets/drawer-menu.liquid') by filename."""
    if "/" not in ref.reference_name:
        return None

    file_name = ref.reference_name.split("/")[-1]
    if not file_name:
        return None

    candidates = context.get_nodes_by_name(file_name)
    file_nodes = [n for n in candidates if n.kind == NodeKind.FILE]

    if not file_nodes:
        return None

    # Exact path match
    for n in file_nodes:
        if n.qualified_name == ref.reference_name or n.file_path == ref.reference_name:
            return ResolvedRef(
                original=ref,
                target_node_id=n.id,
                confidence=0.95,
                resolved_by="file-path",
            )

    # Suffix match
    for n in file_nodes:
        if n.qualified_name.endswith(ref.reference_name) or n.file_path.endswith(
            ref.reference_name
        ):
            return ResolvedRef(
                original=ref,
                target_node_id=n.id,
                confidence=0.85,
                resolved_by="file-path",
            )

    # Single candidate
    if len(file_nodes) == 1:
        return ResolvedRef(
            original=ref,
            target_node_id=file_nodes[0].id,
            confidence=0.7,
            resolved_by="file-path",
        )

    return None


def match_by_qualified_name(
    ref: UnresolvedRef, context: ResolutionContext
) -> ResolvedRef | None:
    """Match by qualified name (e.g., 'obj.method', 'Class::method')."""
    if "." not in ref.reference_name and "::" not in ref.reference_name:
        return None

    candidates = context.get_nodes_by_qualified_name(ref.reference_name)

    if len(candidates) == 1:
        return ResolvedRef(
            original=ref,
            target_node_id=candidates[0].id,
            confidence=0.95,
            resolved_by="qualified-name",
        )

    # Partial qualified name match
    parts = re.split(r"[.:]", ref.reference_name)
    last_name = parts[-1]
    if last_name:
        partial_candidates = context.get_nodes_by_name(last_name)
        for candidate in partial_candidates:
            if candidate.qualified_name.endswith(ref.reference_name):
                return ResolvedRef(
                    original=ref,
                    target_node_id=candidate.id,
                    confidence=0.85,
                    resolved_by="qualified-name",
                )

    return None


def match_method_call(
    ref: UnresolvedRef, context: ResolutionContext
) -> ResolvedRef | None:
    """Match method calls like 'obj.method' or 'Class::method'."""
    dot_match = re.match(r"^(\w+)\.(\w+)$", ref.reference_name)
    colon_match = re.match(r"^(\w+)::(\w+)$", ref.reference_name)

    m = dot_match or colon_match
    if not m:
        return None

    object_or_class = m.group(1)
    method_name = m.group(2)

    class_like = (NodeKind.CLASS, NodeKind.STRUCT, NodeKind.INTERFACE)

    # Strategy 1: Direct class name match
    class_candidates = context.get_nodes_by_name(object_or_class)
    for class_node in class_candidates:
        if class_node.kind not in class_like:
            continue
        if class_node.language.value != ref.language:
            continue
        nodes_in_file = context.get_nodes_in_file(class_node.file_path)
        for n in nodes_in_file:
            if (
                n.kind == NodeKind.METHOD
                and n.name == method_name
                and class_node.name in n.qualified_name
            ):
                return ResolvedRef(
                    original=ref,
                    target_node_id=n.id,
                    confidence=0.85,
                    resolved_by="qualified-name",
                )

    # Strategy 2: Capitalized receiver → class lookup
    capitalized = object_or_class[0].upper() + object_or_class[1:]
    if capitalized != object_or_class:
        fuzzy_class_candidates = context.get_nodes_by_name(capitalized)
        for class_node in fuzzy_class_candidates:
            if class_node.kind not in class_like:
                continue
            if class_node.language.value != ref.language:
                continue
            nodes_in_file = context.get_nodes_in_file(class_node.file_path)
            for n in nodes_in_file:
                if (
                    n.kind == NodeKind.METHOD
                    and n.name == method_name
                    and class_node.name in n.qualified_name
                ):
                    return ResolvedRef(
                        original=ref,
                        target_node_id=n.id,
                        confidence=0.8,
                        resolved_by="instance-method",
                    )

    # Strategy 3: Find methods by name, disambiguate by receiver-class similarity
    if method_name:
        method_candidates = context.get_nodes_by_name(method_name)
        methods = [
            n
            for n in method_candidates
            if n.kind == NodeKind.METHOD and n.name == method_name
        ]

        same_lang = [
            m_node for m_node in methods if m_node.language.value == ref.language
        ]
        target_methods = same_lang if same_lang else methods

        if (
            len(target_methods) == 1
            and target_methods[0].language.value == ref.language
        ):
            return ResolvedRef(
                original=ref,
                target_node_id=target_methods[0].id,
                confidence=0.7,
                resolved_by="instance-method",
            )

        if len(target_methods) > 1:
            receiver_words = _split_camel_case(object_or_class)
            best_match = None
            best_score = 0
            for method_node in target_methods:
                class_words = _split_camel_case(method_node.qualified_name)
                score = sum(
                    1
                    for w in receiver_words
                    if any(cw.lower() == w.lower() for cw in class_words)
                )
                if method_node.language.value == ref.language:
                    score += 1
                if score > best_score:
                    best_score = score
                    best_match = method_node
            if best_match and best_score >= 2:
                return ResolvedRef(
                    original=ref,
                    target_node_id=best_match.id,
                    confidence=0.65,
                    resolved_by="instance-method",
                )

    return None


def match_by_exact_name(
    ref: UnresolvedRef, context: ResolutionContext
) -> ResolvedRef | None:
    """Match by exact symbol name."""
    candidates = context.get_nodes_by_name(ref.reference_name)

    if not candidates:
        return None

    if len(candidates) == 1:
        is_cross_language = candidates[0].language.value != ref.language
        return ResolvedRef(
            original=ref,
            target_node_id=candidates[0].id,
            confidence=0.5 if is_cross_language else 0.9,
            resolved_by="exact-match",
        )

    # Multiple matches — find best
    best = _find_best_match(ref, candidates, context)
    if best:
        proximity = _compute_path_proximity(ref.file_path, best.file_path)
        confidence = 0.7 if proximity >= 30 else 0.4
        return ResolvedRef(
            original=ref,
            target_node_id=best.id,
            confidence=confidence,
            resolved_by="exact-match",
        )

    return None


def match_fuzzy(ref: UnresolvedRef, context: ResolutionContext) -> ResolvedRef | None:
    """Case-insensitive fallback match."""
    lower_name = ref.reference_name.lower()
    candidates = context.get_nodes_by_lower_name(lower_name)

    callable_kinds = {NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS}
    callable_candidates = [n for n in candidates if n.kind in callable_kinds]

    same_lang = [n for n in callable_candidates if n.language.value == ref.language]
    final = same_lang if same_lang else callable_candidates

    if len(final) == 1:
        is_cross_language = final[0].language.value != ref.language
        return ResolvedRef(
            original=ref,
            target_node_id=final[0].id,
            confidence=0.3 if is_cross_language else 0.5,
            resolved_by="fuzzy",
        )

    return None


def match_reference(
    ref: UnresolvedRef, context: ResolutionContext
) -> ResolvedRef | None:
    """Try all matching strategies in order of confidence."""
    result = match_by_file_path(ref, context)
    if result:
        return result

    result = match_by_qualified_name(ref, context)
    if result:
        return result

    result = match_method_call(ref, context)
    if result:
        return result

    result = match_by_exact_name(ref, context)
    if result:
        return result

    result = match_fuzzy(ref, context)
    if result:
        return result

    return None


# --- Helpers ---


def _split_camel_case(s: str) -> list[str]:
    """Split a camelCase or PascalCase string into words."""
    return [
        w
        for w in re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
        .replace("_", " ")
        .replace(":", " ")
        .replace(".", " ")
        .replace("/", " ")
        .replace("\\", " ")
        .split()
        if len(w) > 1
    ]


def _compute_path_proximity(path1: str, path2: str) -> int:
    """Score based on shared directory segments."""
    dir1 = path1.split("/")[:-1]
    dir2 = path2.split("/")[:-1]
    shared = 0
    for i in range(min(len(dir1), len(dir2))):
        if dir1[i] == dir2[i]:
            shared += 1
        else:
            break
    return min(shared * 15, 80)


def _find_best_match(
    ref: UnresolvedRef,
    candidates: list[Node],
    context: ResolutionContext,
) -> Node | None:
    """Find the best matching node among multiple candidates."""
    best_score = -1
    best_node: Node | None = None

    for candidate in candidates:
        score = 0

        # Same file bonus
        if candidate.file_path == ref.file_path:
            score += 100

        # Directory proximity
        score += _compute_path_proximity(ref.file_path, candidate.file_path)

        # Language matching
        if candidate.language.value == ref.language:
            score += 50
        else:
            score -= 80

        # Kind preference
        if ref.reference_kind.value == "calls":
            if candidate.kind in (NodeKind.FUNCTION, NodeKind.METHOD):
                score += 25
        elif ref.reference_kind.value == "instantiates":
            if candidate.kind in (NodeKind.CLASS, NodeKind.STRUCT, NodeKind.INTERFACE):
                score += 25
        elif ref.reference_kind.value == "decorates":
            if candidate.kind in (NodeKind.FUNCTION, NodeKind.METHOD):
                score += 25
            elif candidate.kind in (NodeKind.CLASS, NodeKind.INTERFACE):
                score += 15

        # Exported bonus
        if candidate.is_exported:
            score += 10

        # Line proximity (same file)
        if candidate.file_path == ref.file_path and candidate.start_line:
            distance = abs(candidate.start_line - ref.line)
            score += max(0, 20 - distance // 10)

        if score > best_score:
            best_score = score
            best_node = candidate

    return best_node
