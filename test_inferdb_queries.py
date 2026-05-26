"""Optional InferDB integration smoke tests.

Run with:
    INFERDB_TEST_URL='mysql+pymysql://user:pass@host:port/db?backend=inferdb' uv run python test_inferdb_queries.py
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import make_url

from pycodegraph import CodeGraph
from pycodegraph.types import Edge, EdgeKind, FileRecord, Language, Node, NodeKind, UnresolvedReference
from pycodegraph.db import prepare_engine_url, resolve_backend_name
from pycodegraph.db.dialects import InferDBQueryDialect, get_query_dialect
from pycodegraph.db.queries import _node_row, _node_search_text
from pycodegraph.db.queries import QueryBuilder
from pycodegraph.db.tables import metadata


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


def _test_node(node_id: str, file_path: str, name: str) -> Node:
    return Node(
        id=node_id,
        kind=NodeKind.FUNCTION,
        name=name,
        qualified_name=name,
        file_path=file_path,
        language=Language.PYTHON,
        start_line=1,
        end_line=1,
        start_column=0,
        end_column=10,
        updated_at=123,
    )


def _test_file(path: str, node_count: int) -> FileRecord:
    return FileRecord(
        path=path,
        content_hash=f"hash-{path}",
        language=Language.PYTHON,
        size=10,
        modified_at=1.0,
        indexed_at=123,
        node_count=node_count,
    )


def _sqlite_queries():
    engine = create_engine("sqlite:///:memory:")
    conn = engine.connect()
    metadata.create_all(conn)
    return QueryBuilder(conn), conn, engine


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


def test_prepare_node_rows_fts_text_handling() -> None:
    row = {"id": "node-1", "name": "Widget", "fts_text": "Widget docs"}

    for dialect_name in ("sqlite", "postgresql", "unknown"):
        prepared = get_query_dialect(dialect_name).prepare_node_rows([row])
        check(
            f"{dialect_name} prepare_node_rows strips fts_text",
            prepared == [{"id": "node-1", "name": "Widget"}],
            str(prepared),
        )
        check(
            f"{dialect_name} prepare_node_rows leaves input row intact",
            row["fts_text"] == "Widget docs",
            str(row),
        )

    prepared = get_query_dialect("inferdb").prepare_node_rows([row])
    check("InferDB prepare_node_rows keeps fts_text", prepared == [row], str(prepared))


def test_node_search_text_and_row() -> None:
    node = Node(
        id="node-1",
        kind=NodeKind.FUNCTION,
        name="search_nodes",
        qualified_name="pycodegraph.db.queries.QueryBuilder.search_nodes",
        file_path="src/pycodegraph/db/queries.py",
        language=Language.PYTHON,
        start_line=10,
        end_line=20,
        start_column=4,
        end_column=8,
        updated_at=123,
        docstring="Find nodes by text",
        signature="def search_nodes(query: str)",
        visibility="public",
        is_exported=True,
        is_async=False,
        is_static=True,
        is_abstract=False,
        decorators='["cached"]',
        type_parameters=None,
    )

    search_text = _node_search_text(node)
    for expected in (
        "search_nodes",
        "pycodegraph.db.queries.QueryBuilder.search_nodes",
        "Find nodes by text",
        "def search_nodes(query: str)",
    ):
        check(f"node search text includes {expected}", expected in search_text, search_text)

    row = _node_row(node)
    check("node row includes fts_text", row["fts_text"] == search_text, str(row))
    check("node row stores enum values", row["kind"] == "function" and row["language"] == "python", str(row))
    check("node row stores bools as ints", row["is_exported"] == 1 and row["is_static"] == 1, str(row))


def test_delete_file_removes_nodes_edges_refs_and_file_record() -> None:
    queries, conn, engine = _sqlite_queries()
    try:
        nodes = [
            _test_node("a1", "a.py", "a_one"),
            _test_node("a2", "a.py", "a_two"),
        ]
        queries.insert_nodes(nodes)
        queries.insert_edges([Edge(source="a1", target="a2", kind=EdgeKind.CALLS)])
        queries.insert_unresolved_refs_batch([
            UnresolvedReference(
                from_node_id="a1",
                reference_name="missing",
                reference_kind=EdgeKind.REFERENCES,
                line=1,
                column=0,
                file_path="a.py",
                language="python",
            )
        ])
        queries.upsert_file(_test_file("a.py", 2))

        queries.delete_file("a.py")

        check("delete_file removes file nodes", queries.get_nodes_by_file("a.py") == [])
        check("delete_file removes outgoing edges", queries.get_outgoing_edges("a1") == [])
        check("delete_file removes incoming edges", queries.get_incoming_edges("a2") == [])
        check("delete_file removes unresolved refs", queries.get_unresolved_refs_count() == 0)
        check("delete_file removes file record", queries.get_file_by_path("a.py") is None)
    finally:
        conn.close()
        engine.dispose()


def test_delete_files_batch_removes_nodes_for_all_paths() -> None:
    queries, conn, engine = _sqlite_queries()
    try:
        queries.insert_nodes([
            _test_node("a1", "a.py", "a_one"),
            _test_node("b1", "b.py", "b_one"),
            _test_node("c1", "c.py", "c_one"),
        ])
        queries.upsert_file(_test_file("a.py", 1))
        queries.upsert_file(_test_file("b.py", 1))
        queries.upsert_file(_test_file("c.py", 1))

        queries.delete_files_batch(["a.py", "b.py"])

        check("delete_files_batch removes a.py nodes", queries.get_nodes_by_file("a.py") == [])
        check("delete_files_batch removes b.py nodes", queries.get_nodes_by_file("b.py") == [])
        check("delete_files_batch preserves other file nodes", len(queries.get_nodes_by_file("c.py")) == 1)
    finally:
        conn.close()
        engine.dispose()


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
    check("InferDB node insert includes fts_text", "fts_text" in insert_sql, insert_sql)

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
    test_prepare_node_rows_fts_text_handling()
    test_node_search_text_and_row()
    test_delete_file_removes_nodes_edges_refs_and_file_record()
    test_delete_files_batch_removes_nodes_for_all_paths()
    test_inferdb_fts_sql_shape()
    test_inferdb_after_nodes_changed_sql_shape()
    test_inferdb_mysql_statement_shapes()
    test_inferdb_smoke()
    print("InferDB smoke checks completed")
