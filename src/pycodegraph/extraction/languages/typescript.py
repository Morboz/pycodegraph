"""TypeScript/JavaScript language extractor."""

from __future__ import annotations

from tree_sitter import Node as TSNode

from .base import LanguageExtractor
from ...extraction.helpers import get_node_text, get_child_by_field


def _ts_get_signature(node: TSNode, source: bytes) -> str | None:
    params = get_child_by_field(node, "parameters")
    ret = get_child_by_field(node, "return_type")
    if not params:
        return None
    sig = get_node_text(params, source)
    if ret:
        sig += ": " + get_node_text(ret, source)
    return sig


def _ts_is_exported(node: TSNode, source: bytes) -> bool:
    parent = node.parent
    while parent:
        if parent.type == "export_statement":
            return True
        if parent.type in ("function_declaration", "class_declaration",
                           "lexical_declaration", "variable_declaration"):
            break
        parent = parent.parent
    return False


def _ts_is_async(node: TSNode) -> bool:
    child = node.child_by_field_name("body")
    if child and child.type == "async":
        return True
    for i in range(node.child_count):
        c = node.child(i)
        if c and c.type == "async":
            return True
    return False


def _ts_is_const(node: TSNode) -> bool:
    if node.type == "lexical_declaration":
        for i in range(node.child_count):
            c = node.child(i)
            if c and c.type == "const":
                return True
    return False


def _ts_extract_import(node: TSNode, source: bytes) -> dict | None:
    source_str = get_node_text(node, source)
    # import_statement: source is the module
    if node.type == "import_statement":
        for i in range(node.named_child_count):
            c = node.named_child(i)
            if c and c.type == "string":
                name = get_node_text(c, source).strip("'\"")
                return {"module_name": name, "signature": source_str}
        return None
    # import_clause: from "module" import ...
    if node.type == "import_from_clause":
        # Walk parent import_statement to find source
        parent = node.parent
        if parent:
            for i in range(parent.named_child_count):
                c = parent.named_child(i)
                if c and c.type == "string":
                    name = get_node_text(c, source).strip("'\"")
                    return {"module_name": name, "signature": get_node_text(parent, source)}
    return None


def _ts_get_visibility(node: TSNode) -> str | None:
    for i in range(node.child_count):
        c = node.child(i)
        if c and not c.is_named:
            text = c.type
            if text in ("public", "private", "protected"):
                return text
    return None


TYPESCRIPT_EXTRACTOR = LanguageExtractor(
    function_types=["function_declaration", "function_expression", "arrow_function", "generator_function_declaration"],
    class_types=["class_declaration", "class"],
    method_types=["method_definition", "generator_function_declaration"],
    interface_types=["interface_declaration"],
    struct_types=[],
    enum_types=["enum_declaration"],
    type_alias_types=["type_alias_declaration"],
    import_types=["import_statement"],
    call_types=["call_expression"],
    variable_types=["lexical_declaration", "variable_declaration"],
    property_types=["public_field_definition", "property_definition"],
    field_types=["public_field_definition"],
    name_field="name",
    body_field="body",
    params_field="parameters",
    return_field="return_type",
    get_signature=_ts_get_signature,
    get_visibility=_ts_get_visibility,
    is_exported=_ts_is_exported,
    is_async=_ts_is_async,
    is_const=_ts_is_const,
    extract_import=_ts_extract_import,
)

JAVASCRIPT_EXTRACTOR = TYPESCRIPT_EXTRACTOR
JSX_EXTRACTOR = TYPESCRIPT_EXTRACTOR
TSX_EXTRACTOR = TYPESCRIPT_EXTRACTOR
