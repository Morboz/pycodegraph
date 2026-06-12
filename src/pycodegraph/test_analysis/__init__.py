"""Test Analysis — the third indexing stage.

Identifies test Nodes and creates TESTS Edges linking test functions
to the production symbols they directly exercise.
"""

from __future__ import annotations

from ..search.query_utils import is_test_file
from ..types import Node, NodeKind


def is_test_node(node: Node) -> bool:
    """Check whether *node* represents a test function or method.

    Combines file-level test detection (:func:`is_test_file`) with naming
    conventions.  For the MVP slice, only ``test_``-prefixed functions and
    methods in detected test files are recognised.  Decorator detection and
    test-class method support are out of scope.
    """
    if node.kind not in (NodeKind.FUNCTION, NodeKind.METHOD):
        return False

    if not is_test_file(node.file_path):
        return False

    return node.name.startswith("test_")


# Imported after is_test_node to avoid circular imports with analyzer.
from .analyzer import TestAnalysisResult, TestAnalyzer  # noqa: E402

__all__ = ["TestAnalysisResult", "TestAnalyzer", "is_test_node"]
