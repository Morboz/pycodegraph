"""Query builder using SQLAlchemy Core — supports SQLite and PostgreSQL."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Connection, case, delete, func, insert, or_, select, tuple_

from ..types import (
    Edge,
    EdgeKind,
    FileRecord,
    Language,
    Node,
    NodeKind,
    UnresolvedReference,
)
from ._cache import LRUCache
from .backend import get_backend
from .tables import edges, files, nodes, unresolved_refs

_NODE_COLUMNS = (
    nodes.c.id,
    nodes.c.kind,
    nodes.c.name,
    nodes.c.qualified_name,
    nodes.c.file_path,
    nodes.c.language,
    nodes.c.start_line,
    nodes.c.end_line,
    nodes.c.start_column,
    nodes.c.end_column,
    nodes.c.docstring,
    nodes.c.signature,
    nodes.c.visibility,
    nodes.c.is_exported,
    nodes.c.is_async,
    nodes.c.is_static,
    nodes.c.is_abstract,
    nodes.c.decorators,
    nodes.c.type_parameters,
    nodes.c.updated_at,
)

_EDGE_COLUMNS = (
    edges.c.source,
    edges.c.target,
    edges.c.kind,
    edges.c.metadata,
    edges.c.line,
    edges.c.col,
    edges.c.provenance,
)

_REF_COLUMNS = (
    unresolved_refs.c.from_node_id,
    unresolved_refs.c.reference_name,
    unresolved_refs.c.reference_kind,
    unresolved_refs.c.line,
    unresolved_refs.c.col,
    unresolved_refs.c.candidates,
    unresolved_refs.c.file_path,
    unresolved_refs.c.language,
)


def _node_search_text(node: Node) -> str:
    return " ".join(
        part
        for part in (
            node.name,
            node.qualified_name,
            node.docstring,
            node.signature,
        )
        if part
    )


def _node_row(node: Node) -> dict:
    return {
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
        "fts_text": _node_search_text(node),
    }


class QueryBuilder:
    def __init__(self, conn: Connection):
        self._conn = conn
        self._backend = get_backend(
            conn.info.get("pycodegraph_backend", conn.engine.dialect.name)
        )
        self._node_cache = LRUCache[Node]()

    # =========================================================================
    # Node write operations
    # =========================================================================

    def insert_nodes(self, nodes_data: list[Node]) -> None:
        if not nodes_data:
            return
        rows = self._backend.prepare_node_rows([_node_row(n) for n in nodes_data])
        stmt = self._backend.insert_nodes_ignore()
        self._conn.execute(stmt, rows)
        self._backend.after_nodes_changed(self._conn)
        self._conn.commit()

    # =========================================================================
    # Edge write operations
    # =========================================================================

    def insert_edges(self, edges_data: list[Edge]) -> None:
        if not edges_data:
            return
        rows = [
            {
                "source": e.source,
                "target": e.target,
                "kind": e.kind.value,
                "metadata": (
                    e.metadata
                    if isinstance(e.metadata, str | None)
                    else json.dumps(e.metadata)
                    if e.metadata
                    else None
                ),
                "line": e.line,
                "col": e.col,
                "provenance": e.provenance,
            }
            for e in edges_data
        ]
        self._conn.execute(insert(edges), rows)
        self._conn.commit()

    def insert_unresolved_refs_batch(self, refs: list[UnresolvedReference]) -> None:
        if not refs:
            return
        rows = [
            {
                "from_node_id": r.from_node_id,
                "reference_name": r.reference_name,
                "reference_kind": r.reference_kind.value,
                "line": r.line,
                "col": r.column,
                "candidates": (
                    r.candidates
                    if isinstance(r.candidates, str | None)
                    else json.dumps(r.candidates)
                    if r.candidates
                    else None
                ),
                "file_path": r.file_path,
                "language": r.language,
            }
            for r in refs
        ]
        self._conn.execute(insert(unresolved_refs), rows)
        self._conn.commit()

    # =========================================================================
    # File operations
    # =========================================================================

    def get_file_by_path(self, file_path: str) -> FileRecord | None:
        stmt = select(
            files.c.path,
            files.c.content_hash,
            files.c.language,
            files.c.size,
            files.c.modified_at,
            files.c.indexed_at,
            files.c.node_count,
            files.c.errors,
        ).where(files.c.path == file_path)
        row = self._conn.execute(stmt).fetchone()
        if not row:
            return None
        return FileRecord(
            path=row[0],
            content_hash=row[1],
            language=Language(row[2]),
            size=row[3],
            modified_at=row[4],
            indexed_at=row[5],
            node_count=row[6],
            errors=row[7],
        )

    def get_all_files(self) -> list[FileRecord]:
        stmt = select(
            files.c.path,
            files.c.content_hash,
            files.c.language,
            files.c.size,
            files.c.modified_at,
            files.c.indexed_at,
            files.c.node_count,
            files.c.errors,
        ).order_by(files.c.path)
        return [
            FileRecord(
                path=row[0],
                content_hash=row[1],
                language=Language(row[2]),
                size=row[3],
                modified_at=row[4],
                indexed_at=row[5],
                node_count=row[6],
                errors=row[7],
            )
            for row in self._conn.execute(stmt).fetchall()
        ]

    def upsert_file(self, record: FileRecord) -> None:
        row = {
            "path": record.path,
            "content_hash": record.content_hash,
            "language": record.language.value,
            "size": record.size,
            "modified_at": record.modified_at,
            "indexed_at": record.indexed_at,
            "node_count": record.node_count,
            "errors": record.errors,
        }
        stmt = self._backend.upsert_file(row)
        self._conn.execute(stmt)
        self._conn.commit()

    def delete_file(self, file_path: str) -> None:
        node_ids = [
            row[0]
            for row in self._conn.execute(
                select(nodes.c.id).where(nodes.c.file_path == file_path)
            ).fetchall()
        ]
        if node_ids:
            self._conn.execute(
                delete(unresolved_refs).where(
                    unresolved_refs.c.from_node_id.in_(node_ids)
                )
            )
            self._conn.execute(
                delete(edges).where(
                    or_(
                        edges.c.source.in_(node_ids),
                        edges.c.target.in_(node_ids),
                    )
                )
            )
            self._conn.execute(delete(nodes).where(nodes.c.file_path == file_path))
        self._conn.execute(delete(files).where(files.c.path == file_path))
        self._node_cache.invalidate_by_attr("file_path", file_path)
        self._backend.after_nodes_changed(self._conn)
        self._conn.commit()

    # =========================================================================
    # Node query operations
    # =========================================================================

    def get_node_by_id(self, node_id: str) -> Node | None:
        cached = self._node_cache.get(node_id)
        if cached is not None:
            return cached
        stmt = select(*_NODE_COLUMNS).where(nodes.c.id == node_id)
        row = self._conn.execute(stmt).fetchone()
        if not row:
            return None
        node = self._row_to_node(row)
        self._node_cache.put(node_id, node)
        return node

    def get_nodes_by_name(self, name: str) -> list[Node]:
        stmt = select(*_NODE_COLUMNS).where(nodes.c.name == name)
        return [self._row_to_node(r) for r in self._conn.execute(stmt).fetchall()]

    def get_nodes_by_qualified_name(self, qualified_name: str) -> list[Node]:
        stmt = select(*_NODE_COLUMNS).where(nodes.c.qualified_name == qualified_name)
        return [self._row_to_node(r) for r in self._conn.execute(stmt).fetchall()]

    def get_nodes_by_lower_name(self, lower_name: str) -> list[Node]:
        stmt = select(*_NODE_COLUMNS).where(func.lower(nodes.c.name) == lower_name)
        return [self._row_to_node(r) for r in self._conn.execute(stmt).fetchall()]

    def get_nodes_by_file(self, file_path: str) -> list[Node]:
        stmt = (
            select(*_NODE_COLUMNS)
            .where(
                nodes.c.file_path == file_path,
            )
            .order_by(nodes.c.start_line)
        )
        return [self._row_to_node(r) for r in self._conn.execute(stmt).fetchall()]

    def get_nodes_by_kind(self, kind) -> list[Node]:
        kind_val = kind.value if hasattr(kind, "value") else str(kind)
        stmt = select(*_NODE_COLUMNS).where(nodes.c.kind == kind_val)
        return [self._row_to_node(r) for r in self._conn.execute(stmt).fetchall()]

    def get_all_nodes(self, limit: int = 50000, offset: int = 0) -> list[Node]:
        stmt = (
            select(*_NODE_COLUMNS)
            .order_by(nodes.c.file_path, nodes.c.start_line)
            .limit(limit)
            .offset(offset)
        )
        return [self._row_to_node(r) for r in self._conn.execute(stmt).fetchall()]

    def get_all_edges(self, limit: int = 100000, offset: int = 0) -> list[Edge]:
        stmt = select(*_EDGE_COLUMNS).limit(limit).offset(offset)
        return [self._row_to_edge(r) for r in self._conn.execute(stmt).fetchall()]

    # =========================================================================
    # Edge query operations
    # =========================================================================

    def get_callers(self, node_id: str) -> list[Edge]:
        stmt = select(*_EDGE_COLUMNS).where(
            edges.c.target == node_id,
            edges.c.kind == "calls",
        )
        return [self._row_to_edge(r) for r in self._conn.execute(stmt).fetchall()]

    def get_callees(self, node_id: str) -> list[Edge]:
        stmt = select(*_EDGE_COLUMNS).where(
            edges.c.source == node_id,
            edges.c.kind == "calls",
        )
        return [self._row_to_edge(r) for r in self._conn.execute(stmt).fetchall()]

    def get_outgoing_edges(
        self, source_id: str, kinds: list[str] | None = None
    ) -> list[Edge]:
        stmt = select(*_EDGE_COLUMNS).where(edges.c.source == source_id)
        if kinds:
            stmt = stmt.where(edges.c.kind.in_(kinds))
        return [self._row_to_edge(r) for r in self._conn.execute(stmt).fetchall()]

    def get_incoming_edges(
        self, target_id: str, kinds: list[str] | None = None
    ) -> list[Edge]:
        stmt = select(*_EDGE_COLUMNS).where(edges.c.target == target_id)
        if kinds:
            stmt = stmt.where(edges.c.kind.in_(kinds))
        return [self._row_to_edge(r) for r in self._conn.execute(stmt).fetchall()]

    def find_edges_between_nodes(
        self, node_ids: list[str], kinds: list[str] | None = None
    ) -> list[Edge]:
        if not node_ids:
            return []
        rows = self._backend.find_edges_between_nodes(self._conn, node_ids, kinds)
        return [self._row_to_edge(r) for r in rows]

    # =========================================================================
    # Search data primitives
    # =========================================================================

    def search_fts(
        self,
        text: str,
        kinds: list[str] | None,
        languages: list[str] | None,
        limit: int,
        offset: int,
    ) -> list[tuple]:
        """Execute FTS search via the dialect layer. Returns raw rows.

        Each row has 20 node columns followed by the FTS score at index 20.
        Returns an empty list on any error (e.g. missing FTS index).
        """
        try:
            return self._backend.search_nodes_fts(
                self._conn,
                text,
                kinds,
                languages,
                limit,
                offset,
            )
        except Exception:
            return []

    def search_like(
        self,
        text: str,
        kinds: list[str] | None,
        languages: list[str] | None,
        limit: int,
        offset: int,
    ) -> list[tuple]:
        """Execute LIKE-based search. Returns raw rows with a SQL-computed score.

        Each row has 20 node columns followed by the LIKE score at index 20.
        """
        starts_with = f"{text}%"
        contains = f"%{text}%"

        score_expr = case(
            (nodes.c.name == text, 1.0),
            (nodes.c.name.like(starts_with), 0.9),
            (nodes.c.name.like(contains), 0.8),
            (nodes.c.qualified_name.like(contains), 0.7),
            else_=0.5,
        ).label("score")

        stmt = select(*_NODE_COLUMNS, score_expr).where(
            nodes.c.name.like(contains)
            | nodes.c.qualified_name.like(contains)
            | nodes.c.name.like(starts_with),
        )
        if kinds:
            stmt = stmt.where(nodes.c.kind.in_(kinds))
        if languages:
            stmt = stmt.where(nodes.c.language.in_(languages))
        stmt = (
            stmt.order_by(score_expr.desc(), func.length(nodes.c.name).asc())
            .limit(limit)
            .offset(offset)
        )

        return [tuple(r) for r in self._conn.execute(stmt).fetchall()]

    def search_by_filters(
        self,
        kinds: list[str] | None,
        languages: list[str] | None,
        limit: int,
    ) -> list[tuple]:
        """Return nodes matching kind/language filters, ordered by name."""
        stmt = select(*_NODE_COLUMNS)
        if kinds:
            stmt = stmt.where(nodes.c.kind.in_(kinds))
        if languages:
            stmt = stmt.where(nodes.c.language.in_(languages))
        stmt = stmt.order_by(nodes.c.name).limit(limit)
        return [tuple(r) for r in self._conn.execute(stmt).fetchall()]

    def find_exact_name_files(
        self, name: str, kinds: list[str] | None = None
    ) -> set[str]:
        """Return file paths containing a node with the given name (case-insensitive)."""
        stmt = (
            select(files.c.path)
            .where(
                func.lower(nodes.c.name) == name.lower(),
            )
            .select_from(nodes)
        )
        if kinds:
            stmt = stmt.where(nodes.c.kind.in_(kinds))
        stmt = stmt.limit(100)
        return {r[0] for r in self._conn.execute(stmt).fetchall()}

    def find_nodes_by_name_substring(
        self,
        substring: str,
        kinds: list[str] | None = None,
        languages: list[str] | None = None,
        limit: int = 30,
        exclude_prefix: bool = False,
    ) -> list[Node]:
        """LIKE-based substring search. Returns matching nodes."""
        stmt = select(*_NODE_COLUMNS).where(
            nodes.c.name.like(f"%{substring}%"),
        )
        if exclude_prefix:
            stmt = stmt.where(nodes.c.name.notlike(f"{substring}%"))
        if kinds:
            stmt = stmt.where(nodes.c.kind.in_(kinds))
        if languages:
            stmt = stmt.where(nodes.c.language.in_(languages))
        stmt = stmt.order_by(func.length(nodes.c.name).asc()).limit(limit)

        rows = self._conn.execute(stmt).fetchall()
        return [self._row_to_node(r) for r in rows]

    # =========================================================================
    # Unresolved reference operations
    # =========================================================================

    def get_unresolved_refs_count(self) -> int:
        stmt = select(func.count()).select_from(unresolved_refs)
        return self._conn.execute(stmt).scalar_one()

    def get_all_unresolved_refs(
        self, limit: int = 5000, offset: int = 0
    ) -> list[UnresolvedReference]:
        stmt = select(*_REF_COLUMNS).limit(limit).offset(offset)
        return [self._row_to_ref(r) for r in self._conn.execute(stmt).fetchall()]

    def delete_all_unresolved_refs(self) -> None:
        """Delete all unresolved refs in one statement."""
        self._conn.execute(delete(unresolved_refs))
        self._conn.commit()

    def delete_unresolved_refs(self, from_node_id: str, reference_name: str) -> None:
        self._conn.execute(
            delete(unresolved_refs).where(
                unresolved_refs.c.from_node_id == from_node_id,
                unresolved_refs.c.reference_name == reference_name,
            )
        )
        self._conn.commit()

    def get_unresolved_refs_batch(
        self, offset: int = 0, limit: int = 5000
    ) -> list[UnresolvedReference]:
        stmt = select(*_REF_COLUMNS).limit(limit).offset(offset)
        return [self._row_to_ref(r) for r in self._conn.execute(stmt).fetchall()]

    def delete_specific_resolved_refs(self, refs: list[dict]) -> None:
        if not refs:
            return
        # Batch DELETE using tuple IN instead of one statement per row
        keys = [
            (r["from_node_id"], r["reference_name"], r["reference_kind"], r["line"])
            for r in refs
        ]
        for i in range(0, len(keys), 500):
            chunk = keys[i : i + 500]
            self._conn.execute(
                delete(unresolved_refs).where(
                    tuple_(
                        unresolved_refs.c.from_node_id,
                        unresolved_refs.c.reference_name,
                        unresolved_refs.c.reference_kind,
                        unresolved_refs.c.line,
                    ).in_(chunk)
                )
            )
        self._conn.commit()

    # =========================================================================
    # Utility operations
    # =========================================================================

    def get_all_file_paths(self) -> list[str]:
        stmt = select(files.c.path).order_by(files.c.path)
        return [r[0] for r in self._conn.execute(stmt).fetchall()]

    def get_all_file_paths_indexed(self) -> dict[str, str]:
        """Return {path: content_hash} for all indexed files."""
        stmt = select(files.c.path, files.c.content_hash)
        return {r[0]: r[1] for r in self._conn.execute(stmt).fetchall()}

    def delete_files_batch(self, file_paths: list[str]) -> None:
        """Delete multiple files and their associated nodes/edges by path."""
        if not file_paths:
            return
        # nodes.file_path is not an FK, so remove graph rows explicitly.
        for i in range(0, len(file_paths), 500):
            chunk = file_paths[i : i + 500]
            node_ids = [
                row[0]
                for row in self._conn.execute(
                    select(nodes.c.id).where(nodes.c.file_path.in_(chunk))
                ).fetchall()
            ]
            if node_ids:
                self._conn.execute(
                    delete(unresolved_refs).where(
                        unresolved_refs.c.from_node_id.in_(node_ids)
                    )
                )
                self._conn.execute(
                    delete(edges).where(
                        or_(
                            edges.c.source.in_(node_ids),
                            edges.c.target.in_(node_ids),
                        )
                    )
                )
                self._conn.execute(delete(nodes).where(nodes.c.file_path.in_(chunk)))
            self._conn.execute(delete(files).where(files.c.path.in_(chunk)))
            self._node_cache.invalidate_by_attr_in("file_path", set(chunk))
        self._backend.after_nodes_changed(self._conn)
        self._conn.commit()

    def bulk_insert(
        self,
        nodes_data: list[Node],
        edges_data: list[Edge],
        refs_data: list[UnresolvedReference],
        file_records: list[FileRecord],
    ) -> None:
        """Bulk insert nodes, edges, refs, and file records in a single transaction."""
        # Nodes
        if nodes_data:
            rows = self._backend.prepare_node_rows([_node_row(n) for n in nodes_data])
            stmt = self._backend.insert_nodes_ignore()
            self._conn.execute(stmt, rows)

        # Edges
        if edges_data:
            rows = [
                {
                    "source": e.source,
                    "target": e.target,
                    "kind": e.kind.value,
                    "metadata": (
                        e.metadata
                        if isinstance(e.metadata, str | None)
                        else json.dumps(e.metadata)
                        if e.metadata
                        else None
                    ),
                    "line": e.line,
                    "col": e.col,
                    "provenance": e.provenance,
                }
                for e in edges_data
            ]
            self._conn.execute(insert(edges), rows)

        # Unresolved refs
        if refs_data:
            rows = [
                {
                    "from_node_id": r.from_node_id,
                    "reference_name": r.reference_name,
                    "reference_kind": r.reference_kind.value,
                    "line": r.line,
                    "col": r.column,
                    "candidates": (
                        r.candidates
                        if isinstance(r.candidates, str | None)
                        else json.dumps(r.candidates)
                        if r.candidates
                        else None
                    ),
                    "file_path": r.file_path,
                    "language": r.language,
                }
                for r in refs_data
            ]
            self._conn.execute(insert(unresolved_refs), rows)

        # Files
        if file_records:
            for rec in file_records:
                row = {
                    "path": rec.path,
                    "content_hash": rec.content_hash,
                    "language": rec.language.value,
                    "size": rec.size,
                    "modified_at": rec.modified_at,
                    "indexed_at": rec.indexed_at,
                    "node_count": rec.node_count,
                    "errors": rec.errors,
                }
                stmt = self._backend.upsert_file(row)
                self._conn.execute(stmt)

        if nodes_data:
            self._backend.after_nodes_changed(self._conn)
        self._conn.commit()

    def get_all_node_names(self) -> list[str]:
        stmt = select(nodes.c.name).distinct()
        return [r[0] for r in self._conn.execute(stmt).fetchall()]

    def get_stats(self) -> dict:
        nc = self._conn.execute(select(func.count()).select_from(nodes)).scalar_one()
        ec = self._conn.execute(select(func.count()).select_from(edges)).scalar_one()
        fc = self._conn.execute(select(func.count()).select_from(files)).scalar_one()
        return {"node_count": nc, "edge_count": ec, "file_count": fc}

    def clear_cache(self) -> None:
        self._node_cache.clear()

    # =========================================================================
    # Row converters
    # =========================================================================

    @staticmethod
    def _row_to_node(row: Any) -> Node:
        return Node(
            id=row[0],
            kind=NodeKind(row[1]) if isinstance(row[1], str) else row[1],
            name=row[2],
            qualified_name=row[3],
            file_path=row[4],
            language=Language(row[5]) if isinstance(row[5], str) else row[5],
            start_line=row[6],
            end_line=row[7],
            start_column=row[8],
            end_column=row[9],
            docstring=row[10],
            signature=row[11],
            visibility=row[12],
            is_exported=bool(row[13]),
            is_async=bool(row[14]),
            is_static=bool(row[15]),
            is_abstract=bool(row[16]),
            decorators=row[17],
            type_parameters=row[18],
            updated_at=row[19],
        )

    @staticmethod
    def _row_to_edge(row: Any) -> Edge:
        return Edge(
            source=row[0],
            target=row[1],
            kind=EdgeKind(row[2]) if isinstance(row[2], str) else row[2],
            metadata=row[3],
            line=row[4],
            col=row[5],
            provenance=row[6],
        )

    @staticmethod
    def _row_to_ref(row: Any) -> UnresolvedReference:
        return UnresolvedReference(
            from_node_id=row[0],
            reference_name=row[1],
            reference_kind=EdgeKind(row[2]) if isinstance(row[2], str) else row[2],
            line=row[3],
            column=row[4],
            candidates=row[5],
            file_path=row[6],
            language=row[7],
        )
