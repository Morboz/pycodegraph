"""
独立的语义查询脚本 —— 仅连接远程 PG 并做 semantic_explore 自然语义查询。

不索引、不构建，假设 PG 上已有数据（已通过 build_semantic_layer 构建过）。

Usage:
  # 默认查询 uri 的参数透传链
  uv run python examples/semantic_query.py

  # 查询其他函数
  uv run python examples/semantic_query.py "fetch_url"

  # 多个单词或短语查询
  uv run python examples/semantic_query.py ansible uri module
  uv run python examples/semantic_query.py fetch_url unredirected_headers

  # 跳过 relation 统计，只查语义链
  uv run python examples/semantic_query.py "uri" --disable-stats

  # 指定其他 PG 地址
  PG_URL="postgresql+psycopg://user:pass@host:5432/db" uv run python examples/semantic_query.py
"""

from __future__ import annotations

import argparse
import os
import sys

DB_URL = os.environ.get(
    "PG_URL",
    "postgresql+psycopg://admin:admin@172.18.120.106:5432/ai_fwd",
)
ANSIBLE_SRC = os.environ.get(
    "ANSIBLE_SRC",
    "/Users/xx/software/wanggen/ansible-code-and-docs/ansible",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "query",
        nargs="*",
        default=["uri"],
        help="自然语义查询，例如 'uri' / 'fetch_url' / 'open_url'（默认 'uri'）支持多个单词或短语",
    )
    parser.add_argument(
        "--disable-stats",
        action="store_true",
        help="跳过 relation 统计输出，只显示语义链",
    )
    args = parser.parse_args()
    query = " ".join(args.query) if args.query else "uri"

    from pycodegraph import CodeGraph

    print("=" * 60)
    print("pycodegraph 语义查询")
    print("=" * 60)
    print(f"    查询: {query}")
    print(f"    PG:   {DB_URL}")

    cg = CodeGraph.open_from_url(DB_URL, ANSIBLE_SRC)

    print(cg.semantic_explore(query, disable_stats=args.disable_stats))

    cg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
