"""
Demo: index ansible code on PG, build semantic layer,
then query FORWARDS_VALUE (intra + inter) and show results.

Usage:
  uv run python examples/forwards_value_demo.py
"""

from __future__ import annotations

import os

# ── DB config ──────────────────────────────────────────────────────────────
DB_URL = os.environ.get(
    "PG_URL",
    "postgresql+psycopg://admin:admin@127.0.0.1:5433/ai_fwd",
)
ANSIBLE_SRC = os.environ.get(
    "ANSIBLE_SRC",
    "/Users/xx/software/wanggen/ansible-code-and-docs/ansible",
)
DOC_GRAPHIFY_OUT = os.environ.get(
    "DOC_GRAPHIFY_OUT",
    "/Users/xx/software/wanggen/ansible-documentation/graphify-out",
)


def main():
    from pycodegraph import CodeGraph
    from pycodegraph.semantic.store import read_relations
    from pycodegraph.semantic.types import RelationKind

    print("=" * 60)
    print("FORWARDS_VALUE demo — Ansible code on PG")
    print("=" * 60)

    # ── 1. Open CodeGraph on PG ──────────────────────────────────────────
    print(f"\n[1] Opening CodeGraph on PG at {DB_URL}")
    print(f"    Source: {ANSIBLE_SRC}")
    # Ensure schema exists (open_from_url does not create tables on a fresh DB)
    from pycodegraph.db import DatabaseConnection

    DatabaseConnection.initialize(DB_URL)
    cg = CodeGraph.open_from_url(DB_URL, ANSIBLE_SRC)

    # ── 2. Index ansible code ────────────────────────────────────────────
    print("\n[2] Indexing ansible code (this may take a minute)...")
    index_result = cg.index_all()
    print(
        f"    Indexed {index_result.nodes_created:,} nodes, {index_result.edges_created:,} edges in {index_result.files_indexed:,} files"
    )

    # ── 3. Build semantic layer ──────────────────────────────────────────
    print("\n[3] Building semantic layer...")
    import time

    build_result = cg.build_semantic_layer(
        repository_id="ansible/ansible",
        revision_value="devel",
        built_at=int(time.time()),
    )
    print(f"    Build: {build_result.relations_emitted:,} relations emitted")
    print(f"    Extractors run: {build_result.extractors_run}")
    if build_result.errors:
        for err in build_result.errors[:5]:
            print(f"    ERROR: {err}")

    conn = cg._queries.connection

    # ── 4. Count all FORWARDS_VALUE ──────────────────────────────────────
    print("\n[4] FORWARDS_VALUE counts:")
    all_fv = read_relations(conn, relation_kind=RelationKind.FORWARDS_VALUE)
    print(f"    Total FORWARDS_VALUE relations: {len(all_fv):,}")

    # Split into intra (from InlineFact) and inter (from registered extractor)
    intra = [
        r
        for r in all_fv
        if r.condition_expression is None
        or (
            r.condition_expression.get("arg_type") == "positional"
            and "forwards_type" not in r.condition_expression
        )
    ]
    inter = [
        r
        for r in all_fv
        if r.condition_expression
        and r.condition_expression.get("forwards_type") == "inter"
    ]
    # Relations that don't fit either pattern (no condition_expression or other)
    other = len(all_fv) - len(inter)

    print(f"    Intra-procedural (#118, InlineFact): {len(intra):,}")
    print(f"    Inter-procedural (#120, registered extractor): {len(inter):,}")
    print(f"    Other: {other:,}")

    # ── 5. Show examples ─────────────────────────────────────────────────
    print("\n[5] Sample inter-procedural FORWARDS_VALUE (up to 10):")
    for r in sorted(inter, key=lambda x: x.subject_entity_id)[:10]:
        ce = r.condition_expression or {}
        obj = r.literal_object
        print(f"    {r.subject_entity_id}")
        print(f"      -> {obj}")
        print(
            f"      caller_param={ce.get('caller_param')}, callee_param={ce.get('callee_param')}"
        )

    # ── 6. Show unique forwarding chains ─────────────────────────────────
    print("\n[6] Unique caller→callee forwarding pairs (up to 15):")
    pairs: set[tuple[str, str]] = set()
    for r in inter:
        # Extract function names from subject
        subject = (
            r.subject_entity_id.split("::")[0]
            if "::" in r.subject_entity_id
            else r.subject_entity_id
        )
        obj_str = str(r.literal_object or "")
        callee_name = obj_str.split(".")[0] if "." in obj_str else obj_str
        pairs.add((subject, callee_name))
    for subject, callee in sorted(pairs)[:15]:
        print(f"    {subject} → {callee}")

    # ── 7. Overall relation report ───────────────────────────────────────
    print("\n[7] All 24 relation kind counts:")
    for rk in RelationKind:
        count = len(read_relations(conn, relation_kind=rk))
        if count > 0:
            print(f"    {rk.value:35s} {count:>6,}")

    cg.close()
    print(f"\n{'=' * 60}")
    print("Done.")


if __name__ == "__main__":
    main()
