"""Optional InferDB integration smoke tests.

Run with:
    INFERDB_TEST_URL='mysql+pymysql://user:pass@host:port/db?backend=inferdb' uv run python test_inferdb_queries.py
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy.dialects import mysql
from sqlalchemy.engine import make_url

from pycodegraph import CodeGraph
from pycodegraph.db import prepare_engine_url, resolve_backend_name
from pycodegraph.db.dialects import InferDBQueryDialect, get_query_dialect


TEST_URL = os.environ.get("INFERDB_TEST_URL")


class _FakeResult:
    def fetchall(self) -> list[tuple]:
        return []


class _FakeConnection:
    def __init__(self, database: str = "codegraph_query_test") -> None:
        self.engine = SimpleNamespace(url=SimpleNamespace(database=database))
        self.sql: list[str] = []
        self.params: list[dict] = []

    def execute(self, stmt, params=None):
        self.sql.append(str(stmt))
        self.params.append(params or {})
        return _FakeResult()


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


def test_inferdb_query_dialect() -> None:
    check(
        "inferdb query dialect resolves to inferdb",
        get_query_dialect("inferdb").name == "inferdb",
    )


def test_inferdb_fts_sql_shape() -> None:
    conn = _FakeConnection()
    InferDBQueryDialect().search_nodes_fts(
        conn,
        "QueryBuilder",
        kinds=["class"],
        languages=["python"],
        limit=5,
        offset=0,
    )
    sql = conn.sql[-1]

    check("InferDB FTS uses DuckDB execution hint", "/*+ duck_execute */" in sql, sql)
    check("InferDB FTS uses match_bm25", "match_bm25(" in sql, sql)
    check("InferDB FTS scores n.id", "n.id" in sql, sql)
    check("InferDB FTS uses fts_text field", "fields := 'fts_text'" in sql, sql)
    check("InferDB FTS filters non-matches", "WHERE score IS NOT NULL" in sql, sql)
    check("InferDB FTS does not join FTS table", "JOIN ltmdb_sql" not in sql, sql)


def test_inferdb_after_nodes_changed_sql_shape() -> None:
    conn = _FakeConnection("code'graph")
    InferDBQueryDialect().after_nodes_changed(conn)
    sql = conn.sql[-1]

    check("InferDB refresh creates FTS index", "PRAGMA create_fts_index" in sql, sql)
    check("InferDB refresh uses DuckDB execution hint", "/*+ duck_execute */" in sql, sql)
    check("InferDB refresh indexes fts_text", "fts_text" in sql, sql)
    check("InferDB refresh escapes database string", "code''graph" in sql, sql)


def test_inferdb_mysql_statement_shapes() -> None:
    dialect = InferDBQueryDialect()
    mysql_dialect = mysql.dialect()

    insert_sql = str(dialect.insert_nodes_ignore().compile(dialect=mysql_dialect))
    check("InferDB node insert uses INSERT IGNORE", "INSERT IGNORE" in insert_sql, insert_sql)

    upsert_sql = str(dialect.upsert_file({
        "path": "sample.py",
        "content_hash": "hash",
        "language": "python",
        "size": 1,
        "modified_at": 1.0,
        "indexed_at": 1,
        "node_count": 1,
        "errors": None,
    }).compile(dialect=mysql_dialect))
    check("InferDB file upsert uses ON DUPLICATE KEY UPDATE", "ON DUPLICATE KEY UPDATE" in upsert_sql, upsert_sql)


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
    test_inferdb_query_dialect()
    test_inferdb_fts_sql_shape()
    test_inferdb_after_nodes_changed_sql_shape()
    test_inferdb_mysql_statement_shapes()
    test_inferdb_smoke()
    print("InferDB smoke checks completed")
