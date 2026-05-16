"""Rust language extractor."""

from __future__ import annotations

from tree_sitter import Node as TSNode

from .base import LanguageExtractor
from ...extraction.helpers import get_node_text, get_child_by_field
from ...types import NodeKind


def _rust_get_visibility(node: TSNode) -> str | None:
    for i in range(node.child_count):
        c = node.child(i)
        if c and not c.is_named:
            t = c.type
            if t == "pub":
                return "public"
            if t == "pub(crate)" or t == "pub(super)":
                return "internal"
    return None


def _rust_is_async(node: TSNode) -> bool:
    for i in range(node.child_count):
        c = node.child(i)
        if c and not c.is_named and c.type == "async":
            return True
    return False


def _rust_is_static(node: TSNode) -> bool:
    # Rust doesn't have "static" methods in the same way; associated functions
    # without self are effectively static
    return False


RUST_EXTRACTOR = LanguageExtractor(
    function_types=["function_item"],
    class_types=[],
    method_types=["function_item"],
    interface_types=["trait_item"],
    struct_types=["struct_item"],
    enum_types=["enum_item"],
    enum_member_types=["enum_variant"],
    type_alias_types=["type_item"],
    import_types=["use_declaration"],
    call_types=["call_expression"],
    variable_types=["let_declaration", "const_item", "static_item"],
    name_field="name",
    body_field="body",
    params_field="parameters",
    return_field="return_type",
    get_visibility=_rust_get_visibility,
    is_async=_rust_is_async,
    is_static=_rust_is_static,
)
