"""Language-specific extraction configurations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Callable

from tree_sitter import Node as TSNode

from ...types import NodeKind


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
    return_field: Optional[str] = None

    # Config flags
    methods_are_top_level: bool = False

    # Hook functions
    get_signature: Optional[Callable] = None
    get_visibility: Optional[Callable] = None
    is_exported: Optional[Callable] = None
    is_async: Optional[Callable] = None
    is_static: Optional[Callable] = None
    is_const: Optional[Callable] = None
    extract_import: Optional[Callable] = None
    get_receiver_type: Optional[Callable] = None
    resolve_type_alias_kind: Optional[Callable] = None
    classify_class_node: Optional[Callable] = None
