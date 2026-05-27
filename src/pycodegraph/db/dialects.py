"""Dialect-specific query fragments for SQLAlchemy Core operations."""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import Connection, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .tables import files, nodes


class QueryDialect:
    """Base implementation for database-specific query behavior."""

    name = "default"

    def insert_nodes_ignore(self):
        raise NotImplementedError(f"{self.name} does not support insert_nodes_ignore")

    def upsert_file(self, row: dict[str, Any]):
        raise NotImplementedError(f"{self.name} does not support upsert_file")

    def find_edges_between_nodes(
        self,
        conn: Connection,
        node_ids: list[str],
        kinds: Optional[list[str]] = None,
    ) -> list[tuple]:
        raise NotImplementedError(f"{self.name} does not support find_edges_between_nodes")

    def search_nodes_fts(
        self,
        conn: Connection,
        query_text: str,
        kinds: Optional[list[str]],
        languages: Optional[list[str]],
        limit: int,
        offset: int,
    ) -> list[tuple]:
        return []

    def after_nodes_changed(self, conn: Connection) -> None:
        """Hook for dialects that maintain external node search indexes."""
        return None

    def prepare_node_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Prepare node insert rows for dialect-specific columns."""
        return [
            {key: value for key, value in row.items() if key != "fts_text"}
            for row in rows
        ]


class SQLiteQueryDialect(QueryDialect):
    name = "sqlite"

    def insert_nodes_ignore(self):
        return sqlite_insert(nodes).on_conflict_do_nothing(index_elements=["id"])

    def upsert_file(self, row: dict[str, Any]):
        return sqlite_insert(files).values(**row).on_conflict_do_update(
            index_elements=["path"],
            set_=row,
        )

    def find_edges_between_nodes(
        self,
        conn: Connection,
        node_ids: list[str],
        kinds: Optional[list[str]] = None,
    ) -> list[tuple]:
        ids_json = json.dumps(node_ids)
        sql = (
            "SELECT source, target, kind, metadata, line, col, provenance FROM edges "
            "WHERE source IN (SELECT value FROM json_each(:ids)) "
            "AND target IN (SELECT value FROM json_each(:ids2))"
        )
        params: dict[str, Any] = {"ids": ids_json, "ids2": ids_json}
        if kinds:
            placeholders = ",".join(f":k{i}" for i in range(len(kinds)))
            sql += f" AND kind IN ({placeholders})"
            for i, kind in enumerate(kinds):
                params[f"k{i}"] = kind
        return conn.execute(text(sql), params).fetchall()

    def search_nodes_fts(
        self,
        conn: Connection,
        query_text: str,
        kinds: Optional[list[str]],
        languages: Optional[list[str]],
        limit: int,
        offset: int,
    ) -> list[tuple]:
        fts_terms = " OR ".join(
            f'"{term}"*'
            for term in query_text.split()
            if term and term.upper() not in ("AND", "OR", "NOT", "NEAR")
        )
        if not fts_terms:
            return []

        fts_limit = max(limit * 5, 100)
        sql = (
            "SELECT n.id, n.kind, n.name, n.qualified_name, n.file_path, n.language, "
            "n.start_line, n.end_line, n.start_column, n.end_column, "
            "n.docstring, n.signature, n.visibility, n.is_exported, n.is_async, "
            "n.is_static, n.is_abstract, n.decorators, n.type_parameters, n.updated_at, "
            "bm25(nodes_fts, 0, 20, 5, 1, 2) as score "
            "FROM nodes_fts fts JOIN nodes n ON n.id = fts.id "
            "WHERE nodes_fts MATCH :match"
        )
        params: dict[str, Any] = {"match": fts_terms}
        sql, params = _append_filters(sql, params, kinds, languages)
        sql += " ORDER BY score LIMIT :lim OFFSET :off"
        params["lim"] = fts_limit
        params["off"] = offset

        return conn.execute(text(sql), params).fetchall()


class PostgreSQLQueryDialect(QueryDialect):
    name = "postgresql"

    def insert_nodes_ignore(self):
        return pg_insert(nodes).on_conflict_do_nothing(index_elements=["id"])

    def upsert_file(self, row: dict[str, Any]):
        return pg_insert(files).values(**row).on_conflict_do_update(
            index_elements=["path"],
            set_=row,
        )

    def find_edges_between_nodes(
        self,
        conn: Connection,
        node_ids: list[str],
        kinds: Optional[list[str]] = None,
    ) -> list[tuple]:
        sql = (
            "SELECT source, target, kind, metadata, line, col, provenance FROM edges "
            "WHERE source = ANY(:ids) AND target = ANY(:ids2)"
        )
        params: dict[str, Any] = {"ids": node_ids, "ids2": node_ids}
        if kinds:
            placeholders = ",".join(f":k{i}" for i in range(len(kinds)))
            sql += f" AND kind IN ({placeholders})"
            for i, kind in enumerate(kinds):
                params[f"k{i}"] = kind
        return conn.execute(text(sql), params).fetchall()

    def search_nodes_fts(
        self,
        conn: Connection,
        query_text: str,
        kinds: Optional[list[str]],
        languages: Optional[list[str]],
        limit: int,
        offset: int,
    ) -> list[tuple]:
        fts_limit = max(limit * 5, 100)
        sql = (
            "SELECT n.id, n.kind, n.name, n.qualified_name, n.file_path, n.language, "
            "n.start_line, n.end_line, n.start_column, n.end_column, "
            "n.docstring, n.signature, n.visibility, n.is_exported, n.is_async, "
            "n.is_static, n.is_abstract, n.decorators, n.type_parameters, n.updated_at, "
            "ts_rank(n.fts, ts_q) as score "
            "FROM nodes n, "
            "(SELECT replace(plainto_tsquery('simple', :query)::text, '&', '|')::tsquery AS ts_q) sub "
            "WHERE n.fts @@ sub.ts_q"
        )
        params: dict[str, Any] = {"query": query_text}
        sql, params = _append_filters(sql, params, kinds, languages)
        sql += " ORDER BY score DESC LIMIT :lim OFFSET :off"
        params["lim"] = fts_limit
        params["off"] = offset

        return conn.execute(text(sql), params).fetchall()


class InferDBQueryDialect(QueryDialect):
    name = "inferdb"
    _fts_table = "pycodegraph_nodes_fts"

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
        return mysql_insert(files).values(**row).on_duplicate_key_update(**update_values)

    def find_edges_between_nodes(
        self,
        conn: Connection,
        node_ids: list[str],
        kinds: Optional[list[str]] = None,
    ) -> list[tuple]:
        ids_placeholders = ",".join(f":id{i}" for i in range(len(node_ids)))
        sql = (
            "SELECT source, target, kind, metadata, line, col, provenance FROM edges "
            f"WHERE source IN ({ids_placeholders}) AND target IN ({ids_placeholders})"
        )
        params: dict[str, Any] = {f"id{i}": node_id for i, node_id in enumerate(node_ids)}
        if kinds:
            placeholders = ",".join(f":k{i}" for i in range(len(kinds)))
            sql += f" AND kind IN ({placeholders})"
            for i, kind in enumerate(kinds):
                params[f"k{i}"] = kind
        return conn.execute(text(sql), params).fetchall()

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
        fts_params: dict[str, Any] = {"query": query_text, "lim": fts_limit, "off": offset}
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
        params: dict[str, Any] = {f"id{i}": node_id for i, node_id in enumerate(node_ids)}
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
        database = conn.engine.url.database
        if not database:
            return
        database_identifier = _duck_identifier(database)
        fts_table_identifier = _duck_identifier(self._fts_table)
        qualified_table = f"ltmdb_sql.{database_identifier}.{fts_table_identifier}"
        _exec_raw_driver_sql(conn, f"/*+ duck_execute */ DROP TABLE IF EXISTS {qualified_table}")
        _exec_raw_driver_sql(
            conn,
            f"/*+ duck_execute */ CREATE TABLE {qualified_table} ("
            "seq_id INTEGER, node_id VARCHAR, fts_text VARCHAR)"
        )
        rows = conn.execute(text(
            "SELECT id, fts_text FROM nodes WHERE fts_text IS NOT NULL AND fts_text != ''"
        )).fetchall()
        for start in range(0, len(rows), 500):
            chunk = rows[start:start + 500]
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
                    f"(seq_id, node_id, fts_text) VALUES {values}"
                )
        if not rows:
            return
        table_name = _sql_string_literal(f"ltmdb_sql.{database}.{self._fts_table}")
        _exec_raw_driver_sql(
            conn,
            f"/*+ duck_execute */ PRAGMA create_fts_index({table_name}, 'seq_id', 'fts_text', overwrite=1)"
        )

    def prepare_node_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return rows


def get_query_dialect(dialect_name: str) -> QueryDialect:
    if dialect_name == "sqlite":
        return SQLiteQueryDialect()
    if dialect_name == "postgresql":
        return PostgreSQLQueryDialect()
    if dialect_name == "inferdb":
        return InferDBQueryDialect()
    return QueryDialect()


def _append_filters(
    sql: str,
    params: dict[str, Any],
    kinds: Optional[list[str]],
    languages: Optional[list[str]],
) -> tuple[str, dict[str, Any]]:
    if kinds:
        placeholders = ",".join(f":k{i}" for i in range(len(kinds)))
        sql += f" AND n.kind IN ({placeholders})"
        for i, kind in enumerate(kinds):
            params[f"k{i}"] = kind
    if languages:
        placeholders = ",".join(f":l{i}" for i in range(len(languages)))
        sql += f" AND n.language IN ({placeholders})"
        for i, language in enumerate(languages):
            params[f"l{i}"] = language
    return sql, params


def _duck_identifier(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) + chr(34))}"'


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _exec_raw_driver_sql(conn: Connection, sql: str) -> None:
    """Execute SQL without SQLAlchemy or DBAPI parameter parsing."""
    with conn.connection.driver_connection.cursor() as cursor:
        cursor.execute(sql)
