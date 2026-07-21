"""Tests for the Python extract_inline_facts hook (issue #115 STORES_DEFAULT)
and the READS_DEFAULT extractor (issue #116).

Verifies that inline_facts are produced during Tree-sitter traversal for
Python function parameters with default values, and that they flush to
SemanticRelation rows via the InlineFact pipeline. Also verifies that
READS_DEFAULT extractor matches call sites to callee defaults.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from pycodegraph import CodeGraph
from pycodegraph.semantic.store import read_relations
from pycodegraph.semantic.types import RelationKind
from pycodegraph.types import InlineFact


def write(root: str, rel_path: str, content: str) -> None:
    full = Path(root) / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


# =============================================================================
# Unit-style tests: verify InlineFact production
# =============================================================================


def _extract_facts(source: str) -> list[InlineFact]:
    """Helper: index a file and return its inline_facts."""
    with tempfile.TemporaryDirectory() as td:
        write(td, "mod.py", source)
        cg = CodeGraph.init(td)
        result = cg.index_all()
        cg.close()
    return result.inline_facts


def _assert_fact(
    fact: InlineFact,
    *,
    param_name: str,
    object_literal: str,
    qname: str,
) -> None:
    assert fact.relation_kind == "stores_default", (
        f"expected stores_default, got {fact.relation_kind}"
    )
    assert fact.object_literal == object_literal, (
        f"expected object_literal={object_literal!r}, got {fact.object_literal!r}"
    )
    assert fact.subject_qualified_name == qname, (
        f"expected subject_qualified_name={qname!r}, got {fact.subject_qualified_name!r}"
    )
    assert fact.metadata.get("parameter_name") == param_name, (
        f"expected parameter_name={param_name!r}, got {fact.metadata.get('parameter_name')!r}"
    )
    assert fact.evidence_kind == "source"
    assert fact.subject_node_id is not None, "expected subject_node_id to be set"


class TestPythonInlineFacts:
    def test_typed_default_parameter(self):
        """def f(x: int = 5) → STORES_DEFAULT(f, "5", parameter_name="x")"""
        facts = _extract_facts("def f(x: int = 5): pass\n")
        assert len(facts) >= 1
        _assert_fact(
            facts[0],
            param_name="x",
            object_literal="5",
            qname="f",
        )

    def test_untyped_default(self):
        """def f(x=10) → STORES_DEFAULT(f, "10")"""
        facts = _extract_facts("def f(x=10): pass\n")
        assert len(facts) >= 1
        _assert_fact(facts[0], param_name="x", object_literal="10", qname="f")

    def test_no_default_typed_param(self):
        """def f(x: int) → no STORES_DEFAULT (no value child)"""
        facts = _extract_facts("def f(x: int): pass\n")
        stores = [f for f in facts if f.relation_kind == "stores_default"]
        assert len(stores) == 0

    def test_no_default_untyped_param(self):
        """def f(x) → no STORES_DEFAULT"""
        facts = _extract_facts("def f(x): pass\n")
        stores = [f for f in facts if f.relation_kind == "stores_default"]
        assert len(stores) == 0

    def test_multiple_defaults(self):
        """def f(a: int = 1, b: str = 'hello') → 2 facts"""
        facts = _extract_facts("def f(a: int = 1, b: str = 'hello'): pass\n")
        stores = sorted(
            [f for f in facts if f.relation_kind == "stores_default"],
            key=lambda f: f.metadata.get("parameter_name", ""),
        )
        assert len(stores) == 2
        _assert_fact(stores[0], param_name="a", object_literal="1", qname="f")
        _assert_fact(stores[1], param_name="b", object_literal="'hello'", qname="f")

    def test_class_method_default(self):
        """Method inside class → qualified_name with class prefix, kind=method"""
        facts = _extract_facts("class Foo:\n    def bar(self, x: int = 42): pass\n")
        stores = [f for f in facts if f.relation_kind == "stores_default"]
        assert len(stores) == 1
        f = stores[0]
        assert f.subject_qualified_name == "Foo::bar", (
            f"expected Foo::bar, got {f.subject_qualified_name!r}"
        )
        assert f.object_literal == "42"

    def test_mixed_params(self):
        """def f(x: int, y: str = 'yes', z): → only 1 STORES_DEFAULT (y)"""
        facts = _extract_facts("def f(x: int, y: str = 'yes', z): pass\n")
        stores = [f for f in facts if f.relation_kind == "stores_default"]
        assert len(stores) == 1
        _assert_fact(stores[0], param_name="y", object_literal="'yes'", qname="f")

    def test_decorated_method_default(self):
        """@classmethod def method(cls, x: bool = True) → method qname"""
        facts = _extract_facts(
            "class Bar:\n    @classmethod\n    def create(cls, x: bool = True): pass\n"
        )
        stores = [f for f in facts if f.relation_kind == "stores_default"]
        assert len(stores) >= 1, "expected at least 1 store for decorated method"
        f = stores[0]
        assert f.subject_qualified_name == "Bar::create", (
            f"expected Bar::create, got {f.subject_qualified_name!r}"
        )
        assert f.object_literal == "True"

    def test_empty_source(self):
        """Empty file → no inline_facts at all."""
        facts = _extract_facts("\n")
        stores = [f for f in facts if f.relation_kind == "stores_default"]
        assert len(stores) == 0

    def test_non_python_ignored(self):
        """Non-Python file → no inline facts."""
        # This test uses a .txt file that won't be parsed by TreeSitterExtractor
        with tempfile.TemporaryDirectory() as td:
            write(td, "readme.txt", "Hello, world!\n")
            cg = CodeGraph.init(td)
            result = cg.index_all()
            cg.close()
        stores = [f for f in result.inline_facts if f.relation_kind == "stores_default"]
        assert len(stores) == 0


# =============================================================================
# Integration test: InlineFact → SemanticRelation pipeline
# =============================================================================


class TestStoresDefaultEndToEnd:
    """Verifies the full pipeline: extraction → flush → read back."""

    SRC = (
        "def run(x: int = 5) -> None:\n"
        "    return None\n"
        "\n"
        "def configure(timeout: int = 30, debug: bool = False) -> None:\n"
        "    pass\n"
    )

    def test_flush_and_read_back(self):
        """index_all + build_semantic_layer → read_relations(STORES_DEFAULT)"""
        with tempfile.TemporaryDirectory() as td:
            write(td, "mod.py", self.SRC)
            cg = CodeGraph.init(td)
            cg.index_all()
            cg.build_semantic_layer(
                repository_id="test/repo",
                revision_value="abc123",
                built_at=1700000000,
            )
            conn = cg._queries.connection
            rels = read_relations(conn, relation_kind=RelationKind.STORES_DEFAULT)
            # Should have 3 STORES_DEFAULT: x=5, timeout=30, debug=False
            assert len(rels) >= 3, (
                f"expected >=3 STORES_DEFAULT relations, got {len(rels)}"
            )
            # Verify each has evidence_refs
            for rel in rels:
                assert len(rel.evidence_refs) >= 1
                ev = rel.evidence_refs[0]
                assert ev.evidence_kind.value == "source"
            cg.close()

    def test_cache_fallback(self):
        """index_all → build_semantic_layer (no inline_facts kwarg) → cached facts used."""
        with tempfile.TemporaryDirectory() as td:
            write(td, "mod.py", self.SRC)
            cg = CodeGraph.init(td)
            cg.index_all()
            # No explicit inline_facts — uses _last_inline_facts cache
            cg.build_semantic_layer(
                repository_id="test/repo",
                revision_value="abc123",
                built_at=1700000000,
                inline_facts=None,
            )
            conn = cg._queries.connection
            rels = read_relations(conn, relation_kind=RelationKind.STORES_DEFAULT)
            assert len(rels) >= 1
            cg.close()

    def test_nonmodule_source(self):
        """Source outside a real module dir should still work with `()` source."""
        with tempfile.TemporaryDirectory() as td:
            write(td, "mod.py", "def empty(): pass\n")
            cg = CodeGraph.init(td)
            result = cg.index_all()
            stores = [
                f for f in result.inline_facts if f.relation_kind == "stores_default"
            ]
            assert len(stores) == 0, "function without defaults -> 0 facts"
            cg.close()


def _reads_build(callee_src: str, caller_src: str) -> CodeGraph:
    """Build a CodeGraph with the given callee (helpers.py) and caller (main.py)
    source and run build_semantic_layer.

    Two-file pattern with ``import helpers; helpers.f()`` is used because
    ``from helpers import f; f()`` doesn't resolve a CALLS edge in the
    resolver (it produces only an IMPORTS edge). The dotted call form
    resolves cleanly.

    Returns an open CodeGraph with READS_DEFAULT relations in the DB.
    Caller must close() when done.
    """
    td = tempfile.mkdtemp()
    write(td, "helpers.py", callee_src)
    write(td, "main.py", caller_src)
    cg = CodeGraph.init(td)
    cg.index_all()
    cg.build_semantic_layer(
        repository_id="test/repo",
        revision_value="abc123",
        built_at=1700000000,
    )
    return cg


class TestReadsDefault:
    """READS_DEFAULT extractor -- call site uses callee's parameter default."""

    # Standard callee source used by most tests
    _CALLEE = "def f(x=5): pass\n"

    def test_basic_default_used(self):
        """def f(x=5); f() -> 1 READS_DEFAULT (x=5 used at call)"""
        cg = _reads_build(self._CALLEE, "import helpers\ndef g():\n    helpers.f()\n")
        conn = cg._queries.connection
        rels = read_relations(conn, relation_kind=RelationKind.READS_DEFAULT)
        assert len(rels) >= 1, f"expected >=1 READS_DEFAULT, got {len(rels)}"
        r = rels[0]
        assert r.literal_object == "5", (
            f"expected literal_object=5, got {r.literal_object!r}"
        )
        cg.close()

    def test_arg_explicitly_passed(self):
        """def f(x=5); f(10) -> 0 READS_DEFAULT (arg explicitly passed)"""
        cg = _reads_build(self._CALLEE, "import helpers\ndef g():\n    helpers.f(10)\n")
        conn = cg._queries.connection
        rels = read_relations(conn, relation_kind=RelationKind.READS_DEFAULT)
        assert len(rels) == 0, f"expected 0 READS_DEFAULT, got {len(rels)}"
        cg.close()

    def test_keyword_arg(self):
        """def f(x=5); f(x=10) -> 0 READS_DEFAULT (keyword explicit)"""
        cg = _reads_build(
            self._CALLEE, "import helpers\ndef g():\n    helpers.f(x=10)\n"
        )
        conn = cg._queries.connection
        rels = read_relations(conn, relation_kind=RelationKind.READS_DEFAULT)
        assert len(rels) == 0
        cg.close()

    def test_two_defaults_both_used(self):
        """def f(x=5, y=10); f() -> 2 READS_DEFAULT"""
        callee = "def f(x=5, y=10): pass\n"
        cg = _reads_build(callee, "import helpers\ndef g():\n    helpers.f()\n")
        conn = cg._queries.connection
        rels = read_relations(conn, relation_kind=RelationKind.READS_DEFAULT)
        assert len(rels) >= 2, f"expected >=2 READS_DEFAULT, got {len(rels)}"
        values = {r.literal_object for r in rels}
        assert "5" in values, f"expected 5 in values, got {values}"
        assert "10" in values, f"expected 10 in values, got {values}"
        cg.close()

    def test_two_defaults_one_partial(self):
        """def f(x=5, y=10); f(1) -> 1 READS_DEFAULT (only y uses default)"""
        callee = "def f(x=5, y=10): pass\n"
        cg = _reads_build(callee, "import helpers\ndef g():\n    helpers.f(1)\n")
        conn = cg._queries.connection
        rels = read_relations(conn, relation_kind=RelationKind.READS_DEFAULT)
        assert len(rels) == 1, f"expected 1 READS_DEFAULT, got {len(rels)}"
        assert rels[0].literal_object == "10", (
            f"expected 10, got {rels[0].literal_object!r}"
        )
        cg.close()

    def test_required_param_first_default_second(self):
        """def f(x, y=10); f(1) -> 1 READS_DEFAULT (y uses default)"""
        callee = "def f(x, y=10): pass\n"
        cg = _reads_build(callee, "import helpers\ndef g():\n    helpers.f(1)\n")
        conn = cg._queries.connection
        rels = read_relations(conn, relation_kind=RelationKind.READS_DEFAULT)
        assert len(rels) == 1
        assert rels[0].literal_object == "10"
        cg.close()

    def test_callee_no_defaults(self):
        """def f(x, y); f(1, 2) -> 0 READS_DEFAULT (no defaults at all)"""
        callee = "def f(x, y): pass\n"
        cg = _reads_build(callee, "import helpers\ndef g():\n    helpers.f(1, 2)\n")
        conn = cg._queries.connection
        rels = read_relations(conn, relation_kind=RelationKind.READS_DEFAULT)
        assert len(rels) == 0
        cg.close()

    def test_subject_uses_line_suffix(self):
        """READS_DEFAULT subject_entity_id ends with ::L{line}"""
        cg = _reads_build(self._CALLEE, "import helpers\ndef g():\n    helpers.f()\n")
        conn = cg._queries.connection
        rels = read_relations(conn, relation_kind=RelationKind.READS_DEFAULT)
        assert len(rels) >= 1
        sid = rels[0].subject_entity_id
        assert "::L" in sid, f"expected '::L' in subject, got {sid!r}"
        assert sid.startswith("g::L"), f"expected g::L prefix, got {sid!r}"
        cg.close()

    def test_condition_expression_has_param_name(self):
        """READS_DEFAULT condition_expression contains parameter_name"""
        cg = _reads_build(self._CALLEE, "import helpers\ndef g():\n    helpers.f()\n")
        conn = cg._queries.connection
        rels = read_relations(conn, relation_kind=RelationKind.READS_DEFAULT)
        assert len(rels) >= 1
        ce = rels[0].condition_expression
        assert ce is not None, "expected condition_expression"
        assert ce.get("parameter_name") == "x", f"expected parameter_name=x, got {ce}"
        cg.close()
