"""Tests for the Test Analysis stage: is_test_node() + TestAnalyzer."""

from __future__ import annotations

from pycodegraph.test_analysis import is_test_node
from pycodegraph.types import Language, Node, NodeKind

# =============================================================================
# Helpers
# =============================================================================


def _make_node(
    name: str,
    file_path: str,
    kind: NodeKind = NodeKind.FUNCTION,
    language: Language = Language.PYTHON,
) -> Node:
    """Create a minimal Node for testing."""
    return Node(
        id=f"test:{file_path}::{name}",
        kind=kind,
        name=name,
        qualified_name=name,
        file_path=file_path,
        language=language,
        start_line=1,
        end_line=2,
        start_column=0,
        end_column=0,
        updated_at=0,
    )


# =============================================================================
# is_test_node() unit tests
# =============================================================================


class TestIsTestNode:
    """Unit tests for is_test_node()."""

    # --- Positive cases ---

    def test_test_prefixed_function_in_test_file(self):
        """test_ prefixed function in a test file is a test node."""
        node = _make_node("test_create_user", "tests/test_user.py")
        assert is_test_node(node) is True

    def test_test_prefixed_function_in_test_dir(self):
        """test_ prefixed function in a tests/ directory is a test node."""
        node = _make_node("test_login", "tests/test_auth.py")
        assert is_test_node(node) is True

    def test_test_prefixed_method_in_test_file(self):
        """test_ prefixed method in a test file is a test node."""
        node = _make_node(
            "test_something",
            "tests/test_thing.py",
            kind=NodeKind.METHOD,
        )
        assert is_test_node(node) is True

    def test_test_prefixed_function_in_conftest(self):
        """test_ prefixed function in conftest.py is a test node."""
        node = _make_node("test_fixture", "tests/conftest.py")
        assert is_test_node(node) is True

    # --- Negative cases ---

    def test_non_test_file_function(self):
        """Function in non-test file is not a test node, even with test_ prefix."""
        node = _make_node("test_helper", "src/utils.py")
        assert is_test_node(node) is False

    def test_helper_function_in_test_file(self):
        """Helper function (no test_ prefix) in a test file is not a test node."""
        node = _make_node("setUp", "tests/test_user.py")
        assert is_test_node(node) is False

    def test_helper_fixture_in_test_file(self):
        """Fixture/helper function (no test_ prefix) in test file is not a test node."""
        node = _make_node("create_test_data", "tests/test_user.py")
        assert is_test_node(node) is False

    def test_non_function_node_in_test_file(self):
        """Class in test file is not a test node."""
        node = _make_node(
            "TestUser",
            "tests/test_user.py",
            kind=NodeKind.CLASS,
        )
        assert is_test_node(node) is False

    def test_variable_in_test_file(self):
        """Variable in test file is not a test node."""
        node = _make_node(
            "SOME_CONSTANT",
            "tests/test_user.py",
            kind=NodeKind.VARIABLE,
        )
        assert is_test_node(node) is False

    def test_test_function_in_non_test_file(self):
        """test_ prefixed function in a regular source file is not a test node."""
        node = _make_node("test_internal_thing", "src/helpers.py")
        assert is_test_node(node) is False


# =============================================================================
# Test Analysis stage — integration tests
# =============================================================================

PROD_MODELS = """\
class User:
    def __init__(self, name: str, email: str):
        self.name = name
        self.email = email

    def greet(self) -> str:
        return f"Hello, {self.name}!"

class Admin(User):
    def greet(self) -> str:
        return f"Hello, admin {self.name}!"
"""

PROD_SERVICES = """\
from src.models import User


def create_user(name: str, email: str) -> User:
    return User(name, email)


def notify_user(user: User) -> str:
    return user.greet()
"""

TEST_MODELS = """\
from src.models import User, Admin


def test_user_creation():
    user = User("Alice", "alice@example.com")
    assert user.name == "Alice"


def test_user_greet():
    user = User("Bob", "bob@example.com")
    greeting = user.greet()
    assert "Bob" in greeting


def helper_build_user(name: str) -> User:
    return User(name, f"{name}@example.com")


def test_admin_greet():
    admin = Admin("Carol", "carol@example.com")
    assert "admin" in admin.greet()
"""

TEST_SERVICES = """\
from src.models import User
from src.services import create_user, notify_user


def test_create_user():
    user = create_user("Dave", "dave@example.com")
    assert user.name == "Dave"


def test_notify_user():
    user = User("Eve", "eve@example.com")
    result = notify_user(user)
    assert "Eve" in result
"""


def _write_project_files(root: str) -> None:
    """Write a synthetic Python project with src/ and tests/ directories."""
    from tests.conftest import write_file

    write_file(root, "src/__init__.py", "")
    write_file(root, "src/models.py", PROD_MODELS)
    write_file(root, "src/services.py", PROD_SERVICES)
    write_file(root, "tests/__init__.py", "")
    write_file(root, "tests/test_models.py", TEST_MODELS)
    write_file(root, "tests/test_services.py", TEST_SERVICES)


class TestTestAnalysisIntegration:
    """Integration tests for the full Test Analysis stage."""

    def test_tests_edges_exist_after_indexing(self, tmp_path):
        """After indexing a project with test files, TESTS edges link test
        functions to the production symbols they directly call."""
        from pycodegraph import CodeGraph

        root = str(tmp_path)
        _write_project_files(root)

        cg = CodeGraph.init(root)
        cg.index_all()

        # Collect all TESTS edges
        all_edges = cg.get_all_edges()
        tests_edges = [e for e in all_edges if e.kind.value == "tests"]

        assert len(tests_edges) > 0, "Expected at least one TESTS edge"

        # Build lookup: test_node_id -> set of target node names
        test_to_targets: dict[str, set[str]] = {}
        for e in tests_edges:
            test_to_targets.setdefault(e.source, set()).add(e.target)

        # Verify source is a test node, target is a production node
        all_nodes = {n.id: n for n in cg.get_all_nodes()}
        for e in tests_edges:
            assert is_test_node(all_nodes[e.source]), (
                f"TESTS edge source should be a test node: {all_nodes[e.source].name}"
            )
            assert not is_test_node(all_nodes[e.target]), (
                f"TESTS edge target should not be a test node: {all_nodes[e.target].name}"
            )

        cg.close()

    def test_direct_calls_create_tests_edges(self, tmp_path):
        """Direct calls from test functions to imported production functions
        create TESTS edges."""
        from pycodegraph import CodeGraph

        root = str(tmp_path)
        _write_project_files(root)

        cg = CodeGraph.init(root)
        cg.index_all()

        all_nodes = {n.id: n for n in cg.get_all_nodes()}
        all_edges = cg.get_all_edges()
        tests_edges = [e for e in all_edges if e.kind.value == "tests"]

        # Find the test_create_user function
        test_funcs = {
            n.id: n
            for n in all_nodes.values()
            if n.name == "test_create_user" and is_test_node(n)
        }
        assert len(test_funcs) >= 1

        for test_id in test_funcs:
            # Should have a TESTS edge to create_user
            outgoing_tests = [e for e in tests_edges if e.source == test_id]
            targets = {all_nodes[e.target].name for e in outgoing_tests}
            assert "create_user" in targets or "User" in targets, (
                "Expected test_create_user to have TESTS edge to create_user or User"
            )

        cg.close()

    def test_tests_edges_deduplicated(self, tmp_path):
        """TESTS edges are deduplicated per (source, target) pair."""
        from pycodegraph import CodeGraph

        root = str(tmp_path)
        _write_project_files(root)

        cg = CodeGraph.init(root)
        cg.index_all()

        tests_edges = [e for e in cg.get_all_edges() if e.kind.value == "tests"]

        # Check no duplicate (source, target) pairs
        pairs = [(e.source, e.target) for e in tests_edges]
        assert len(pairs) == len(set(pairs)), "Found duplicate TESTS edge pairs"

        cg.close()

    def test_helper_functions_not_test_nodes(self, tmp_path):
        """Helper functions in test files do NOT get TESTS edges as sources."""
        from pycodegraph import CodeGraph

        root = str(tmp_path)
        _write_project_files(root)

        cg = CodeGraph.init(root)
        cg.index_all()

        tests_edges = [e for e in cg.get_all_edges() if e.kind.value == "tests"]

        # helper_build_user should not be a source of any TESTS edge
        all_nodes = {n.id: n for n in cg.get_all_nodes()}
        helper_sources = [
            e.source
            for e in tests_edges
            if all_nodes.get(e.source)
            and all_nodes[e.source].name == "helper_build_user"
        ]
        assert len(helper_sources) == 0, (
            "Helper function should not be a TESTS edge source"
        )

        cg.close()

    def test_no_tests_edges_for_non_imported_modules(self, tmp_path):
        """No TESTS edges are created for calls to modules not imported by the test file."""
        from pycodegraph import CodeGraph

        root = str(tmp_path)
        _write_project_files(root)
        # Add a utils module that no test file imports
        from tests.conftest import write_file

        write_file(
            root,
            "src/utils.py",
            "def format_date(ts: int) -> str:\n    return str(ts)\n",
        )

        cg = CodeGraph.init(root)
        cg.index_all()

        all_nodes = {n.id: n for n in cg.get_all_nodes()}
        tests_edges = [e for e in cg.get_all_edges() if e.kind.value == "tests"]

        # No TESTS edge target should be in src/utils.py (not imported by any test)
        for e in tests_edges:
            target = all_nodes.get(e.target)
            if target:
                assert target.file_path != "src/utils.py", (
                    f"TESTS edge to {target.name} in src/utils.py — module not imported by tests"
                )

        cg.close()
