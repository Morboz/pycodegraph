# InferDB Support Design

## Goal

Add InferDB as a third database backend for pycodegraph. InferDB should use MySQL-compatible relational tables for durable storage and InferDB's DuckDB-backed FTS index for search. Existing `QueryBuilder` public methods must remain unchanged.

## Backend Selection

InferDB will be selected explicitly, not inferred from every SQLAlchemy `mysql` dialect connection. The recommended URL form is:

```text
mysql+pymysql://user:pass@host:port/db?backend=inferdb
```

The database layer will resolve a logical backend name separately from SQLAlchemy's driver dialect:

- `sqlite` for SQLite URLs.
- `postgresql` for PostgreSQL URLs.
- `inferdb` for MySQL URLs with `backend=inferdb`.
- Plain MySQL remains unsupported until it has a separate compatibility contract.

## Schema

InferDB will not use `metadata.create_all()` because the current generic SQLAlchemy table definitions include `TEXT` primary keys, which are not valid for MySQL-style table creation. Instead, initialization will be split by backend:

- SQLite keeps the current metadata-based schema plus SQLite FTS setup.
- PostgreSQL keeps the current metadata-based schema plus PostgreSQL FTS setup.
- InferDB gets explicit MySQL-compatible DDL.

InferDB table columns will preserve the existing logical model while using bounded key columns:

- `nodes.id VARCHAR(512) PRIMARY KEY`
- `nodes.name VARCHAR(512)`
- `nodes.qualified_name VARCHAR(2048)`
- `nodes.file_path VARCHAR(2048)`
- `edges.id BIGINT AUTO_INCREMENT PRIMARY KEY`
- `files.path VARCHAR(2048) PRIMARY KEY`
- JSON-like values remain `TEXT` for portability.

The `nodes` table will add an InferDB-only `fts_text TEXT` column containing the searchable text built from:

```text
name qualified_name docstring signature
```

## Query Dialect

An `InferDBQueryDialect` will be added alongside the existing SQLite and PostgreSQL query dialects. It will implement:

- `insert_nodes_ignore` using MySQL-compatible insert-ignore behavior.
- `upsert_file` using `ON DUPLICATE KEY UPDATE`.
- `find_edges_between_nodes` using MySQL-compatible `IN (...)` predicates.
- `search_nodes_fts` using InferDB `match_bm25` over the `nodes` FTS index.

The `QueryBuilder` API stays stable. Internally, it will ask for a logical backend dialect instead of relying only on `conn.engine.dialect.name`.

## FTS Lifecycle

InferDB FTS will be refreshed after node-changing operations, not per row:

- `insert_nodes`
- `bulk_insert`
- `delete_file`
- `delete_files_batch`

The dialect interface will gain an `after_nodes_changed(conn)` hook. SQLite and PostgreSQL implement it as a no-op. InferDB executes:

```sql
/*+ duck_execute */
PRAGMA create_fts_index('ltmdb_sql.<database>.nodes', 'id', 'fts_text')
```

FTS query shape:

```sql
/*+ duck_execute */
SELECT n.id, ..., ltmdb_sql.fts_<database>_nodes.match_bm25(
  n.id,
  :query,
  fields := 'fts_text'
) AS score
FROM ltmdb_sql.<database>.nodes n
WHERE score IS NOT NULL
ORDER BY score DESC
LIMIT :lim OFFSET :off
```

## Testing

Add an InferDB smoke test mirroring the PostgreSQL query tests:

- initialize schema
- bulk index a small project
- exact node lookup
- duplicate node insert ignored
- file upsert updates
- `find_edges_between_nodes` returns expected edges
- FTS search returns known symbols
- deleting a file refreshes FTS results

The test should read the connection URL from an environment variable so it does not require InferDB in normal local runs.

## Non-Goals

- Plain MySQL support without InferDB FTS.
- Replacing SQLite or PostgreSQL FTS behavior.
- Changing `QueryBuilder` public methods or result models.
