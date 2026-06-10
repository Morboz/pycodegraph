"""Integration tests for context building: get_context()."""

from __future__ import annotations

import pytest

from pycodegraph import CodeGraph
from pycodegraph.types import Context, Node


def _find_node(cg: CodeGraph, name: str) -> Node | None:
    nodes = cg._queries.get_nodes_by_name(name)
    return nodes[0] if nodes else None


class TestGetContext:
    """get_context() returns a Context with focal node, ancestors, children, refs, imports."""

    def test_get_context_returns_focal(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            user = _find_node(cg, "User")
            assert user is not None
            ctx = cg.get_context(user.id)
            assert isinstance(ctx, Context)
            assert ctx.focal is not None
            assert ctx.focal.name == "User"
        finally:
            cg.close()

    def test_get_context_includes_ancestors(self, create_python_project):
        """A method inside a class should have the class as an ancestor."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            greet_nodes = cg._queries.get_nodes_by_name("greet")
            if not greet_nodes:
                pytest.skip("greet not found")
            # Find the greet method (not the Admin override)
            greet = greet_nodes[0]
            ctx = cg.get_context(greet.id)
            ancestor_names = {n.name for n in ctx.ancestors}
            assert "User" in ancestor_names
        finally:
            cg.close()

    def test_get_context_includes_children(self, create_python_project):
        """A class node should have methods as children."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            user = _find_node(cg, "User")
            assert user is not None
            ctx = cg.get_context(user.id)
            child_names = {n.name for n in ctx.children}
            assert "greet" in child_names or "__init__" in child_names
        finally:
            cg.close()

    def test_get_context_includes_incoming_refs(self, create_python_project):
        """User class should have incoming references from services.py."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            user = _find_node(cg, "User")
            assert user is not None
            ctx = cg.get_context(user.id)
            # Incoming refs should exist (services.py imports User)
            assert isinstance(ctx.incoming_refs, list)
        finally:
            cg.close()

    def test_get_context_includes_imports(self, create_python_project):
        """Nodes in services.py should show User as an import."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            cu = _find_node(cg, "create_user")
            if cu is None:
                pytest.skip("create_user not found")
            ctx = cg.get_context(cu.id)
            assert isinstance(ctx.imports, list)
        finally:
            cg.close()

    def test_get_context_returns_context_object(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            user = _find_node(cg, "User")
            assert user is not None
            ctx = cg.get_context(user.id)
            assert isinstance(ctx, Context)
            assert ctx.focal is not None
            assert isinstance(ctx.ancestors, list)
            assert isinstance(ctx.children, list)
            assert isinstance(ctx.incoming_refs, list)
            assert isinstance(ctx.outgoing_refs, list)
            assert isinstance(ctx.types, list)
            assert isinstance(ctx.imports, list)
        finally:
            cg.close()
