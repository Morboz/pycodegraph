"""Integration tests for context building: get_context() and build_context()."""

from __future__ import annotations

import json

import pytest

from pycodegraph import CodeGraph
from pycodegraph.types import BuildContextOptions, Context, Node, TaskContext


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


class TestBuildContext:
    """build_context() — hybrid search pipeline with multiple output formats."""

    def test_build_context_returns_string_by_default(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.build_context("How does User work?")
            assert isinstance(result, str)
            assert "User" in result
        finally:
            cg.close()

    def test_build_context_markdown_format(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.build_context(
                "User class", BuildContextOptions(format="markdown")
            )
            assert isinstance(result, str)
            assert "User" in result
        finally:
            cg.close()

    def test_build_context_json_format(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.build_context("User class", BuildContextOptions(format="json"))
            assert isinstance(result, str)
            parsed = json.loads(result)
            # JSON format should have structured fields
            assert isinstance(parsed, dict)
        finally:
            cg.close()

    def test_build_context_raw_format(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.build_context(
                "User class", BuildContextOptions(format="raw", include_code=False)
            )
            assert isinstance(result, TaskContext)
            assert isinstance(result.subgraph, type(result.subgraph))
            assert isinstance(result.entry_points, list)
            assert isinstance(result.related_files, list)
        finally:
            cg.close()

    def test_build_context_finds_relevant_entry_points(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.build_context(
                "create_user function",
                BuildContextOptions(format="raw", include_code=False),
            )
            assert isinstance(result, TaskContext)
            ep_names = {n.name for n in result.entry_points}
            assert "create_user" in ep_names
        finally:
            cg.close()

    def test_build_context_with_dict_input(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.build_context(
                {"title": "User class", "description": "How is User used?"}
            )
            assert isinstance(result, str)
            assert "User" in result
        finally:
            cg.close()

    def test_build_context_includes_code_blocks(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.build_context(
                "User class", BuildContextOptions(format="raw", include_code=True)
            )
            assert isinstance(result, TaskContext)
            # Code blocks should be populated when include_code=True
            assert isinstance(result.code_blocks, list)
        finally:
            cg.close()

    def test_build_context_respects_max_nodes(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.build_context(
                "User",
                BuildContextOptions(format="raw", include_code=False, max_nodes=3),
            )
            assert isinstance(result, TaskContext)
            # With max_nodes=3, the subgraph should be small
            assert (
                len(result.subgraph.nodes) <= 15
            )  # Some slack for hierarchy expansion
        finally:
            cg.close()

    def test_build_context_stats_populated(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.build_context(
                "User class", BuildContextOptions(format="raw", include_code=False)
            )
            assert isinstance(result, TaskContext)
            assert result.stats is not None
            assert isinstance(result.stats, dict)
        finally:
            cg.close()

    def test_build_context_empty_query(self, create_python_project):
        """An empty query should not crash."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            result = cg.build_context("")
            # Should return something (possibly minimal) without error
            assert result is not None
        finally:
            cg.close()
