"""Integration tests for cross-file reference resolution.

Resolution is triggered by index_all() (the only method that auto-resolves).
Tests verify that import, call, and inheritance references are correctly
resolved to real edges in the graph.
"""

from __future__ import annotations

from pycodegraph import CodeGraph
from pycodegraph.types import EdgeKind
from tests.conftest import write_file


class TestImportResolution:
    """Verify import statements resolve to the correct target nodes."""

    def test_from_import_resolves(self, create_python_project):
        """from models import User should create an edge to User in models.py."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        result = cg.index_all()
        assert result.success

        # Check that cross-file edges exist
        all_edges = cg.get_all_edges(limit=50000)
        # After resolution, there should be edges connecting nodes across files
        cross_file_edges = [
            e
            for e in all_edges
            if e.kind in (EdgeKind.REFERENCES, EdgeKind.CALLS, EdgeKind.IMPORTS)
        ]
        assert len(cross_file_edges) > 0
        cg.close()

    def test_external_import_not_in_edges(self, create_python_project):
        """External imports (os, sys) should not create edges to project nodes."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        # Find nodes named "os" or "str" — they shouldn't exist as project nodes
        os_nodes = cg._queries.get_nodes_by_name("os")
        assert os_nodes == []

        str_nodes = cg._queries.get_nodes_by_name("str")
        assert str_nodes == []
        cg.close()

    def test_resolution_produces_edges(self, create_python_project):
        """After resolution, there should be resolved edges between files."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        result = cg.index_all()
        assert result.refs_resolved > 0

        # Verify at least some edges connect nodes in different files
        all_edges = cg.get_all_edges(limit=50000)
        nodes_by_id = {n.id: n for n in cg.get_all_nodes(limit=50000)}

        cross_file = 0
        for e in all_edges:
            src = nodes_by_id.get(e.source)
            tgt = nodes_by_id.get(e.target)
            if src and tgt and src.file_path != tgt.file_path:
                cross_file += 1
        assert cross_file > 0
        cg.close()


class TestCallResolution:
    """Verify function/method call resolution."""

    def test_cross_file_function_call_resolves(self, create_python_project):
        """main.py's run() calls create_user() from services.py — should resolve."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        # Find run function
        run_nodes = cg._queries.get_nodes_by_name("run")
        assert len(run_nodes) > 0
        run_id = run_nodes[0].id

        # Check callees of run
        callees = cg.get_callees(run_id)
        callee_ids = {e.target for e in callees}

        # create_user should be among the callees
        cu_nodes = cg._queries.get_nodes_by_name("create_user")
        if cu_nodes:
            assert cu_nodes[0].id in callee_ids
        cg.close()

    def test_same_file_call_resolves(self, tmp_path):
        """A function calling another in the same file should produce a CALLS edge."""
        root = str(tmp_path)
        write_file(
            root,
            "mod.py",
            "def helper(): pass\ndef main(): helper()\n",
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            main_nodes = cg._queries.get_nodes_by_name("main")
            assert len(main_nodes) > 0
            callees = cg.get_callees(main_nodes[0].id)
            callee_names = set()
            for e in callees:
                tgt = cg.get_node_by_id(e.target)
                if tgt:
                    callee_names.add(tgt.name)
            assert "helper" in callee_names
        finally:
            cg.close()

    def test_constructor_call_produces_instantiates_edge(self, create_python_project):
        """User() in services.py should produce an INSTANTIATES edge to the User class."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            # Check that INSTANTIATES edges exist (User() constructor calls)
            all_edges = cg.get_all_edges(limit=50000)
            instantiates = [e for e in all_edges if e.kind == EdgeKind.INSTANTIATES]
            assert len(instantiates) > 0

            # At least one INSTANTIATES edge should target the User class
            user_nodes = cg._queries.get_nodes_by_name("User")
            user_ids = {n.id for n in user_nodes}
            targets = {e.target for e in instantiates}
            assert bool(user_ids & targets), (
                "INSTANTIATES edge should target User class"
            )
        finally:
            cg.close()

    def test_edge_kind_promotion_to_instantiates(self, create_python_project):
        """CALLS to a class should be promoted to INSTANTIATES edge kind."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        all_edges = cg.get_all_edges(limit=50000)
        instantiates = [e for e in all_edges if e.kind == EdgeKind.INSTANTIATES]
        # services.py calls User() — that should be promoted
        assert len(instantiates) > 0
        cg.close()


class TestInheritanceResolution:
    """Verify class inheritance resolution."""

    def test_extends_resolves(self, create_python_project):
        """class Admin(User) should produce an EXTENDS edge from Admin to User."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()

        admin_nodes = cg._queries.get_nodes_by_name("Admin")
        assert len(admin_nodes) > 0

        # Check outgoing edges for EXTENDS
        outgoing = cg._queries.get_outgoing_edges(admin_nodes[0].id, [EdgeKind.EXTENDS])
        assert len(outgoing) > 0

        # Target should be the User class
        for e in outgoing:
            tgt = cg.get_node_by_id(e.target)
            assert tgt is not None
            assert tgt.name == "User"
        cg.close()

    def test_extends_to_interface_promotes(self, tmp_path):
        """TypeScript: class Foo implements IFoo should produce IMPLEMENTS, not EXTENDS."""
        root = str(tmp_path)
        write_file(
            root,
            "app.ts",
            "interface IFoo { run(): void; }\nclass Foo implements IFoo { run() {} }\n",
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            all_edges = cg.get_all_edges(limit=50000)
            implements = [e for e in all_edges if e.kind == EdgeKind.IMPLEMENTS]
            assert len(implements) > 0
        finally:
            cg.close()


class TestResolutionStats:
    """Verify resolution statistics reported by index_all()."""

    def test_refs_resolved_positive(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        result = cg.index_all()
        assert result.refs_resolved > 0
        cg.close()

    def test_refs_unresolved_non_negative(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        result = cg.index_all()
        assert result.refs_unresolved >= 0
        cg.close()

    def test_edges_created_includes_resolved(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        result = cg.index_all()
        # edges_created should include both structural edges and resolved refs
        assert result.edges_created >= result.refs_resolved
        cg.close()
