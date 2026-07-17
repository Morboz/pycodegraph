"""Tests for ``_rst_extractor.py`` — RST + YAML option/default extraction.

Uses synthetic RST snippets (5-10 lines) to verify each extraction path in
isolation, with ``tmp_path`` as the "rst_root".
"""

from __future__ import annotations

from pathlib import Path

from pycodegraph.semantic.adapters.graphify._rst_extractor import (
    _content_digest_for_span,
    _extract_admonition_sections,
    _extract_option_sections,
    _extract_yaml_documentation_block,
    _parse_documentation_yaml,
    extract_behavior_and_safety_relations,
    extract_option_and_default_relations,
)

# =============================================================================
# RST ``.. option::`` directive extraction
# =============================================================================


class TestExtractOptionSections:
    def test_simple_option_directive(self):
        rst = """\
.. option:: ANSIBLE_PERSISTENT_COMMAND_TIMEOUT

   Command timeout setting (default 30s).
"""
        results = _extract_option_sections(rst, "test.rst")
        assert len(results) == 1
        assert results[0]["option_name"] == "ANSIBLE_PERSISTENT_COMMAND_TIMEOUT"
        assert results[0]["directive_line"] == 1

    def test_option_with_body(self):
        rst = """\
.. option:: ANSIBLE_PERSISTENT_CONNECT_TIMEOUT

   Persistent connection idle timeout.

   Set this to control timeout behavior.
"""
        results = _extract_option_sections(rst, "test.rst")
        assert len(results) == 1
        assert results[0]["option_name"] == "ANSIBLE_PERSISTENT_CONNECT_TIMEOUT"
        assert len(results[0]["body_lines"]) >= 1

    def test_no_option_directive(self):
        rst = """\
Some regular RST text.

.. note:: A note.

Another paragraph.
"""
        results = _extract_option_sections(rst, "test.rst")
        assert len(results) == 0

    def test_multiple_option_directives(self):
        rst = """\
.. option:: option-one

   Body one.

.. option:: option-two

   Body two.
"""
        results = _extract_option_sections(rst, "test.rst")
        assert len(results) == 2
        names = [r["option_name"] for r in results]
        assert "option-one" in names
        assert "option-two" in names

    def test_option_line_numbers(self):
        rst = """\
Header

.. option:: my-option

   Body text.
"""
        results = _extract_option_sections(rst, "test.rst")
        assert len(results) == 1
        assert results[0]["directive_line"] == 3  # 1-based


# =============================================================================
# YAML DOCUMENTATION block extraction
# =============================================================================


class TestExtractYamlDocumentationBlock:
    def test_documentation_triple_quotes(self):
        rst = """\
DOCUMENTATION = r'''
module: my_test
options:
  name:
    description: The name.
    type: str
'''
"""
        block = _extract_yaml_documentation_block(rst)
        assert block is not None
        assert "module: my_test" in block
        assert "options:" in block

    def test_documentation_double_triple_quotes(self):
        rst = 'DOCUMENTATION = """\nmodule: my_test\noptions: {}\n"""\n'
        block = _extract_yaml_documentation_block(rst)
        assert block is not None
        assert "module: my_test" in block

    def test_no_documentation_block(self):
        rst = "Just a plain text file.\n"
        block = _extract_yaml_documentation_block(rst)
        assert block is None

    def test_documentation_with_multiline(self):
        rst = """\
DOCUMENTATION = r'''
module: my_module
short_description: Test module
description:
  - A test module.
options:
  option1:
    description: First option.
    type: str
    default: hello
'''
"""
        block = _extract_yaml_documentation_block(rst)
        assert block is not None
        assert "option1" in block


# =============================================================================
# YAML options parsing
# =============================================================================


class TestParseDocumentationYaml:
    def test_parse_options_with_default(self):
        yaml = """\
module: my_module
options:
  name:
    description: The name parameter.
    type: str
    default: alice
  timeout:
    description: Timeout in seconds.
    type: int
    default: 30
"""
        results = _parse_documentation_yaml(yaml, line_offset=0)
        assert len(results) == 2
        assert results[0]["option_name"] == "name"
        assert results[0]["default"] == "alice"
        assert results[1]["option_name"] == "timeout"
        assert results[1]["default"] == "30"

    def test_parse_options_without_default(self):
        yaml = """\
module: my_module
options:
  name:
    description: The name parameter.
    type: str
  timeout:
    description: Timeout.
    type: int
"""
        results = _parse_documentation_yaml(yaml, line_offset=0)
        assert len(results) == 2
        assert results[0]["default"] is None
        assert results[1]["default"] is None

    def test_parse_options_empty(self):
        yaml = "module: my_module\noptions: {}\n"
        results = _parse_documentation_yaml(yaml, line_offset=0)
        assert len(results) == 0

    def test_no_options_section(self):
        yaml = "module: my_module\nshort_description: test\n"
        results = _parse_documentation_yaml(yaml, line_offset=0)
        assert len(results) == 0

    def test_line_offset_applied(self):
        yaml = """\
options:
  opt1:
    description: Option 1
    default: val1
"""
        results = _parse_documentation_yaml(yaml, line_offset=10)
        assert len(results) == 1
        assert (
            results[0]["doc_line"] == 12
        )  # line_offset=10 + 1-based indexing of line 2  # 10 + 1 (options: is line 1)


# =============================================================================
# Content digest
# =============================================================================


class TestContentDigestForSpan:
    def test_digest_format(self):
        digest = _content_digest_for_span("path/to/file.rst", 1, 10)
        assert digest.startswith("sha256:")
        assert len(digest) == len("sha256:") + 16

    def test_digest_deterministic(self):
        d1 = _content_digest_for_span("file.rst", 5, 15)
        d2 = _content_digest_for_span("file.rst", 5, 15)
        assert d1 == d2

    def test_digest_differs_for_different_path(self):
        d1 = _content_digest_for_span("a.rst", 5, 15)
        d2 = _content_digest_for_span("b.rst", 5, 15)
        assert d1 != d2


# =============================================================================
# End-to-end extraction with synthetic RST files
# =============================================================================


class TestExtractOptionAndDefaultRelations:
    def test_extract_from_rst_option_directive(self, tmp_path: Path):
        """RST with ``.. option::`` directive should produce option relations."""
        rst_dir = tmp_path / "docs"
        rst_dir.mkdir()
        rst_file = rst_dir / "test.rst"
        rst_file.write_text(
            """\
.. option:: my-cool-option

   A cool option for testing.
"""
        )
        rel_path = "docs/test.rst"
        option_results, default_results = extract_option_and_default_relations(
            str(rel_path), str(tmp_path)
        )
        assert len(option_results) >= 1
        names = [o["option_name"] for o in option_results]
        assert "my-cool-option" in names
        assert len(default_results) == 0

    def test_extract_from_yaml_documentation_block(self, tmp_path: Path):
        """RST with a YAML DOCUMENTATION block should produce option+default."""
        rst_dir = tmp_path / "docs"
        rst_dir.mkdir()
        rst_file = rst_dir / "my_module.rst"
        rst_file.write_text(
            """\
DOCUMENTATION = r'''
module: my_module
options:
  username:
    description: The username.
    type: str
    default: admin
  timeout:
    description: Timeout value.
    type: int
'''
"""
        )
        rel_path = "docs/my_module.rst"
        option_results, default_results = extract_option_and_default_relations(
            str(rel_path), str(tmp_path)
        )
        assert len(option_results) >= 2  # username + timeout
        assert len(default_results) >= 1  # only username has default
        default_names = [d["option_name"] for d in default_results]
        assert "username" in default_names

    def test_extract_with_default_value(self, tmp_path: Path):
        """Default values should be captured in default_results."""
        rst_dir = tmp_path / "docs"
        rst_dir.mkdir()
        rst_file = rst_dir / "config.rst"
        rst_file.write_text(
            """\
DOCUMENTATION = r'''
module: config
options:
  retries:
    description: Number of retries.
    type: int
    default: 3
'''
"""
        )
        rel_path = "docs/config.rst"
        option_results, default_results = extract_option_and_default_relations(
            str(rel_path), str(tmp_path)
        )
        assert len(option_results) >= 1
        assert len(default_results) >= 1
        assert default_results[0]["default_value"] == "3"
        assert default_results[0]["option_name"] == "retries"

    def test_nonexistent_file_returns_empty(self, tmp_path: Path):
        """Non-existent RST file should return empty results."""
        option_results, default_results = extract_option_and_default_relations(
            "docs/nonexistent.rst", str(tmp_path)
        )
        assert len(option_results) == 0
        assert len(default_results) == 0

    def test_file_with_no_options_returns_empty(self, tmp_path: Path):
        """RST file with no options content should return empty."""
        rst_dir = tmp_path / "docs"
        rst_dir.mkdir()
        rst_file = rst_dir / "plain.rst"
        rst_file.write_text("Just a plain RST file.\n\n.. note:: Nothing here.\n")
        rel_path = "docs/plain.rst"
        option_results, default_results = extract_option_and_default_relations(
            str(rel_path), str(tmp_path)
        )
        assert len(option_results) == 0
        assert len(default_results) == 0

    def test_content_digest_in_results(self, tmp_path: Path):
        """Results should include valid content_digest values."""
        rst_dir = tmp_path / "docs"
        rst_dir.mkdir()
        rst_file = rst_dir / "digest.rst"
        rst_file.write_text(
            """\
DOCUMENTATION = r'''
module: digest
options:
  opt1:
    description: Test option.
    type: str
    default: val1
'''
"""
        )
        rel_path = "docs/digest.rst"
        option_results, default_results = extract_option_and_default_relations(
            str(rel_path), str(tmp_path)
        )
        for opt in option_results:
            assert opt["content_digest"].startswith("sha256:")
        for dft in default_results:
            assert dft["content_digest"].startswith("sha256:")


# =============================================================================
# Admonition extraction (Phase 2 — issue #102)
# =============================================================================


class TestExtractAdmonitionSections:
    def test_note_directive_is_behavior(self):
        rst = """\
.. note::

   This is a note about behavior.
"""
        results = _extract_admonition_sections(rst, "test.rst")
        assert len(results) == 1
        assert results[0]["admonition_kind"] == "note"
        assert results[0]["category"] == "behavior"
        assert results[0]["directive_line"] == 1

    def test_warning_directive_is_safety(self):
        rst = """\
.. warning::

   This is a warning about safety.
"""
        results = _extract_admonition_sections(rst, "test.rst")
        assert len(results) == 1
        assert results[0]["admonition_kind"] == "warning"
        assert results[0]["category"] == "safety"

    def test_danger_directive_is_safety(self):
        rst = """\
.. danger::

   This is dangerous.
"""
        results = _extract_admonition_sections(rst, "test.rst")
        assert len(results) == 1
        assert results[0]["admonition_kind"] == "danger"
        assert results[0]["category"] == "safety"

    def test_tip_and_important_are_behavior(self):
        rst = """\
.. tip::

   Use this trick.

.. important::

   Don't forget this.
"""
        results = _extract_admonition_sections(rst, "test.rst")
        assert len(results) == 2
        assert all(r["category"] == "behavior" for r in results)
        kinds = {r["admonition_kind"] for r in results}
        assert kinds == {"tip", "important"}

    def test_caution_is_safety(self):
        rst = """\
.. caution::

   Be careful.
"""
        results = _extract_admonition_sections(rst, "test.rst")
        assert len(results) == 1
        assert results[0]["admonition_kind"] == "caution"
        assert results[0]["category"] == "safety"

    def test_non_admonition_directive_ignored(self):
        rst = """\
.. option:: ANSIBLE_FOO

   Some option.

.. code-block:: bash

   echo hello
"""
        results = _extract_admonition_sections(rst, "test.rst")
        assert len(results) == 0

    def test_admonition_body_collected(self):
        rst = """\
.. note::

   First line of note.

   Second paragraph of note.
"""
        results = _extract_admonition_sections(rst, "test.rst")
        assert len(results) == 1
        assert len(results[0]["body_lines"]) >= 1
        assert "First line of note" in " ".join(results[0]["body_lines"])

    def test_multiple_admonitions(self):
        rst = """\
.. note::

   Note one.

.. warning::

   Warning one.

.. note::

   Note two.
"""
        results = _extract_admonition_sections(rst, "test.rst")
        assert len(results) == 3
        behavior_count = sum(1 for r in results if r["category"] == "behavior")
        safety_count = sum(1 for r in results if r["category"] == "safety")
        assert behavior_count == 2
        assert safety_count == 1


class TestExtractBehaviorAndSafetyRelations:
    def test_extract_from_note(self, tmp_path: Path):
        rst_dir = tmp_path / "docs"
        rst_dir.mkdir()
        rst_file = rst_dir / "test.rst"
        rst_file.write_text(
            """\
.. note::

   Important behavior note.
"""
        )
        rel_path = "docs/test.rst"
        behavior, safety = extract_behavior_and_safety_relations(
            str(rel_path), str(tmp_path)
        )
        assert len(behavior) == 1
        assert len(safety) == 0
        assert behavior[0]["admonition_kind"] == "note"
        assert behavior[0]["content_digest"].startswith("sha256:")

    def test_extract_from_warning(self, tmp_path: Path):
        rst_dir = tmp_path / "docs"
        rst_dir.mkdir()
        rst_file = rst_dir / "test.rst"
        rst_file.write_text(
            """\
.. warning::

   Safety warning.
"""
        )
        rel_path = "docs/test.rst"
        behavior, safety = extract_behavior_and_safety_relations(
            str(rel_path), str(tmp_path)
        )
        assert len(behavior) == 0
        assert len(safety) == 1
        assert safety[0]["admonition_kind"] == "warning"

    def test_extract_mixed_admonitions(self, tmp_path: Path):
        rst_dir = tmp_path / "docs"
        rst_dir.mkdir()
        rst_file = rst_dir / "test.rst"
        rst_file.write_text(
            """\
.. note::

   A note.

.. warning::

   A warning.

.. danger::

   A danger.
"""
        )
        rel_path = "docs/test.rst"
        behavior, safety = extract_behavior_and_safety_relations(
            str(rel_path), str(tmp_path)
        )
        assert len(behavior) == 1
        assert len(safety) == 2

    def test_nonexistent_file_returns_empty(self, tmp_path: Path):
        behavior, safety = extract_behavior_and_safety_relations(
            "docs/nonexistent.rst", str(tmp_path)
        )
        assert len(behavior) == 0
        assert len(safety) == 0

    def test_file_with_no_admonitions_returns_empty(self, tmp_path: Path):
        rst_dir = tmp_path / "docs"
        rst_dir.mkdir()
        rst_file = rst_dir / "plain.rst"
        rst_file.write_text("Just plain text.\n")
        rel_path = "docs/plain.rst"
        behavior, safety = extract_behavior_and_safety_relations(
            str(rel_path), str(tmp_path)
        )
        assert len(behavior) == 0
        assert len(safety) == 0

    def test_results_have_line_spans(self, tmp_path: Path):
        rst_dir = tmp_path / "docs"
        rst_dir.mkdir()
        rst_file = rst_dir / "test.rst"
        rst_file.write_text(
            """\
Header

.. note::

   Body text.
"""
        )
        rel_path = "docs/test.rst"
        behavior, _safety = extract_behavior_and_safety_relations(
            str(rel_path), str(tmp_path)
        )
        assert len(behavior) == 1
        assert behavior[0]["start_line"] == 3  # 1-based line of `.. note::`
        assert behavior[0]["end_line"] > behavior[0]["start_line"]
