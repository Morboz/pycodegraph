"""Context module - context building and formatting for code understanding."""

from .builder import ContextBuilder
from .formatter import (
    format_context_as_json,
    format_context_as_markdown,
    format_subgraph_tree,
)

__all__ = [
    "ContextBuilder",
    "format_context_as_json",
    "format_context_as_markdown",
    "format_subgraph_tree",
]
