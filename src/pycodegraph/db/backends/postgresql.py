"""PostgreSQL backend for pycodegraph."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import Connection, Engine, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..backend import Backend, register_backend
from ..tables import files, metadata, nodes


@register_backend
class PostgreSQLBackend(Backend):
    """Backend for PostgreSQL — tsvector FTS, GIN index, ANY-array queries."""

    name = "postgresql"

    # -------------------------------------------------------------------
    # Schema lifecycle
    # -------------------------------------------------------------------

    @classmethod
    def configure_engine(cls, engine: Engine) -> None:
        """PostgreSQL needs no extra engine configuration."""

    @classmethod
    def initialize_schema(cls, engine: Engine) -> None:
        with engine.begin() as conn:
            metadata.create_all(conn)
            conn.execute(
                text(
                    "INSERT INTO schema_versions (version, applied_at, description)"
                    " VALUES (1, :ts, 'Initial schema') ON CONFLICT DO NOTHING"
                ),
                {"ts": int(time.time() * 1000)},
            )
        _init_postgresql_fts(engine)

    # -------------------------------------------------------------------
    # Query dialect
    # -------------------------------------------------------------------

    def insert_nodes_ignore(self):
        return pg_insert(nodes).on_conflict_do_nothing(index_elements=["id"])

    def upsert_file(self, row: dict[str, Any]):
        return (
            pg_insert(files)
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

        return list(conn.execute(text(sql), params).fetchall())

    def search_claims_fts(
        self,
        conn: Connection,
        query_text: str,
        claim_type: str | None,
        limit: int,
    ) -> list[Any]:
        sql = (
            "SELECT sc.id, sc.claim_type, sc.claim_text, "
            "ts_rank(sc.fts, sub.ts_q) as score "
            "FROM summary_claims sc, "
            "(SELECT replace(plainto_tsquery('english', :query)::text, '&', '|')::tsquery AS ts_q) sub "
            "WHERE sc.fts @@ sub.ts_q"
        )
        params: dict[str, Any] = {"query": query_text}
        if claim_type:
            sql += " AND sc.claim_type = :ct"
            params["ct"] = claim_type
        sql += " ORDER BY score DESC LIMIT :lim"
        params["lim"] = limit
        return list(conn.execute(text(sql), params).fetchall())

    def after_nodes_changed(self, conn: Connection) -> None:
        """PostgreSQL tsvector auto-updates via GENERATED column — no-op."""

    def prepare_node_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """PostgreSQL nodes table has no ``fts_text`` column — strip it."""
        return [
            {key: value for key, value in row.items() if key != "fts_text"}
            for row in rows
        ]


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _init_postgresql_fts(engine: Engine) -> None:
    """Create tsvector columns, GIN indexes, and auto-update trigger for PostgreSQL."""
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        conn.execute(
            text(
                "ALTER TABLE nodes ADD COLUMN IF NOT EXISTS fts tsvector"
                " GENERATED ALWAYS AS ("
                "  setweight(to_tsvector('simple', coalesce(name, '')), 'A') ||"
                "  setweight(to_tsvector('simple', coalesce(qualified_name, '')), 'B') ||"
                "  setweight(to_tsvector('simple', coalesce(docstring, '')), 'C') ||"
                "  setweight(to_tsvector('simple', coalesce(signature, '')), 'D')"
                " ) STORED"
            )
        )
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS idx_nodes_fts ON nodes USING GIN (fts)")
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_nodes_lower_name ON nodes (lower(name))"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_nodes_name_trgm ON nodes USING GIN (name gin_trgm_ops)"
            )
        )
        _init_postgresql_claims_fts(conn)


def _init_postgresql_claims_fts(conn: Connection) -> None:
    """Create the Summary Claims tsvector (english stemming) + GIN index.

    Mirrors :func:`_init_postgresql_fts`'s nodes pattern: a GENERATED tsvector
    column over ``summary_claims.claim_text`` kept fresh automatically by PG
    (no triggers needed). The ``'english'`` configuration stems claim text so
    paraphrased natural-language queries match (e.g. ``\"decompression\"`` vs
    ``\"decompress\"``), equivalent to SQLite's ``porter`` tokenizer.
    """
    conn.execute(
        text(
            "ALTER TABLE summary_claims ADD COLUMN IF NOT EXISTS fts tsvector"
            " GENERATED ALWAYS AS ("
            "  to_tsvector('english', coalesce(claim_text, ''))"
            " ) STORED"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_summary_claims_fts"
            " ON summary_claims USING GIN (fts)"
        )
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
