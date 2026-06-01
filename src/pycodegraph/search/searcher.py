"""Node search orchestration — strategy selection, scoring, and ranking.

This module owns the search intelligence (FTS → LIKE → fuzzy cascade,
multi-signal scoring, co-location boosting) while delegating raw data
queries to :class:`QueryBuilder`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..types import Language, NodeKind, SearchOptions, SearchResult
from .query_parser import bounded_edit_distance, parse_query
from .query_utils import kind_bonus, name_match_bonus, score_path_relevance

if TYPE_CHECKING:
    from ..db.queries import QueryBuilder


class NodeSearcher:
    """High-level search over a CodeGraph database.

    Takes a :class:`QueryBuilder` and orchestrates multi-strategy search
    (FTS, LIKE, fuzzy) with multi-signal scoring on top of the raw data
    queries.
    """

    def __init__(self, queries: QueryBuilder) -> None:
        self._queries = queries

    # =========================================================================
    # Public search API
    # =========================================================================

    def search_nodes(
        self, query: str, options: SearchOptions | None = None
    ) -> list[SearchResult]:
        """Multi-strategy search: FTS → LIKE → fuzzy fallback, with scoring."""
        if options is None:
            options = SearchOptions()
        limit = options.limit
        offset = options.offset

        parsed = parse_query(query)
        merged_kinds = (
            list(set((options.kinds or []) + [NodeKind(k) for k in parsed.kinds]))
            if parsed.kinds
            else (options.kinds or [])
        )
        merged_languages = (
            list(
                set(
                    (options.languages or [])
                    + [Language(lang) for lang in parsed.languages]
                )
            )
            if parsed.languages
            else (options.languages or [])
        )
        path_filters = parsed.path_filters
        name_filters = parsed.name_filters
        text = parsed.text

        kind_strs = (
            [k.value if hasattr(k, "value") else str(k) for k in merged_kinds]
            if merged_kinds
            else None
        )
        lang_strs = (
            [
                lang.value if hasattr(lang, "value") else str(lang)
                for lang in merged_languages
            ]
            if merged_languages
            else None
        )

        # Strategy 1: FTS
        results = (
            self._search_fts(text, kind_strs, lang_strs, limit, offset)
            if text
            else self._search_all_by_filters(kind_strs, lang_strs, limit * 5)
        )

        # Strategy 2: LIKE
        if not results and text and len(text) >= 2:
            results = self._search_like(text, kind_strs, lang_strs, limit, offset)

        # Strategy 3: Fuzzy
        if not results and text and len(text) >= 3:
            results = self._search_fuzzy(text, kind_strs, lang_strs, limit)

        # Ensure exact name matches are always candidates
        if results and text:
            existing_ids = {r.node.id for r in results}
            max_fts_score = max(r.score for r in results)
            for term in text.split():
                if len(term) < 2:
                    continue
                nodes = self._queries.get_nodes_by_lower_name(term.lower())
                for node in nodes:
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
                    + kind_bonus(
                        r.node.kind.value
                        if hasattr(r.node.kind, "value")
                        else str(r.node.kind)
                    )
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
                r
                for r in results
                if any(p in r.node.file_path.lower() for p in lowered_paths)
            ]
        if name_filters:
            lowered_names = [n.lower() for n in name_filters]
            results = [
                r
                for r in results
                if any(n in r.node.name.lower() for n in lowered_names)
            ]

        return results

    def find_nodes_by_exact_name(
        self,
        names: list[str],
        options: SearchOptions | None = None,
    ) -> list[SearchResult]:
        """Exact name lookup with co-location boosting."""
        if not names:
            return []
        if options is None:
            options = SearchOptions()
        limit = options.limit or 50
        kind_strs = (
            [k.value if hasattr(k, "value") else str(k) for k in options.kinds]
            if options.kinds
            else None
        )
        lang_strs = (
            [
                lang.value if hasattr(lang, "value") else str(lang)
                for lang in options.languages
            ]
            if options.languages
            else None
        )

        # Pass 1: Find files containing each name
        name_to_files: dict[str, set[str]] = {}
        for name in names:
            name_to_files[name.lower()] = self._queries.find_exact_name_files(
                name, kinds=kind_strs
            )

        distinctive_files: set[str] = set()
        for file_set in name_to_files.values():
            if 0 < len(file_set) < 10:
                distinctive_files.update(file_set)

        # Pass 2: Query each name with co-location scoring
        per_name_limit = max(8, limit // len(names))
        all_results: list[SearchResult] = []
        seen_ids: set[str] = set()

        for name in names:
            nodes = self._queries.get_nodes_by_lower_name(name.lower())
            if kind_strs:
                nodes = [n for n in nodes if n.kind.value in kind_strs]
            if lang_strs:
                nodes = [n for n in nodes if n.language.value in lang_strs]
            nodes = nodes[: max(per_name_limit * 3, 50)]

            name_results: list[SearchResult] = []
            for node in nodes:
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
        self,
        substring: str,
        kinds: list[str] | None = None,
        languages: list[str] | None = None,
        limit: int = 30,
        exclude_prefix: bool = False,
    ) -> list[SearchResult]:
        """LIKE-based substring search with uniform scoring."""
        nodes = self._queries.find_nodes_by_name_substring(
            substring,
            kinds=kinds,
            languages=languages,
            limit=limit,
            exclude_prefix=exclude_prefix,
        )
        return [SearchResult(node=n, score=1.0) for n in nodes]

    # =========================================================================
    # Private strategy helpers
    # =========================================================================

    def _search_fts(
        self,
        text: str,
        kinds: list[str] | None,
        languages: list[str] | None,
        limit: int,
        offset: int,
    ) -> list[SearchResult]:
        rows = self._queries.search_fts(text, kinds, languages, limit, offset)
        if not rows:
            return []
        return [
            SearchResult(
                node=self._queries._row_to_node(r[:20]),
                score=abs(r[20]),
            )
            for r in rows
        ]

    def _search_like(
        self,
        text: str,
        kinds: list[str] | None,
        languages: list[str] | None,
        limit: int,
        offset: int,
    ) -> list[SearchResult]:
        rows = self._queries.search_like(text, kinds, languages, limit, offset)
        if not rows:
            return []
        return [
            SearchResult(
                node=self._queries._row_to_node(r[:20]),
                score=r[20],
            )
            for r in rows
        ]

    def _search_fuzzy(
        self,
        text: str,
        kinds: list[str] | None,
        languages: list[str] | None,
        limit: int,
    ) -> list[SearchResult]:
        lowered = text.lower()
        max_dist = 1 if len(lowered) <= 4 else 2

        all_names = self._queries.get_all_node_names()
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
            matched_nodes = self._queries.get_nodes_by_name(name)
            if kinds:
                matched_nodes = [n for n in matched_nodes if n.kind.value in kinds]
            if languages:
                matched_nodes = [
                    n for n in matched_nodes if n.language.value in languages
                ]
            for node in matched_nodes[:5]:
                if node.id in seen:
                    continue
                seen.add(node.id)
                results.append(SearchResult(node=node, score=1.0 / (1 + dist)))
                if len(results) >= limit:
                    break

        return results

    def _search_all_by_filters(
        self,
        kinds: list[str] | None,
        languages: list[str] | None,
        limit: int,
    ) -> list[SearchResult]:
        rows = self._queries.search_by_filters(kinds, languages, limit)
        return [
            SearchResult(node=self._queries._row_to_node(r), score=1.0) for r in rows
        ]
