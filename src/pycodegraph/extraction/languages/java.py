"""Java language extractor."""

from __future__ import annotations

from tree_sitter import Node as TSNode

from ...extraction.helpers import get_child_by_field, get_node_text
from .base import LanguageExtractor


def _java_get_visibility(node: TSNode) -> str | None:
    for i in range(node.child_count):
        c = node.child(i)
        if c and not c.is_named:
            t = c.type
            if t in ("public", "private", "protected"):
                return t
    return None


def _java_is_static(node: TSNode) -> bool:
    for i in range(node.child_count):
        c = node.child(i)
        if c and not c.is_named and c.type == "static":
            return True
    return False


def _java_get_signature(node: TSNode, source: bytes) -> str | None:
    params = get_child_by_field(node, "parameters")
    ret = get_child_by_field(node, "type")
    sig_parts = []
    if ret:
        sig_parts.append(get_node_text(ret, source))
    if params:
        sig_parts.append(get_node_text(params, source))
    return " ".join(sig_parts) if sig_parts else None


JAVA_EXTRACTOR = LanguageExtractor(
    function_types=["method_declaration", "constructor_declaration"],
    class_types=["class_declaration"],
    method_types=["method_declaration", "constructor_declaration"],
    interface_types=["interface_declaration"],
    struct_types=[],
    enum_types=["enum_declaration"],
    type_alias_types=[],
    import_types=["import_declaration"],
    call_types=["method_invocation"],
    variable_types=[],
    field_types=["field_declaration"],
    name_field="name",
    body_field="body",
    params_field="parameters",
    return_field="type",
    get_signature=_java_get_signature,
    get_visibility=_java_get_visibility,
    is_static=_java_is_static,
)
