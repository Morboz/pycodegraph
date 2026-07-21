"""Language-specific extraction configurations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

# InlineFact is used as a type hint for the extract_inline_facts hook.
# Imported at runtime via TYPE_CHECKING to avoid circular dependency.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass
class LanguageExtractor:
    """Per-language extraction configuration mapping AST node types to extraction logic."""

    # Node type mappings
    function_types: list[str] = field(default_factory=list)
    class_types: list[str] = field(default_factory=list)
    method_types: list[str] = field(default_factory=list)
    interface_types: list[str] = field(default_factory=list)
    struct_types: list[str] = field(default_factory=list)
    enum_types: list[str] = field(default_factory=list)
    enum_member_types: list[str] = field(default_factory=list)
    type_alias_types: list[str] = field(default_factory=list)
    import_types: list[str] = field(default_factory=list)
    call_types: list[str] = field(default_factory=list)
    variable_types: list[str] = field(default_factory=list)
    field_types: list[str] = field(default_factory=list)
    property_types: list[str] = field(default_factory=list)

    # Field name mappings
    name_field: str = "name"
    body_field: str = "body"
    params_field: str = "parameters"
    return_field: str | None = None

    # Config flags
    methods_are_top_level: bool = False

    # Node type that wraps decorated definitions (Python: decorated_definition)
    decorated_definition_types: list[str] = field(default_factory=list)

    # Hook functions
    get_signature: Callable | None = None
    get_visibility: Callable | None = None
    is_exported: Callable | None = None
    is_async: Callable | None = None
    is_static: Callable | None = None
    is_classmethod: Callable | None = None
    is_const: Callable | None = None
    is_property: Callable | None = None
    extract_import: Callable | None = None
    get_receiver_type: Callable | None = None
    resolve_type_alias_kind: Callable | None = None
    classify_class_node: Callable | None = None

    # Inline fact extraction hook (issue #114). Called after a function/method
    # node's body has been visited during Tree-sitter traversal. Receives the
    # function/method AST node, source bytes, and file path. Returns any typed
    # facts discovered (parameter defaults, call-site arguments, branch
    # conditions, etc.) for later flush into SemanticRelation rows.
    #
    # Signature: (node: TSNode, source_bytes: bytes, file_path: str) -> list[InlineFact]
    # source_bytes is raw file content (bytes), consistent with other Python
    # lang hooks that use get_node_text(node, source_bytes).
    extract_inline_facts: Callable | None = None
