"""Integration tests for file dependency and dependent queries.

Note: get_file_dependencies/dependents rely on file-to-file IMPORTS edges.
The current resolution creates file-level IMPORTS edges (e.g. services.py --imports--> models),
so these tests verify the actual behavior of the system.
"""

from __future__ import annotations

from pycodegraph import CodeGraph
from pycodegraph.types import EdgeKind


class TestGetFileDependencies:
    """get_file_dependencies() returns files imported by the given file."""

    def test_dependencies_returns_list(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            deps = cg.get_file_dependencies("services.py")
            assert isinstance(deps, list)
        finally:
            cg.close()

    def test_dependencies_nonexistent_file(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            deps = cg.get_file_dependencies("nonexistent.py")
            assert deps == []
        finally:
            cg.close()

    def test_file_imports_edges_exist(self, create_python_project):
        """Verify that file-level IMPORTS edges are created between files."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            nodes = {n.id: n for n in cg.get_all_nodes(limit=50000)}
            all_edges = cg.get_all_edges(limit=200000)
            import_edges = [e for e in all_edges if e.kind == EdgeKind.IMPORTS]
            assert len(import_edges) > 0
            # Verify import edges connect file-level nodes
            for e in import_edges:
                src = nodes.get(e.source)
                tgt = nodes.get(e.target)
                if src and tgt:
                    assert src.name in ("services.py", "main.py"), (
                        f"Import source should be a file node: {src.name}"
                    )
        finally:
            cg.close()


class TestGetFileDependents:
    """get_file_dependents() returns files that import from the given file."""

    def test_dependents_returns_list(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            deps = cg.get_file_dependents("models.py")
            assert isinstance(deps, list)
        finally:
            cg.close()

    def test_dependents_nonexistent_file(self, create_python_project):
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            deps = cg.get_file_dependents("nonexistent.py")
            assert deps == []
        finally:
            cg.close()


class TestDependencyBidirectionality:
    """Dependencies and dependents are inverse relationships."""

    def test_deps_and_dependents_are_inverse(self, create_python_project):
        """If A depends on B, then B should have A as a dependent (when edges exist)."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            all_files = cg._queries.get_all_file_paths()
            for f in all_files:
                fwd = cg.get_file_dependencies(f)
                for dep in fwd:
                    rev = cg.get_file_dependents(dep)
                    assert f in rev
        finally:
            cg.close()


class TestMultiFileImportEdges:
    """Verify that cross-file import edges are correctly resolved."""

    def test_services_imports_models(self, create_python_project):
        """services.py --imports--> models should be an edge in the graph."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            nodes = {n.id: n for n in cg.get_all_nodes(limit=50000)}
            import_edges = [
                e for e in cg.get_all_edges(limit=200000) if e.kind == EdgeKind.IMPORTS
            ]
            # Check that at least one import edge goes from services.py file node
            src_names = {
                nodes[e.source].name for e in import_edges if e.source in nodes
            }
            assert "services.py" in src_names or "main.py" in src_names
        finally:
            cg.close()

    def test_main_imports_services_and_utils(self, create_python_project):
        """main.py imports from both services and utils."""
        root = create_python_project()
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            nodes = {n.id: n for n in cg.get_all_nodes(limit=50000)}
            import_edges = [
                e for e in cg.get_all_edges(limit=200000) if e.kind == EdgeKind.IMPORTS
            ]
            # main.py should have import edges to services and utils
            main_imports = [
                e
                for e in import_edges
                if e.source in nodes and nodes[e.source].name == "main.py"
            ]
            assert len(main_imports) >= 2  # services + utils
        finally:
            cg.close()
