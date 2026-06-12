"""Test Analysis — the third indexing stage.

Identifies test Nodes and creates TESTS Edges linking test functions
to the production symbols they directly exercise.
"""

from __future__ import annotations

import json

from ..search.query_utils import is_test_file
from ..types import Node, NodeKind

# Setup/teardown methods that should never be identified as test Nodes
# even when they appear inside a Test-prefixed class in a test file.
_LIFECYCLE_METHODS = frozenset(
    {
        "setUp",
        "tearDown",
        "setUpClass",
        "tearDownClass",
        "setUpModule",
        "tearDownModule",
        "setup_method",
        "teardown_method",
        "setup",
        "teardown",
        "setup_class",
        "teardown_class",
    }
)


def _parse_decorators(node: Node) -> list[str]:
    """Parse the JSON ``decorators`` field of a Node into a list of names."""
    if not node.decorators:
        return []
    try:
        return json.loads(node.decorators)  # type: ignore[no-any-return]
    except (json.JSONDecodeError, TypeError):
        return []


def _is_pytest_fixture(decorator_names: list[str]) -> bool:
    """Return True if any decorator matches ``pytest.fixture``."""
    return any(d == "pytest.fixture" for d in decorator_names)


def is_test_node(node: Node) -> bool:
    """Check whether *node* represents a test function or method.

    A node is recognised as a test if it is a FUNCTION or METHOD inside
    a test file AND (a) its name starts with ``test_`` OR (b) it carries
    a ``@pytest.mark.*`` decorator.

    Exclusions (in priority order, applied before positive checks):

    * **Lifecycle methods** — setup/teardown functions are never tests.
    * ``@pytest.fixture`` — fixture factories are never tests, even if
      their name starts with ``test_``.
    """
    if node.kind not in (NodeKind.FUNCTION, NodeKind.METHOD):
        return False

    if not is_test_file(node.file_path):
        return False

    # 1. Lifecycle method exclusion (fast path — no JSON parse needed)
    if node.name in _LIFECYCLE_METHODS:
        return False

    # 2. Fixture exclusion — @pytest.fixture overrides naming convention
    decorator_names = _parse_decorators(node)
    if _is_pytest_fixture(decorator_names):
        return False

    # 3. Naming-based detection
    if node.name.startswith("test_"):
        return True

    # 4. Decorator-based detection: @pytest.mark.* (fixtures already excluded above)
    return any(d.startswith("pytest.mark.") for d in decorator_names)


# Imported after is_test_node to avoid circular imports with analyzer.
from .analyzer import TestAnalysisResult, TestAnalyzer  # noqa: E402

__all__ = ["TestAnalysisResult", "TestAnalyzer", "is_test_node"]
