"""Query builder for SQLite CRUD operations."""

from __future__ import annotations

import json
import sqlite3
from collections import OrderedDict
from typing import Optional

from ..types import (
    Node, Edge, UnresolvedReference, FileRecord, Language,
    NodeKind, EdgeKind, SearchOptions, SearchResult,
)
from ..search.query_parser import parse_query, bounded_edit_distance
from ..search.query_utils import kind_bonus, name_match_bonus, score_path_relevance


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
    def __init__(self, db: sqlite3.Connection):
        self._db = db
        self._node_cache = _LRUNodeCache()
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

    # =========================================================================
    # Node write operations
    # =========================================================================

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

    # =========================================================================
    # Edge write operations
    # =========================================================================

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

    # =========================================================================
    # File operations
    # =========================================================================

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

    def get_all_files(self) -> list[FileRecord]:
        cur = self._db.execute(
            "SELECT path, content_hash, language, size, modified_at, indexed_at, node_count, errors "
            "FROM files ORDER BY path"
        )
        return [
            FileRecord(
                path=row[0], content_hash=row[1], language=Language(row[2]),
                size=row[3], modified_at=row[4], indexed_at=row[5],
                node_count=row[6], errors=row[7],
            )
            for row in cur.fetchall()
        ]

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
        self._node_cache.invalidate_file(file_path)
        self._db.commit()

    # =========================================================================
    # Node query operations
    # =========================================================================

    def get_node_by_id(self, node_id: str) -> Optional[Node]:
        cached = self._node_cache.get(node_id)
        if cached is not None:
            return cached

        cur = self._db.execute(
            "SELECT id, kind, name, qualified_name, file_path, language, "
            "start_line, end_line, start_column, end_column, "
            "docstring, signature, visibility, is_exported, is_async, "
            "is_static, is_abstract, decorators, type_parameters, updated_at "
            "FROM nodes WHERE id = ?",
            (node_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        node = self._row_to_node(row)
        self._node_cache.put(node_id, node)
        return node

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

    # =========================================================================
    # Edge query operations
    # =========================================================================

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

    def get_outgoing_edges(self, source_id: str, kinds: Optional[list[str]] = None) -> list[Edge]:
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            cur = self._db.execute(
                f"SELECT source, target, kind, metadata, line, col, provenance "
                f"FROM edges WHERE source = ? AND kind IN ({placeholders})",
                (source_id, *kinds),
            )
        else:
            cur = self._db.execute(
                "SELECT source, target, kind, metadata, line, col, provenance "
                "FROM edges WHERE source = ?",
                (source_id,),
            )
        return [self._row_to_edge(r) for r in cur.fetchall()]

    def get_incoming_edges(self, target_id: str, kinds: Optional[list[str]] = None) -> list[Edge]:
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            cur = self._db.execute(
                f"SELECT source, target, kind, metadata, line, col, provenance "
                f"FROM edges WHERE target = ? AND kind IN ({placeholders})",
                (target_id, *kinds),
            )
        else:
            cur = self._db.execute(
                "SELECT source, target, kind, metadata, line, col, provenance "
                "FROM edges WHERE target = ?",
                (target_id,),
            )
        return [self._row_to_edge(r) for r in cur.fetchall()]

    def find_edges_between_nodes(self, node_ids: list[str], kinds: Optional[list[str]] = None) -> list[Edge]:
        if not node_ids:
            return []
        ids_json = json.dumps(node_ids)
        sql = (
            "SELECT source, target, kind, metadata, line, col, provenance FROM edges "
            "WHERE source IN (SELECT value FROM json_each(?)) "
            "AND target IN (SELECT value FROM json_each(?))"
        )
        params: list = [ids_json, ids_json]
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            sql += f" AND kind IN ({placeholders})"
            params.extend(kinds)
        cur = self._db.execute(sql, params)
        return [self._row_to_edge(r) for r in cur.fetchall()]

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

        # Strategy 1: FTS5
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
                sql = "SELECT id, kind, name, qualified_name, file_path, language, start_line, end_line, start_column, end_column, docstring, signature, visibility, is_exported, is_async, is_static, is_abstract, decorators, type_parameters, updated_at FROM nodes WHERE name = ? COLLATE NOCASE"
                params: list = [term]
                if kind_strs:
                    sql += f" AND kind IN ({','.join('?' for _ in kind_strs)})"
                    params.extend(kind_strs)
                if lang_strs:
                    sql += f" AND language IN ({','.join('?' for _ in lang_strs)})"
                    params.extend(lang_strs)
                sql += " LIMIT 20"
                rows = self._db.execute(sql, params).fetchall()
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
            sql = "SELECT DISTINCT file_path FROM nodes WHERE name = ? COLLATE NOCASE"
            params: list = [name]
            if kind_strs:
                sql += f" AND kind IN ({','.join('?' for _ in kind_strs)})"
                params.extend(kind_strs)
            sql += " LIMIT 100"
            rows = self._db.execute(sql, params).fetchall()
            name_to_files[name.lower()] = {r[0] for r in rows}

        distinctive_files: set[str] = set()
        for files in name_to_files.values():
            if 0 < len(files) < 10:
                distinctive_files.update(files)

        # Pass 2: Query each name with co-location scoring
        per_name_limit = max(8, limit // len(names))
        all_results: list[SearchResult] = []
        seen_ids: set[str] = set()

        for name in names:
            sql = (
                "SELECT id, kind, name, qualified_name, file_path, language, "
                "start_line, end_line, start_column, end_column, "
                "docstring, signature, visibility, is_exported, is_async, "
                "is_static, is_abstract, decorators, type_parameters, updated_at "
                "FROM nodes WHERE name = ? COLLATE NOCASE"
            )
            params = [name]
            if kind_strs:
                sql += f" AND kind IN ({','.join('?' for _ in kind_strs)})"
                params.extend(kind_strs)
            if lang_strs:
                sql += f" AND language IN ({','.join('?' for _ in lang_strs)})"
                params.extend(lang_strs)
            sql += f" LIMIT {max(per_name_limit * 3, 50)}"

            rows = self._db.execute(sql, params).fetchall()
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
        sql = (
            "SELECT id, kind, name, qualified_name, file_path, language, "
            "start_line, end_line, start_column, end_column, "
            "docstring, signature, visibility, is_exported, is_async, "
            "is_static, is_abstract, decorators, type_parameters, updated_at "
            "FROM nodes WHERE name LIKE ?"
        )
        params: list = [f"%{substring}%"]

        if exclude_prefix:
            sql += " AND name NOT LIKE ?"
            params.append(f"{substring}%")
        if kinds:
            sql += f" AND kind IN ({','.join('?' for _ in kinds)})"
            params.extend(kinds)
        if languages:
            sql += f" AND language IN ({','.join('?' for _ in languages)})"
            params.extend(languages)
        sql += " ORDER BY length(name) ASC LIMIT ?"
        params.append(limit)

        rows = self._db.execute(sql, params).fetchall()
        return [SearchResult(node=self._row_to_node(r), score=1.0) for r in rows]

    # =========================================================================
    # Unresolved reference operations
    # =========================================================================

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

    # =========================================================================
    # Utility operations
    # =========================================================================

    def get_all_file_paths(self) -> list[str]:
        cur = self._db.execute("SELECT path FROM files ORDER BY path")
        return [r[0] for r in cur.fetchall()]

    def get_all_node_names(self) -> list[str]:
        cur = self._db.execute("SELECT DISTINCT name FROM nodes")
        return [r[0] for r in cur.fetchall()]

    def get_stats(self) -> dict:
        node_count = self._db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edge_count = self._db.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        file_count = self._db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        return {"node_count": node_count, "edge_count": edge_count, "file_count": file_count}

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
            "WHERE nodes_fts MATCH ?"
        )
        params: list = [fts_terms]
        if kinds:
            sql += f" AND n.kind IN ({','.join('?' for _ in kinds)})"
            params.extend(kinds)
        if languages:
            sql += f" AND n.language IN ({','.join('?' for _ in languages)})"
            params.extend(languages)
        sql += " ORDER BY score LIMIT ? OFFSET ?"
        params.extend([fts_limit, offset])

        try:
            rows = self._db.execute(sql, params).fetchall()
            return [SearchResult(node=self._row_to_node(r[:20]), score=abs(r[20])) for r in rows]
        except sqlite3.OperationalError:
            return []

    def _search_nodes_like(
        self, text: str,
        kinds: Optional[list[str]], languages: Optional[list[str]],
        limit: int, offset: int,
    ) -> list[SearchResult]:
        starts_with = f"{text}%"
        contains = f"%{text}%"

        sql = (
            "SELECT id, kind, name, qualified_name, file_path, language, "
            "start_line, end_line, start_column, end_column, "
            "docstring, signature, visibility, is_exported, is_async, "
            "is_static, is_abstract, decorators, type_parameters, updated_at, "
            "CASE "
            "  WHEN name = ? THEN 1.0 "
            "  WHEN name LIKE ? THEN 0.9 "
            "  WHEN name LIKE ? THEN 0.8 "
            "  WHEN qualified_name LIKE ? THEN 0.7 "
            "  ELSE 0.5 "
            "END as score "
            "FROM nodes WHERE (name LIKE ? OR qualified_name LIKE ? OR name LIKE ?)"
        )
        params: list = [text, starts_with, contains, contains, contains, contains, starts_with]
        if kinds:
            sql += f" AND kind IN ({','.join('?' for _ in kinds)})"
            params.extend(kinds)
        if languages:
            sql += f" AND language IN ({','.join('?' for _ in languages)})"
            params.extend(languages)
        sql += " ORDER BY score DESC, length(name) ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._db.execute(sql, params).fetchall()
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
            sql = (
                "SELECT id, kind, name, qualified_name, file_path, language, "
                "start_line, end_line, start_column, end_column, "
                "docstring, signature, visibility, is_exported, is_async, "
                "is_static, is_abstract, decorators, type_parameters, updated_at "
                "FROM nodes WHERE name = ?"
            )
            params: list = [name]
            if kinds:
                sql += f" AND kind IN ({','.join('?' for _ in kinds)})"
                params.extend(kinds)
            if languages:
                sql += f" AND language IN ({','.join('?' for _ in languages)})"
                params.extend(languages)
            sql += " LIMIT 5"
            rows = self._db.execute(sql, params).fetchall()
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
        sql = (
            "SELECT id, kind, name, qualified_name, file_path, language, "
            "start_line, end_line, start_column, end_column, "
            "docstring, signature, visibility, is_exported, is_async, "
            "is_static, is_abstract, decorators, type_parameters, updated_at "
            "FROM nodes WHERE 1=1"
        )
        params: list = []
        if kinds:
            sql += f" AND kind IN ({','.join('?' for _ in kinds)})"
            params.extend(kinds)
        if languages:
            sql += f" AND language IN ({','.join('?' for _ in languages)})"
            params.extend(languages)
        sql += " ORDER BY name LIMIT ?"
        params.append(limit)
        rows = self._db.execute(sql, params).fetchall()
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
