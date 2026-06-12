"""Tests for the Test Analysis stage: is_test_node() + TestAnalyzer."""

from __future__ import annotations

import json

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
    *,
    decorators: list[str] | None = None,
    qualified_name: str | None = None,
) -> Node:
    """Create a minimal Node for testing."""
    kwargs: dict = {}
    if decorators is not None:
        kwargs["decorators"] = json.dumps(decorators)
    return Node(
        id=f"test:{file_path}::{name}",
        kind=kind,
        name=name,
        qualified_name=qualified_name or name,
        file_path=file_path,
        language=language,
        start_line=1,
        end_line=2,
        start_column=0,
        end_column=0,
        updated_at=0,
        **kwargs,
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

    # --- Fixture exclusion ---

    def test_pytest_fixture_excluded_even_with_test_prefix(self):
        """Function decorated with @pytest.fixture is not a test node,
        even if its name starts with test_."""
        node = _make_node(
            "test_db",
            "tests/test_user.py",
            decorators=["pytest.fixture"],
        )
        assert is_test_node(node) is False

    def test_pytest_fixture_excluded(self):
        """Function decorated with @pytest.fixture is not a test node."""
        node = _make_node(
            "db_session",
            "tests/test_user.py",
            decorators=["pytest.fixture"],
        )
        assert is_test_node(node) is False

    # --- Decorator-based detection ---

    def test_decorated_with_pytest_mark(self):
        """Function decorated with @pytest.mark.slow is a test node,
        even without test_ prefix."""
        node = _make_node(
            "slow_test",
            "tests/test_user.py",
            decorators=["pytest.mark.slow"],
        )
        assert is_test_node(node) is True

    def test_decorated_with_pytest_mark_parametrize(self):
        """Function decorated with @pytest.mark.parametrize is a test node."""
        node = _make_node(
            "parametrized_check",
            "tests/test_user.py",
            decorators=["pytest.mark.parametrize"],
        )
        assert is_test_node(node) is True

    def test_non_decorated_non_test_prefixed_not_test(self):
        """Helper function without decorators and without test_ prefix
        is not a test node in a test file."""
        node = _make_node("process_data", "tests/test_user.py")
        assert is_test_node(node) is False

    # --- Lifecycle method exclusion ---

    def test_lifecycle_setup_not_test(self):
        """setUp in a test file is not a test node."""
        node = _make_node("setUp", "tests/test_user.py")
        assert is_test_node(node) is False

    def test_lifecycle_teardown_not_test(self):
        """tearDown in a test file is not a test node."""
        node = _make_node("tearDown", "tests/test_user.py")
        assert is_test_node(node) is False

    def test_lifecycle_setup_method_not_test(self):
        """setup_method in a test file is not a test node."""
        node = _make_node("setup_method", "tests/test_user.py")
        assert is_test_node(node) is False

    def test_lifecycle_setup_class_not_test(self):
        """setup_class in a test file is not a test node."""
        node = _make_node("setup_class", "tests/test_user.py")
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

PROD_UTILS = """\
def format_date(ts: int) -> str:
    return str(ts)


def validate_email(email: str) -> bool:
    return "@" in email
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

TEST_EXTENDED = """\
from src.models import User
from src.utils import format_date, validate_email

import pytest


@pytest.mark.parametrize
def parametrized_date_check():
    user = User("Frank", "frank@example.com")
    result = format_date(42)
    return result


@pytest.mark.slow
def slow_validation_check():
    return validate_email("test@example.com")


@pytest.fixture
def test_db_session():
    return User("TestDB", "test@example.com")


@pytest.fixture
def test_fixture_with_name():
    return User("Fixture", "fixture@example.com")
"""

TEST_CLASS = """\
from src.models import User
from src.services import create_user


class TestUser:
    def test_create(self):
        user = create_user("Grace", "grace@example.com")
        assert user.name == "Grace"

    def test_default_greet(self):
        user = User("Heidi", "heidi@example.com")
        assert "Heidi" in user.greet()

    def setup_method(self):
        self._cache = {}

    def test_with_cache(self):
        self._cache["key"] = User("Ivan", "ivan@example.com")
        user = self._cache["key"]
        assert user.name == "Ivan"

    def tearDown(self):
        self._cache = None
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


def _write_extended_project_files(root: str) -> None:
    """Write a synthetic Python project that includes decorator-based,
    test-class, and fixture patterns for extended detection testing."""
    _write_project_files(root)
    from tests.conftest import write_file

    write_file(root, "src/utils.py", PROD_UTILS)
    write_file(root, "tests/test_extended.py", TEST_EXTENDED)
    write_file(root, "tests/test_class.py", TEST_CLASS)


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


class TestExtendedDetectionIntegration:
    """Integration tests for extended is_test_node() detection patterns.

    Verifies that decorator-based detection, test class methods, and
    fixture exclusion work end-to-end through the full indexing pipeline.
    """

    def test_decorated_function_creates_tests_edges(self, tmp_path):
        """A @pytest.mark.parametrize decorated function (without test_ prefix)
        is recognised as a test node and produces TESTS edges."""
        from pycodegraph import CodeGraph

        root = str(tmp_path)
        _write_extended_project_files(root)

        cg = CodeGraph.init(root)
        cg.index_all()

        all_nodes = {n.id: n for n in cg.get_all_nodes()}
        tests_edges = [e for e in cg.get_all_edges() if e.kind.value == "tests"]

        # Find the parametrized_date_check function
        dec_nodes = {
            n.id: n for n in all_nodes.values() if n.name == "parametrized_date_check"
        }
        assert len(dec_nodes) >= 1, "parametrized_date_check should exist in the graph"

        for nid in dec_nodes:
            assert is_test_node(all_nodes[nid]), (
                "parametrized_date_check should be identified as a test node"
            )
            outgoing = [e for e in tests_edges if e.source == nid]
            assert len(outgoing) >= 1, "parametrized_date_check should have TESTS edges"
            targets = {all_nodes[e.target].name for e in outgoing}
            assert "format_date" in targets, "Expected TESTS edge to format_date"

        cg.close()

    def test_slow_marked_function_creates_tests_edges(self, tmp_path):
        """A @pytest.mark.slow decorated function is a test node."""
        from pycodegraph import CodeGraph

        root = str(tmp_path)
        _write_extended_project_files(root)

        cg = CodeGraph.init(root)
        cg.index_all()

        all_nodes = {n.id: n for n in cg.get_all_nodes()}
        tests_edges = [e for e in cg.get_all_edges() if e.kind.value == "tests"]

        slow_nodes = {
            n.id: n for n in all_nodes.values() if n.name == "slow_validation_check"
        }
        assert len(slow_nodes) >= 1

        for nid in slow_nodes:
            assert is_test_node(all_nodes[nid]), (
                "slow_validation_check should be a test node"
            )
            outgoing = [e for e in tests_edges if e.source == nid]
            assert len(outgoing) >= 1
            targets = {all_nodes[e.target].name for e in outgoing}
            assert "validate_email" in targets

        cg.close()

    def test_fixture_not_test_node(self, tmp_path):
        """A @pytest.fixture decorated function is NOT a test node,
        even if its name starts with test_."""
        from pycodegraph import CodeGraph

        root = str(tmp_path)
        _write_extended_project_files(root)

        cg = CodeGraph.init(root)
        cg.index_all()

        all_nodes = {n.id: n for n in cg.get_all_nodes()}
        tests_edges = [e for e in cg.get_all_edges() if e.kind.value == "tests"]

        # test_db_session starts with test_ but is a fixture — not a test
        fixture_nodes = {
            n.id: n for n in all_nodes.values() if n.name == "test_db_session"
        }
        for nid in fixture_nodes:
            assert not is_test_node(all_nodes[nid]), (
                "test_db_session (fixture) should NOT be a test node"
            )
            outgoing = [e for e in tests_edges if e.source == nid]
            assert len(outgoing) == 0, "Fixture should not be a TESTS edge source"

        # test_fixture_with_name similarly
        fixture2_nodes = {
            n.id: n for n in all_nodes.values() if n.name == "test_fixture_with_name"
        }
        for nid in fixture2_nodes:
            assert not is_test_node(all_nodes[nid])
            outgoing = [e for e in tests_edges if e.source == nid]
            assert len(outgoing) == 0

        cg.close()

    def test_class_method_creates_tests_edges(self, tmp_path):
        """A test_ method inside a Test-prefixed class produces TESTS edges."""
        from pycodegraph import CodeGraph

        root = str(tmp_path)
        _write_extended_project_files(root)

        cg = CodeGraph.init(root)
        cg.index_all()

        all_nodes = {n.id: n for n in cg.get_all_nodes()}
        tests_edges = [e for e in cg.get_all_edges() if e.kind.value == "tests"]

        # Find TestUser.test_create method
        test_create_nodes = {
            n.id: n for n in all_nodes.values() if n.name == "test_create"
        }
        assert len(test_create_nodes) >= 1

        for nid in test_create_nodes:
            assert is_test_node(all_nodes[nid]), (
                "TestUser.test_create should be a test node"
            )
            outgoing = [e for e in tests_edges if e.source == nid]
            targets = {all_nodes[e.target].name for e in outgoing}
            assert "create_user" in targets, (
                "Expected TESTS edge from TestUser.test_create to create_user"
            )

        cg.close()

    def test_lifecycle_method_not_test_node(self, tmp_path):
        """Lifecycle methods inside a test class are NOT test nodes."""
        from pycodegraph import CodeGraph

        root = str(tmp_path)
        _write_extended_project_files(root)

        cg = CodeGraph.init(root)
        cg.index_all()

        all_nodes = {n.id: n for n in cg.get_all_nodes()}

        # setup_method in TestUser class
        sm_nodes = {n.id: n for n in all_nodes.values() if n.name == "setup_method"}
        for nid in sm_nodes:
            assert not is_test_node(all_nodes[nid]), (
                "setup_method should not be a test node"
            )

        # tearDown in TestUser class
        td_nodes = {n.id: n for n in all_nodes.values() if n.name == "tearDown"}
        for nid in td_nodes:
            assert not is_test_node(all_nodes[nid]), (
                "tearDown should not be a test node"
            )

        # Regular test method with_cache should still be a test node
        wc_nodes = {n.id: n for n in all_nodes.values() if n.name == "test_with_cache"}
        for nid in wc_nodes:
            assert is_test_node(all_nodes[nid]), (
                "test_with_cache in TestUser class should be a test node"
            )

        cg.close()
