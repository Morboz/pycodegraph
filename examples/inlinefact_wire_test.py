"""Wire-test for the InlineFact pipeline (issue #114).

Manually constructs an InlineFact list, passes it through
CodeGraph.build_semantic_layer(inline_facts=...), and verifies the flushed
SemanticRelation can be read back via read_relations.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from pycodegraph import CodeGraph
from pycodegraph.semantic.store import read_relations
from pycodegraph.semantic.types import RelationKind
from pycodegraph.types import InlineFact

SAMPLE_PYTHON = """\
def process_request(payload: dict, strict: bool = False) -> dict:
    if strict:
        return {"valid": True, **payload}
    return payload
"""


def write(root: str, rel_path: str, content: str) -> None:
    full = Path(root) / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        write(td, "sample.py", SAMPLE_PYTHON)
        cg = CodeGraph.init(td)
        cg.index_all()

        # Simulate InlineFact production by manually constructing facts.
        # In real usage these would come from a LanguageExtractor.extract_inline_facts
        # hook during traversal.
        # subject_node_id is the CodeGraph Node.id for `process_request`.
        nodes = cg.search("process_request", limit=5)
        process_node = next(
            (n for n in nodes if n.qualified_name == "process_request"), None
        )
        assert process_node is not None, "expected to find process_request node"
        facts = [
            InlineFact(
                relation_kind=RelationKind.STORES_DEFAULT.value,
                subject_node_id=process_node.id,
                subject_qualified_name="process_request",
                subject_file_path="sample.py",
                object_literal="False",
                object_node_id=None,
                start_line=1,
                end_line=1,
                evidence_kind="source",
                extraction_method="parser",
                metadata={"parameter_name": "strict"},
            )
        ]

        # Wire #1: build_semantic_layer without explicit inline_facts
        # uses cached _last_inline_facts from index_all (empty here since
        # no extract_inline_facts hook is registered).
        print("=== Wire #1: build_semantic_layer with no hook ===")
        result = cg.build_semantic_layer(
            repository_id="demo/repo",
            revision_value="abc123",
            built_at=1700000000,
        )
        print(f"  build_id: {result.build_id}")
        print(f"  relations_emitted (no inline_facts): {result.relations_emitted}")

        # Wire #2: build_semantic_layer with explicit inline_facts list
        print()
        print("=== Wire #2: build_semantic_layer with inline_facts ===")
        result = cg.build_semantic_layer(
            repository_id="demo/repo",
            revision_value="abc123",
            built_at=1700000001,
            inline_facts=facts,
        )
        print(f"  relations_emitted: {result.relations_emitted}")

        # Verify the STORES_DEFAULT relation was flushed.
        conn = cg._queries.connection
        reloaded = read_relations(conn, relation_kind=RelationKind.STORES_DEFAULT)
        assert len(reloaded) >= 1, "expected at least 1 STORES_DEFAULT relation"
        rel = reloaded[0]
        print("  read back STORES_DEFAULT:")
        print(f"    relation_id:       {rel.relation_id}")
        print(f"    subject_entity_id: {rel.subject_entity_id}")
        print(
            f"    object:            literal={rel.literal_object}, entity={rel.object_entity_id}"
        )
        print(f"    evidence_refs:     {len(rel.evidence_refs)}")
        ev = rel.evidence_refs[0]
        print(f"      evidence_kind:   {ev.evidence_kind}")
        print(f"      locator path:    {ev.locator.path_or_document_id}")
        print(f"      locator lines:   {ev.locator.start_line}-{ev.locator.end_line}")
        print(f"      symbol:          {ev.locator.symbol_or_section}")

        # Wire #3: explicit inline_facts=None uses cached _last_inline_facts.
        # Since the real index_all didn't produce any inline_facts (no hook
        # registered), this should flush 0 additional inline relations.
        print()
        print("=== Wire #3: inline_facts=None falls back to cache ===")
        result3 = cg.build_semantic_layer(
            repository_id="demo/repo",
            revision_value="abc123",
            built_at=1700000003,
            inline_facts=None,
        )
        print(f"  relations_emitted: {result3.relations_emitted}")
        # The default 4 extractors (CALLS, OWNS_CONTROL, EXPOSES_PUBLIC_SURFACE)
        # plus STORES_DEFAULT from the explicit inline_facts in Wire #2 —
        # stored with a different dataset_id, so they accumulate.
        reloaded3 = read_relations(conn, relation_kind=RelationKind.STORES_DEFAULT)
        print(f"  STORES_DEFAULT count after Wire #3: {len(reloaded3)}")

        # Wire #4: rebuild with no inline_facts at all (neither explicit nor cached)
        # to verify the pipeline doesn't crash when both are empty.
        print()
        print("=== Wire #4: no inline_facts at all ===")
        cg._last_inline_facts = []
        result4 = cg.build_semantic_layer(
            repository_id="demo/repo",
            revision_value="abc123",
            built_at=1700000004,
            inline_facts=[],
        )
        print(f"  relations_emitted: {result4.relations_emitted}")

        cg.close()

        cg.close()

        print()
        print("=" * 60)
        print("  InlineFact pipeline wire-test PASSED")
        print("=" * 60)


if __name__ == "__main__":
    main()
