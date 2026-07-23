"""端到端集成 demo:索引 ansible 代码 + 构建语义层 + 查询参数透传链。

展示外部程序如何集成 pycodegraph 的 value-flow propagation 能力:
  1. 一句话完成:建表 + 索引 + 构建语义层(含 FORWARDS_VALUE 关键字参数支持)
  2. BFS 多跳链查询:给定函数名 + 参数名,输出完整透传链
  3. 全量 relation 报告

Usage:
  # 全量建库 + 查询 unredirected_headers 透传链
  uv run python examples/forwards_value_demo.py

  # 跳过建库(库已存在),直接查链
  uv run python examples/forwards_value_demo.py --skip-build

  # 自定义链查询
  uv run python examples/forwards_value_demo.py --chain uri unredirected_headers

  # 只看 relation 统计,不查链
  uv run python examples/forwards_value_demo.py --no-chain
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import deque
from typing import Any

# ── DB config ──────────────────────────────────────────────────────────────
DB_URL = os.environ.get(
    "PG_URL",
    "postgresql+psycopg://admin:admin@172.18.120.106:5432/ai_fwd",
)
ANSIBLE_SRC = os.environ.get(
    "ANSIBLE_SRC",
    "/Users/xx/software/wanggen/ansible-code-and-docs/ansible",
)


# =============================================================================
# BFS 多跳链查询 (issue #122)
# =============================================================================


def query_forwards_chain(
    conn: Any,
    start_func: str,
    start_param: str,
    *,
    max_hops: int = 8,
) -> list[dict[str, Any]]:
    """BFS over inter-procedural FORWARDS_VALUE relations.

    给定起点 (函数名, 参数名),沿 FORWARDS_VALUE 边做多跳遍历,
    输出完整的参数透传链。

    拼接逻辑:
    - 起点 (start_func, start_param)
    - 找所有 subject 属于 start_func、caller_param == start_param 的 inter 边
    - 每条边的 object 给出 (callee_func, callee_param)
    - 把 (callee_func, callee_param) 作为下一跳起点,重复
    - 直到无匹配或达到 max_hops

    返回链的每一跳,每跳含 caller_func/caller_param/callee_func/callee_param/
    call_site/arg_type。
    """
    from pycodegraph.semantic.store import read_relations
    from pycodegraph.semantic.types import RelationKind

    # 读全部 inter FORWARDS_VALUE,按 caller_func + caller_param 建索引
    all_fv = read_relations(conn, relation_kind=RelationKind.FORWARDS_VALUE)
    # 索引:(caller_func, caller_param) -> [边...]
    out_edges: dict[tuple[str, str], list[Any]] = {}
    for r in all_fv:
        ce = r.condition_expression
        if not ce or ce.get("forwards_type") != "inter":
            continue
        caller_func = (
            r.subject_entity_id.split("::")[0]
            if "::" in r.subject_entity_id
            else r.subject_entity_id
        )
        caller_param = ce.get("caller_param")
        if not caller_param:
            continue
        out_edges.setdefault((caller_func, caller_param), []).append(r)

    # BFS
    chain: list[dict[str, Any]] = []
    visited: set[tuple[str, str]] = set()
    queue: deque[tuple[str, str, int]] = deque([(start_func, start_param, 0)])

    while queue:
        func, param, hop = queue.popleft()
        if hop >= max_hops:
            continue
        key = (func, param)
        if key in visited:
            continue
        visited.add(key)

        edges = out_edges.get(key, [])
        for edge in edges:
            ce = edge.condition_expression
            obj = str(edge.literal_object or "")
            # object 形如 "fetch_url.unredirected_headers"
            if "." in obj:
                callee_func, callee_param = obj.rsplit(".", 1)
            else:
                callee_func, callee_param = obj, ce.get("callee_param", "")
            call_site = edge.subject_entity_id
            arg_type = ce.get("arg_type", "?")
            chain.append(
                {
                    "hop": hop + 1,
                    "caller_func": func,
                    "caller_param": param,
                    "callee_func": callee_func,
                    "callee_param": callee_param,
                    "call_site": call_site,
                    "arg_type": arg_type,
                    "relation_id": edge.relation_id,
                }
            )
            next_key = (callee_func, callee_param)
            if next_key not in visited:
                queue.append((callee_func, callee_param, hop + 1))

    return chain


def print_chain(chain: list[dict[str, Any]], start_func: str, start_param: str) -> None:
    """格式化输出透传链。"""
    if not chain:
        print(f"    (no forwarding chain found for {start_func}.{start_param})")
        return
    print(f"    起点: {start_func}({start_param})")
    # 按 hop 分组
    by_hop: dict[int, list[dict[str, Any]]] = {}
    for h in chain:
        by_hop.setdefault(h["hop"], []).append(h)
    for hop in sorted(by_hop):
        for h in by_hop[hop]:
            print(
                f"    跳{hop}: {h['caller_func']}({h['caller_param']}) "
                f"--[{h['arg_type']}, {h['call_site']}]--> "
                f"{h['callee_func']}({h['callee_param']})"
            )


# =============================================================================
# 构建步骤
# =============================================================================


def build_codegraph(
    cg: Any, *, repo_id: str = "ansible/ansible", revision: str = "devel"
) -> None:
    """索引 + 构建语义层。"""
    import time

    print("\n[build] Indexing source code...")
    result = cg.index_all()
    print(
        f"        Indexed {result.nodes_created:,} nodes, "
        f"{result.edges_created:,} edges in {result.files_indexed:,} files"
    )

    print("[build] Building semantic layer (FORWARDS_VALUE etc.)...")
    build_result = cg.build_semantic_layer(
        repository_id=repo_id,
        revision_value=revision,
        built_at=int(time.time()),
    )
    print(
        f"        {build_result.relations_emitted:,} relations emitted, "
        f"{build_result.extractors_run} extractors run"
    )
    if build_result.errors:
        for err in build_result.errors[:5]:
            print(f"        ERROR: {err}")


def relation_report(conn: Any) -> None:
    """打印所有 relation kind 的数量。"""
    from pycodegraph.semantic.store import read_relations
    from pycodegraph.semantic.types import RelationKind

    print("\n── Relation 报告 ──")
    for rk in RelationKind:
        count = len(read_relations(conn, relation_kind=rk))
        if count > 0:
            print(f"    {rk.value:35s} {count:>6,}")


# =============================================================================
# 主流程
# =============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="跳过建库(假设 PG 已有数据),只查询",
    )
    parser.add_argument(
        "--chain",
        nargs=2,
        metavar=("FUNC", "PARAM"),
        default=["uri", "unredirected_headers"],
        help="查询参数透传链,默认 'uri unredirected_headers'",
    )
    parser.add_argument(
        "--no-chain",
        action="store_true",
        help="不查链,只打印 relation 报告",
    )
    parser.add_argument(
        "--disable-stats",
        action="store_true",
        help="semantic_explore 时跳过 relation 统计,只查语义链",
    )
    parser.add_argument(
        "--semantic",
        nargs="?",
        const="uri",
        metavar="QUERY",
        help="semantic_explore 自然语义查询(默认 'uri')",
    )
    parser.add_argument(
        "--max-hops",
        type=int,
        default=8,
        help="链查询最大跳数(默认 8)",
    )
    args = parser.parse_args()

    from pycodegraph import CodeGraph
    from pycodegraph.db import DatabaseConnection

    print("=" * 60)
    print("pycodegraph value-flow propagation 集成 demo")
    print("=" * 60)
    print(f"    PG:     {DB_URL}")
    print(f"    Source: {ANSIBLE_SRC}")

    # ── 1. 打开/建库 ───────────────────────────────────────────────────
    print("\n[1] Opening CodeGraph")
    DatabaseConnection.initialize(DB_URL)  # 建表(幂等)
    cg = CodeGraph.open_from_url(DB_URL, ANSIBLE_SRC)

    # ── 2. 索引 + 构建语义层 ───────────────────────────────────────────
    if args.skip_build:
        print("\n[2] 跳过建库(--skip-build)")
    else:
        print("\n[2] 构建图 + 语义层")
        build_codegraph(cg)

    conn = cg._queries.connection

    # ── 3. Relation 报告 ───────────────────────────────────────────────
    print("\n[3] Relation 报告")
    relation_report(conn)

    # ── 4. semantic_explore 自然语义查询 ─────────────────────────────────
    if args.semantic is not None:
        query_str = args.semantic if args.semantic else "uri"
        print(f"\n[4] semantic_explore: {query_str}")
        print(cg.semantic_explore(query_str, disable_stats=args.disable_stats))

    # ── 5. 参数透传链查询 ──────────────────────────────────────────────
    elif not args.no_chain:
        func, param = args.chain
        print(f"\n[4] 参数透传链查询: {func}({param})")
        chain = query_forwards_chain(conn, func, param, max_hops=args.max_hops)
        print(f"    找到 {len(chain)} 跳:")
        print_chain(chain, func, param)

        # 也展示 FORWARDS_VALUE 总量
        from pycodegraph.semantic.store import read_relations
        from pycodegraph.semantic.types import RelationKind

        all_fv = read_relations(conn, relation_kind=RelationKind.FORWARDS_VALUE)
        inter = [
            r
            for r in all_fv
            if r.condition_expression
            and r.condition_expression.get("forwards_type") == "inter"
        ]
        print(f"\n    FORWARDS_VALUE 总计: {len(all_fv):,} (inter {len(inter):,})")

    cg.close()
    print(f"\n{'=' * 60}")
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
