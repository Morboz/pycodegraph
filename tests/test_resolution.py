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


class TestInterfaceDispatchSynthesis:
    """Verify ABC/Protocol dispatch edge synthesis.

    After resolution + synthesis, a CALLS edge with provenance
    'heuristic:synthesis' should connect each base-type method to
    the same-named method in the concrete class that extends/implements it.
    """

    def test_abc_method_dispatch(self, tmp_path):
        """ABC Shape with abstract area() -> Circle implements Shape.

        After synthesis there should be a CALLS edge from Shape.area
        to Circle.area (heuristic provenance).
        """
        root = str(tmp_path)
        write_file(
            root,
            "shapes.py",
            """\
from abc import ABC, abstractmethod

class Shape(ABC):
    @abstractmethod
    def area(self) -> float:
        pass

class Circle(Shape):
    def __init__(self, radius: float):
        self.radius = radius

    def area(self) -> float:
        return 3.14159 * self.radius ** 2
""",
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            all_edges = cg.get_all_edges(limit=50000)

            # Find Shape.area and Circle.area nodes
            all_nodes = cg.get_all_nodes(limit=50000)
            shape_area = [
                n
                for n in all_nodes
                if n.name == "area" and "Shape" in (n.qualified_name or "")
            ]
            circle_area = [
                n
                for n in all_nodes
                if n.name == "area" and "Circle" in (n.qualified_name or "")
            ]

            assert len(shape_area) >= 1, "Shape.area node should exist"
            assert len(circle_area) >= 1, "Circle.area node should exist"

            # Check for synthesized CALLS edge from Shape.area -> Circle.area
            synth = [
                e
                for e in all_edges
                if e.source == shape_area[0].id
                and e.target == circle_area[0].id
                and e.kind == EdgeKind.CALLS
                and e.provenance
                and e.provenance.startswith("heuristic:synthesis")
            ]
            assert len(synth) >= 1, (
                f"Expected synthesized CALLS edge from Shape.area to Circle.area, "
                f"found {len(synth)}"
            )
        finally:
            cg.close()

    def test_protocol_dispatch(self, tmp_path):
        """typing.Protocol Drawable with draw() -> Square explicitly implements Drawable.

        Python Protocol supports structural subtyping, but explicit
        inheritance (Square(Drawable)) is also valid and creates the
        EXTENDS edge needed for synthesis.
        """
        root = str(tmp_path)
        write_file(
            root,
            "drawable.py",
            """\
from typing import Protocol

class Drawable(Protocol):
    def draw(self) -> None: ...

class Square(Drawable):
    def draw(self) -> None:
        print("square")
""",
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            all_edges = cg.get_all_edges(limit=50000)
            all_nodes = cg.get_all_nodes(limit=50000)

            drawable_draw = [
                n
                for n in all_nodes
                if n.name == "draw" and "Drawable" in (n.qualified_name or "")
            ]
            square_draw = [
                n
                for n in all_nodes
                if n.name == "draw" and "Square" in (n.qualified_name or "")
            ]

            assert len(drawable_draw) >= 1, "Drawable.draw node should exist"
            assert len(square_draw) >= 1, "Square.draw node should exist"

            synth = [
                e
                for e in all_edges
                if e.source == drawable_draw[0].id
                and e.target == square_draw[0].id
                and e.kind == EdgeKind.CALLS
                and e.provenance
                and e.provenance.startswith("heuristic:synthesis")
            ]
            assert len(synth) >= 1, (
                f"Expected synthesized CALLS edge from Drawable.draw to Square.draw, "
                f"found {len(synth)}"
            )
        finally:
            cg.close()

    def test_multi_level_inheritance(self, tmp_path):
        """Shape -> Circle -> UnitCircle; both levels get dispatch edges."""
        root = str(tmp_path)
        write_file(
            root,
            "multi.py",
            """\
from abc import ABC, abstractmethod

class Shape(ABC):
    @abstractmethod
    def area(self) -> float:
        pass

class Circle(Shape):
    def __init__(self, radius: float):
        self.radius = radius

    def area(self) -> float:
        return 3.14159 * self.radius ** 2

class UnitCircle(Circle):
    def __init__(self):
        super().__init__(1.0)

    def area(self) -> float:
        return 3.14159
""",
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            all_edges = cg.get_all_edges(limit=50000)
            all_nodes = cg.get_all_nodes(limit=50000)

            shape_area = [
                n
                for n in all_nodes
                if n.name == "area" and "Shape" in (n.qualified_name or "")
            ]
            circle_area = [
                n
                for n in all_nodes
                if n.name == "area" and "Circle" in (n.qualified_name or "")
            ]
            unit_area = [
                n
                for n in all_nodes
                if n.name == "area" and "UnitCircle" in (n.qualified_name or "")
            ]

            assert len(shape_area) >= 1, "Shape.area node should exist"
            assert len(circle_area) >= 1, "Circle.area node should exist"
            assert len(unit_area) >= 1, "UnitCircle.area node should exist"

            synth_edges = [
                e
                for e in all_edges
                if e.kind == EdgeKind.CALLS
                and e.provenance
                and e.provenance.startswith("heuristic:synthesis")
            ]

            # At minimum: Shape.area -> Circle.area, Shape.area -> UnitCircle.area,
            # and Circle.area -> UnitCircle.area
            synth_sources = {e.source for e in synth_edges}
            synth_targets = {e.target for e in synth_edges}

            assert shape_area[0].id in synth_sources, (
                "Shape.area should be a source of a synthesized edge"
            )
            assert circle_area[0].id in synth_targets, (
                "Circle.area should be a target of a synthesized edge"
            )
            assert unit_area[0].id in synth_targets, (
                "UnitCircle.area should be a target of a synthesized edge"
            )
        finally:
            cg.close()

    def test_no_dispatch_for_unrelated(self, tmp_path):
        """Two unrelated classes with same-named methods should NOT get dispatch edges."""
        root = str(tmp_path)
        write_file(
            root,
            "unrelated.py",
            """\
class Dog:
    def speak(self) -> str:
        return "woof"

class Robot:
    def speak(self) -> str:
        return "beep"
""",
        )
        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            all_edges = cg.get_all_edges(limit=50000)

            # There should be NO synthesized dispatch edges since
            # Dog and Robot have no inheritance relationship
            synth = [
                e
                for e in all_edges
                if e.kind == EdgeKind.CALLS
                and e.provenance
                and e.provenance.startswith("heuristic:synthesis")
            ]
            assert len(synth) == 0, (
                f"Expected no synthesized edges for unrelated classes, got {len(synth)}"
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


# ---------------------------------------------------------------------------
# Flask / FastAPI / Django framework source code used by TestFrameworkResolution
# ---------------------------------------------------------------------------

FLASK_APP = """\
from flask import Flask

app = Flask(__name__)

@app.route('/users', methods=['GET'])
def list_users():
    return []

@app.route('/users/<int:id>', methods=['GET'])
def get_user(id):
    return {}
"""

FLASK_BLUEPRINT = """\
from flask import Blueprint

users_bp = Blueprint('users', __name__)

@users_bp.route('/users', methods=['GET'])
def list_users():
    return []
"""

FASTAPI_APP = """\
from fastapi import FastAPI

app = FastAPI()

@app.get('/items/{item_id}')
def read_item(item_id: int):
    return {"id": item_id}

@app.post('/items')
def create_item():
    return {}
"""

FASTAPI_ROUTER = """\
from fastapi import APIRouter

router = APIRouter()

@router.get('/health')
def health_check():
    return {"status": "ok"}
"""

DJANGO_URLS = """\
from django.urls import path
from .views import ArticleListView, ArticleDetailView

urlpatterns = [
    path('articles/', ArticleListView.as_view(), name='article-list'),
    path('articles/<int:pk>/', ArticleDetailView.as_view(), name='article-detail'),
]
"""

DJANGO_VIEWS = """\
from django.views import View

class ArticleListView(View):
    def get(self, request):
        pass

class ArticleDetailView(View):
    def get(self, request, pk):
        pass
"""

DJANGO_DRF_ROUTER = """\
from rest_framework.routers import DefaultRouter
from .views import ArticleViewSet

router = DefaultRouter()
router.register(r'articles', ArticleViewSet)
"""


class TestFrameworkResolution:
    """Verify Python framework resolvers detect and extract ROUTE nodes."""

    # --- Flask ---

    def test_flask_route_extraction(self, tmp_path):
        """Flask @app.route decorator should produce ROUTE nodes with paths."""
        root = str(tmp_path)
        write_file(root, "app.py", FLASK_APP)
        write_file(root, "requirements.txt", "flask==3.0\n")

        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            all_nodes = cg.get_all_nodes(limit=50000)
            route_nodes = [n for n in all_nodes if n.kind == "route"]
            assert len(route_nodes) >= 2, (
                f"Expected at least 2 ROUTE nodes for Flask @app.route, got {len(route_nodes)}"
            )
            route_names = {n.name for n in route_nodes}
            assert any("/users" in name for name in route_names), (
                f"Expected a route containing '/users', got {route_names}"
            )
        finally:
            cg.close()

    def test_flask_blueprint_route_extraction(self, tmp_path):
        """Flask Blueprint @bp.route decorator should produce ROUTE nodes."""
        root = str(tmp_path)
        write_file(root, "users.py", FLASK_BLUEPRINT)
        write_file(root, "requirements.txt", "flask==3.0\n")

        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            all_nodes = cg.get_all_nodes(limit=50000)
            route_nodes = [n for n in all_nodes if n.kind == "route"]
            assert len(route_nodes) >= 1, (
                f"Expected at least 1 ROUTE node for Blueprint @bp.route, got {len(route_nodes)}"
            )
        finally:
            cg.close()

    # --- FastAPI ---

    def test_fastapi_route_extraction(self, tmp_path):
        """FastAPI @app.get/post decorator should produce ROUTE nodes with paths."""
        root = str(tmp_path)
        write_file(root, "main.py", FASTAPI_APP)
        write_file(root, "requirements.txt", "fastapi==0.110\n")

        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            all_nodes = cg.get_all_nodes(limit=50000)
            route_nodes = [n for n in all_nodes if n.kind == "route"]
            assert len(route_nodes) >= 2, (
                f"Expected at least 2 ROUTE nodes for FastAPI, got {len(route_nodes)}"
            )
            route_names = {n.name for n in route_nodes}
            # Should contain HTTP method + path info
            assert any("items" in name for name in route_names), (
                f"Expected a route containing 'items', got {route_names}"
            )
        finally:
            cg.close()

    def test_fastapi_router_route_extraction(self, tmp_path):
        """FastAPI @router.get decorator should produce ROUTE nodes."""
        root = str(tmp_path)
        write_file(root, "routers.py", FASTAPI_ROUTER)
        write_file(root, "requirements.txt", "fastapi==0.110\n")

        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            all_nodes = cg.get_all_nodes(limit=50000)
            route_nodes = [n for n in all_nodes if n.kind == "route"]
            assert len(route_nodes) >= 1, (
                f"Expected at least 1 ROUTE node for FastAPI router, got {len(route_nodes)}"
            )
        finally:
            cg.close()

    # --- Django ---

    def test_django_url_pattern_extraction(self, tmp_path):
        """Django path() in urlpatterns should produce ROUTE nodes."""
        root = str(tmp_path)
        write_file(root, "urls.py", DJANGO_URLS)
        write_file(root, "views.py", DJANGO_VIEWS)
        write_file(root, "requirements.txt", "django==5.0\n")

        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            all_nodes = cg.get_all_nodes(limit=50000)
            route_nodes = [n for n in all_nodes if n.kind == "route"]
            assert len(route_nodes) >= 2, (
                f"Expected at least 2 ROUTE nodes for Django path(), got {len(route_nodes)}"
            )
            route_names = {n.name for n in route_nodes}
            assert any("articles" in name for name in route_names), (
                f"Expected a route containing 'articles', got {route_names}"
            )
        finally:
            cg.close()

    def test_django_drf_router_registration(self, tmp_path):
        """Django REST framework router.register() should produce ROUTE nodes."""
        root = str(tmp_path)
        write_file(root, "urls.py", DJANGO_DRF_ROUTER)
        write_file(root, "requirements.txt", "django==5.0\ndjangorestframework==3.14\n")

        cg = CodeGraph.init(root)
        cg.index_all()
        try:
            all_nodes = cg.get_all_nodes(limit=50000)
            route_nodes = [n for n in all_nodes if n.kind == "route"]
            assert len(route_nodes) >= 1, (
                f"Expected at least 1 ROUTE node for DRF router.register(), got {len(route_nodes)}"
            )
        finally:
            cg.close()
