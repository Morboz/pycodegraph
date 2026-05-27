# InferDB Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add InferDB as an explicit pycodegraph backend using MySQL-compatible relational tables and InferDB FTS search.

**Architecture:** Keep `QueryBuilder` public APIs unchanged and route database-specific behavior through backend-specific schema initializers and query dialects. InferDB is selected explicitly from a MySQL URL with `backend=inferdb`, uses MySQL DDL/DML for relational storage, and refreshes an InferDB DuckDB shadow FTS table from `nodes.fts_text` after node-changing writes.

**Tech Stack:** Python 3.10+, SQLAlchemy Core, SQLite, PostgreSQL, MySQL-compatible InferDB, pytest/standalone smoke scripts.

---

## File Structure

- Modify `src/pycodegraph/db/__init__.py`: resolve logical backend names, split schema initialization by backend, add InferDB MySQL-compatible DDL and InferDB FTS setup.
- Modify `src/pycodegraph/db/dialects.py`: add `InferDBQueryDialect`, backend factory input, MySQL DML helpers, FTS shadow-table refresh hook, and InferDB FTS search SQL.
- Modify `src/pycodegraph/db/queries.py`: pass logical backend into dialect selection, populate InferDB-only `fts_text` safely, and call node-change hooks after node insert/delete operations.
- Create `test_inferdb_queries.py`: optional smoke test driven by `INFERDB_TEST_URL`, skipped when the environment variable is absent.
- Do not change `QueryBuilder` public methods or type models.

## Task 1: Backend Resolution and InferDB Schema Initializer

**Files:**
- Modify: `src/pycodegraph/db/__init__.py`
- Test: `test_inferdb_queries.py`

- [ ] **Step 1: Add a backend resolver test scaffold**

Create `test_inferdb_queries.py` with a small import-level check and skip behavior:

```python
"""Optional InferDB integration smoke tests.

Run with:
    INFERDB_TEST_URL='mysql+pymysql://user:pass@host:port/db?backend=inferdb' uv run python test_inferdb_queries.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from pycodegraph import CodeGraph
from pycodegraph.db import resolve_backend_name


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


def test_inferdb_smoke() -> None:
    if not TEST_URL:
        print("[skip] INFERDB_TEST_URL is not set")
        return

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "sample.py").write_text(
            "class QueryBuilder:\\n"
            "    def search_nodes(self):\\n"
            "        return 'inferdb fts smoke'\\n",
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
    test_inferdb_smoke()
    print("InferDB smoke checks completed")
```

- [ ] **Step 2: Run the new test and verify it fails on missing resolver**

Run:

```bash
uv run python test_inferdb_queries.py
```

Expected: FAIL with an import error for `resolve_backend_name`.

- [ ] **Step 3: Implement `resolve_backend_name` and store backend on `DatabaseConnection`**

In `src/pycodegraph/db/__init__.py`, add imports:

```python
from urllib.parse import parse_qs, urlparse
```

Add this function above `DatabaseConnection`:

```python
def resolve_backend_name(db_url: str, dialect_name: str) -> str:
    """Resolve pycodegraph's logical backend from a SQLAlchemy URL and driver dialect."""
    query = parse_qs(urlparse(db_url).query)
    backend = query.get("backend", [None])[0]
    if dialect_name == "mysql" and backend == "inferdb":
        return "inferdb"
    return dialect_name
```

Change `DatabaseConnection.__init__` and `dialect_name`:

```python
class DatabaseConnection:
    """Wraps a SQLAlchemy Engine with dialect-specific initialization."""

    def __init__(
        self,
        engine: Engine,
        connection: SAConnection | None = None,
        backend_name: str | None = None,
    ) -> None:
        self._engine = engine
        self._connection = connection
        self._backend_name = backend_name or engine.dialect.name

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def dialect_name(self) -> str:
        return self._backend_name
```

- [ ] **Step 4: Add explicit InferDB schema initializer**

In `src/pycodegraph/db/__init__.py`, add:

```python
def _init_inferdb_schema(engine: Engine) -> None:
    """Create MySQL-compatible tables for InferDB."""
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_versions ("
            "version INT PRIMARY KEY,"
            "applied_at BIGINT NOT NULL,"
            "description TEXT"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS nodes ("
            "id VARCHAR(512) PRIMARY KEY,"
            "kind VARCHAR(64) NOT NULL,"
            "name VARCHAR(512) NOT NULL,"
            "qualified_name VARCHAR(2048) NOT NULL,"
            "file_path VARCHAR(2048) NOT NULL,"
            "language VARCHAR(64) NOT NULL,"
            "start_line INT NOT NULL,"
            "end_line INT NOT NULL,"
            "start_column INT NOT NULL,"
            "end_column INT NOT NULL,"
            "docstring TEXT,"
            "signature TEXT,"
            "visibility VARCHAR(64),"
            "is_exported INT DEFAULT 0,"
            "is_async INT DEFAULT 0,"
            "is_static INT DEFAULT 0,"
            "is_abstract INT DEFAULT 0,"
            "decorators TEXT,"
            "type_parameters TEXT,"
            "updated_at BIGINT NOT NULL,"
            "fts_text TEXT"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS edges ("
            "id BIGINT AUTO_INCREMENT PRIMARY KEY,"
            "source VARCHAR(512) NOT NULL,"
            "target VARCHAR(512) NOT NULL,"
            "kind VARCHAR(64) NOT NULL,"
            "metadata TEXT,"
            "line INT,"
            "col INT,"
            "provenance TEXT"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS files ("
            "path VARCHAR(2048) PRIMARY KEY,"
            "content_hash VARCHAR(128) NOT NULL,"
            "language VARCHAR(64) NOT NULL,"
            "size INT NOT NULL,"
            "modified_at DOUBLE NOT NULL,"
            "indexed_at BIGINT NOT NULL,"
            "node_count INT DEFAULT 0,"
            "errors TEXT"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS unresolved_refs ("
            "id BIGINT AUTO_INCREMENT PRIMARY KEY,"
            "from_node_id VARCHAR(512) NOT NULL,"
            "reference_name VARCHAR(512) NOT NULL,"
            "reference_kind VARCHAR(64) NOT NULL,"
            "line INT NOT NULL,"
            "col INT NOT NULL,"
            "candidates TEXT,"
            "file_path VARCHAR(2048) NOT NULL DEFAULT '',"
            "language VARCHAR(64) NOT NULL DEFAULT 'unknown'"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS project_metadata ("
            "`key` VARCHAR(255) PRIMARY KEY,"
            "value TEXT NOT NULL,"
            "updated_at BIGINT NOT NULL"
            ")"
        ))
        conn.execute(text(
            "INSERT IGNORE INTO schema_versions (version, applied_at, description)"
            " VALUES (1, :ts, 'Initial schema')"
        ), {"ts": _now_ms()})
```

- [ ] **Step 5: Wire backend-specific initialization**

In `DatabaseConnection.initialize`, after `engine = create_engine(db_url)`, compute:

```python
backend_name = resolve_backend_name(db_url, engine.dialect.name)
```

Replace the schema block with:

```python
if backend_name == "inferdb":
    _init_inferdb_schema(engine)
else:
    with engine.begin() as conn:
        metadata.create_all(conn)
        if backend_name == "sqlite":
            conn.execute(text(
                "INSERT OR IGNORE INTO schema_versions (version, applied_at, description)"
                " VALUES (1, :ts, 'Initial schema')"
            ), {"ts": _now_ms()})
        else:
            conn.execute(text(
                "INSERT INTO schema_versions (version, applied_at, description)"
                " VALUES (1, :ts, 'Initial schema') ON CONFLICT DO NOTHING"
            ), {"ts": _now_ms()})

if backend_name == "sqlite":
    _init_sqlite_fts(engine)
elif backend_name == "postgresql":
    _init_postgresql_fts(engine)
```

Return:

```python
return cls(engine, backend_name=backend_name)
```

In `DatabaseConnection.open`, compute `backend_name` and return it:

```python
backend_name = resolve_backend_name(db_url, engine.dialect.name)
return cls(engine, backend_name=backend_name)
```

- [ ] **Step 6: Run resolver-only check**

Run:

```bash
uv run python test_inferdb_queries.py
```

Expected without `INFERDB_TEST_URL`: resolver checks pass and InferDB smoke is skipped.

- [ ] **Step 7: Commit backend resolution and schema initializer**

```bash
git add src/pycodegraph/db/__init__.py test_inferdb_queries.py
git commit -m "feat: add inferdb backend initialization"
```

## Task 2: InferDB Query Dialect

**Files:**
- Modify: `src/pycodegraph/db/dialects.py`
- Modify: `src/pycodegraph/db/queries.py`
- Test: `test_inferdb_queries.py`

- [ ] **Step 1: Add a dialect factory failure expectation**

Temporarily extend `test_backend_resolution` in `test_inferdb_queries.py`:

```python
from pycodegraph.db.dialects import get_query_dialect


def test_backend_resolution() -> None:
    check(
        "mysql URL with backend=inferdb resolves to inferdb",
        resolve_backend_name("mysql+pymysql://u:p@localhost/db?backend=inferdb", "mysql") == "inferdb",
    )
    check(
        "inferdb query dialect is selected",
        get_query_dialect("inferdb").name == "inferdb",
    )
    check(
        "sqlite URL resolves to sqlite",
        resolve_backend_name("sqlite:////tmp/codegraph.db", "sqlite") == "sqlite",
    )
    check(
        "postgresql URL resolves to postgresql",
        resolve_backend_name("postgresql+psycopg://u:p@localhost/db", "postgresql") == "postgresql",
    )
```

- [ ] **Step 2: Run test to verify it fails on missing dialect**

Run:

```bash
uv run python test_inferdb_queries.py
```

Expected: FAIL because `get_query_dialect("inferdb").name` is not `inferdb`.

- [ ] **Step 3: Add MySQL helpers and InferDB dialect**

In `src/pycodegraph/db/dialects.py`, add import:

```python
from sqlalchemy.dialects.mysql import insert as mysql_insert
```

Add no-op hook to `QueryDialect`:

```python
def after_nodes_changed(self, conn: Connection) -> None:
    return None
```

Add this class after `PostgreSQLQueryDialect`:

```python
class InferDBQueryDialect(QueryDialect):
    name = "inferdb"

    def insert_nodes_ignore(self):
        return mysql_insert(nodes).prefix_with("IGNORE")

    def upsert_file(self, row: dict[str, Any]):
        stmt = mysql_insert(files).values(**row)
        update_row = {k: stmt.inserted[k] for k in row if k != "path"}
        return stmt.on_duplicate_key_update(**update_row)

    def find_edges_between_nodes(
        self,
        conn: Connection,
        node_ids: list[str],
        kinds: Optional[list[str]] = None,
    ) -> list[tuple]:
        source_placeholders = ",".join(f":sid{i}" for i in range(len(node_ids)))
        target_placeholders = ",".join(f":tid{i}" for i in range(len(node_ids)))
        sql = (
            "SELECT source, target, kind, metadata, line, col, provenance FROM edges "
            f"WHERE source IN ({source_placeholders}) "
            f"AND target IN ({target_placeholders})"
        )
        params: dict[str, Any] = {}
        for i, node_id in enumerate(node_ids):
            params[f"sid{i}"] = node_id
            params[f"tid{i}"] = node_id
        if kinds:
            kind_placeholders = ",".join(f":k{i}" for i in range(len(kinds)))
            sql += f" AND kind IN ({kind_placeholders})"
            for i, kind in enumerate(kinds):
                params[f"k{i}"] = kind
        return conn.execute(text(sql), params).fetchall()

    def after_nodes_changed(self, conn: Connection) -> None:
        database = conn.engine.url.database
        conn.execute(text(
            "/*+ duck_execute */ "
            f"PRAGMA create_fts_index('ltmdb_sql.{database}.nodes', 'id', 'fts_text')"
        ))

    def search_nodes_fts(
        self,
        conn: Connection,
        query_text: str,
        kinds: Optional[list[str]],
        languages: Optional[list[str]],
        limit: int,
        offset: int,
    ) -> list[tuple]:
        database = conn.engine.url.database
        fts_limit = max(limit * 5, 100)
        sql = (
            "/*+ duck_execute */ "
            "SELECT n.id, n.kind, n.name, n.qualified_name, n.file_path, n.language, "
            "n.start_line, n.end_line, n.start_column, n.end_column, "
            "n.docstring, n.signature, n.visibility, n.is_exported, n.is_async, "
            "n.is_static, n.is_abstract, n.decorators, n.type_parameters, n.updated_at, "
            f"ltmdb_sql.fts_{database}_nodes.match_bm25("
            "n.id, :query, fields := 'fts_text') AS score "
            f"FROM ltmdb_sql.{database}.nodes n "
            "WHERE score IS NOT NULL"
        )
        params: dict[str, Any] = {"query": query_text}
        sql, params = _append_filters(sql, params, kinds, languages)
        sql += " ORDER BY score DESC LIMIT :lim OFFSET :off"
        params["lim"] = fts_limit
        params["off"] = offset
        return conn.execute(text(sql), params).fetchall()
```

Update factory:

```python
def get_query_dialect(dialect_name: str) -> QueryDialect:
    if dialect_name == "sqlite":
        return SQLiteQueryDialect()
    if dialect_name == "postgresql":
        return PostgreSQLQueryDialect()
    if dialect_name == "inferdb":
        return InferDBQueryDialect()
    return QueryDialect()
```

- [ ] **Step 4: Pass logical backend to `QueryBuilder`**

In `src/pycodegraph/db/queries.py`, update `__init__`:

```python
def __init__(self, conn: Connection):
    self._conn = conn
    backend_name = conn.info.get("pycodegraph_backend", conn.engine.dialect.name)
    self._dialect = get_query_dialect(backend_name)
    self._node_cache = _LRUNodeCache()
```

In `DatabaseConnection.get_connection`, set connection info before returning:

```python
if self._connection is None:
    self._connection = self._engine.connect()
self._connection.info["pycodegraph_backend"] = self._backend_name
return self._connection
```

- [ ] **Step 5: Run dialect selection test**

Run:

```bash
uv run python test_inferdb_queries.py
```

Expected without `INFERDB_TEST_URL`: resolver and dialect checks pass, InferDB smoke is skipped.

- [ ] **Step 6: Commit query dialect**

```bash
git add src/pycodegraph/db/__init__.py src/pycodegraph/db/dialects.py src/pycodegraph/db/queries.py test_inferdb_queries.py
git commit -m "feat: add inferdb query dialect"
```

## Task 3: Populate `fts_text` and Refresh FTS After Node Changes

**Files:**
- Modify: `src/pycodegraph/db/queries.py`
- Test: `test_inferdb_queries.py`

- [ ] **Step 1: Add helper functions in `queries.py`**

Below `_REF_COLUMNS`, add:

```python
def _node_search_text(node: Node) -> str:
    return " ".join(
        part for part in (
            node.name,
            node.qualified_name,
            node.docstring,
            node.signature,
        )
        if part
    )


def _node_row(node: Node) -> dict:
    row = {
        "id": node.id,
        "kind": node.kind.value,
        "name": node.name,
        "qualified_name": node.qualified_name,
        "file_path": node.file_path,
        "language": node.language.value,
        "start_line": node.start_line,
        "end_line": node.end_line,
        "start_column": node.start_column,
        "end_column": node.end_column,
        "docstring": node.docstring,
        "signature": node.signature,
        "visibility": node.visibility,
        "is_exported": int(node.is_exported),
        "is_async": int(node.is_async),
        "is_static": int(node.is_static),
        "is_abstract": int(node.is_abstract),
        "decorators": node.decorators,
        "type_parameters": node.type_parameters,
        "updated_at": node.updated_at,
    }
    row["fts_text"] = _node_search_text(node)
    return row
```

- [ ] **Step 2: Let non-InferDB dialects strip unknown row columns**

In `src/pycodegraph/db/dialects.py`, add to `QueryDialect`:

```python
def prepare_node_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in row.items() if key != "fts_text"}
        for row in rows
    ]
```

Override in `InferDBQueryDialect`:

```python
def prepare_node_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return rows
```

- [ ] **Step 3: Replace duplicated node row construction**

In `insert_nodes`, replace the current list comprehension with:

```python
rows = self._dialect.prepare_node_rows([_node_row(n) for n in nodes_data])
```

In `bulk_insert`, replace the current node row list comprehension with the same pattern:

```python
rows = self._dialect.prepare_node_rows([_node_row(n) for n in nodes_data])
```

- [ ] **Step 4: Call FTS refresh hooks after node changes**

In `insert_nodes`, after `self._conn.execute(stmt, rows)`, call:

```python
self._dialect.after_nodes_changed(self._conn)
```

In `bulk_insert`, after all inserts and before `self._conn.commit()`, call:

```python
if nodes_data:
    self._dialect.after_nodes_changed(self._conn)
```

In `delete_file`, after executing delete:

```python
self._dialect.after_nodes_changed(self._conn)
```

In `delete_files_batch`, after all delete chunks:

```python
self._dialect.after_nodes_changed(self._conn)
```

- [ ] **Step 5: Run SQLite and PostgreSQL regression checks**

Run:

```bash
uv run python -m compileall -q src/pycodegraph
uv run python test_pg_queries.py
uv run python test_pg_verify.py
uv run python test_inferdb_queries.py
```

Expected:

- compileall exits 0.
- PG query script reports `44/44 passed`.
- PG verify script reports `88/88 passed`.
- InferDB script skips smoke when `INFERDB_TEST_URL` is unset.

- [ ] **Step 6: Run optional InferDB smoke**

If InferDB is available, run:

```bash
INFERDB_TEST_URL='mysql+pymysql://user:pass@host:port/db?backend=inferdb' uv run python test_inferdb_queries.py
```

Expected: backend checks pass, indexing succeeds, `QueryBuilder` is found by FTS search, stats show nodes.

- [ ] **Step 7: Commit FTS row and refresh hooks**

```bash
git add src/pycodegraph/db/queries.py src/pycodegraph/db/dialects.py test_inferdb_queries.py
git commit -m "feat: refresh inferdb fts after node changes"
```

## Task 4: Final Verification and Documentation Update

**Files:**
- Modify: `README.md`
- Verify: project scripts

- [ ] **Step 1: Document InferDB URL usage**

Add this short section to `README.md` near existing database usage:

```markdown
### InferDB backend

InferDB is supported as a MySQL-compatible relational backend with InferDB FTS.
Use a MySQL SQLAlchemy URL and mark the logical backend explicitly:

```bash
python -m pycodegraph.example /path/to/project \
  --db 'mysql+pymysql://user:pass@host:port/db?backend=inferdb'
```

The InferDB backend creates MySQL-compatible tables and uses InferDB's
`PRAGMA create_fts_index` / `match_bm25` support for symbol search.
```
```

- [ ] **Step 2: Run full available verification**

Run:

```bash
uv run python -m compileall -q src/pycodegraph
uv run python test_pg_queries.py
uv run python test_pg_verify.py
uv run python test_inferdb_queries.py
git diff --check
```

Expected:

- All commands exit 0.
- PG tests remain green.
- InferDB optional test skips when URL is absent.
- `git diff --check` reports no whitespace errors.

- [ ] **Step 3: Inspect public API stability**

Run:

```bash
rg "def (insert_nodes|insert_edges|insert_unresolved_refs_batch|get_file_by_path|get_all_files|upsert_file|delete_file|get_node_by_id|get_nodes_by_name|get_nodes_by_qualified_name|get_nodes_by_lower_name|get_nodes_by_file|get_nodes_by_kind|get_all_nodes|get_all_edges|get_callers|get_callees|get_outgoing_edges|get_incoming_edges|find_edges_between_nodes|search_nodes|find_nodes_by_exact_name|find_nodes_by_name_substring|get_unresolved_refs_count|get_all_unresolved_refs|delete_all_unresolved_refs|delete_unresolved_refs|get_unresolved_refs_batch|delete_specific_resolved_refs|get_all_file_paths|get_all_file_paths_indexed|delete_files_batch|bulk_insert|get_all_node_names|get_stats|clear_cache)\\(" -n src/pycodegraph/db/queries.py
```

Expected: method names are unchanged from the existing `QueryBuilder` API.

- [ ] **Step 4: Commit docs and final verification**

```bash
git add README.md
git commit -m "docs: document inferdb backend"
```

## Self-Review

- Spec coverage: backend selection, schema, dialect DML, FTS lifecycle, FTS query, testing, and API stability are covered by tasks.
- Placeholder scan: no TBD/TODO placeholders remain.
- Type consistency: new functions are `resolve_backend_name`, `InferDBQueryDialect`, `prepare_node_rows`, `after_nodes_changed`, `_node_row`, and `_node_search_text`; all later task references match these names.
