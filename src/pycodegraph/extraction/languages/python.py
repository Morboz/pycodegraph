"""Python language extractor."""

from __future__ import annotations

from tree_sitter import Node as TSNode

from ...extraction.helpers import get_child_by_field, get_node_text
from .base import LanguageExtractor


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


def _python_is_static(node: TSNode) -> bool:
    prev = node.prev_named_sibling
    if prev and prev.type == "decorator":
        return False
    return False


def _python_extract_import(node: TSNode, source: bytes) -> dict | None:
    if node.type == "import_from_statement":
        module = get_child_by_field(node, "module_name")
        if module:
            return {
                "module_name": get_node_text(module, source),
                "signature": get_node_text(node, source),
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
    get_signature=_python_get_signature,
    is_async=_python_is_async,
    extract_import=_python_extract_import,
)
