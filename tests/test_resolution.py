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


class TestPythonAbsoluteImportResolution:
    """Verify Python absolute import path resolution (issues #51, #52)."""

    def test_absolute_import_resolves_dot_path(self, tmp_path):
        """from myapp.models import User — dots in myapp.models must convert to myapp/models."""
        from tests.conftest import write_file

        root = str(tmp_path)
        # Create a package structure: myapp/models.py with a User class
        write_file(root, "myapp/__init__.py", "")
        write_file(root, "myapp/models.py", "class User:\n    pass\n")
        write_file(
            root,
            "main.py",
            "from myapp.models import User\n\ndef run():\n    user = User()\n    return user\n",
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            # Find the User class node
            user_nodes = cg._queries.get_nodes_by_name("User")
            assert len(user_nodes) > 0, "User class should be indexed"
            user_node = user_nodes[0]
            assert user_node.file_path == "myapp/models.py", (
                f"User should be in myapp/models.py, got {user_node.file_path}"
            )

            # There should be a CALLS or INSTANTIATES edge from main.py to User
            all_edges = cg.get_all_edges(limit=50000)
            user_ids = {n.id for n in user_nodes}
            edges_to_user = [
                e
                for e in all_edges
                if e.target in user_ids
                and e.kind
                in (EdgeKind.CALLS, EdgeKind.INSTANTIATES, EdgeKind.REFERENCES)
            ]
            assert len(edges_to_user) > 0, (
                "There should be a resolved edge from main.py to User class"
            )
        finally:
            cg.close()

    def test_absolute_import_package_init(self, tmp_path):
        """from myapp.models import User where models is a package with __init__.py."""
        from tests.conftest import write_file

        root = str(tmp_path)
        # Create a package structure: myapp/models/__init__.py
        write_file(root, "myapp/__init__.py", "")
        write_file(root, "myapp/models/__init__.py", "class User:\n    pass\n")
        write_file(
            root,
            "main.py",
            "from myapp.models import User\n\ndef run():\n    user = User()\n    return user\n",
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            user_nodes = cg._queries.get_nodes_by_name("User")
            assert len(user_nodes) > 0, "User class should be indexed"
            user_node = user_nodes[0]
            assert user_node.file_path == "myapp/models/__init__.py", (
                f"User should be in myapp/models/__init__.py, got {user_node.file_path}"
            )

            all_edges = cg.get_all_edges(limit=50000)
            user_ids = {n.id for n in user_nodes}
            edges_to_user = [
                e
                for e in all_edges
                if e.target in user_ids
                and e.kind
                in (EdgeKind.CALLS, EdgeKind.INSTANTIATES, EdgeKind.REFERENCES)
            ]
            assert len(edges_to_user) > 0, (
                "There should be a resolved edge from main.py to User class"
            )
        finally:
            cg.close()

    def test_module_member_call_resolution(self, tmp_path):
        """import utils; utils.helper() — should resolve helper as a CALLS edge."""
        from tests.conftest import write_file

        root = str(tmp_path)
        write_file(root, "utils.py", "def helper():\n    pass\n")
        write_file(
            root,
            "main.py",
            "import utils\n\ndef run():\n    utils.helper()\n",
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            helper_nodes = cg._queries.get_nodes_by_name("helper")
            assert len(helper_nodes) > 0, "helper function should be indexed"
            helper_node = helper_nodes[0]
            assert helper_node.file_path == "utils.py", (
                f"helper should be in utils.py, got {helper_node.file_path}"
            )

            all_edges = cg.get_all_edges(limit=50000)
            helper_ids = {n.id for n in helper_nodes}
            calls_to_helper = [
                e
                for e in all_edges
                if e.target in helper_ids and e.kind == EdgeKind.CALLS
            ]
            assert len(calls_to_helper) > 0, (
                "There should be a CALLS edge from main.py to utils.helper()"
            )
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
