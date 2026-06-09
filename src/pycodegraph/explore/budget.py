"""Explore budget helpers — call-count tiers and formatting."""

from __future__ import annotations


def get_explore_budget(file_count: int) -> int:
    """Return the recommended number of explore() calls for a project.

    Mirrors the TS CodeGraph tier breakpoints.
    """
    if file_count < 500:
        return 1
    if file_count < 5_000:
        return 2
    if file_count < 15_000:
        return 3
    if file_count < 25_000:
        return 4
    return 5


def format_budget_note(budget_calls: int, file_count: int) -> str:
    """Format the explore-budget blockquote appended to the output.

    Mirrors the TS CodeGraph budget note (src/mcp/tools.ts:2480-2489).
    """
    return (
        f"> **Explore budget: {budget_calls} calls for this project "
        f"({file_count:,} files indexed).** Each call covers ~6 files; "
        f"if your question spans more, spend your remaining calls on the "
        f"uncovered area BEFORE falling back to Read — another explore "
        f"is cheaper and more complete than reading those files."
    )
