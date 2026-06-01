"""InferDB backend for pycodegraph."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import Connection, Engine, text
from sqlalchemy.dialects.mysql import insert as mysql_insert

from ..backend import Backend, register_backend
from ..tables import files


@register_backend
class InferDBBackend(Backend):
    """Backend for InferDB — MySQL-compatible DDL, DuckDB-side FTS."""

    name = "inferdb"
    _fts_table = "pycodegraph_nodes_fts"

    # -------------------------------------------------------------------
    # Schema lifecycle
    # -------------------------------------------------------------------

    @classmethod
    def configure_engine(cls, engine: Engine) -> None:
        """InferDB needs no extra engine configuration."""

    @classmethod
    def initialize_schema(cls, engine: Engine) -> None:
        ensure_inferdb_duck_schema(engine)
        _init_inferdb_schema(engine)

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    @staticmethod
    def ensure_inferdb_duck_schema(engine: Engine, database: str | None = None) -> None:
        """Ensure InferDB's DuckDB catalog has ``ltmdb_sql.<database>``."""
        ensure_inferdb_duck_schema(engine, database)

    @staticmethod
    def drop_inferdb_duck_schema(engine: Engine, database: str) -> None:
        """Drop InferDB's DuckDB catalog ``ltmdb_sql.<database>``."""
        drop_inferdb_duck_schema(engine, database)

    # -------------------------------------------------------------------
    # Query dialect
    # -------------------------------------------------------------------

    def insert_nodes_ignore(self):
        return text(
            "INSERT IGNORE INTO nodes ("
            "id, kind, name, qualified_name, file_path, language, "
            "start_line, end_line, start_column, end_column, "
            "docstring, signature, visibility, is_exported, is_async, "
            "is_static, is_abstract, decorators, type_parameters, updated_at, "
            "fts_text"
            ") VALUES ("
            ":id, :kind, :name, :qualified_name, :file_path, :language, "
            ":start_line, :end_line, :start_column, :end_column, "
            ":docstring, :signature, :visibility, :is_exported, :is_async, "
            ":is_static, :is_abstract, :decorators, :type_parameters, :updated_at, "
            ":fts_text"
            ")"
        )

    def upsert_file(self, row: dict[str, Any]):
        update_values = {key: value for key, value in row.items() if key != "path"}
        return (
            mysql_insert(files).values(**row).on_duplicate_key_update(**update_values)
        )

    def find_edges_between_nodes(
        self,
        conn: Connection,
        node_ids: list[str],
        kinds: list[str] | None = None,
    ) -> list[Any]:
        ids_placeholders = ",".join(f":id{i}" for i in range(len(node_ids)))
        sql = (
            "SELECT source, target, kind, metadata, line, col, provenance FROM edges "
            f"WHERE source IN ({ids_placeholders}) AND target IN ({ids_placeholders})"
        )
        params: dict[str, Any] = {
            f"id{i}": node_id for i, node_id in enumerate(node_ids)
        }
        if kinds:
            placeholders = ",".join(f":k{i}" for i in range(len(kinds)))
            sql += f" AND kind IN ({placeholders})"
            for i, kind in enumerate(kinds):
                params[f"k{i}"] = kind
        return list(conn.execute(text(sql), params).fetchall())

    def search_nodes_fts(
        self,
        conn: Connection,
        query_text: str,
        kinds: list[str] | None,
        languages: list[str] | None,
        limit: int,
        offset: int,
    ) -> list[Any]:
        database = conn.engine.url.database
        if not database:
            return []
        database_identifier = _duck_identifier(database)
        fts_database_identifier = _duck_identifier(f"fts_{database}_{self._fts_table}")
        fts_table_identifier = _duck_identifier(self._fts_table)
        fts_limit = max(limit * 5, 100)
        fts_sql = (
            "/*+ duck_execute */ SELECT "
            "node_id, "
            f"ltmdb_sql.{fts_database_identifier}.match_bm25("
            "seq_id, :query, fields := 'fts_text') AS score "
            f"FROM ltmdb_sql.{database_identifier}.{fts_table_identifier} "
            "WHERE score IS NOT NULL"
        )
        fts_params: dict[str, Any] = {
            "query": query_text,
            "lim": fts_limit,
            "off": offset,
        }
        fts_sql += " ORDER BY score DESC LIMIT :lim OFFSET :off"
        matches = conn.execute(text(fts_sql), fts_params).fetchall()
        if not matches:
            return []

        node_ids = [row[0] for row in matches]
        scores = {row[0]: row[1] for row in matches}
        placeholders = ",".join(f":id{i}" for i in range(len(node_ids)))
        sql = (
            "SELECT id, kind, name, qualified_name, file_path, language, "
            "start_line, end_line, start_column, end_column, "
            "docstring, signature, visibility, is_exported, is_async, "
            "is_static, is_abstract, decorators, type_parameters, updated_at "
            f"FROM nodes WHERE id IN ({placeholders})"
        )
        params: dict[str, Any] = {
            f"id{i}": node_id for i, node_id in enumerate(node_ids)
        }
        if kinds:
            placeholders = ",".join(f":k{i}" for i in range(len(kinds)))
            sql += f" AND kind IN ({placeholders})"
            for i, kind in enumerate(kinds):
                params[f"k{i}"] = kind
        if languages:
            placeholders = ",".join(f":l{i}" for i in range(len(languages)))
            sql += f" AND language IN ({placeholders})"
            for i, language in enumerate(languages):
                params[f"l{i}"] = language

        rows = conn.execute(text(sql), params).fetchall()
        row_by_id = {row[0]: tuple(row) for row in rows}
        return [
            row_by_id[node_id] + (scores[node_id],)
            for node_id in node_ids
            if node_id in row_by_id
        ]

    def after_nodes_changed(self, conn: Connection) -> None:
        """Rebuild DuckDB-side FTS index from scratch."""
        database = conn.engine.url.database
        if not database:
            return
        database_identifier = _duck_identifier(database)
        fts_table_identifier = _duck_identifier(self._fts_table)
        qualified_table = f"ltmdb_sql.{database_identifier}.{fts_table_identifier}"
        _exec_raw_driver_sql(
            conn, f"/*+ duck_execute */ DROP TABLE IF EXISTS {qualified_table}"
        )
        _exec_raw_driver_sql(
            conn,
            f"/*+ duck_execute */ CREATE TABLE {qualified_table} ("
            "seq_id INTEGER, node_id VARCHAR, fts_text VARCHAR)",
        )
        rows = conn.execute(
            text(
                "SELECT id, fts_text FROM nodes WHERE fts_text IS NOT NULL AND fts_text != ''"
            )
        ).fetchall()
        for start in range(0, len(rows), 500):
            chunk = rows[start : start + 500]
            values = ", ".join(
                "("
                f"{start + i + 1}, "
                f"{_sql_string_literal(str(row[0]))}, "
                f"{_sql_string_literal(str(row[1]))}"
                ")"
                for i, row in enumerate(chunk)
            )
            if values:
                _exec_raw_driver_sql(
                    conn,
                    f"/*+ duck_execute */ INSERT INTO {qualified_table} "
                    f"(seq_id, node_id, fts_text) VALUES {values}",
                )
        if not rows:
            return
        table_name = _sql_string_literal(f"ltmdb_sql.{database}.{self._fts_table}")
        _exec_raw_driver_sql(
            conn,
            f"/*+ duck_execute */ PRAGMA create_fts_index({table_name}, 'seq_id', 'fts_text', overwrite=1)",
        )

    def prepare_node_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """InferDB nodes table has a ``fts_text`` column — keep it."""
        return rows


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _duck_identifier(identifier: str) -> str:
    """Quote a DuckDB/SQL identifier, doubling embedded quotes."""
    return '"' + identifier.replace('"', '""') + '"'


def _raw_driver_execute(engine: Engine, sql: str) -> None:
    """Execute *sql* via the raw DBAPI cursor on *engine*."""
    with engine.connect() as conn:
        raw = conn.connection.driver_connection
        assert raw is not None
        with raw.cursor() as cursor:
            cursor.execute(sql)


def _exec_raw_driver_sql(conn, sql: str) -> None:
    """Execute SQL without SQLAlchemy or DBAPI parameter parsing."""
    raw = conn.connection.driver_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(sql)


def _sql_string_literal(value: str) -> str:
    """Escape *value* as a SQL string literal (single-quoted)."""
    return "'" + value.replace("'", "''") + "'"


def ensure_inferdb_duck_schema(engine: Engine, database: str | None = None) -> None:
    """Ensure InferDB's DuckDB catalog has ``ltmdb_sql.<database>``."""
    if database is None:
        with engine.connect() as conn:
            database = conn.engine.url.database
    if not database:
        return
    _raw_driver_execute(
        engine,
        f"/*+ duck_execute */ CREATE SCHEMA IF NOT EXISTS ltmdb_sql.{_duck_identifier(database)}",
    )


def drop_inferdb_duck_schema(engine: Engine, database: str) -> None:
    """Drop InferDB's DuckDB catalog ``ltmdb_sql.<database>``."""
    _raw_driver_execute(
        engine,
        f"/*+ duck_execute */ DROP SCHEMA IF EXISTS ltmdb_sql.{_duck_identifier(database)} CASCADE",
    )


def _init_inferdb_schema(engine: Engine) -> None:
    """Create MySQL-compatible tables for InferDB."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_versions ("
                "version INT PRIMARY KEY,"
                "applied_at BIGINT NOT NULL,"
                "description TEXT"
                ")"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS nodes ("
                "id VARCHAR(512) PRIMARY KEY,"
                "kind VARCHAR(64) NOT NULL,"
                "name VARCHAR(512) NOT NULL,"
                "qualified_name VARCHAR(2048) NOT NULL,"
                "file_path VARCHAR(768) NOT NULL,"
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
            )
        )
        conn.execute(
            text(
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
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS files ("
                "path VARCHAR(768) PRIMARY KEY,"
                "content_hash VARCHAR(128) NOT NULL,"
                "language VARCHAR(64) NOT NULL,"
                "size INT NOT NULL,"
                "modified_at DOUBLE NOT NULL,"
                "indexed_at BIGINT NOT NULL,"
                "node_count INT DEFAULT 0,"
                "errors TEXT"
                ")"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS unresolved_refs ("
                "id BIGINT AUTO_INCREMENT PRIMARY KEY,"
                "from_node_id VARCHAR(512) NOT NULL,"
                "reference_name TEXT NOT NULL,"
                "reference_kind VARCHAR(64) NOT NULL,"
                "line INT NOT NULL,"
                "col INT NOT NULL,"
                "candidates TEXT,"
                "file_path VARCHAR(768) NOT NULL DEFAULT '',"
                "language VARCHAR(64) NOT NULL DEFAULT 'unknown'"
                ")"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS project_metadata ("
                "`key` VARCHAR(255) PRIMARY KEY,"
                "value TEXT NOT NULL,"
                "updated_at BIGINT NOT NULL"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT IGNORE INTO schema_versions (version, applied_at, description)"
                " VALUES (1, :ts, 'Initial schema')"
            ),
            {"ts": int(time.time() * 1000)},
        )
