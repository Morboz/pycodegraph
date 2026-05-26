"""Dialect-specific query fragments for SQLAlchemy Core operations."""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import Connection, text
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


def get_query_dialect(dialect_name: str) -> QueryDialect:
    if dialect_name == "sqlite":
        return SQLiteQueryDialect()
    if dialect_name == "postgresql":
        return PostgreSQLQueryDialect()
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
