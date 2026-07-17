"""RST + YAML extraction for option + default documentation relations.

Extracts ``documents_option`` and ``documents_default`` relations from RST
source files by reading YAML ``DOCUMENTATION`` blocks and RST ``.. option::``
directives.

Decision A (issue #102): docutils is a soft dependency under
``[project.optional-dependencies] docgraph``. If docutils is not installed,
the import raises ``ImportError`` immediately.

Decision H (issue #102): the extractor is constructed with an ``rst_root``
path pointing at the ansible-documentation repository root. Relpaths from
graphify-out nodes are resolved relative to this root.

Decision F (issue #102): ``content_digest`` is ``sha256:<rst_rel_path>:<start_line>:<end_line>``.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

try:
    import docutils.core
    import docutils.frontend
    import docutils.nodes
    import docutils.parsers.rst
    import docutils.utils
except ImportError:
    raise ImportError(
        "docutils is required for RST extraction. "
        "Install with: pip install pycodegraph[docgraph]"
    ) from None


def _node_line(node: docutils.nodes.Node) -> int | None:
    """Get the 1-based source line of a docutils node."""
    line = node.line
    if isinstance(line, int):
        return line
    for child in node.children:
        child_line = _node_line(child)
        if child_line is not None:
            return child_line
    return None


def _collect_text_lines(node: docutils.nodes.Node) -> list[str]:
    """Collect text lines from a docutils node."""
    lines: list[str] = []
    for child in node.children:
        if isinstance(child, docutils.nodes.paragraph):
            lines.append(child.astext())
        elif isinstance(child, docutils.nodes.literal_block):
            lines.append(f":: {child.astext()}")
        elif isinstance(child, docutils.nodes.enumerated_list):
            for item in child.children:
                lines.append(f"- {item.astext()}")
        elif isinstance(child, docutils.nodes.bullet_list):
            for item in child.children:
                lines.append(f"* {item.astext()}")
        elif isinstance(child, docutils.nodes.system_message):
            continue
        else:
            text = child.astext().strip()
            if text:
                lines.append(text)
    return lines


def _extract_option_sections(
    rst_text: str, source_path: str = ""
) -> list[dict[str, Any]]:
    """Parse ``.. option::`` directives from RST text using line-based parsing.

    Standard docutils does not support the ``.. option::`` directive — it's a
    Sphinx / Ansible extension. So we use a regex-based approach.

    Returns a list of dicts::

        {
            "directive_line": int,        # 1-based line of ``.. option::``
            "option_name": str,           # e.g. "ANSIBLE_PERSISTENT_COMMAND_TIMEOUT"
            "body_lines": list[str],      # body text lines after the directive
        }
    """
    results: list[dict[str, Any]] = []
    lines = rst_text.splitlines()

    for i, line in enumerate(lines):
        stripped = line.strip()
        m = re.match(r"^\.\.\s+option::\s+(.+)$", stripped)
        if not m:
            continue

        option_name = m.group(1).strip()
        directive_line = i + 1  # 1-based

        # Collect body text (indented lines after the directive).
        body_lines: list[str] = []
        for j in range(i + 1, len(lines)):
            next_line = lines[j]
            # Body is indented; a non-indented line or a new directive ends it.
            if (
                next_line.strip()
                and not next_line.startswith(" ")
                and not next_line.startswith("\t")
            ):
                break
            if next_line.startswith(".. ") and "::" in next_line:
                break
            body_text = next_line.strip()
            if body_text:
                body_lines.append(body_text)

        results.append(
            {
                "directive_line": directive_line,
                "option_name": option_name,
                "body_lines": body_lines,
            }
        )

    return results


# ---------------------------------------------------------------------------
# Admonition extraction (Phase 2 — issue #102)
# ---------------------------------------------------------------------------

#: Admonition directives that map to ``documents_behavior`` (informational).
_BEHAVIOR_DIRECTIVES: frozenset[str] = frozenset({"note", "tip", "important"})

#: Admonition directives that map to ``documents_safety`` (warnings/dangers).
_SAFETY_DIRECTIVES: frozenset[str] = frozenset(
    {"warning", "danger", "caution", "error"}
)


def _extract_admonition_sections(
    rst_text: str,
    source_path: str = "",
) -> list[dict[str, Any]]:
    """Parse ``.. note::`` / ``.. warning::`` / ``.. danger::`` etc. directives.

    Returns a list of dicts::

        {
            "directive_line": int,        # 1-based line of the directive
            "admonition_kind": str,       # "note" | "warning" | "danger" | ...
            "category": str,              # "behavior" | "safety"
            "body_lines": list[str],      # body text lines after the directive
        }
    """
    results: list[dict[str, Any]] = []
    lines = rst_text.splitlines()

    directive_re = re.compile(r"^\.\.\s+([a-zA-Z]+)::\s*(.*)$")

    for i, line in enumerate(lines):
        stripped = line.strip()
        m = directive_re.match(stripped)
        if not m:
            continue

        admonition_kind = m.group(1).lower()
        if admonition_kind in _BEHAVIOR_DIRECTIVES:
            category = "behavior"
        elif admonition_kind in _SAFETY_DIRECTIVES:
            category = "safety"
        else:
            continue  # not an admonition we care about

        directive_line = i + 1  # 1-based

        # Collect body text (indented lines after the directive).
        body_lines: list[str] = []
        for j in range(i + 1, len(lines)):
            next_line = lines[j]
            # Body is indented; a non-indented line or a new directive ends it.
            if (
                next_line.strip()
                and not next_line.startswith(" ")
                and not next_line.startswith("\t")
            ):
                break
            if next_line.startswith(".. ") and "::" in next_line:
                break
            body_text = next_line.strip()
            if body_text:
                body_lines.append(body_text)

        results.append(
            {
                "directive_line": directive_line,
                "admonition_kind": admonition_kind,
                "category": category,
                "body_lines": body_lines,
            }
        )

    return results


def extract_behavior_and_safety_relations(
    rst_rel_path: str,
    rst_root: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract ``documents_behavior`` and ``documents_safety`` from an RST file.

    Reads the RST file at ``rst_root / rst_rel_path`` (with the same path
    normalization as :func:`extract_option_and_default_relations`) and
    returns ``(behavior_results, safety_results)``.

    Each result dict has::

        {
            "admonition_kind": str,   # "note" | "warning" | "danger" | ...
            "source_file": str,
            "start_line": int,
            "end_line": int,
            "description": str,
            "content_digest": str,
        }
    """
    rst_text, used_path = _read_rst_file(rst_rel_path, rst_root)
    if rst_text is None:
        return [], []

    behavior_results: list[dict[str, Any]] = []
    safety_results: list[dict[str, Any]] = []

    sections = _extract_admonition_sections(rst_text, used_path)
    for section in sections:
        start_line = section["directive_line"]
        body = " ".join(section["body_lines"])
        end_line = start_line + max(len(section["body_lines"]), 1) + 1
        content_digest = _content_digest_for_span(used_path, start_line, end_line)

        record = {
            "admonition_kind": section["admonition_kind"],
            "source_file": used_path,
            "start_line": start_line,
            "end_line": end_line,
            "description": body,
            "content_digest": content_digest,
        }

        if section["category"] == "behavior":
            behavior_results.append(record)
        else:
            safety_results.append(record)

    return behavior_results, safety_results


# ---------------------------------------------------------------------------
# seealso extraction (Phase 3 — issue #102)
# ---------------------------------------------------------------------------


def _extract_seealso_sections(
    rst_text: str,
    source_path: str = "",
) -> list[dict[str, Any]]:
    """Parse ``.. seealso::`` directives from RST text.

    Returns a list of dicts::

        {
            "directive_line": int,        # 1-based line of ``.. seealso::``
            "body_lines": list[str],      # body text lines after the directive
            "refs": list[str],            # extracted :ref: and link targets
        }
    """
    results: list[dict[str, Any]] = []
    lines = rst_text.splitlines()

    seealso_re = re.compile(r"^\.\.\s+seealso::\s*(.*)$")

    for i, line in enumerate(lines):
        stripped = line.strip()
        m = seealso_re.match(stripped)
        if not m:
            continue

        directive_line = i + 1  # 1-based

        # Collect body text (indented lines after the directive).
        body_lines: list[str] = []
        refs: list[str] = []
        ref_re = re.compile(r":ref:`([^`<]+)(?:\s*<([^`]+)>)?`")
        link_re = re.compile(r"`[^`<]+\s*<([^`]+)>`_")

        for j in range(i + 1, len(lines)):
            next_line = lines[j]
            # Body is indented; a non-indented line or a new directive ends it.
            if (
                next_line.strip()
                and not next_line.startswith(" ")
                and not next_line.startswith("\t")
            ):
                break
            if next_line.startswith(".. ") and "::" in next_line:
                break
            body_text = next_line.strip()
            if body_text:
                body_lines.append(body_text)
                # Extract :ref:`...` targets.
                for ref_m in ref_re.finditer(body_text):
                    label, target = ref_m.group(1), ref_m.group(2)
                    refs.append(target if target else label)
                # Extract hyperlink targets `<URL>`_.
                for link_m in link_re.finditer(body_text):
                    refs.append(link_m.group(1))

        results.append(
            {
                "directive_line": directive_line,
                "body_lines": body_lines,
                "refs": refs,
            }
        )

    return results


def extract_validation_relations(
    rst_rel_path: str,
    rst_root: str,
) -> list[dict[str, Any]]:
    """Extract ``documents_validation`` relations from RST ``.. seealso::`` blocks.

    Reads the RST file at ``rst_root / rst_rel_path`` (with the same path
    normalization as :func:`extract_option_and_default_relations`) and
    returns a list of result dicts.

    Each result dict has::

        {
            "source_file": str,
            "start_line": int,
            "end_line": int,
            "description": str,
            "refs": list[str],        # extracted :ref: and link targets
            "content_digest": str,
        }
    """
    rst_text, used_path = _read_rst_file(rst_rel_path, rst_root)
    if rst_text is None:
        return []

    results: list[dict[str, Any]] = []
    sections = _extract_seealso_sections(rst_text, used_path)
    for section in sections:
        start_line = section["directive_line"]
        body = " ".join(section["body_lines"])
        end_line = start_line + max(len(section["body_lines"]), 1) + 1
        content_digest = _content_digest_for_span(used_path, start_line, end_line)

        results.append(
            {
                "source_file": used_path,
                "start_line": start_line,
                "end_line": end_line,
                "description": body,
                "refs": section["refs"],
                "content_digest": content_digest,
            }
        )

    return results


_DOCUMENTATION_BLOCK_RE = re.compile(
    r"DOCUMENTATION\s*=\s*r?['\"]{3}(.*?)['\"]{3}",
    re.DOTALL,
)


def _extract_yaml_documentation_block(rst_text: str) -> str | None:
    """Extract the YAML string from a Python ``DOCUMENTATION = r'''...'''`` block.

    Returns the raw YAML string or ``None`` if no documentation block is found.
    """
    m = _DOCUMENTATION_BLOCK_RE.search(rst_text)
    if m:
        return m.group(1).strip()
    return None


def _parse_documentation_yaml(
    yaml_text: str,
    line_offset: int,
) -> list[dict[str, Any]]:
    """Parse options from a YAML DOCUMENTATION block.

    Uses a line-based parser since the YAML is embedded in Python docstrings.
    Also handles the case where yaml_text has its first line already dedented
    (after extraction from r'''...''').

    Returns::

        [
            {
                "option_name": str,
                "default": str | None,
                "doc_line": int,
                "description": str,
            },
        ]
    """
    results: list[dict[str, Any]] = []
    in_options = False
    lines = yaml_text.splitlines()

    # YAML block is typically indented inside a Python docstring.
    # Normalize: strip common leading whitespace.
    if lines:
        first_line = lines[0]
        indent = len(first_line) - len(first_line.lstrip())
        if indent > 0 and first_line.strip():
            # Partial dedent
            pass

    for i, line in enumerate(lines):
        abs_line = line_offset + i + 1
        stripped = line.strip()

        if stripped == "options:":
            in_options = True
            continue
        if stripped.startswith("EXAMPLES:") or stripped.startswith("RETURN:"):
            break
        if not in_options:
            continue

        # Match lines like "  name:" or "    timeout:" (at least 1 indent)
        m = re.match(r"^(\s+)([a-zA-Z_][a-zA-Z0-9_]*):\s*$", line)
        if m:
            indent_len = len(m.group(1))
            option_name = m.group(2)
            # Top-level options inside the options block typically have
            # 2-4 spaces of indentation.
            if indent_len < 2:
                continue

            default_value: str | None = None
            description = ""
            for j in range(i + 1, min(i + 20, len(lines))):
                next_line = lines[j]
                next_stripped = next_line.strip()
                # Stop if we hit another option at the same indentation level
                next_m = re.match(r"^(\s+)([a-zA-Z_][a-zA-Z0-9_]*):\s*$", next_line)
                if next_m and len(next_m.group(1)) == indent_len:
                    break
                # Stop if we hit a section boundary (less indented line)
                if (
                    next_stripped
                    and not next_line.startswith(" ")
                    and ":" in next_stripped
                ):
                    break
                default_m = re.match(r"^\s+default:\s*(.+)$", next_line)
                if default_m:
                    default_value = default_m.group(1).strip()
                desc_m = re.match(r"^\s+description:\s*(.+)$", next_line)
                if desc_m and not description:
                    raw_desc = desc_m.group(1).strip()
                    # Strip leading dash for list-style descriptions
                    description = raw_desc.lstrip("- ").strip()

            results.append(
                {
                    "option_name": option_name,
                    "default": default_value,
                    "doc_line": abs_line,
                    "description": description or option_name,
                }
            )

    return results


def extract_option_and_default_relations(
    rst_rel_path: str,
    rst_root: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract option + default relations from an RST or Python module file.

    Handles two extraction paths:
    1. RST ``.. option::`` directives (in ``.rst`` files).
    2. YAML ``DOCUMENTATION`` blocks (in ``.py`` or ``.rst`` files).

    Returns ``(option_results, default_results)`` where each is a list of
    dicts ready for conversion to ``SemanticRelation``.

    Args:
        rst_rel_path: Relative path from the ansible-documentation repo root.
        rst_root: Absolute path to the ansible-documentation repo root.

    Returns:
        Tuple of (option_results, default_results). Each result dict has::

            {
                "option_name": str,
                "source_file": str,
                "start_line": int,
                "end_line": int,
                "description": str,
                "content_digest": str,
                "default_value": str | None,  # only in default_results
            }
    """
    rst_text, used_path = _read_rst_file(rst_rel_path, rst_root)
    if rst_text is None:
        return [], []

    # Use the relative path that was actually read for content_digest.
    rel_path_for_digest = used_path

    option_results: list[dict[str, Any]] = []
    default_results: list[dict[str, Any]] = []

    # Path 1: RST ``.. option::`` directives
    option_sections = _extract_option_sections(rst_text, rel_path_for_digest)
    for section in option_sections:
        start_line = section["directive_line"]
        body = " ".join(section["body_lines"])
        end_line = start_line + max(len(section["body_lines"]), 1) + 1
        content_digest = _content_digest_for_span(
            rel_path_for_digest, start_line, end_line
        )

        option_results.append(
            {
                "option_name": section["option_name"],
                "source_file": rel_path_for_digest,
                "start_line": start_line,
                "end_line": end_line,
                "description": body,
                "content_digest": content_digest,
                "default_value": None,
            }
        )

    # Path 2: YAML DOCUMENTATION block
    yaml_text = _extract_yaml_documentation_block(rst_text)
    if yaml_text:
        doc_m = re.search(r"DOCUMENTATION\s*=", rst_text)
        doc_line = 1
        if doc_m:
            doc_line = rst_text[: doc_m.start()].count("\n") + 1

        options = _parse_documentation_yaml(yaml_text, doc_line)
        for opt in options:
            start_line = opt["doc_line"]
            end_line = start_line + 5
            content_digest = _content_digest_for_span(
                rel_path_for_digest, start_line, end_line
            )

            option_results.append(
                {
                    "option_name": opt["option_name"],
                    "source_file": rel_path_for_digest,
                    "start_line": start_line,
                    "end_line": end_line,
                    "description": opt["description"],
                    "content_digest": content_digest,
                    "default_value": None,
                }
            )

            if opt["default"] is not None:
                default_results.append(
                    {
                        "option_name": opt["option_name"],
                        "default_value": opt["default"],
                        "source_file": rel_path_for_digest,
                        "start_line": start_line,
                        "end_line": end_line,
                        "description": opt["description"],
                        "content_digest": content_digest,
                    }
                )

    return option_results, default_results


def _read_rst_file(rst_rel_path: str, rst_root: str) -> tuple[str | None, str]:
    """Read an RST file, trying multiple path variants.

    graphify-out emits source_file in three formats:
    1. Absolute path (``/Users/.../foo.rst``)
    2. Relative with docs/ prefix (``docs/docsite/.../foo.rst``)
    3. Relative without docs/ prefix (``docsite/.../foo.rst``)

    Returns ``(text, rel_path_used)``. The ``rel_path_used`` is the
    relative path from rst_root that was actually read (for digest computation).
    Returns ``(None, "")`` if no path variant could be opened.
    """

    # Candidate relative paths to try
    candidates: list[str] = []
    if os.path.isabs(rst_rel_path):
        # Absolute path — convert to relative if possible
        try:
            rel = os.path.relpath(rst_rel_path, rst_root)
            candidates.append(rel)
        except ValueError:
            pass
        candidates.append(rst_rel_path)
    else:
        candidates.append(rst_rel_path)
        # Try with docs/ prefix if not already there
        if not rst_rel_path.startswith("docs/"):
            candidates.append(f"docs/{rst_rel_path}")

    for candidate in candidates:
        full_path = os.path.join(rst_root, candidate)
        if os.path.isfile(full_path):
            with open(full_path) as f:
                return f.read(), candidate

    return None, ""


def _content_digest_for_span(rel_path: str, start: int, end: int) -> str:
    """Decision F: sha256 of ``<rel_path>:<start>:<end>``."""
    raw = f"{rel_path}:{start}:{end}"
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Precedence extraction (issue #106)
# ---------------------------------------------------------------------------

#: Precedence-related path fragments to scan for.
#:
#: Ansible-specific (hardcoded for ansible-documentation repo, issue #106):
#: these are the two known .rst files where precedence rules live —
#: ``general_precedence.rst`` documents the four precedence categories
#:   (config / CLI / playbook keywords / variables) and how they override
#:   each other. Located at ``docs/docsite/rst/reference_appendices/``.
#: ``playbooks_variables`` (``playbooks_variables.rst``) contains the
#:   21-step variable precedence list (role defaults → extra vars).
#:   Located at ``docs/docsite/rst/playbook_guide/``.
#:
#: If ansible-documentation adds other precedence .rst files in the future,
#: they will still be caught by the content-scan fallback below
#: (:func:`scan_rst_for_precedence`). But to avoid drift if filenames
#: change, this set should be updated whenever the docs are reorganized.
_PRECEDENCE_PATH_FRAGMENTS: frozenset[str] = frozenset(
    {
        "general_precedence",
        "playbooks_variables",
    }
)

#: RST heading underline characters in order of precedence.
_HEADING_CHARS = "=-^~"


def _detect_heading_level(line: str) -> int | None:
    """Return the heading level (0=lowest) based on the underline character, or None."""
    stripped = line.rstrip()
    if not stripped:
        return None
    chars = set(stripped)
    if len(chars) != 1:
        return None
    char = stripped[0]
    if char not in _HEADING_CHARS:
        return None
    return _HEADING_CHARS.index(char)


def _extract_precedence_sections(
    rst_text: str,
    source_path: str = "",
) -> list[dict[str, Any]]:
    """Extract precedence-related sections from RST text.

    Identifies sections whose titles contain "precedence" (case-insensitive)
    and returns their content as structured records.

    Returns a list of dicts::

        {
            "start_line": int,            # 1-based line of the section title
            "end_line": int,              # 1-based end line of the section body
            "section_title": str,         # the section heading text
            "body_lines": list[str],      # body text lines
            "is_precedence_doc": bool,    # True if whole doc is about precedence
        }
    """
    results: list[dict[str, Any]] = []
    lines = rst_text.splitlines()

    # First pass: detect all headings with levels.
    headings: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        # Check if the next line is a heading underline.
        if i + 1 < len(lines):
            next_line = lines[i + 1]
            level = _detect_heading_level(next_line)
            if level is not None:
                headings.append(
                    {
                        "title": stripped,
                        "line": i + 1,  # 1-based
                        "level": level,
                    }
                )
                continue  # skip the underline line

    if not headings:
        return results

    # Check if the document as a whole is about precedence (first heading).
    doc_is_about_precedence = False
    if headings:
        first_title = headings[0]["title"].lower()
        doc_is_about_precedence = "precedence" in first_title

    for i, heading in enumerate(headings):
        title_lower = heading["title"].lower()
        if "precedence" not in title_lower:
            continue

        start_line = heading["line"]
        heading_level = heading["level"]

        # Determine end_line: next heading at same or higher level, or EOF.
        end_line = len(lines) + 1
        for j in range(i + 1, len(headings)):
            if headings[j]["level"] <= heading_level:
                end_line = headings[j]["line"]
                break

        # Collect body lines (between title and end).
        body_lines: list[str] = []
        for j in range(start_line, end_line - 1):
            raw_line = lines[j]
            stripped_line = raw_line.strip()
            if stripped_line:
                body_lines.append(stripped_line)

        results.append(
            {
                "start_line": start_line,
                "end_line": end_line,
                "section_title": heading["title"],
                "body_lines": body_lines,
                "is_precedence_doc": doc_is_about_precedence,
            }
        )

    # If the document is generally about precedence but no specific section
    # heading contains "precedence", still capture the whole doc as one
    # precedence relation.
    if not results and doc_is_about_precedence:
        results.append(
            {
                "start_line": headings[0]["line"],
                "end_line": len(lines) + 1,
                "section_title": headings[0]["title"],
                "body_lines": [line.strip() for line in lines if line.strip()],
                "is_precedence_doc": True,
            }
        )

    return results


def extract_precedence_relations(
    rst_rel_path: str,
    rst_root: str,
) -> list[dict[str, Any]]:
    """Extract ``documents_precedence`` relations from an RST file.

    Uses content scanning (方案 A from issue #106): reads the RST file at
    ``rst_root / rst_rel_path`` and extracts precedence-related sections.

    Each result dict has::

        {
            "source_file": str,
            "start_line": int,
            "end_line": int,
            "section_title": str,
            "description": str,
            "content_digest": str,
        }
    """
    rst_text, used_path = _read_rst_file(rst_rel_path, rst_root)
    if rst_text is None:
        return []

    results: list[dict[str, Any]] = []
    sections = _extract_precedence_sections(rst_text, used_path)
    for section in sections:
        start_line = section["start_line"]
        end_line = section["end_line"]
        description = " ".join(section["body_lines"][:10])
        content_digest = _content_digest_for_span(used_path, start_line, end_line)

        results.append(
            {
                "source_file": used_path,
                "start_line": start_line,
                "end_line": end_line,
                "section_title": section["section_title"],
                "description": description[:500],
                "content_digest": content_digest,
            }
        )

    return results


def scan_rst_for_precedence(rst_root: str) -> list[str]:
    """Scan ``rst_root`` for RST files containing precedence-related content.

    方案 A (issue #106): directly traverse the docs directory tree looking
    for files whose path contains precedence-related fragments.

    Two-pass discovery (Ansible-specific, see :data:`_PRECEDENCE_PATH_FRAGMENTS`):

    1. **Path fragment match** — fast, deterministic. Catches the two known
       precedence .rst files (``general_precedence.rst``,
       ``playbooks_variables.rst``) by filename.
    2. **Content scan fallback** — reads the first 50 lines of every other
       .rst file and checks for the substring "precedence". Catches future
       / reorganized precedence docs that don't match the path fragments.

    The 50-line limit is intentional: precedence docs always announce
    themselves in the title or intro paragraph (well within 50 lines), so
    reading the whole file would be wasted I/O on the long tail of
    non-precedence .rst files in the docs tree.
    """
    precedence_files: list[str] = []
    docs_dir = os.path.join(rst_root, "docs")
    if not os.path.isdir(docs_dir):
        return precedence_files

    for dirpath, _dirnames, filenames in os.walk(docs_dir):
        for fname in filenames:
            if not fname.endswith(".rst"):
                continue
            rel_path = os.path.relpath(os.path.join(dirpath, fname), rst_root)
            # Pass 1: path-fragment match (Ansible-specific known files).
            for fragment in _PRECEDENCE_PATH_FRAGMENTS:
                if fragment in fname:
                    precedence_files.append(rel_path)
                    break
            else:
                # Pass 2: content-scan fallback for unknown precedence docs.
                full_path = os.path.join(dirpath, fname)
                try:
                    with open(full_path) as f:
                        head = "".join(f.readline() for _ in range(50))
                        if "precedence" in head.lower():
                            precedence_files.append(rel_path)
                except OSError:
                    continue

    return precedence_files
