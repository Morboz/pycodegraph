"""SQLite backend for pycodegraph."""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import Connection, Engine, event, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..backend import Backend, register_backend
from ..tables import files, metadata, nodes


@register_backend
class SQLiteBackend(Backend):
    """Backend for SQLite — FTS5 virtual table, WAL mode, JSON-each queries."""

    name = "sqlite"

    # -------------------------------------------------------------------
    # Schema lifecycle
    # -------------------------------------------------------------------

    @classmethod
    def configure_engine(cls, engine: Engine) -> None:
        """Apply SQLite performance PRAGMAs on each new connection."""
        event.listen(engine, "connect", _apply_sqlite_pragmas)

    @classmethod
    def initialize_schema(cls, engine: Engine) -> None:
        with engine.begin() as conn:
            metadata.create_all(conn)
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO schema_versions (version, applied_at, description)"
                    " VALUES (1, :ts, 'Initial schema')"
                ),
                {"ts": int(time.time() * 1000)},
            )
        _init_sqlite_fts(engine)

    # -------------------------------------------------------------------
    # Query dialect
    # -------------------------------------------------------------------

    def insert_nodes_ignore(self):
        return sqlite_insert(nodes).on_conflict_do_nothing(index_elements=["id"])

    def upsert_file(self, row: dict[str, Any]):
        return (
            sqlite_insert(files)
            .values(**row)
            .on_conflict_do_update(
                index_elements=["path"],
                set_=row,
            )
        )

    def find_edges_between_nodes(
        self,
        conn: Connection,
        node_ids: list[str],
        kinds: list[str] | None = None,
    ) -> list[Any]:
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
        fts_terms = _build_fts_match(query_text)
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

        return list(conn.execute(text(sql), params).fetchall())

    def search_claims_fts(
        self,
        conn: Connection,
        query_text: str,
        claim_type: str | None,
        limit: int,
    ) -> list[Any]:
        fts_terms = _build_fts_match(query_text)
        if not fts_terms:
            return []

        sql = (
            "SELECT sc.id, sc.claim_type, sc.claim_text, "
            "bm25(claims_fts) as score "
            "FROM claims_fts fts JOIN summary_claims sc ON sc.rowid = fts.rowid "
            "WHERE claims_fts MATCH :match"
        )
        params: dict[str, Any] = {"match": fts_terms}
        if claim_type:
            sql += " AND sc.claim_type = :ct"
            params["ct"] = claim_type
        sql += " ORDER BY score LIMIT :lim"
        params["lim"] = limit
        return list(conn.execute(text(sql), params).fetchall())

    def after_nodes_changed(self, conn: Connection) -> None:
        """SQLite FTS5 auto-syncs via triggers — no-op."""

    def prepare_node_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """SQLite nodes table has no ``fts_text`` column — strip it."""
        return [
            {key: value for key, value in row.items() if key != "fts_text"}
            for row in rows
        ]


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _apply_sqlite_pragmas(dbapi_connection, connection_record):
    """Apply SQLite performance PRAGMAs on each new connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA busy_timeout = 120000")
    cursor.execute("PRAGMA synchronous = NORMAL")
    cursor.execute("PRAGMA cache_size = -64000")
    cursor.execute("PRAGMA temp_store = MEMORY")
    cursor.execute("PRAGMA mmap_size = 268435456")
    cursor.close()


def _init_sqlite_fts(engine: Engine) -> None:
    """Create FTS5 virtual table and sync triggers for SQLite."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5("
                "id, name, qualified_name, docstring, signature,"
                "content='nodes', content_rowid='rowid')"
            )
        )
        conn.execute(
            text(
                "CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN"
                "  INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)"
                "  VALUES (NEW.rowid, NEW.id, NEW.name, NEW.qualified_name, NEW.docstring, NEW.signature);"
                "END"
            )
        )
        conn.execute(
            text(
                "CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN"
                "  INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature)"
                "  VALUES ('delete', OLD.rowid, OLD.id, OLD.name, OLD.qualified_name, OLD.docstring, OLD.signature);"
                "END"
            )
        )
        conn.execute(
            text(
                "CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE ON nodes BEGIN"
                "  INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature)"
                "  VALUES ('delete', OLD.rowid, OLD.id, OLD.name, OLD.qualified_name, OLD.docstring, OLD.signature);"
                "  INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)"
                "  VALUES (NEW.rowid, NEW.id, NEW.name, NEW.qualified_name, NEW.docstring, NEW.signature);"
                "END"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_nodes_lower_name ON nodes(lower(name))"
            )
        )
        _init_claims_fts(conn)


def _init_claims_fts(conn: Connection) -> None:
    """Create the Summary Claims FTS5 index (porter stemming) and sync triggers.

    Mirrors :func:`_init_sqlite_fts`'s nodes_fts pattern: an external-content
    FTS5 table over ``summary_claims`` kept in sync by AFTER INSERT/DELETE/
    UPDATE triggers. Only ``claim_text`` is indexed — the claim ``id`` is a
    UUID hex string that can otherwise prefix-match natural-language query
    terms (e.g. ``face``, ``dead``). The ``porter`` tokenizer stems claim text
    so paraphrased natural-language queries match (e.g. "decompression" vs
    "decompress").
    """
    conn.execute(
        text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5("
            "claim_text,"
            "content='summary_claims', content_rowid='rowid', tokenize='porter')"
        )
    )
    conn.execute(
        text(
            "CREATE TRIGGER IF NOT EXISTS claims_ai AFTER INSERT ON summary_claims BEGIN"
            "  INSERT INTO claims_fts(rowid, claim_text)"
            "  VALUES (NEW.rowid, NEW.claim_text);"
            "END"
        )
    )
    conn.execute(
        text(
            "CREATE TRIGGER IF NOT EXISTS claims_ad AFTER DELETE ON summary_claims BEGIN"
            "  INSERT INTO claims_fts(claims_fts, rowid, claim_text)"
            "  VALUES ('delete', OLD.rowid, OLD.claim_text);"
            "END"
        )
    )
    conn.execute(
        text(
            "CREATE TRIGGER IF NOT EXISTS claims_au AFTER UPDATE ON summary_claims BEGIN"
            "  INSERT INTO claims_fts(claims_fts, rowid, claim_text)"
            "  VALUES ('delete', OLD.rowid, OLD.claim_text);"
            "  INSERT INTO claims_fts(rowid, claim_text)"
            "  VALUES (NEW.rowid, NEW.claim_text);"
            "END"
        )
    )


def _build_fts_match(query_text: str) -> str:
    """Build an FTS5 MATCH expression: each non-keyword term as a prefix term.

    Returns an empty string when *query_text* has no usable terms, signalling
    the caller to short-circuit with no results.
    """
    return " OR ".join(
        f'"{term}"*'
        for term in query_text.split()
        if term and term.upper() not in ("AND", "OR", "NOT", "NEAR")
    )


def _append_filters(
    sql: str,
    params: dict[str, Any],
    kinds: list[str] | None,
    languages: list[str] | None,
) -> tuple[str, dict[str, Any]]:
    """Append kind/language filters to an FTS SQL query."""
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
