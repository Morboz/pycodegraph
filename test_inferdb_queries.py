"""Optional InferDB integration smoke tests.

Run with:
    INFERDB_TEST_URL='mysql+pymysql://user:pass@host:port/db?backend=inferdb' uv run python test_inferdb_queries.py
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from sqlalchemy.engine import make_url

from pycodegraph import CodeGraph
from pycodegraph.db import prepare_engine_url, resolve_backend_name


TEST_URL = os.environ.get("INFERDB_TEST_URL")


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name}: {detail}")
    raise AssertionError(name)


def test_backend_resolution() -> None:
    check(
        "mysql URL with backend=inferdb resolves to inferdb",
        resolve_backend_name("mysql+pymysql://u:p@localhost/db?backend=inferdb", "mysql") == "inferdb",
    )
    check(
        "sqlite URL resolves to sqlite",
        resolve_backend_name("sqlite:////tmp/codegraph.db", "sqlite") == "sqlite",
    )
    check(
        "postgresql URL resolves to postgresql",
        resolve_backend_name("postgresql+psycopg://u:p@localhost/db", "postgresql") == "postgresql",
    )


def test_engine_url_sanitization() -> None:
    engine_url, backend_name = prepare_engine_url(
        "mysql+pymysql://u:p@localhost/db?backend=inferdb&charset=utf8mb4"
    )
    parsed = make_url(engine_url)

    check(
        "engine URL backend resolves to inferdb",
        backend_name == "inferdb",
        backend_name,
    )
    check(
        "engine URL strips backend query param",
        "backend" not in parsed.query,
        str(parsed.query),
    )
    check(
        "engine URL preserves other query params",
        parsed.query.get("charset") == "utf8mb4",
        str(parsed.query),
    )


def test_inferdb_smoke() -> None:
    if not TEST_URL:
        print("[skip] INFERDB_TEST_URL is not set")
        return

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "sample.py").write_text(
            "class QueryBuilder:\n"
            "    def search_nodes(self):\n"
            "        return 'inferdb fts smoke'\n",
            encoding="utf-8",
        )

        graph = CodeGraph.init(str(root), {"db_url": TEST_URL})
        try:
            result = graph.index_all()
            check("index_all succeeds", result.success, str(result.errors))
            matches = graph.search("QueryBuilder", limit=5)
            check("FTS search finds QueryBuilder", any(n.name == "QueryBuilder" for n in matches))
            stats = graph.get_stats()
            check("stats include nodes", stats["node_count"] > 0, str(stats))
        finally:
            graph.close()


if __name__ == "__main__":
    test_backend_resolution()
    test_engine_url_sanitization()
    test_inferdb_smoke()
    print("InferDB smoke checks completed")
