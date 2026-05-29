"""InferDB integration unit tests.

Pure mock/SQLite — no external services required.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql

from pycodegraph import CodeGraph, InferDBCodeGraphBackend, create_resolver
from pycodegraph.types import Edge, EdgeKind, FileRecord, Language, Node, NodeKind, UnresolvedReference
from pycodegraph.db import _init_inferdb_schema, prepare_engine_url, resolve_backend_name
from pycodegraph.db.dialects import InferDBQueryDialect, get_query_dialect
from pycodegraph.db.queries import _node_row, _node_search_text
from pycodegraph.db.queries import QueryBuilder
from pycodegraph.db.tables import metadata


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, rows: list[tuple] | None = None) -> None:
        self._rows = rows or []

    def fetchall(self) -> list[tuple]:
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeConnection:
    def __init__(self, database: str = "codegraph_query_test") -> None:
        self.engine = SimpleNamespace(url=SimpleNamespace(database=database))
        self.sql: list[str] = []
        self.driver_sql: list[str] = []
        self.raw_sql: list[str] = []
        self.params: list[dict] = []
        self.connection = SimpleNamespace(driver_connection=_FakeDriverConnection(self))

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sql.append(sql)
        self.params.append(params or {})
        if sql == "SELECT id, fts_text FROM nodes WHERE fts_text IS NOT NULL AND fts_text != ''":
            return _FakeResult([("node-1", "QueryBuilder search_nodes")])
        return _FakeResult()

    def exec_driver_sql(self, sql: str):
        self.sql.append(sql)
        self.driver_sql.append(sql)
        self.params.append({})
        return _FakeResult()


class _FakeCursor:
    def __init__(self, conn: _FakeConnection) -> None:
        self.conn = conn

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, sql: str) -> None:
        self.conn.sql.append(sql)
        self.conn.raw_sql.append(sql)


class _FakeDriverConnection:
    def __init__(self, conn: _FakeConnection) -> None:
        self.conn = conn

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.conn)


class _FakeBegin:
    def __init__(self, conn: _FakeConnection) -> None:
        self.conn = conn

    def __enter__(self) -> _FakeConnection:
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeEngine:
    def __init__(self) -> None:
        self.conn = _FakeConnection()

    def begin(self) -> _FakeBegin:
        return _FakeBegin(self.conn)

    def connect(self) -> _FakeBegin:
        return _FakeBegin(self.conn)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBackendResolution:
    def test_mysql_url_with_backend_inferdb(self):
        assert resolve_backend_name("mysql+pymysql://u:p@localhost/db?backend=inferdb", "mysql") == "inferdb"

    def test_sqlite_url(self):
        assert resolve_backend_name("sqlite:////tmp/codegraph.db", "sqlite") == "sqlite"

    def test_postgresql_url(self):
        assert resolve_backend_name("postgresql+psycopg://u:p@localhost/db", "postgresql") == "postgresql"


class TestEngineUrlSanitization:
    def test_backend_resolves_to_inferdb(self):
        engine_url, backend_name = prepare_engine_url(
            "mysql+pymysql://u:p@localhost/db?backend=inferdb&charset=utf8mb4"
        )
        assert backend_name == "inferdb"

    def test_strips_backend_query_param(self):
        from sqlalchemy.engine import make_url
        engine_url, _ = prepare_engine_url(
            "mysql+pymysql://u:p@localhost/db?backend=inferdb&charset=utf8mb4"
        )
        parsed = make_url(engine_url)
        assert "backend" not in parsed.query

    def test_preserves_other_query_params(self):
        from sqlalchemy.engine import make_url
        engine_url, _ = prepare_engine_url(
            "mysql+pymysql://u:p@localhost/db?backend=inferdb&charset=utf8mb4"
        )
        parsed = make_url(engine_url)
        assert parsed.query.get("charset") == "utf8mb4"


class TestInferDBBackendLifecycle:
    def test_ensure_database(self):
        engine = _FakeEngine()
        backend = InferDBCodeGraphBackend(
            host="db.local", port=3307, user="test", password="p@ss/word",
            engine_factory=lambda _url: engine,
        )
        db_url = backend.ensure_database("cg_1234abcd")
        assert db_url == "mysql+pymysql://test:p%40ss%2Fword@db.local:3307/cg_1234abcd?backend=inferdb"
        assert "CREATE DATABASE IF NOT EXISTS `cg_1234abcd`" in engine.conn.sql
        assert '/*+ duck_execute */ CREATE SCHEMA IF NOT EXISTS ltmdb_sql."cg_1234abcd"' in engine.conn.raw_sql

    def test_drop_database(self):
        engine = _FakeEngine()
        backend = InferDBCodeGraphBackend(
            host="db.local", port=3307, user="test", password="p@ss/word",
            engine_factory=lambda _url: engine,
        )
        backend.ensure_database("cg_1234abcd")
        backend.drop_database("cg_1234abcd")
        assert '/*+ duck_execute */ DROP SCHEMA IF EXISTS ltmdb_sql."cg_1234abcd" CASCADE' in engine.conn.raw_sql

    def test_existing_database_returns_none_when_missing(self):
        engine = _FakeEngine()
        backend = InferDBCodeGraphBackend(
            host="db.local", port=3307, user="test", password="secret",
            engine_factory=lambda _url: engine,
        )
        assert backend.existing_database_url("missing_db") is None
        assert backend.open_codegraph("missing_db") is None
        assert not any("CREATE DATABASE" in sql for sql in engine.conn.sql)


class TestInferDBSchema:
    def test_keeps_long_reference_names(self):
        engine = _FakeEngine()
        _init_inferdb_schema(engine)
        unresolved_sql = next(
            stmt for stmt in engine.conn.sql if "CREATE TABLE IF NOT EXISTS unresolved_refs" in stmt
        )
        assert "unresolved_refs" in "\n".join(engine.conn.sql)
        assert '/*+ duck_execute */ CREATE SCHEMA IF NOT EXISTS ltmdb_sql."codegraph_query_test"' in engine.conn.raw_sql
        assert "reference_name TEXT NOT NULL" in unresolved_sql


class TestInferDBQueryDialect:
    def test_resolves_to_inferdb(self):
        assert get_query_dialect("inferdb").name == "inferdb"


class TestPrepareNodeRows:
    @pytest.mark.parametrize("dialect_name", ["sqlite", "postgresql", "unknown"])
    def test_strips_fts_text(self, dialect_name):
        row = {"id": "node-1", "name": "Widget", "fts_text": "Widget docs"}
        prepared = get_query_dialect(dialect_name).prepare_node_rows([row])
        assert prepared == [{"id": "node-1", "name": "Widget"}]
        assert row["fts_text"] == "Widget docs"

    def test_inferdb_keeps_fts_text(self):
        row = {"id": "node-1", "name": "Widget", "fts_text": "Widget docs"}
        prepared = get_query_dialect("inferdb").prepare_node_rows([row])
        assert prepared == [row]


class TestNodeSearchTextAndRow:
    def _make_node(self):
        return Node(
            id="node-1", kind=NodeKind.FUNCTION, name="search_nodes",
            qualified_name="pycodegraph.db.queries.QueryBuilder.search_nodes",
            file_path="src/pycodegraph/db/queries.py", language=Language.PYTHON,
            start_line=10, end_line=20, start_column=4, end_column=8, updated_at=123,
            docstring="Find nodes by text", signature="def search_nodes(query: str)",
            visibility="public", is_exported=True, is_async=False, is_static=True,
            is_abstract=False, decorators='["cached"]', type_parameters=None,
        )

    def test_search_text_content(self):
        node = self._make_node()
        search_text = _node_search_text(node)
        for expected in ("search_nodes", "pycodegraph.db.queries.QueryBuilder.search_nodes",
                         "Find nodes by text", "def search_nodes(query: str)"):
            assert expected in search_text

    def test_node_row(self):
        node = self._make_node()
        row = _node_row(node)
        assert row["fts_text"] == _node_search_text(node)
        assert row["kind"] == "function" and row["language"] == "python"
        assert row["is_exported"] == 1 and row["is_static"] == 1


class TestDeleteFile:
    def test_removes_nodes_edges_refs_and_file_record(self):
        queries, conn, engine = _sqlite_queries()
        try:
            queries.insert_nodes([_test_node("a1", "a.py", "a_one"), _test_node("a2", "a.py", "a_two")])
            queries.insert_edges([Edge(source="a1", target="a2", kind=EdgeKind.CALLS)])
            queries.insert_unresolved_refs_batch([
                UnresolvedReference(
                    from_node_id="a1", reference_name="missing", reference_kind=EdgeKind.REFERENCES,
                    line=1, column=0, file_path="a.py", language="python",
                )
            ])
            queries.upsert_file(_test_file("a.py", 2))
            queries.delete_file("a.py")
            assert queries.get_nodes_by_file("a.py") == []
            assert queries.get_outgoing_edges("a1") == []
            assert queries.get_incoming_edges("a2") == []
            assert queries.get_unresolved_refs_count() == 0
            assert queries.get_file_by_path("a.py") is None
        finally:
            conn.close()
            engine.dispose()


class TestDeleteFilesBatch:
    def test_removes_nodes_for_all_paths(self):
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
            assert queries.get_nodes_by_file("a.py") == []
            assert queries.get_nodes_by_file("b.py") == []
            assert len(queries.get_nodes_by_file("c.py")) == 1
        finally:
            conn.close()
            engine.dispose()


class TestInferDBFtsSql:
    def test_fts_search_sql_shape(self):
        conn = _FakeConnection()
        InferDBQueryDialect().search_nodes_fts(
            conn, "QueryBuilder", kinds=["class"], languages=["python"], limit=5, offset=0,
        )
        sql = conn.sql[-1]
        assert "/*+ duck_execute */" in sql
        assert "match_bm25(" in sql
        assert "seq_id" in sql
        assert "fields := 'fts_text'" in sql
        assert "pycodegraph_nodes_fts" in sql
        assert "WHERE score IS NOT NULL" in sql
        assert "JOIN ltmdb_sql" not in sql

    def test_after_nodes_changed_sql_shape(self):
        conn = _FakeConnection()
        InferDBQueryDialect().after_nodes_changed(conn)
        sql = conn.sql[-1]
        assert "PRAGMA create_fts_index" in sql
        assert "/*+ duck_execute */" in sql
        assert "fts_text" in sql
        assert "'seq_id'" in sql
        assert "pycodegraph_nodes_fts" in sql
        assert "overwrite=1" in sql

    def test_after_nodes_changed_escapes_database_string(self):
        conn = _FakeConnection("code'graph")
        InferDBQueryDialect().after_nodes_changed(conn)
        sql = conn.sql[-1]
        assert "code''graph" in sql

    def test_after_nodes_changed_uses_driver_sql_for_fts_literals(self):
        conn = _FakeConnection()

        def execute(stmt, params=None):
            sql = str(stmt)
            conn.sql.append(sql)
            conn.params.append(params or {})
            if sql == "SELECT id, fts_text FROM nodes WHERE fts_text IS NOT NULL AND fts_text != ''":
                return _FakeResult([("node-1", "field_size_re (?P<var>char)")])
            return _FakeResult()

        conn.execute = execute
        InferDBQueryDialect().after_nodes_changed(conn)
        insert_sql = next(stmt for stmt in conn.sql if "INSERT INTO ltmdb_sql" in stmt)
        assert insert_sql in conn.raw_sql


class TestInferDBMysqlStatementShapes:
    def test_insert_nodes_ignore(self):
        dialect = InferDBQueryDialect()
        mysql_dialect = mysql.dialect()
        insert_sql = str(dialect.insert_nodes_ignore().compile(dialect=mysql_dialect))
        assert "INSERT IGNORE" in insert_sql
        assert "fts_text" in insert_sql

    def test_upsert_file(self):
        dialect = InferDBQueryDialect()
        mysql_dialect = mysql.dialect()
        upsert_sql = str(dialect.upsert_file({
            "path": "sample.py", "content_hash": "hash", "language": "python",
            "size": 1, "modified_at": 1.0, "indexed_at": 1, "node_count": 1, "errors": None,
        }).compile(dialect=mysql_dialect))
        assert "ON DUPLICATE KEY UPDATE" in upsert_sql


class TestCreateResolverTopLevelExport:
    """Verify create_resolver is accessible from the top-level package (issue gap #4)."""

    def test_create_resolver_importable_from_top_level(self):
        # Before the fix, this would raise ImportError.
        from pycodegraph import create_resolver as cr
        assert callable(cr)

    def test_create_resolver_in_dunder_all(self):
        import pycodegraph
        assert "create_resolver" in pycodegraph.__all__

    def test_create_resolver_is_same_as_resolution_module(self):
        from pycodegraph import create_resolver as cr
        from pycodegraph.resolution import create_resolver as cr2
        assert cr is cr2


class TestCodeGraphDeleteFile:
    """Verify CodeGraph.delete_file() is a public method (issue gap #3)."""

    def _make_codegraph(self, queries, conn, tmp_dir: str = "/tmp"):
        from pycodegraph.config import CodeGraphConfig
        from types import SimpleNamespace
        from pycodegraph.extraction.orchestrator import ExtractionOrchestrator
        from pycodegraph.graph import GraphTraverser, GraphQueryManager
        from pycodegraph.context.builder import ContextBuilder

        config = CodeGraphConfig()
        cg = CodeGraph.__new__(CodeGraph)
        cg._db = SimpleNamespace(get_connection=lambda: conn, close=lambda: None)
        cg._conn = conn
        cg._queries = queries
        cg._config = config
        cg._project_root = tmp_dir
        cg._orchestrator = ExtractionOrchestrator(tmp_dir, config, queries)
        cg._traverser = GraphTraverser(queries)
        cg._graph_manager = GraphQueryManager(queries)
        cg._context_builder = ContextBuilder(tmp_dir, queries, cg._traverser)
        return cg

    def test_delete_file_is_public_method_on_codegraph_class(self):
        # Before the fix, there was no public delete_file on CodeGraph.
        assert callable(getattr(CodeGraph, "delete_file", None))
        assert not getattr(CodeGraph.delete_file, "__name__", "").startswith("_")

    def test_delete_file_removes_nodes_and_file_record(self):
        """CodeGraph.delete_file() removes data without reaching into _queries."""
        queries, conn, engine = _sqlite_queries()
        try:
            queries.insert_nodes([_test_node("a1", "a.py", "a_one")])
            queries.upsert_file(_test_file("a.py", 1))

            cg = self._make_codegraph(queries, conn)

            assert queries.get_nodes_by_file("a.py") != []
            # Call the public method — no access to _queries required.
            cg.delete_file("a.py")
            assert queries.get_nodes_by_file("a.py") == []
            assert queries.get_file_by_path("a.py") is None
        finally:
            conn.close()
            engine.dispose()

    def test_delete_file_delegates_to_queries_delete_file(self):
        """CodeGraph.delete_file() must call the underlying QueryBuilder.delete_file."""
        from unittest.mock import patch
        queries, conn, engine = _sqlite_queries()
        try:
            cg = self._make_codegraph(queries, conn)
            with patch.object(queries, "delete_file") as mock_del:
                cg.delete_file("some/file.py")
                mock_del.assert_called_once_with("some/file.py")
        finally:
            conn.close()
            engine.dispose()


class TestCodeGraphApplyDelta:
    """Verify CodeGraph.apply_delta() (issue gap #1 and #2)."""

    def _make_codegraph(self, queries, conn, tmp_dir: str):
        from pycodegraph.config import CodeGraphConfig
        from types import SimpleNamespace
        from pycodegraph.extraction.orchestrator import ExtractionOrchestrator
        from pycodegraph.graph import GraphTraverser, GraphQueryManager
        from pycodegraph.context.builder import ContextBuilder

        config = CodeGraphConfig()
        cg = CodeGraph.__new__(CodeGraph)
        cg._db = SimpleNamespace(get_connection=lambda: conn, close=lambda: None)
        cg._conn = conn
        cg._queries = queries
        cg._config = config
        cg._project_root = tmp_dir
        cg._orchestrator = ExtractionOrchestrator(tmp_dir, config, queries)
        cg._traverser = GraphTraverser(queries)
        cg._graph_manager = GraphQueryManager(queries)
        cg._context_builder = ContextBuilder(tmp_dir, queries, cg._traverser)
        return cg

    def test_apply_delta_is_public_method_on_codegraph_class(self):
        assert callable(getattr(CodeGraph, "apply_delta", None))

    def test_apply_delta_indexes_changed_files(self):
        """apply_delta must index the listed changed files."""
        queries, conn, engine = _sqlite_queries()
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                (Path(tmp_dir) / "new_file.py").write_text("def hello(): pass\n")
                cg = self._make_codegraph(queries, conn, tmp_dir)
                result = cg.apply_delta(changed_files=["new_file.py"], removed_files=[])
                assert result.success
                assert queries.get_file_by_path("new_file.py") is not None
        finally:
            conn.close()
            engine.dispose()

    def test_apply_delta_removes_deleted_files(self):
        """apply_delta must delete nodes/records for removed files."""
        queries, conn, engine = _sqlite_queries()
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                queries.insert_nodes([_test_node("old1", "old.py", "old_func")])
                queries.upsert_file(_test_file("old.py", 1))

                cg = self._make_codegraph(queries, conn, tmp_dir)
                result = cg.apply_delta(changed_files=[], removed_files=["old.py"])

                assert result.success
                assert queries.get_nodes_by_file("old.py") == []
                assert queries.get_file_by_path("old.py") is None
        finally:
            conn.close()
            engine.dispose()

    def test_apply_delta_runs_resolution_on_success(self):
        """apply_delta must call resolve_and_persist — this was the key missing step
        in the old index_file() and the main reason apply_delta was requested."""
        from unittest.mock import patch, MagicMock
        queries, conn, engine = _sqlite_queries()
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                (Path(tmp_dir) / "mod.py").write_text("def foo(): pass\n")
                cg = self._make_codegraph(queries, conn, tmp_dir)

                fake_resolution = MagicMock()
                fake_resolution.stats = {"resolved": 3, "unresolved": 0}
                fake_resolver = MagicMock()
                fake_resolver.resolve_and_persist.return_value = fake_resolution

                with patch("pycodegraph.codegraph.create_resolver", return_value=fake_resolver) as mock_cr:
                    result = cg.apply_delta(changed_files=["mod.py"], removed_files=[])

                mock_cr.assert_called_once()
                fake_resolver.resolve_and_persist.assert_called_once()
                assert result.success
        finally:
            conn.close()
            engine.dispose()

    def test_apply_delta_skips_resolution_on_errors(self):
        """apply_delta must NOT run resolution when extraction errors occurred,
        matching the documented behaviour."""
        from unittest.mock import patch
        queries, conn, engine = _sqlite_queries()
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                # "nonexistent.py" will cause a read error
                cg = self._make_codegraph(queries, conn, tmp_dir)
                with patch("pycodegraph.codegraph.create_resolver") as mock_cr:
                    result = cg.apply_delta(
                        changed_files=["nonexistent.py"], removed_files=[]
                    )
                mock_cr.assert_not_called()
                assert not result.success
                assert result.errors
        finally:
            conn.close()
            engine.dispose()

    def test_apply_delta_returns_index_result_type(self):
        from pycodegraph.types import IndexResult
        queries, conn, engine = _sqlite_queries()
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                cg = self._make_codegraph(queries, conn, tmp_dir)
                result = cg.apply_delta(changed_files=[], removed_files=[])
                assert isinstance(result, IndexResult)
                assert result.success
                assert result.files_indexed == 0
        finally:
            conn.close()
            engine.dispose()

    def test_apply_delta_combined_changed_and_removed(self):
        """apply_delta handles both changed and removed files in one call."""
        queries, conn, engine = _sqlite_queries()
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                # Pre-existing file to be removed
                queries.insert_nodes([_test_node("old1", "old.py", "old_func")])
                queries.upsert_file(_test_file("old.py", 1))
                # New file to be indexed
                (Path(tmp_dir) / "new_file.py").write_text("def hello(): pass\n")

                cg = self._make_codegraph(queries, conn, tmp_dir)
                result = cg.apply_delta(
                    changed_files=["new_file.py"],
                    removed_files=["old.py"],
                )

                assert result.success
                assert queries.get_nodes_by_file("old.py") == []
                assert queries.get_file_by_path("old.py") is None
                assert queries.get_file_by_path("new_file.py") is not None
        finally:
            conn.close()
            engine.dispose()
