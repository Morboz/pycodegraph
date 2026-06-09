"""Language-specific extraction configurations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


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
