"""Tree-sitter AST visitor that extracts nodes, edges, and unresolved references."""

from __future__ import annotations

import json
import time
from pathlib import Path

from tree_sitter import Node as TSNode
from tree_sitter import Tree

from ..types import (
    Edge,
    EdgeKind,
    ExtractionError,
    ExtractionResult,
    Language,
    Node,
    NodeKind,
    UnresolvedReference,
)
from .grammars import detect_language, get_parser, is_language_supported
from .helpers import (
    generate_node_id,
    get_child_by_field,
    get_node_text,
    get_preceding_docstring,
)
from .languages import EXTRACTORS
from .languages.base import LanguageExtractor

INSTANTIATION_KINDS = frozenset(
    [
        "new_expression",
        "object_creation_expression",
        "instance_creation_expression",
    ]
)

BUILTIN_TYPES = frozenset(
    [
        "string",
        "number",
        "boolean",
        "void",
        "null",
        "undefined",
        "never",
        "any",
        "unknown",
        "object",
        "symbol",
        "bigint",
        "true",
        "false",
        "str",
        "bool",
        "int",
        "float",
        "complex",
        "bytes",
        "bytearray",
        "i8",
        "i16",
        "i32",
        "i64",
        "isize",
        "u8",
        "u16",
        "u32",
        "u64",
        "usize",
        "f32",
        "f64",
        "char",
        "long",
        "short",
        "byte",
        "double",
        "int8",
        "int16",
        "int32",
        "int64",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "float32",
        "float64",
        "complex64",
        "complex128",
        "rune",
        "error",
    ]
)

TYPE_ANNOTATION_LANGUAGES = frozenset(
    [
        Language.TYPESCRIPT,
        Language.TSX,
        Language.DART,
        Language.KOTLIN,
        Language.SWIFT,
        Language.RUST,
        Language.GO,
        Language.JAVA,
        Language.CSHARP,
    ]
)


class TreeSitterExtractor:
    """Core AST visitor that extracts code symbols from a parsed source file."""

    def __init__(self, file_path: str, source: str, language: Language | None = None):
        self.file_path = file_path
        self.source_bytes = source.encode("utf-8")
        self.language = language or detect_language(file_path)
        self.extractor: LanguageExtractor | None = EXTRACTORS.get(self.language)
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        self.unresolved_refs: list[UnresolvedReference] = []
        self.errors: list[ExtractionError] = []
        self.node_stack: list[str] = []
        # Decorator context: (name, line, column) tuples for decorators on the
        # current decorated_definition
        self._pending_decorators: list[tuple[str, int, int]] = []

    def extract(self) -> ExtractionResult:
        start = time.time()

        if not is_language_supported(self.language):
            return ExtractionResult(
                errors=[
                    ExtractionError(
                        message=f"Unsupported language: {self.language}",
                        file_path=self.file_path,
                        code="unsupported_language",
                    )
                ],
                duration_ms=int((time.time() - start) * 1000),
            )

        parser = get_parser(self.language)
        if not parser:
            return ExtractionResult(
                errors=[
                    ExtractionError(
                        message=f"No parser for language: {self.language}",
                        file_path=self.file_path,
                        code="parser_error",
                    )
                ],
                duration_ms=int((time.time() - start) * 1000),
            )

        try:
            tree: Tree = parser.parse(self.source_bytes)
            if not tree or not tree.root_node:
                raise ValueError("Parser returned null tree")

            # Create file node
            line_count = self.source_bytes.count(b"\n") + 1
            file_node = Node(
                id=f"file:{self.file_path}",
                kind=NodeKind.FILE,
                name=Path(self.file_path).name,
                qualified_name=self.file_path,
                file_path=self.file_path,
                language=self.language,
                start_line=1,
                end_line=line_count,
                start_column=0,
                end_column=0,
                updated_at=int(time.time() * 1000),
            )
            self.nodes.append(file_node)

            self.node_stack.append(file_node.id)
            self._visit_node(tree.root_node)
            self.node_stack.pop()

        except Exception as e:
            self.errors.append(
                ExtractionError(
                    message=f"Parse error: {e}",
                    file_path=self.file_path,
                    code="parse_error",
                )
            )
        finally:
            if "tree" in dir():
                pass  # Python tree-sitter GC handles cleanup

        return ExtractionResult(
            nodes=self.nodes,
            edges=self.edges,
            unresolved_references=self.unresolved_refs,
            errors=self.errors,
            duration_ms=int((time.time() - start) * 1000),
        )

    # =========================================================================
    # AST Visitor
    # =========================================================================

    def _visit_node(self, node: TSNode) -> None:
        if not self.extractor:
            return

        node_type = node.type
        skip_children = False

        # Decorated definitions (e.g. Python @decorator def foo(): ...)
        # We extract decorator names here and pass them to the inner definition
        # handler so that Node.decorators, DECORATES refs, and is_static/is_property
        # are populated correctly.
        if node_type in (self.extractor.decorated_definition_types or []):
            self._handle_decorated_definition(node)
            skip_children = True

        # Function declarations
        elif node_type in self.extractor.function_types:
            if (
                self._is_inside_class_like()
                and node_type in self.extractor.method_types
            ):
                self._extract_method(node)
            else:
                self._extract_function(node)
            skip_children = True

        # Class declarations
        elif node_type in self.extractor.class_types:
            classification = "class"
            if self.extractor.classify_class_node:
                classification = self.extractor.classify_class_node(
                    node, self.source_bytes
                )
            if classification == "struct":
                self._extract_struct(node)
            elif classification == "enum":
                self._extract_enum(node)
            elif classification == "interface":
                self._extract_interface(node)
            elif classification == "trait":
                self._extract_class(node, kind=NodeKind.TRAIT)
            else:
                self._extract_class(node)
            skip_children = True

        # Method declarations (not already handled by function_types)
        elif node_type in self.extractor.method_types:
            self._extract_method(node)
            skip_children = True

        # Interface/protocol/trait
        elif node_type in self.extractor.interface_types:
            self._extract_interface(node)
            skip_children = True

        # Struct
        elif node_type in self.extractor.struct_types:
            self._extract_struct(node)
            skip_children = True

        # Enum
        elif node_type in self.extractor.enum_types:
            self._extract_enum(node)
            skip_children = True

        # Type alias
        elif node_type in self.extractor.type_alias_types:
            skip_children = self._extract_type_alias(node)

        # Property
        elif (
            node_type in (self.extractor.property_types or [])
            and self._is_inside_class_like()
        ):
            self._extract_property(node)
            skip_children = True

        # Field
        elif (
            node_type in (self.extractor.field_types or [])
            and self._is_inside_class_like()
        ):
            self._extract_field(node)
            skip_children = True

        # Variable
        elif (
            node_type in self.extractor.variable_types
            and not self._is_inside_class_like()
        ):
            self._extract_variable(node)
            skip_children = True

        # Import
        elif node_type in self.extractor.import_types:
            self._extract_import(node)

        # Call
        elif node_type in self.extractor.call_types:
            self._extract_call(node)

        # Instantiation (new expression)
        elif node_type in INSTANTIATION_KINDS:
            self._extract_instantiation(node)

        # Rust impl_item
        elif node_type == "impl_item":
            self._extract_rust_impl_item(node)

        # Recurse into children if not handled
        if not skip_children:
            for i in range(node.named_child_count):
                child = node.named_child(i)
                if child:
                    self._visit_node(child)

    # =========================================================================
    # Node Creation
    # =========================================================================

    def _create_node(
        self,
        kind: NodeKind,
        name: str,
        node: TSNode,
        **extra,
    ) -> Node | None:
        if not name:
            return None

        node_id = generate_node_id(self.file_path, kind, name, node.start_point[0] + 1)

        qname = extra.pop("qualified_name", None) or self._build_qualified_name(name)

        new_node = Node(
            id=node_id,
            kind=kind,
            name=name,
            qualified_name=qname,
            file_path=self.file_path,
            language=self.language,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            start_column=node.start_point[1],
            end_column=node.end_point[1],
            updated_at=int(time.time() * 1000),
            **extra,
        )
        self.nodes.append(new_node)

        # Containment edge from parent
        if self.node_stack:
            parent_id = self.node_stack[-1]
            if parent_id:
                self.edges.append(
                    Edge(
                        source=parent_id,
                        target=node_id,
                        kind=EdgeKind.CONTAINS,
                    )
                )

        return new_node

    def _build_qualified_name(self, name: str) -> str:
        parts: list[str] = []
        for nid in self.node_stack:
            node = next((n for n in self.nodes if n.id == nid), None)
            if node and node.kind != NodeKind.FILE:
                parts.append(node.name)
        parts.append(name)
        return "::".join(parts)

    def _is_inside_class_like(self) -> bool:
        if not self.node_stack:
            return False
        parent_id = self.node_stack[-1]
        if not parent_id:
            return False
        parent = next((n for n in self.nodes if n.id == parent_id), None)
        if not parent:
            return False
        return parent.kind in (
            NodeKind.CLASS,
            NodeKind.STRUCT,
            NodeKind.INTERFACE,
            NodeKind.TRAIT,
            NodeKind.ENUM,
            NodeKind.MODULE,
        )

    @property
    def _pending_decorator_names(self) -> list[str]:
        """Convenience: list of just the decorator names from pending decorators."""
        return [name for name, _line, _col in self._pending_decorators]

    def _call_hook_with_decorator_names(
        self, hook: callable, node: TSNode
    ) -> bool | None:
        """Call an is_static/is_property/is_classmethod hook safely.

        Some hooks accept (node, decorator_names) while others only accept (node).
        Try the extended signature first; fall back to the basic one on TypeError.
        """
        try:
            return hook(node, self._pending_decorator_names)  # type: ignore[call-arg]
        except TypeError:
            return hook(node)  # type: ignore[call-arg]

    # =========================================================================
    # Decorator Handling
    # =========================================================================

    def _handle_decorated_definition(self, node: TSNode) -> None:
        """Handle a decorated_definition node by extracting decorator names,
        then visiting the inner definition so it picks up the pending decorators."""
        decorators: list[tuple[str, int, int]] = []

        for i in range(node.named_child_count):
            child = node.named_child(i)
            if not child:
                continue
            if child.type == "decorator":
                name = self._extract_decorator_name(child)
                if name:
                    decorators.append(
                        (name, child.start_point[0] + 1, child.start_point[1])
                    )

        # Store pending decorators for the inner definition to pick up
        self._pending_decorators = decorators

        # Visit the inner (non-decorator) children
        for i in range(node.named_child_count):
            child = node.named_child(i)
            if not child:
                continue
            if child.type != "decorator":
                self._visit_node(child)

        # Clear pending decorators after processing the inner definition
        self._pending_decorators = []

    def _extract_decorator_name(self, decorator_node: TSNode) -> str | None:
        """Extract the name from a decorator node.

        Handles:
          @name             -> "name"
          @dotted.name      -> "dotted.name"
          @name(args)       -> "name"
          @dotted.name(args)-> "dotted.name"
        """
        # The decorator node has named children: identifier, attribute, or call
        for i in range(decorator_node.named_child_count):
            child = decorator_node.named_child(i)
            if not child:
                continue

            if child.type == "identifier":
                return get_node_text(child, self.source_bytes)

            if child.type == "attribute":
                # e.g. app.route — reconstruct the dotted name
                return get_node_text(child, self.source_bytes)

            if child.type == "call":
                # Decorator with arguments: @name(args) or @dotted.name(args)
                # The function part is the first named child
                func = child.named_child(0)
                if func:
                    if func.type == "identifier":
                        return get_node_text(func, self.source_bytes)
                    if func.type == "attribute":
                        return get_node_text(func, self.source_bytes)
                # Fallback: text of the first named child
                return get_node_text(func, self.source_bytes) if func else None

        # Fallback: try to get text from the node itself (strip the @)
        return None

    def _emit_decorates_refs(self, decorated_node_id: str) -> None:
        """Emit UnresolvedReference with kind=DECORATES for each pending decorator."""
        for dec_name, dec_line, dec_column in self._pending_decorators:
            self.unresolved_refs.append(
                UnresolvedReference(
                    from_node_id=decorated_node_id,
                    reference_name=dec_name,
                    reference_kind=EdgeKind.DECORATES,
                    line=dec_line,
                    column=dec_column,
                )
            )

    # =========================================================================
    # Symbol Extractors
    # =========================================================================

    def _extract_name(self, node: TSNode) -> str:
        """Extract the name of a symbol from an AST node."""
        if not self.extractor:
            return "<anonymous>"

        name_node = get_child_by_field(node, self.extractor.name_field)
        if name_node:
            resolved = name_node
            while resolved.type == "pointer_declarator":
                inner = get_child_by_field(
                    resolved, "declarator"
                ) or resolved.named_child(0)
                if not inner:
                    break
                resolved = inner
            if resolved.type in ("function_declarator", "declarator"):
                inner = get_child_by_field(
                    resolved, "declarator"
                ) or resolved.named_child(0)
                return (
                    get_node_text(inner, self.source_bytes)
                    if inner
                    else get_node_text(resolved, self.source_bytes)
                )
            return get_node_text(resolved, self.source_bytes)

        # Anonymous functions
        if node.type in ("arrow_function", "function_expression"):
            parent = node.parent
            if parent and parent.type == "variable_declarator":
                var_name = get_child_by_field(parent, "name")
                if var_name:
                    return get_node_text(var_name, self.source_bytes)
            return "<anonymous>"

        # Fallback: first identifier child
        for i in range(node.named_child_count):
            c = node.named_child(i)
            if c and c.type in (
                "identifier",
                "type_identifier",
                "simple_identifier",
                "constant",
            ):
                return get_node_text(c, self.source_bytes)

        return "<anonymous>"

    def _extract_function(self, node: TSNode) -> None:
        if not self.extractor:
            return

        # Check for receiver type (Go/Rust methods)
        if self.extractor.get_receiver_type:
            receiver = self.extractor.get_receiver_type(node, self.source_bytes)
            if receiver:
                self._extract_method(node)
                return

        name = self._extract_name(node)
        if name == "<anonymous>" and node.type in (
            "arrow_function",
            "function_expression",
        ):
            # Try parent variable_declarator for arrow functions
            parent = node.parent
            if parent and parent.type == "variable_declarator":
                var_name = get_child_by_field(parent, "name")
                if var_name:
                    name = get_node_text(var_name, self.source_bytes)
        if name == "<anonymous>":
            return

        docstring = (
            get_preceding_docstring(node, self.source_bytes) if self.extractor else None
        )
        signature = (
            self.extractor.get_signature(node, self.source_bytes)
            if self.extractor.get_signature
            else None
        )
        visibility = (
            self.extractor.get_visibility(node)
            if self.extractor.get_visibility
            else None
        )
        is_exported = (
            self.extractor.is_exported(node, self.source_bytes)
            if self.extractor.is_exported
            else None
        )
        is_async = self.extractor.is_async(node) if self.extractor.is_async else None
        is_static = (
            self._call_hook_with_decorator_names(self.extractor.is_static, node)
            if self.extractor.is_static
            else None
        )

        # Build extra kwargs for decorators
        extra_kwargs: dict = {}
        if self._pending_decorator_names:
            extra_kwargs["decorators"] = json.dumps(self._pending_decorator_names)

        func_node = self._create_node(
            NodeKind.FUNCTION,
            name,
            node,
            docstring=docstring,
            signature=signature,
            visibility=visibility,
            is_exported=bool(is_exported) if is_exported else False,
            is_async=bool(is_async) if is_async else False,
            is_static=bool(is_static) if is_static else False,
            **extra_kwargs,
        )
        if not func_node:
            return

        # Emit DECORATES unresolved refs for each decorator
        self._emit_decorates_refs(func_node.id)

        self.node_stack.append(func_node.id)
        body = get_child_by_field(node, self.extractor.body_field)
        if body:
            self._visit_function_body(body, func_node.id)
        self.node_stack.pop()

    def _extract_method(self, node: TSNode) -> None:
        if not self.extractor:
            return

        receiver_type = None
        if self.extractor.get_receiver_type:
            receiver_type = self.extractor.get_receiver_type(node, self.source_bytes)

        if (
            not self._is_inside_class_like()
            and not self.extractor.methods_are_top_level
            and not receiver_type
        ):
            if node.parent and node.parent.type in ("object", "object_expression"):
                return
            self._extract_function(node)
            return

        name = self._extract_name(node)
        docstring = get_preceding_docstring(node, self.source_bytes)
        signature = (
            self.extractor.get_signature(node, self.source_bytes)
            if self.extractor.get_signature
            else None
        )
        visibility = (
            self.extractor.get_visibility(node)
            if self.extractor.get_visibility
            else None
        )
        is_async = self.extractor.is_async(node) if self.extractor.is_async else None
        is_static = (
            self._call_hook_with_decorator_names(self.extractor.is_static, node)
            if self.extractor.is_static
            else None
        )

        # Check if this is a @property
        is_prop = (
            self._call_hook_with_decorator_names(self.extractor.is_property, node)
            if self.extractor.is_property
            else False
        )

        qname = f"{receiver_type}::{name}" if receiver_type else None

        # Build extra kwargs for decorators
        extra_kwargs: dict = {}
        if self._pending_decorator_names:
            extra_kwargs["decorators"] = json.dumps(self._pending_decorator_names)

        # Use NodeKind.PROPERTY for @property-decorated methods inside a class
        node_kind = NodeKind.PROPERTY if is_prop else NodeKind.METHOD

        method_node = self._create_node(
            node_kind,
            name,
            node,
            docstring=docstring,
            signature=signature,
            visibility=visibility,
            is_async=bool(is_async) if is_async else False,
            is_static=bool(is_static) if is_static else False,
            qualified_name=qname,
            **extra_kwargs,
        )
        if not method_node:
            return

        # Emit DECORATES unresolved refs for each decorator
        self._emit_decorates_refs(method_node.id)

        self.node_stack.append(method_node.id)
        body = get_child_by_field(node, self.extractor.body_field)
        if body:
            self._visit_function_body(body, method_node.id)
        self.node_stack.pop()

    def _extract_class(self, node: TSNode, kind: NodeKind = NodeKind.CLASS) -> None:
        if not self.extractor:
            return

        name = self._extract_name(node)
        docstring = get_preceding_docstring(node, self.source_bytes)
        visibility = (
            self.extractor.get_visibility(node)
            if self.extractor.get_visibility
            else None
        )
        is_exported = (
            self.extractor.is_exported(node, self.source_bytes)
            if self.extractor.is_exported
            else None
        )

        # Build extra kwargs for decorators
        extra_kwargs: dict = {}
        if self._pending_decorator_names:
            extra_kwargs["decorators"] = json.dumps(self._pending_decorator_names)

        class_node = self._create_node(
            kind,
            name,
            node,
            docstring=docstring,
            visibility=visibility,
            is_exported=bool(is_exported) if is_exported else False,
            **extra_kwargs,
        )
        if not class_node:
            return

        # Emit DECORATES unresolved refs for each decorator
        self._emit_decorates_refs(class_node.id)

        self._extract_inheritance(node, class_node.id)

        self.node_stack.append(class_node.id)
        body = get_child_by_field(node, self.extractor.body_field) or node
        for i in range(body.named_child_count):
            child = body.named_child(i)
            if child:
                self._visit_node(child)
        self.node_stack.pop()

    def _extract_interface(self, node: TSNode) -> None:
        if not self.extractor:
            return

        name = self._extract_name(node)
        docstring = get_preceding_docstring(node, self.source_bytes)
        is_exported = (
            self.extractor.is_exported(node, self.source_bytes)
            if self.extractor.is_exported
            else None
        )

        # Build extra kwargs for decorators
        extra_kwargs: dict = {}
        if self._pending_decorator_names:
            extra_kwargs["decorators"] = json.dumps(self._pending_decorator_names)

        iface_node = self._create_node(
            NodeKind.INTERFACE,
            name,
            node,
            docstring=docstring,
            is_exported=bool(is_exported) if is_exported else False,
            **extra_kwargs,
        )
        if not iface_node:
            return

        # Emit DECORATES unresolved refs for each decorator
        self._emit_decorates_refs(iface_node.id)

        self._extract_inheritance(node, iface_node.id)

        self.node_stack.append(iface_node.id)
        body = get_child_by_field(node, self.extractor.body_field) or node
        for i in range(body.named_child_count):
            child = body.named_child(i)
            if child:
                self._visit_node(child)
        self.node_stack.pop()

    def _extract_struct(self, node: TSNode) -> None:
        if not self.extractor:
            return
        body = get_child_by_field(node, self.extractor.body_field)
        if not body:
            return

        name = self._extract_name(node)
        docstring = get_preceding_docstring(node, self.source_bytes)
        visibility = (
            self.extractor.get_visibility(node)
            if self.extractor.get_visibility
            else None
        )

        # Build extra kwargs for decorators
        extra_kwargs: dict = {}
        if self._pending_decorator_names:
            extra_kwargs["decorators"] = json.dumps(self._pending_decorator_names)

        struct_node = self._create_node(
            NodeKind.STRUCT,
            name,
            node,
            docstring=docstring,
            visibility=visibility,
            **extra_kwargs,
        )
        if not struct_node:
            return

        # Emit DECORATES unresolved refs for each decorator
        self._emit_decorates_refs(struct_node.id)

        self._extract_inheritance(node, struct_node.id)

        self.node_stack.append(struct_node.id)
        for i in range(body.named_child_count):
            child = body.named_child(i)
            if child:
                self._visit_node(child)
        self.node_stack.pop()

    def _extract_enum(self, node: TSNode) -> None:
        if not self.extractor:
            return
        body = get_child_by_field(node, self.extractor.body_field)
        if not body:
            return

        name = self._extract_name(node)
        docstring = get_preceding_docstring(node, self.source_bytes)
        visibility = (
            self.extractor.get_visibility(node)
            if self.extractor.get_visibility
            else None
        )

        # Build extra kwargs for decorators
        extra_kwargs: dict = {}
        if self._pending_decorator_names:
            extra_kwargs["decorators"] = json.dumps(self._pending_decorator_names)

        enum_node = self._create_node(
            NodeKind.ENUM,
            name,
            node,
            docstring=docstring,
            visibility=visibility,
            **extra_kwargs,
        )
        if not enum_node:
            return

        # Emit DECORATES unresolved refs for each decorator
        self._emit_decorates_refs(enum_node.id)

        self._extract_inheritance(node, enum_node.id)

        self.node_stack.append(enum_node.id)
        member_types = self.extractor.enum_member_types or []
        for i in range(body.named_child_count):
            child = body.named_child(i)
            if not child:
                continue
            if child.type in member_types:
                self._extract_enum_member(child)
            else:
                self._visit_node(child)
        self.node_stack.pop()

    def _extract_enum_member(self, node: TSNode) -> None:
        name_node = get_child_by_field(node, "name")
        if name_node:
            self._create_node(
                NodeKind.ENUM_MEMBER, get_node_text(name_node, self.source_bytes), node
            )
            return
        for i in range(node.named_child_count):
            c = node.named_child(i)
            if c and c.type in (
                "simple_identifier",
                "identifier",
                "property_identifier",
            ):
                self._create_node(
                    NodeKind.ENUM_MEMBER, get_node_text(c, self.source_bytes), c
                )
                return
        if node.named_child_count == 0:
            self._create_node(
                NodeKind.ENUM_MEMBER, get_node_text(node, self.source_bytes), node
            )

    def _extract_property(self, node: TSNode) -> None:
        if not self.extractor:
            return
        name_node = get_child_by_field(node, "name") or next(
            (c for c in node.named_children if c.type == "identifier"), None
        )
        if not name_node:
            return
        name = get_node_text(name_node, self.source_bytes)
        visibility = (
            self.extractor.get_visibility(node)
            if self.extractor.get_visibility
            else None
        )
        self._create_node(NodeKind.PROPERTY, name, node, visibility=visibility)

    def _extract_field(self, node: TSNode) -> None:
        if not self.extractor:
            return
        visibility = (
            self.extractor.get_visibility(node)
            if self.extractor.get_visibility
            else None
        )

        # Find variable_declarator children
        declarators = [
            c for c in node.named_children if c.type == "variable_declarator"
        ]
        if not declarators:
            var_decl = next(
                (c for c in node.named_children if c.type == "variable_declaration"),
                None,
            )
            if var_decl:
                declarators = [
                    c
                    for c in var_decl.named_children
                    if c.type == "variable_declarator"
                ]

        if declarators:
            for decl in declarators:
                name_node = get_child_by_field(decl, "name") or next(
                    (c for c in decl.named_children if c.type == "identifier"), None
                )
                if name_node:
                    name = get_node_text(name_node, self.source_bytes)
                    self._create_node(NodeKind.FIELD, name, decl, visibility=visibility)
        else:
            name_node = get_child_by_field(node, "name") or next(
                (c for c in node.named_children if c.type == "identifier"), None
            )
            if name_node:
                name = get_node_text(name_node, self.source_bytes)
                self._create_node(NodeKind.FIELD, name, node, visibility=visibility)

    def _extract_variable(self, node: TSNode) -> None:
        if not self.extractor:
            return

        is_const = self.extractor.is_const(node) if self.extractor.is_const else False
        kind = NodeKind.CONSTANT if is_const else NodeKind.VARIABLE

        if self.language in (
            Language.TYPESCRIPT,
            Language.JAVASCRIPT,
            Language.TSX,
            Language.JSX,
        ):
            for i in range(node.named_child_count):
                child = node.named_child(i)
                if child and child.type == "variable_declarator":
                    name_node = get_child_by_field(child, "name")
                    value_node = get_child_by_field(child, "value")
                    if name_node:
                        if name_node.type in ("object_pattern", "array_pattern"):
                            continue
                        name = get_node_text(name_node, self.source_bytes)
                        if value_node and value_node.type in (
                            "arrow_function",
                            "function_expression",
                        ):
                            self._extract_function(value_node)
                            continue
                        init_val = (
                            get_node_text(value_node, self.source_bytes)[:100]
                            if value_node
                            else None
                        )
                        sig = (
                            f"= {init_val}{'...' if init_val and len(init_val) >= 100 else ''}"
                            if init_val
                            else None
                        )
                        self._create_node(kind, name, child, signature=sig)

        elif self.language in (Language.PYTHON, Language.RUBY):
            left = get_child_by_field(node, "left") or node.named_child(0)
            right = get_child_by_field(node, "right") or node.named_child(1)
            if left and left.type == "identifier":
                name = get_node_text(left, self.source_bytes)
                init_val = (
                    get_node_text(right, self.source_bytes)[:100] if right else None
                )
                sig = (
                    f"= {init_val}{'...' if init_val and len(init_val) >= 100 else ''}"
                    if init_val
                    else None
                )
                self._create_node(kind, name, node, signature=sig)

        elif self.language == Language.GO:
            specs = [
                c for c in node.named_children if c.type in ("var_spec", "const_spec")
            ]
            for spec in specs:
                name_child = spec.named_child(0)
                if name_child and name_child.type == "identifier":
                    name = get_node_text(name_child, self.source_bytes)
                    vkind = (
                        NodeKind.CONSTANT
                        if node.type == "const_declaration"
                        else NodeKind.VARIABLE
                    )
                    self._create_node(vkind, name, spec)
            if node.type == "short_var_declaration":
                left = get_child_by_field(node, "left")
                if left:
                    ids = (
                        [c for c in left.named_children if c.type == "identifier"]
                        if left.type == "expression_list"
                        else [left]
                    )
                    for ident in ids:
                        name = get_node_text(ident, self.source_bytes)
                        self._create_node(NodeKind.VARIABLE, name, node)

        elif self.language == Language.RUST:
            if node.type == "const_item":
                name_node = get_child_by_field(node, "name")
                if name_node:
                    self._create_node(
                        NodeKind.CONSTANT,
                        get_node_text(name_node, self.source_bytes),
                        node,
                    )
            elif node.type == "let_declaration":
                name_node = get_child_by_field(node, "name")
                if name_node:
                    self._create_node(
                        NodeKind.VARIABLE,
                        get_node_text(name_node, self.source_bytes),
                        node,
                    )

        else:
            for i in range(node.named_child_count):
                c = node.named_child(i)
                if c and c.type in ("identifier", "variable_declarator"):
                    name = (
                        get_node_text(c, self.source_bytes)
                        if c.type == "identifier"
                        else self._extract_name(c)
                    )
                    if name and name != "<anonymous>":
                        self._create_node(kind, name, c)

    def _extract_import(self, node: TSNode) -> None:
        if not self.extractor:
            return
        import_text = get_node_text(node, self.source_bytes).strip()

        # Try language-specific hook
        if self.extractor.extract_import:
            info = self.extractor.extract_import(node, self.source_bytes)
            if info:
                self._create_node(
                    NodeKind.IMPORT,
                    info["module_name"],
                    node,
                    signature=info.get("signature"),
                )
                if (
                    not info.get("handled_refs")
                    and info["module_name"]
                    and self.node_stack
                ):
                    parent_id = self.node_stack[-1]
                    self.unresolved_refs.append(
                        UnresolvedReference(
                            from_node_id=parent_id,
                            reference_name=info["module_name"],
                            reference_kind=EdgeKind.IMPORTS,
                            line=node.start_point[0] + 1,
                            column=node.start_point[1],
                        )
                    )
                return

        # Python: import os, sys → multiple imports
        if self.language == Language.PYTHON and node.type == "import_statement":
            for i in range(node.named_child_count):
                c = node.named_child(i)
                if c and c.type == "dotted_name":
                    self._create_node(
                        NodeKind.IMPORT,
                        get_node_text(c, self.source_bytes),
                        node,
                        signature=import_text,
                    )
                elif c and c.type == "aliased_import":
                    dotted = next(
                        (x for x in c.named_children if x.type == "dotted_name"), None
                    )
                    if dotted:
                        self._create_node(
                            NodeKind.IMPORT,
                            get_node_text(dotted, self.source_bytes),
                            node,
                            signature=import_text,
                        )
            return

        # Go imports
        if self.language == Language.GO:
            go_parent_id: str | None = self.node_stack[-1] if self.node_stack else None
            specs = [c for c in node.named_children if c.type == "import_spec"]
            spec_list = next(
                (c for c in node.named_children if c.type == "import_spec_list"), None
            )
            if spec_list:
                specs = [c for c in spec_list.named_children if c.type == "import_spec"]
            for spec in specs:
                str_lit = next(
                    (
                        c
                        for c in spec.named_children
                        if c.type == "interpreted_string_literal"
                    ),
                    None,
                )
                if str_lit:
                    path = get_node_text(str_lit, self.source_bytes).strip("'\"")
                    if path:
                        self._create_node(
                            NodeKind.IMPORT,
                            path,
                            spec,
                            signature=get_node_text(spec, self.source_bytes).strip(),
                        )
                        if go_parent_id:
                            self.unresolved_refs.append(
                                UnresolvedReference(
                                    from_node_id=go_parent_id,
                                    reference_name=path,
                                    reference_kind=EdgeKind.IMPORTS,
                                    line=spec.start_point[0] + 1,
                                    column=spec.start_point[1],
                                )
                            )
            return

        # Java imports
        if self.language == Language.JAVA:
            name_node = get_child_by_field(node, "name")
            if name_node:
                name = get_node_text(name_node, self.source_bytes)
                self._create_node(NodeKind.IMPORT, name, node, signature=import_text)
            return

        # Generic fallback
        if not self.extractor.extract_import:
            self._create_node(NodeKind.IMPORT, import_text, node, signature=import_text)

    def _extract_call(self, node: TSNode) -> None:
        if not self.node_stack:
            return
        caller_id = self.node_stack[-1]
        if not caller_id:
            return

        callee_name = ""

        # Java/Kotlin/PHP method_invocation with name + object fields
        name_field = get_child_by_field(node, "name")
        object_field = get_child_by_field(node, "object") or get_child_by_field(
            node, "scope"
        )

        if (
            name_field
            and object_field
            and node.type
            in ("method_invocation", "member_call_expression", "scoped_call_expression")
        ):
            method_name = get_node_text(name_field, self.source_bytes)
            receiver_name = get_node_text(object_field, self.source_bytes).lstrip("$")
            skip_receivers = {"self", "this", "cls", "super", "parent", "static"}
            callee_name = (
                method_name
                if receiver_name in skip_receivers
                else f"{receiver_name}.{method_name}"
            )
        else:
            func = get_child_by_field(node, "function") or node.named_child(0)
            if func:
                if func.type in (
                    "member_expression",
                    "attribute",
                    "selector_expression",
                    "navigation_expression",
                ):
                    prop = get_child_by_field(func, "property") or get_child_by_field(
                        func, "field"
                    )
                    if not prop:
                        child1 = func.named_child(1)
                        if child1 and child1.type == "navigation_suffix":
                            prop = next(
                                (
                                    c
                                    for c in child1.named_children
                                    if c.type == "simple_identifier"
                                ),
                                child1,
                            )
                        else:
                            prop = child1
                    if prop:
                        method_name = get_node_text(prop, self.source_bytes)
                        receiver = (
                            get_child_by_field(func, "object")
                            or get_child_by_field(func, "operand")
                            or func.named_child(0)
                        )
                        skip_receivers = {"self", "this", "cls", "super"}
                        if receiver and receiver.type in (
                            "identifier",
                            "simple_identifier",
                        ):
                            rname = get_node_text(receiver, self.source_bytes)
                            callee_name = (
                                method_name
                                if rname in skip_receivers
                                else f"{rname}.{method_name}"
                            )
                        else:
                            callee_name = method_name
                else:
                    callee_name = get_node_text(func, self.source_bytes)

        if callee_name:
            self.unresolved_refs.append(
                UnresolvedReference(
                    from_node_id=caller_id,
                    reference_name=callee_name,
                    reference_kind=EdgeKind.CALLS,
                    line=node.start_point[0] + 1,
                    column=node.start_point[1],
                )
            )

    def _extract_instantiation(self, node: TSNode) -> None:
        if not self.node_stack:
            return
        from_id = self.node_stack[-1]
        if not from_id:
            return

        ctor = (
            get_child_by_field(node, "constructor")
            or get_child_by_field(node, "type")
            or get_child_by_field(node, "name")
            or node.named_child(0)
        )
        if not ctor:
            return

        class_name = get_node_text(ctor, self.source_bytes)
        lt = class_name.find("<")
        if lt > 0:
            class_name = class_name[:lt]
        last_dot = max(class_name.rfind("."), class_name.rfind("::"))
        if last_dot >= 0:
            class_name = class_name[last_dot + 1 :].lstrip(":.")
        class_name = class_name.strip()

        if class_name:
            self.unresolved_refs.append(
                UnresolvedReference(
                    from_node_id=from_id,
                    reference_name=class_name,
                    reference_kind=EdgeKind.INSTANTIATES,
                    line=node.start_point[0] + 1,
                    column=node.start_point[1],
                )
            )

    def _extract_type_alias(self, node: TSNode) -> bool:
        if not self.extractor:
            return False

        name = self._extract_name(node)
        if name == "<anonymous>":
            return False

        docstring = get_preceding_docstring(node, self.source_bytes)
        is_exported = (
            self.extractor.is_exported(node, self.source_bytes)
            if self.extractor.is_exported
            else None
        )

        # Go: type_declaration wraps struct/interface
        if self.extractor.resolve_type_alias_kind:
            resolved = self.extractor.resolve_type_alias_kind(node, self.source_bytes)
            if resolved == NodeKind.STRUCT:
                struct_node = self._create_node(
                    NodeKind.STRUCT,
                    name,
                    node,
                    docstring=docstring,
                    is_exported=bool(is_exported) if is_exported else False,
                )
                if struct_node:
                    type_child = get_child_by_field(node, "type")
                    if type_child:
                        body = (
                            get_child_by_field(type_child, self.extractor.body_field)
                            or type_child
                        )
                        self.node_stack.append(struct_node.id)
                        for i in range(body.named_child_count):
                            child = body.named_child(i)
                            if child:
                                self._visit_node(child)
                        self.node_stack.pop()
                return True
            if resolved == NodeKind.INTERFACE:
                self._create_node(
                    NodeKind.INTERFACE,
                    name,
                    node,
                    docstring=docstring,
                    is_exported=bool(is_exported) if is_exported else False,
                )
                return True

        self._create_node(
            NodeKind.TYPE_ALIAS,
            name,
            node,
            docstring=docstring,
            is_exported=bool(is_exported) if is_exported else False,
        )
        return False

    def _extract_rust_impl_item(self, node: TSNode) -> None:
        has_for = any(c.type == "for" and not c.is_named for c in node.children)
        if not has_for:
            return

        type_idents = [
            c
            for c in node.named_children
            if c.type in ("type_identifier", "generic_type", "scoped_type_identifier")
        ]
        if len(type_idents) < 2:
            return

        trait_node = type_idents[0]
        type_node = type_idents[-1]

        trait_name = get_node_text(trait_node, self.source_bytes)
        if type_node.type == "generic_type":
            inner = next(
                (c for c in type_node.named_children if c.type == "type_identifier"),
                None,
            )
            type_name = (
                get_node_text(inner, self.source_bytes)
                if inner
                else get_node_text(type_node, self.source_bytes)
            )
        else:
            type_name = get_node_text(type_node, self.source_bytes)

        type_node_id = self._find_node_by_name(type_name)
        if type_node_id:
            self.unresolved_refs.append(
                UnresolvedReference(
                    from_node_id=type_node_id,
                    reference_name=trait_name,
                    reference_kind=EdgeKind.IMPLEMENTS,
                    line=trait_node.start_point[0] + 1,
                    column=trait_node.start_point[1],
                )
            )

    def _find_node_by_name(self, name: str) -> str | None:
        for n in self.nodes:
            if n.name == name and n.kind in (
                NodeKind.STRUCT,
                NodeKind.ENUM,
                NodeKind.CLASS,
            ):
                return n.id
        return None

    # =========================================================================
    # Inheritance
    # =========================================================================

    def _extract_inheritance(self, node: TSNode, class_id: str) -> None:
        for i in range(node.named_child_count):
            child = node.named_child(i)
            if not child:
                continue

            ct = child.type

            if ct in (
                "extends_clause",
                "superclass",
                "base_clause",
                "extends_interfaces",
            ):
                type_list = next(
                    (c for c in child.named_children if c.type == "type_list"), None
                )
                targets = (
                    type_list.named_children if type_list else [child.named_child(0)]
                )
                for t in targets:
                    if t:
                        self.unresolved_refs.append(
                            UnresolvedReference(
                                from_node_id=class_id,
                                reference_name=get_node_text(t, self.source_bytes),
                                reference_kind=EdgeKind.EXTENDS,
                                line=t.start_point[0] + 1,
                                column=t.start_point[1],
                            )
                        )

            if ct in (
                "implements_clause",
                "class_interface_clause",
                "super_interfaces",
                "interfaces",
            ):
                type_list = next(
                    (c for c in child.named_children if c.type == "type_list"), None
                )
                targets = (
                    type_list.named_children if type_list else child.named_children
                )
                for iface in targets:
                    if iface:
                        self.unresolved_refs.append(
                            UnresolvedReference(
                                from_node_id=class_id,
                                reference_name=get_node_text(iface, self.source_bytes),
                                reference_kind=EdgeKind.IMPLEMENTS,
                                line=iface.start_point[0] + 1,
                                column=iface.start_point[1],
                            )
                        )

            # Python: class Foo(Bar, Baz):
            if ct == "argument_list" and node.type == "class_definition":
                for arg in child.named_children:
                    if arg.type in ("identifier", "attribute"):
                        self.unresolved_refs.append(
                            UnresolvedReference(
                                from_node_id=class_id,
                                reference_name=get_node_text(arg, self.source_bytes),
                                reference_kind=EdgeKind.EXTENDS,
                                line=arg.start_point[0] + 1,
                                column=arg.start_point[1],
                            )
                        )

            # Rust trait_bounds
            if ct == "trait_bounds":
                for bound in child.named_children:
                    if bound.type == "type_identifier":
                        self.unresolved_refs.append(
                            UnresolvedReference(
                                from_node_id=class_id,
                                reference_name=get_node_text(bound, self.source_bytes),
                                reference_kind=EdgeKind.EXTENDS,
                                line=bound.start_point[0] + 1,
                                column=bound.start_point[1],
                            )
                        )

            # C# base_list
            if ct == "base_list":
                for base_type in child.named_children:
                    if base_type:
                        name = get_node_text(base_type, self.source_bytes)
                        if base_type.type == "generic_name":
                            id_node = next(
                                (
                                    c
                                    for c in base_type.named_children
                                    if c.type == "identifier"
                                ),
                                base_type,
                            )
                            name = get_node_text(id_node, self.source_bytes)
                        self.unresolved_refs.append(
                            UnresolvedReference(
                                from_node_id=class_id,
                                reference_name=name,
                                reference_kind=EdgeKind.EXTENDS,
                                line=base_type.start_point[0] + 1,
                                column=base_type.start_point[1],
                            )
                        )

            # Recurse into containers
            if ct in ("field_declaration_list", "class_heritage"):
                self._extract_inheritance(child, class_id)

    # =========================================================================
    # Function Body
    # =========================================================================

    def _visit_function_body(self, body: TSNode, function_id: str) -> None:
        if not self.extractor:
            return
        self._walk_for_calls(body)

    def _walk_for_calls(self, node: TSNode) -> None:
        if not self.extractor:
            return

        nt = node.type
        if nt in self.extractor.call_types:
            self._extract_call(node)
        elif nt in INSTANTIATION_KINDS:
            self._extract_instantiation(node)
        elif self.extractor.class_types and nt in self.extractor.class_types:
            classification = "class"
            if self.extractor.classify_class_node:
                classification = self.extractor.classify_class_node(
                    node, self.source_bytes
                )
            if classification == "struct":
                self._extract_struct(node)
            elif classification == "enum":
                self._extract_enum(node)
            else:
                self._extract_class(node)
            return
        elif nt in self.extractor.struct_types:
            self._extract_struct(node)
            return
        elif nt in self.extractor.enum_types:
            self._extract_enum(node)
            return
        elif nt in self.extractor.interface_types:
            self._extract_interface(node)
            return

        for i in range(node.named_child_count):
            child = node.named_child(i)
            if child:
                self._walk_for_calls(child)
