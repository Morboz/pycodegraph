"""Query builder using SQLAlchemy Core — supports SQLite and PostgreSQL."""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import Optional

from sqlalchemy import Connection, select, insert, delete, text, func, case
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..types import (
    Node, Edge, UnresolvedReference, FileRecord, Language,
    NodeKind, EdgeKind, SearchOptions, SearchResult,
)
from ..search.query_parser import parse_query, bounded_edit_distance
from ..search.query_utils import kind_bonus, name_match_bonus, score_path_relevance
from .tables import nodes, edges, files, unresolved_refs

_NODE_COLUMNS = (
    nodes.c.id, nodes.c.kind, nodes.c.name, nodes.c.qualified_name,
    nodes.c.file_path, nodes.c.language,
    nodes.c.start_line, nodes.c.end_line, nodes.c.start_column, nodes.c.end_column,
    nodes.c.docstring, nodes.c.signature, nodes.c.visibility,
    nodes.c.is_exported, nodes.c.is_async, nodes.c.is_static, nodes.c.is_abstract,
    nodes.c.decorators, nodes.c.type_parameters, nodes.c.updated_at,
)

_EDGE_COLUMNS = (
    edges.c.source, edges.c.target, edges.c.kind,
    edges.c.metadata, edges.c.line, edges.c.col, edges.c.provenance,
)

_REF_COLUMNS = (
    unresolved_refs.c.from_node_id, unresolved_refs.c.reference_name,
    unresolved_refs.c.reference_kind, unresolved_refs.c.line, unresolved_refs.c.col,
    unresolved_refs.c.candidates, unresolved_refs.c.file_path, unresolved_refs.c.language,
)


class _LRUNodeCache:
    """Simple LRU cache for nodes."""

    def __init__(self, max_size: int = 1000) -> None:
        self._cache: OrderedDict[str, Node] = OrderedDict()
        self._max_size = max_size

    def get(self, key: str) -> Optional[Node]:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key: str, node: Node) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
        self._cache[key] = node

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def invalidate_file(self, file_path: str) -> None:
        keys_to_remove = [
            k for k, v in self._cache.items()
            if v.file_path == file_path
        ]
        for k in keys_to_remove:
            del self._cache[k]

    def clear(self) -> None:
        self._cache.clear()


class QueryBuilder:
    def __init__(self, conn: Connection):
        self._conn = conn
        self._dialect = conn.engine.dialect.name
        self._node_cache = _LRUNodeCache()

    # =========================================================================
    # Node write operations
    # =========================================================================

    def insert_nodes(self, nodes_data: list[Node]) -> None:
        if not nodes_data:
            return
        rows = [
            {
                "id": n.id, "kind": n.kind.value, "name": n.name,
                "qualified_name": n.qualified_name, "file_path": n.file_path,
                "language": n.language.value,
                "start_line": n.start_line, "end_line": n.end_line,
                "start_column": n.start_column, "end_column": n.end_column,
                "docstring": n.docstring, "signature": n.signature,
                "visibility": n.visibility,
                "is_exported": int(n.is_exported), "is_async": int(n.is_async),
                "is_static": int(n.is_static), "is_abstract": int(n.is_abstract),
                "decorators": n.decorators, "type_parameters": n.type_parameters,
                "updated_at": n.updated_at,
            }
            for n in nodes_data
        ]
        if self._dialect == "sqlite":
            stmt = sqlite_insert(nodes).on_conflict_do_nothing(
                index_elements=["id"],
            )
        else:
            stmt = insert(nodes).on_conflict_do_nothing(
                index_elements=["id"],
            )
        self._conn.execute(stmt, rows)
        self._conn.commit()

    # =========================================================================
    # Edge write operations
    # =========================================================================

    def insert_edges(self, edges_data: list[Edge]) -> None:
        if not edges_data:
            return
        rows = [
            {
                "source": e.source, "target": e.target, "kind": e.kind.value,
                "metadata": (
                    e.metadata if isinstance(e.metadata, (str, type(None)))
                    else json.dumps(e.metadata) if e.metadata else None
                ),
                "line": e.line, "col": e.col, "provenance": e.provenance,
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
                "from_node_id": r.from_node_id, "reference_name": r.reference_name,
                "reference_kind": r.reference_kind.value,
                "line": r.line, "col": r.column,
                "candidates": (
                    r.candidates if isinstance(r.candidates, (str, type(None)))
                    else json.dumps(r.candidates) if r.candidates else None
                ),
                "file_path": r.file_path, "language": r.language,
            }
            for r in refs
        ]
        self._conn.execute(insert(unresolved_refs), rows)
        self._conn.commit()

    # =========================================================================
    # File operations
    # =========================================================================

    def get_file_by_path(self, file_path: str) -> Optional[FileRecord]:
        stmt = select(
            files.c.path, files.c.content_hash, files.c.language, files.c.size,
            files.c.modified_at, files.c.indexed_at, files.c.node_count, files.c.errors,
        ).where(files.c.path == file_path)
        row = self._conn.execute(stmt).fetchone()
        if not row:
            return None
        return FileRecord(
            path=row[0], content_hash=row[1], language=Language(row[2]),
            size=row[3], modified_at=row[4], indexed_at=row[5],
            node_count=row[6], errors=row[7],
        )

    def get_all_files(self) -> list[FileRecord]:
        stmt = select(
            files.c.path, files.c.content_hash, files.c.language, files.c.size,
            files.c.modified_at, files.c.indexed_at, files.c.node_count, files.c.errors,
        ).order_by(files.c.path)
        return [
            FileRecord(
                path=row[0], content_hash=row[1], language=Language(row[2]),
                size=row[3], modified_at=row[4], indexed_at=row[5],
                node_count=row[6], errors=row[7],
            )
            for row in self._conn.execute(stmt).fetchall()
        ]

    def upsert_file(self, record: FileRecord) -> None:
        row = {
            "path": record.path, "content_hash": record.content_hash,
            "language": record.language.value, "size": record.size,
            "modified_at": record.modified_at, "indexed_at": record.indexed_at,
            "node_count": record.node_count, "errors": record.errors,
        }
        if self._dialect == "sqlite":
            stmt = sqlite_insert(files).values(**row).on_conflict_do_update(
                index_elements=["path"],
                set_=row,
            )
        else:
            stmt = insert(files).values(**row).on_conflict_do_update(
                index_elements=["path"],
                set_=row,
            )
        self._conn.execute(stmt)
        self._conn.commit()

    def delete_file(self, file_path: str) -> None:
        self._conn.execute(delete(files).where(files.c.path == file_path))
        self._node_cache.invalidate_file(file_path)
        self._conn.commit()

    # =========================================================================
    # Node query operations
    # =========================================================================

    def get_node_by_id(self, node_id: str) -> Optional[Node]:
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
        if self._dialect == "postgresql":
            stmt = select(*_NODE_COLUMNS).where(func.lower(nodes.c.name) == lower_name)
        else:
            stmt = select(*_NODE_COLUMNS).where(func.lower(nodes.c.name) == lower_name)
        return [self._row_to_node(r) for r in self._conn.execute(stmt).fetchall()]

    def get_nodes_by_file(self, file_path: str) -> list[Node]:
        stmt = select(*_NODE_COLUMNS).where(
            nodes.c.file_path == file_path,
        ).order_by(nodes.c.start_line)
        return [self._row_to_node(r) for r in self._conn.execute(stmt).fetchall()]

    def get_nodes_by_kind(self, kind) -> list[Node]:
        kind_val = kind.value if hasattr(kind, "value") else str(kind)
        stmt = select(*_NODE_COLUMNS).where(nodes.c.kind == kind_val)
        return [self._row_to_node(r) for r in self._conn.execute(stmt).fetchall()]

    # =========================================================================
    # Edge query operations
    # =========================================================================

    def get_callers(self, node_id: str) -> list[Edge]:
        stmt = select(*_EDGE_COLUMNS).where(
            edges.c.target == node_id, edges.c.kind == "calls",
        )
        return [self._row_to_edge(r) for r in self._conn.execute(stmt).fetchall()]

    def get_callees(self, node_id: str) -> list[Edge]:
        stmt = select(*_EDGE_COLUMNS).where(
            edges.c.source == node_id, edges.c.kind == "calls",
        )
        return [self._row_to_edge(r) for r in self._conn.execute(stmt).fetchall()]

    def get_outgoing_edges(self, source_id: str, kinds: Optional[list[str]] = None) -> list[Edge]:
        stmt = select(*_EDGE_COLUMNS).where(edges.c.source == source_id)
        if kinds:
            stmt = stmt.where(edges.c.kind.in_(kinds))
        return [self._row_to_edge(r) for r in self._conn.execute(stmt).fetchall()]

    def get_incoming_edges(self, target_id: str, kinds: Optional[list[str]] = None) -> list[Edge]:
        stmt = select(*_EDGE_COLUMNS).where(edges.c.target == target_id)
        if kinds:
            stmt = stmt.where(edges.c.kind.in_(kinds))
        return [self._row_to_edge(r) for r in self._conn.execute(stmt).fetchall()]

    def find_edges_between_nodes(self, node_ids: list[str], kinds: Optional[list[str]] = None) -> list[Edge]:
        if not node_ids:
            return []
        if self._dialect == "sqlite":
            ids_json = json.dumps(node_ids)
            sql = (
                "SELECT source, target, kind, metadata, line, col, provenance FROM edges "
                "WHERE source IN (SELECT value FROM json_each(:ids)) "
                "AND target IN (SELECT value FROM json_each(:ids2))"
            )
            params = {"ids": ids_json, "ids2": ids_json}
            if kinds:
                placeholders = ",".join(f":k{i}" for i in range(len(kinds)))
                sql += f" AND kind IN ({placeholders})"
                for i, k in enumerate(kinds):
                    params[f"k{i}"] = k
            rows = self._conn.execute(text(sql), params).fetchall()
        else:
            sql = (
                "SELECT source, target, kind, metadata, line, col, provenance FROM edges "
                "WHERE source = ANY(:ids) AND target = ANY(:ids2)"
            )
            params = {"ids": node_ids, "ids2": node_ids}
            if kinds:
                placeholders = ",".join(f":k{i}" for i in range(len(kinds)))
                sql += f" AND kind IN ({placeholders})"
                for i, k in enumerate(kinds):
                    params[f"k{i}"] = k
            rows = self._conn.execute(text(sql), params).fetchall()
        return [self._row_to_edge(r) for r in rows]

    # =========================================================================
    # Search operations
    # =========================================================================

    def search_nodes(self, query: str, options: Optional[SearchOptions] = None) -> list[SearchResult]:
        """Multi-strategy search: FTS → LIKE → fuzzy fallback, with scoring."""
        if options is None:
            options = SearchOptions()
        limit = options.limit
        offset = options.offset

        parsed = parse_query(query)
        merged_kinds = list(set(
            (options.kinds or []) + [NodeKind(k) for k in parsed.kinds]
        )) if parsed.kinds else (options.kinds or [])
        merged_languages = list(set(
            (options.languages or []) + [Language(l) for l in parsed.languages]
        )) if parsed.languages else (options.languages or [])
        path_filters = parsed.path_filters
        name_filters = parsed.name_filters
        text = parsed.text

        kind_strs = [k.value if hasattr(k, "value") else str(k) for k in merged_kinds] if merged_kinds else None
        lang_strs = [l.value if hasattr(l, "value") else str(l) for l in merged_languages] if merged_languages else None

        # Strategy 1: FTS
        results = (
            self._search_nodes_fts(text, kind_strs, lang_strs, limit, offset)
            if text
            else self._search_all_by_filters(kind_strs, lang_strs, limit * 5)
        )

        # Strategy 2: LIKE
        if not results and len(text) >= 2:
            results = self._search_nodes_like(text, kind_strs, lang_strs, limit, offset)

        # Strategy 3: Fuzzy
        if not results and len(text) >= 3:
            results = self._search_nodes_fuzzy(text, kind_strs, lang_strs, limit)

        # Ensure exact name matches are always candidates
        if results and text:
            existing_ids = {r.node.id for r in results}
            max_fts_score = max(r.score for r in results)
            for term in text.split():
                if len(term) < 2:
                    continue
                stmt = select(*_NODE_COLUMNS).where(
                    func.lower(nodes.c.name) == term.lower(),
                )
                if kind_strs:
                    stmt = stmt.where(nodes.c.kind.in_(kind_strs))
                if lang_strs:
                    stmt = stmt.where(nodes.c.language.in_(lang_strs))
                stmt = stmt.limit(20)
                rows = self._conn.execute(stmt).fetchall()
                for row in rows:
                    node = self._row_to_node(row)
                    if node.id not in existing_ids:
                        results.append(SearchResult(node=node, score=max_fts_score))
                        existing_ids.add(node.id)

        # Multi-signal scoring
        if results and (text or query):
            scoring_query = text or query
            results = [
                SearchResult(
                    node=r.node,
                    score=r.score
                    + kind_bonus(r.node.kind.value if hasattr(r.node.kind, "value") else str(r.node.kind))
                    + score_path_relevance(r.node.file_path, scoring_query)
                    + name_match_bonus(r.node.name, scoring_query),
                )
                for r in results
            ]
            results.sort(key=lambda r: r.score, reverse=True)
            results = results[:limit]

        # Apply path/name filters
        if path_filters:
            lowered_paths = [p.lower() for p in path_filters]
            results = [
                r for r in results
                if any(p in r.node.file_path.lower() for p in lowered_paths)
            ]
        if name_filters:
            lowered_names = [n.lower() for n in name_filters]
            results = [
                r for r in results
                if any(n in r.node.name.lower() for n in lowered_names)
            ]

        return results

    def find_nodes_by_exact_name(
        self, names: list[str], options: Optional[SearchOptions] = None,
    ) -> list[SearchResult]:
        """Exact name lookup with co-location boosting."""
        if not names:
            return []
        if options is None:
            options = SearchOptions()
        limit = options.limit or 50
        kind_strs = [k.value if hasattr(k, "value") else str(k) for k in options.kinds] if options.kinds else None
        lang_strs = [l.value if hasattr(l, "value") else str(l) for l in options.languages] if options.languages else None

        # Pass 1: Find files containing each name
        name_to_files: dict[str, set[str]] = {}
        for name in names:
            stmt = select(files.c.path).where(
                func.lower(nodes.c.name) == name.lower(),
            ).select_from(
                nodes,
            )
            if kind_strs:
                stmt = stmt.where(nodes.c.kind.in_(kind_strs))
            stmt = stmt.limit(100)
            rows = self._conn.execute(stmt).fetchall()
            name_to_files[name.lower()] = {r[0] for r in rows}

        distinctive_files: set[str] = set()
        for file_set in name_to_files.values():
            if 0 < len(file_set) < 10:
                distinctive_files.update(file_set)

        # Pass 2: Query each name with co-location scoring
        per_name_limit = max(8, limit // len(names))
        all_results: list[SearchResult] = []
        seen_ids: set[str] = set()

        for name in names:
            stmt = select(*_NODE_COLUMNS).where(
                func.lower(nodes.c.name) == name.lower(),
            )
            if kind_strs:
                stmt = stmt.where(nodes.c.kind.in_(kind_strs))
            if lang_strs:
                stmt = stmt.where(nodes.c.language.in_(lang_strs))
            stmt = stmt.limit(max(per_name_limit * 3, 50))

            rows = self._conn.execute(stmt).fetchall()
            name_results: list[SearchResult] = []
            for row in rows:
                node = self._row_to_node(row)
                if node.id in seen_ids:
                    continue
                boost = 20.0 if node.file_path in distinctive_files else 0.0
                name_results.append(SearchResult(node=node, score=1.0 + boost))

            name_results.sort(key=lambda r: r.score, reverse=True)
            for r in name_results[:per_name_limit]:
                seen_ids.add(r.node.id)
                all_results.append(r)

        all_results.sort(key=lambda r: r.score, reverse=True)
        return all_results[:limit]

    def find_nodes_by_name_substring(
        self, substring: str,
        kinds: Optional[list[str]] = None,
        languages: Optional[list[str]] = None,
        limit: int = 30,
        exclude_prefix: bool = False,
    ) -> list[SearchResult]:
        """LIKE-based substring search."""
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
        return [SearchResult(node=self._row_to_node(r), score=1.0) for r in rows]

    # =========================================================================
    # Unresolved reference operations
    # =========================================================================

    def get_unresolved_refs_count(self) -> int:
        stmt = select(func.count()).select_from(unresolved_refs)
        return self._conn.execute(stmt).scalar_one()

    def get_all_unresolved_refs(self, limit: int = 5000, offset: int = 0) -> list[UnresolvedReference]:
        stmt = select(*_REF_COLUMNS).limit(limit).offset(offset)
        return [self._row_to_ref(r) for r in self._conn.execute(stmt).fetchall()]

    def delete_unresolved_refs(self, from_node_id: str, reference_name: str) -> None:
        self._conn.execute(
            delete(unresolved_refs).where(
                unresolved_refs.c.from_node_id == from_node_id,
                unresolved_refs.c.reference_name == reference_name,
            )
        )
        self._conn.commit()

    def get_unresolved_refs_batch(self, offset: int = 0, limit: int = 5000) -> list[UnresolvedReference]:
        stmt = select(*_REF_COLUMNS).limit(limit).offset(offset)
        return [self._row_to_ref(r) for r in self._conn.execute(stmt).fetchall()]

    def delete_specific_resolved_refs(self, refs: list[dict]) -> None:
        for ref in refs:
            self._conn.execute(
                delete(unresolved_refs).where(
                    unresolved_refs.c.from_node_id == ref["from_node_id"],
                    unresolved_refs.c.reference_name == ref["reference_name"],
                    unresolved_refs.c.reference_kind == ref["reference_kind"],
                    unresolved_refs.c.line == ref["line"],
                )
            )
        self._conn.commit()

    # =========================================================================
    # Utility operations
    # =========================================================================

    def get_all_file_paths(self) -> list[str]:
        stmt = select(files.c.path).order_by(files.c.path)
        return [r[0] for r in self._conn.execute(stmt).fetchall()]

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
    # Private search helpers
    # =========================================================================

    def _search_nodes_fts(
        self, text: str,
        kinds: Optional[list[str]], languages: Optional[list[str]],
        limit: int, offset: int,
    ) -> list[SearchResult]:
        if self._dialect == "sqlite":
            return self._search_nodes_fts_sqlite(text, kinds, languages, limit, offset)
        elif self._dialect == "postgresql":
            return self._search_nodes_fts_pg(text, kinds, languages, limit, offset)
        return []

    def _search_nodes_fts_sqlite(
        self, text: str,
        kinds: Optional[list[str]], languages: Optional[list[str]],
        limit: int, offset: int,
    ) -> list[SearchResult]:
        fts_terms = " OR ".join(
            f'"{t}"*'
            for t in text.split()
            if t and t.upper() not in ("AND", "OR", "NOT", "NEAR")
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
        params: dict = {"match": fts_terms}
        if kinds:
            ph = ",".join(f":k{i}" for i in range(len(kinds)))
            sql += f" AND n.kind IN ({ph})"
            for i, k in enumerate(kinds):
                params[f"k{i}"] = k
        if languages:
            ph = ",".join(f":l{i}" for i in range(len(languages)))
            sql += f" AND n.language IN ({ph})"
            for i, l in enumerate(languages):
                params[f"l{i}"] = l
        sql += " ORDER BY score LIMIT :lim OFFSET :off"
        params["lim"] = fts_limit
        params["off"] = offset

        try:
            rows = self._conn.execute(text(sql), params).fetchall()
            return [SearchResult(node=self._row_to_node(r[:20]), score=abs(r[20])) for r in rows]
        except Exception:
            return []

    def _search_nodes_fts_pg(
        self, text: str,
        kinds: Optional[list[str]], languages: Optional[list[str]],
        limit: int, offset: int,
    ) -> list[SearchResult]:
        fts_limit = max(limit * 5, 100)
        sql = (
            "SELECT n.id, n.kind, n.name, n.qualified_name, n.file_path, n.language, "
            "n.start_line, n.end_line, n.start_column, n.end_column, "
            "n.docstring, n.signature, n.visibility, n.is_exported, n.is_async, "
            "n.is_static, n.is_abstract, n.decorators, n.type_parameters, n.updated_at, "
            "ts_rank(n.fts, query) as score "
            "FROM nodes n, plainto_tsquery('simple', :query) query "
            "WHERE n.fts @@ query"
        )
        params: dict = {"query": text}
        if kinds:
            ph = ",".join(f":k{i}" for i in range(len(kinds)))
            sql += f" AND n.kind IN ({ph})"
            for i, k in enumerate(kinds):
                params[f"k{i}"] = k
        if languages:
            ph = ",".join(f":l{i}" for i in range(len(languages)))
            sql += f" AND n.language IN ({ph})"
            for i, l in enumerate(languages):
                params[f"l{i}"] = l
        sql += " ORDER BY score DESC LIMIT :lim OFFSET :off"
        params["lim"] = fts_limit
        params["off"] = offset

        try:
            rows = self._conn.execute(text(sql), params).fetchall()
            return [SearchResult(node=self._row_to_node(r[:20]), score=abs(r[20])) for r in rows]
        except Exception:
            return []

    def _search_nodes_like(
        self, text: str,
        kinds: Optional[list[str]], languages: Optional[list[str]],
        limit: int, offset: int,
    ) -> list[SearchResult]:
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
        stmt = stmt.order_by(score_expr.desc(), func.length(nodes.c.name).asc()).limit(limit).offset(offset)

        rows = self._conn.execute(stmt).fetchall()
        return [SearchResult(node=self._row_to_node(r[:20]), score=r[20]) for r in rows]

    def _search_nodes_fuzzy(
        self, text: str,
        kinds: Optional[list[str]], languages: Optional[list[str]],
        limit: int,
    ) -> list[SearchResult]:
        lowered = text.lower()
        max_dist = 1 if len(lowered) <= 4 else 2

        all_names = self.get_all_node_names()
        candidates: list[tuple[str, int]] = []
        for name in all_names:
            dist = bounded_edit_distance(name.lower(), lowered, max_dist)
            if dist <= max_dist:
                candidates.append((name, dist))
        candidates.sort(key=lambda x: x[1])

        cap = max(limit * 2, 50)
        results: list[SearchResult] = []
        seen: set[str] = set()
        for name, dist in candidates[:cap]:
            if len(results) >= limit:
                break
            stmt = select(*_NODE_COLUMNS).where(nodes.c.name == name)
            if kinds:
                stmt = stmt.where(nodes.c.kind.in_(kinds))
            if languages:
                stmt = stmt.where(nodes.c.language.in_(languages))
            stmt = stmt.limit(5)
            rows = self._conn.execute(stmt).fetchall()
            for row in rows:
                node = self._row_to_node(row)
                if node.id in seen:
                    continue
                seen.add(node.id)
                results.append(SearchResult(node=node, score=1.0 / (1 + dist)))
                if len(results) >= limit:
                    break

        return results

    def _search_all_by_filters(
        self,
        kinds: Optional[list[str]], languages: Optional[list[str]],
        limit: int,
    ) -> list[SearchResult]:
        stmt = select(*_NODE_COLUMNS)
        if kinds:
            stmt = stmt.where(nodes.c.kind.in_(kinds))
        if languages:
            stmt = stmt.where(nodes.c.language.in_(languages))
        stmt = stmt.order_by(nodes.c.name).limit(limit)
        rows = self._conn.execute(stmt).fetchall()
        return [SearchResult(node=self._row_to_node(r), score=1.0) for r in rows]

    # =========================================================================
    # Row converters
    # =========================================================================

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
