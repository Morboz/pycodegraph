"""Blast radius — who depends on entry symbols and which tests cover them."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..search.query_utils import is_test_file
from ..types import Node

if TYPE_CHECKING:
    from ..graph.traversal import GraphTraverser

_ROOT_CAP = 5
_FILE_CAP = 4


def compute_blast_radius(
    entry_nodes: list[Node],
    traverser: GraphTraverser,
    query: str = "",
) -> str:
    """Compute blast radius for top entry symbols.

    For each entry symbol, find callers and test coverage.
    Returns a markdown section string, or empty string if nothing found.
    """
    entries = entry_nodes[:_ROOT_CAP]
    if not entries:
        return ""

    entry_lines: list[str] = []
    for node in entries:
        try:
            callers = traverser.get_callers(node.id, max_depth=1)
        except Exception:
            continue

        # Deduplicate
        seen: set[str] = set()
        uniq_callers: list[Node] = []
        for caller_node, _edge in callers:
            if caller_node.id not in seen:
                seen.add(caller_node.id)
                uniq_callers.append(caller_node)

        if not uniq_callers:
            continue

        caller_files = list({n.file_path for n in uniq_callers})
        test_files = [f for f in caller_files if is_test_file(f)]
        non_test = [f for f in caller_files if not is_test_file(f)]

        rel = lambda p: p.replace("\\", "/")  # noqa: E731

        shown = ", ".join(f"`{rel(f)}`" for f in non_test[:_FILE_CAP])
        more = (
            f" +{len(non_test) - _FILE_CAP} more" if len(non_test) > _FILE_CAP else ""
        )
        where = f" in {shown}{more}" if non_test else ""

        if test_files:
            tests = ", ".join(f"`{rel(f)}`" for f in test_files[:_FILE_CAP])
            more_tests = (
                f" +{len(test_files) - _FILE_CAP}"
                if len(test_files) > _FILE_CAP
                else ""
            )
            tests_str = f"; tests: {tests}{more_tests}"
        else:
            tests_str = "; ⚠️ no covering tests found"

        entry_lines.append(
            f"- `{node.name}` ({rel(node.file_path)}:{node.start_line})"
            f" — {len(uniq_callers)} caller{'s' if len(uniq_callers) != 1 else ''}"
            f"{where}{tests_str}"
        )

    if not entry_lines:
        return ""

    return "\n".join(
        [
            "### Blast radius — what depends on these (update/verify before editing)",
            "",
            *entry_lines,
            "",
        ]
    )
