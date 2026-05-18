"""Query builder for SQLite CRUD operations."""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from ..types import Node, Edge, UnresolvedReference, FileRecord, Language


class QueryBuilder:
    def __init__(self, db: sqlite3.Connection):
        self._db = db
        self._prepare_statements()

    def _prepare_statements(self) -> None:
        self._insert_node = self._db.prepare(
            "INSERT OR IGNORE INTO nodes "
            "(id, kind, name, qualified_name, file_path, language, "
            "start_line, end_line, start_column, end_column, "
            "docstring, signature, visibility, is_exported, is_async, "
            "is_static, is_abstract, decorators, type_parameters, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        ) if hasattr(self._db, 'prepare') else None

    # --- Node operations ---

    def insert_nodes(self, nodes: list[Node]) -> None:
        rows = []
        for n in nodes:
            rows.append((
                n.id, n.kind.value, n.name, n.qualified_name,
                n.file_path, n.language.value,
                n.start_line, n.end_line, n.start_column, n.end_column,
                n.docstring, n.signature, n.visibility,
                int(n.is_exported), int(n.is_async), int(n.is_static), int(n.is_abstract),
                n.decorators, n.type_parameters, n.updated_at,
            ))
        self._db.executemany(
            "INSERT OR IGNORE INTO nodes "
            "(id, kind, name, qualified_name, file_path, language, "
            "start_line, end_line, start_column, end_column, "
            "docstring, signature, visibility, is_exported, is_async, "
            "is_static, is_abstract, decorators, type_parameters, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self._db.commit()

    def insert_edges(self, edges: list[Edge]) -> None:
        rows = []
        for e in edges:
            meta = e.metadata if isinstance(e.metadata, (str, type(None))) else json.dumps(e.metadata) if e.metadata else None
            rows.append((e.source, e.target, e.kind.value, meta, e.line, e.col, e.provenance))
        self._db.executemany(
            "INSERT INTO edges (source, target, kind, metadata, line, col, provenance) "
            "VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        self._db.commit()

    def insert_unresolved_refs_batch(self, refs: list[UnresolvedReference]) -> None:
        rows = []
        for r in refs:
            cands = r.candidates if isinstance(r.candidates, (str, type(None))) else json.dumps(r.candidates) if r.candidates else None
            rows.append((
                r.from_node_id, r.reference_name, r.reference_kind.value,
                r.line, r.column, cands, r.file_path, r.language,
            ))
        self._db.executemany(
            "INSERT INTO unresolved_refs "
            "(from_node_id, reference_name, reference_kind, line, col, candidates, file_path, language) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        self._db.commit()

    # --- File operations ---

    def get_file_by_path(self, file_path: str) -> Optional[FileRecord]:
        cur = self._db.execute(
            "SELECT path, content_hash, language, size, modified_at, indexed_at, node_count, errors "
            "FROM files WHERE path = ?",
            (file_path,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return FileRecord(
            path=row[0], content_hash=row[1], language=Language(row[2]),
            size=row[3], modified_at=row[4], indexed_at=row[5],
            node_count=row[6], errors=row[7],
        )

    def upsert_file(self, record: FileRecord) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO files "
            "(path, content_hash, language, size, modified_at, indexed_at, node_count, errors) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                record.path, record.content_hash, record.language.value,
                record.size, record.modified_at, record.indexed_at,
                record.node_count, record.errors,
            ),
        )
        self._db.commit()

    def delete_file(self, file_path: str) -> None:
        self._db.execute("DELETE FROM files WHERE path = ?", (file_path,))
        # Cascading deletes handle nodes, edges, unresolved_refs
        self._db.commit()

    # --- Query operations ---

    def get_nodes_by_name(self, name: str) -> list[Node]:
        cur = self._db.execute(
            "SELECT id, kind, name, qualified_name, file_path, language, "
            "start_line, end_line, start_column, end_column, "
            "docstring, signature, visibility, is_exported, is_async, "
            "is_static, is_abstract, decorators, type_parameters, updated_at "
            "FROM nodes WHERE name = ?",
            (name,),
        )
        return [self._row_to_node(r) for r in cur.fetchall()]

    def search_nodes(self, query: str, limit: int = 20) -> list[Node]:
        cur = self._db.execute(
            "SELECT n.id, n.kind, n.name, n.qualified_name, n.file_path, n.language, "
            "n.start_line, n.end_line, n.start_column, n.end_column, "
            "n.docstring, n.signature, n.visibility, n.is_exported, n.is_async, "
            "n.is_static, n.is_abstract, n.decorators, n.type_parameters, n.updated_at "
            "FROM nodes_fts fts JOIN nodes n ON n.id = fts.id "
            "WHERE nodes_fts MATCH ? LIMIT ?",
            (query, limit),
        )
        return [self._row_to_node(r) for r in cur.fetchall()]

    def get_node_by_id(self, node_id: str) -> Optional[Node]:
        cur = self._db.execute(
            "SELECT id, kind, name, qualified_name, file_path, language, "
            "start_line, end_line, start_column, end_column, "
            "docstring, signature, visibility, is_exported, is_async, "
            "is_static, is_abstract, decorators, type_parameters, updated_at "
            "FROM nodes WHERE id = ?",
            (node_id,),
        )
        row = cur.fetchone()
        return self._row_to_node(row) if row else None

    def get_callers(self, node_id: str) -> list[Edge]:
        cur = self._db.execute(
            "SELECT source, target, kind, metadata, line, col, provenance "
            "FROM edges WHERE target = ? AND kind = 'calls'",
            (node_id,),
        )
        return [self._row_to_edge(r) for r in cur.fetchall()]

    def get_callees(self, node_id: str) -> list[Edge]:
        cur = self._db.execute(
            "SELECT source, target, kind, metadata, line, col, provenance "
            "FROM edges WHERE source = ? AND kind = 'calls'",
            (node_id,),
        )
        return [self._row_to_edge(r) for r in cur.fetchall()]

    def get_unresolved_refs_count(self) -> int:
        cur = self._db.execute("SELECT COUNT(*) FROM unresolved_refs")
        return cur.fetchone()[0]

    def get_all_unresolved_refs(self, limit: int = 5000, offset: int = 0) -> list[UnresolvedReference]:
        cur = self._db.execute(
            "SELECT from_node_id, reference_name, reference_kind, line, col, "
            "candidates, file_path, language "
            "FROM unresolved_refs LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [self._row_to_ref(r) for r in cur.fetchall()]

    def delete_unresolved_refs(self, from_node_id: str, reference_name: str) -> None:
        self._db.execute(
            "DELETE FROM unresolved_refs WHERE from_node_id = ? AND reference_name = ?",
            (from_node_id, reference_name),
        )
        self._db.commit()

    def get_nodes_by_qualified_name(self, qualified_name: str) -> list[Node]:
        cur = self._db.execute(
            "SELECT id, kind, name, qualified_name, file_path, language, "
            "start_line, end_line, start_column, end_column, "
            "docstring, signature, visibility, is_exported, is_async, "
            "is_static, is_abstract, decorators, type_parameters, updated_at "
            "FROM nodes WHERE qualified_name = ?",
            (qualified_name,),
        )
        return [self._row_to_node(r) for r in cur.fetchall()]

    def get_nodes_by_lower_name(self, lower_name: str) -> list[Node]:
        cur = self._db.execute(
            "SELECT id, kind, name, qualified_name, file_path, language, "
            "start_line, end_line, start_column, end_column, "
            "docstring, signature, visibility, is_exported, is_async, "
            "is_static, is_abstract, decorators, type_parameters, updated_at "
            "FROM nodes WHERE lower(name) = ?",
            (lower_name,),
        )
        return [self._row_to_node(r) for r in cur.fetchall()]

    def get_nodes_by_file(self, file_path: str) -> list[Node]:
        cur = self._db.execute(
            "SELECT id, kind, name, qualified_name, file_path, language, "
            "start_line, end_line, start_column, end_column, "
            "docstring, signature, visibility, is_exported, is_async, "
            "is_static, is_abstract, decorators, type_parameters, updated_at "
            "FROM nodes WHERE file_path = ? ORDER BY start_line",
            (file_path,),
        )
        return [self._row_to_node(r) for r in cur.fetchall()]

    def get_nodes_by_kind(self, kind) -> list[Node]:
        kind_val = kind.value if hasattr(kind, "value") else str(kind)
        cur = self._db.execute(
            "SELECT id, kind, name, qualified_name, file_path, language, "
            "start_line, end_line, start_column, end_column, "
            "docstring, signature, visibility, is_exported, is_async, "
            "is_static, is_abstract, decorators, type_parameters, updated_at "
            "FROM nodes WHERE kind = ?",
            (kind_val,),
        )
        return [self._row_to_node(r) for r in cur.fetchall()]

    def get_all_file_paths(self) -> list[str]:
        cur = self._db.execute("SELECT path FROM files ORDER BY path")
        return [r[0] for r in cur.fetchall()]

    def get_all_node_names(self) -> list[str]:
        cur = self._db.execute("SELECT DISTINCT name FROM nodes")
        return [r[0] for r in cur.fetchall()]

    def get_unresolved_refs_batch(self, offset: int = 0, limit: int = 5000) -> list[UnresolvedReference]:
        cur = self._db.execute(
            "SELECT from_node_id, reference_name, reference_kind, line, col, "
            "candidates, file_path, language "
            "FROM unresolved_refs LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [self._row_to_ref(r) for r in cur.fetchall()]

    def delete_specific_resolved_refs(self, refs: list[dict]) -> None:
        for ref in refs:
            self._db.execute(
                "DELETE FROM unresolved_refs "
                "WHERE from_node_id = ? AND reference_name = ? AND reference_kind = ? "
                "AND line = ?",
                (ref["from_node_id"], ref["reference_name"], ref["reference_kind"], ref["line"]),
            )
        self._db.commit()

    def get_stats(self) -> dict:
        node_count = self._db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edge_count = self._db.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        file_count = self._db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        return {"node_count": node_count, "edge_count": edge_count, "file_count": file_count}

    # --- Row converters ---

    @staticmethod
    def _row_to_node(row: tuple) -> Node:
        return Node(
            id=row[0], kind=NodeKind(row[1]) if isinstance(row[1], str) else row[1],
            name=row[2], qualified_name=row[3], file_path=row[4],
            language=Language(row[5]) if isinstance(row[5], str) else row[5],
            start_line=row[6], end_line=row[7], start_column=row[8], end_column=row[9],
            docstring=row[10], signature=row[11], visibility=row[12],
            is_exported=bool(row[13]), is_async=bool(row[14]),
            is_static=bool(row[15]), is_abstract=bool(row[16]),
            decorators=row[17], type_parameters=row[18], updated_at=row[19],
        )

    @staticmethod
    def _fts_row_to_node(row: tuple) -> Node:
        return Node(
            id=row[0], kind=NodeKind(row[1]) if isinstance(row[1], str) else row[1],
            name=row[2], qualified_name=row[3], file_path=row[4],
            language=Language(row[5]) if isinstance(row[5], str) else row[5],
            start_line=row[6], end_line=row[7], start_column=row[8], end_column=row[9],
            docstring=row[10], signature=row[11], visibility=row[12],
            is_exported=bool(row[13]), is_async=bool(row[14]),
            is_static=bool(row[15]), is_abstract=bool(row[16]),
            decorators=row[17], type_parameters=row[18], updated_at=row[19],
        )

    @staticmethod
    def _row_to_edge(row: tuple) -> Edge:
        return Edge(
            source=row[0], target=row[1],
            kind=EdgeKind(row[2]) if isinstance(row[2], str) else row[2],
            metadata=row[3], line=row[4], col=row[5], provenance=row[6],
        )

    @staticmethod
    def _row_to_ref(row: tuple) -> UnresolvedReference:
        return UnresolvedReference(
            from_node_id=row[0], reference_name=row[1],
            reference_kind=EdgeKind(row[2]) if isinstance(row[2], str) else row[2],
            line=row[3], column=row[4], candidates=row[5],
            file_path=row[6], language=row[7],
        )


# Fix circular reference in type annotations
from ..types import NodeKind, EdgeKind  # noqa: E402
