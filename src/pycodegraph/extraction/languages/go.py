"""Go language extractor."""

from __future__ import annotations

from tree_sitter import Node as TSNode

from ...extraction.helpers import get_node_text
from ...types import NodeKind
from .base import LanguageExtractor


def _go_get_receiver_type(node: TSNode, source: bytes) -> str | None:
    receiver = node.child_by_field_name("receiver")
    if not receiver:
        return None
    # (sl *scrapeLoop) → extract "scrapeLoop"
    for i in range(receiver.named_child_count):
        c = receiver.named_child(i)
        if c and c.type == "parameter_declaration":
            typ = c.child_by_field_name("type")
            if typ:
                text = get_node_text(typ, source)
                return text.lstrip("*")
    return None


def _go_resolve_type_alias_kind(node: TSNode, source: bytes) -> NodeKind | None:
    type_child = node.child_by_field_name("type")
    if not type_child:
        return None
    if type_child.type == "struct_type":
        return NodeKind.STRUCT
    if type_child.type == "interface_type":
        return NodeKind.INTERFACE
    return None


def _go_is_const(node: TSNode) -> bool:
    return node.type == "const_declaration"


GO_EXTRACTOR = LanguageExtractor(
    function_types=["function_declaration"],
    class_types=[],
    method_types=["method_declaration", "function_declaration"],
    interface_types=[],
    struct_types=["struct_type"],
    enum_types=[],
    type_alias_types=["type_declaration"],
    import_types=["import_declaration"],
    call_types=["call_expression"],
    variable_types=["var_declaration", "short_var_declaration", "const_declaration"],
    name_field="name",
    body_field="body",
    params_field="parameters",
    return_field="result",
    methods_are_top_level=True,
    get_receiver_type=_go_get_receiver_type,
    resolve_type_alias_kind=_go_resolve_type_alias_kind,
    is_const=_go_is_const,
)
