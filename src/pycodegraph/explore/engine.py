"""ExploreEngine — main orchestrator for LLM-oriented code exploration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..context.builder import ContextBuilder
from ..fs import FileProvider, LocalFileProvider
from ..types import (
    ExploreOptions,
    ExploreOutputBudget,
    FindRelevantContextOptions,
    Node,
)
from .blast_radius import compute_blast_radius
from .clustering import (
    cluster_nodes_in_file,
    extract_source_with_line_numbers,
    extract_whole_file,
    get_file_language,
    score_files,
    select_clusters_within_budget,
    select_files,
)
from .flow import find_flow_chain, format_flow_chain
from .formatter import (
    format_completeness_signal,
    format_header,
    format_relationships,
    format_remaining_files,
    format_source_section,
)
from .rwr import aggregate_to_file_level, compute_rwr
from .seeding import seed_named_symbols
from .skeletonize import (
    compute_unique_named_node_ids,
    render_skeletonized,
    should_skeletonize,
)

if TYPE_CHECKING:
    from ..db.queries import QueryBuilder
    from ..graph.traversal import GraphTraverser
    from ..search.searcher import NodeSearcher

# Whole-file shortcuts: files this small are returned entirely
_WHOLE_FILE_MAX_LINES = 220
_WHOLE_FILE_MAX_CHARS_FACTOR = 3  # x max_chars_per_file


class ExploreEngine:
    """Orchestrates the explore pipeline: seeding, clustering, flow, formatting."""

    def __init__(
        self,
        project_root: str,
        queries: QueryBuilder,
        traverser: GraphTraverser,
        searcher: NodeSearcher,
        file_provider: FileProvider | None = None,
    ) -> None:
        self._project_root = project_root
        self._queries = queries
        self._traverser = traverser
        self._searcher = searcher
        self._file_provider: FileProvider = file_provider or LocalFileProvider(
            project_root
        )
        # Reuse ContextBuilder for the initial subgraph
        self._context_builder = ContextBuilder(
            project_root, queries, traverser, searcher, self._file_provider
        )

    def explore(self, query: str, options: ExploreOptions | None = None) -> str:
        """Run the full explore pipeline and return LLM-ready formatted text.

        Unlike ``build_context`` (which returns symbol-level code blocks),
        ``explore`` groups source by file with line numbers, traces call
        chains among named symbols, and respects adaptive output budgets.
        """
        opts = options or ExploreOptions()

        # ── Step 1: Compute budget ──────────────────────────────────────
        try:
            stats = self._queries.get_stats()
            file_count = stats.get("file_count", 100)
        except Exception:
            file_count = 100
        budget = ExploreOutputBudget.from_file_count(file_count)

        # Override with explicit options
        if opts.max_output_chars is not None:
            budget = ExploreOutputBudget(
                max_output_chars=opts.max_output_chars,
                default_max_files=budget.default_max_files,
                max_chars_per_file=budget.max_chars_per_file,
                gap_threshold=budget.gap_threshold,
                max_symbols_in_header=budget.max_symbols_in_header,
            )
        if opts.max_files is not None:
            budget = ExploreOutputBudget(
                max_output_chars=budget.max_output_chars,
                default_max_files=opts.max_files,
                max_chars_per_file=budget.max_chars_per_file,
                gap_threshold=budget.gap_threshold,
                max_symbols_in_header=budget.max_symbols_in_header,
            )
        if opts.max_chars_per_file is not None:
            budget = ExploreOutputBudget(
                max_output_chars=budget.max_output_chars,
                default_max_files=budget.default_max_files,
                max_chars_per_file=opts.max_chars_per_file,
                gap_threshold=budget.gap_threshold,
                max_symbols_in_header=budget.max_symbols_in_header,
            )

        max_files = budget.default_max_files

        # ── Step 2: Named-symbol seeding ────────────────────────────────
        named_seeds = seed_named_symbols(query, self._searcher)
        named_node_ids = {n.id for n, _ in named_seeds}
        named_boosts = {n.id: boost for n, boost in named_seeds}

        # Will merge RWR scores into clustering importance after Step 4
        node_importance: dict[str, float] = dict(named_boosts)

        # ── Step 3: Get initial subgraph via existing pipeline ──────────
        subgraph = self._context_builder.find_relevant_context(
            query,
            FindRelevantContextOptions(
                search_limit=8,
                traversal_depth=3,
                max_nodes=200,
                min_score=0.2,
            ),
        )

        # Inject named seeds that aren't already in the subgraph
        for node, _ in named_seeds:
            if node.id not in subgraph.nodes:
                subgraph.nodes[node.id] = node

        if not subgraph.nodes:
            return f'No relevant code found for "{query}"'

        # ── Step 3b: Compute unique named node IDs ─────────────────────
        # Names with ≤3 global definitions are "specific" and can spare
        # their file from skeletonization.  Overloaded names (>3 defs)
        # cannot.  This must happen after subgraph is available.
        unique_named_node_ids = compute_unique_named_node_ids(named_node_ids, subgraph)

        # ── Step 4: RWR graph ranking ────────────────────────────────────
        entry_node_ids = set(subgraph.roots) | named_node_ids

        # Build seed scores: named symbols get high score, entry points
        # get medium, roots get their search score
        seed_scores: dict[str, float] = {}
        for node, boost in named_seeds:
            seed_scores[node.id] = boost
        for rid in subgraph.roots:
            if rid not in seed_scores:
                seed_scores[rid] = 10.0

        node_rwr = compute_rwr(
            seed_scores,
            subgraph.edges,
            list(subgraph.nodes.keys()),
        )

        # Aggregate RWR to file level
        file_rwr = aggregate_to_file_level(node_rwr, subgraph.nodes)

        # Merge RWR scores into node importance for clustering
        for nid, rwr_score in node_rwr.items():
            # Named boosts (50/20) dominate; RWR adds differentiation
            node_importance[nid] = node_importance.get(nid, 0.0) + rwr_score * 100.0

        # ── Step 5: Score files (RWR + heuristic) ──────────────────────
        # Combine RWR (primary) with heuristic scoring (named/entry/connected)
        heuristic_scores = score_files(subgraph, named_node_ids, entry_node_ids)

        max_rwr = max(file_rwr.values()) if file_rwr else 0.0
        combined: dict[str, float] = {}
        for fp in set(list(file_rwr.keys()) + list(heuristic_scores.keys())):
            rwr_score = file_rwr.get(fp, 0.0)
            heur_score = heuristic_scores.get(fp, 0.0)
            # Named-symbol files always survive; RWR is the primary signal
            combined[fp] = rwr_score + heur_score * 0.1

        # Relevance gating: drop files with RWR < 6% of max, unless they
        # define a named symbol or an entry point
        if max_rwr > 0:
            entry_files: set[str] = set()
            for nid in entry_node_ids:
                n = subgraph.nodes.get(nid)
                if n:
                    entry_files.add(n.file_path)

            gated = {
                fp: score
                for fp, score in combined.items()
                if file_rwr.get(fp, 0.0) >= max_rwr * 0.06 or fp in entry_files
            }
            if len(gated) >= 2:
                combined = gated

        # ── Step 5b: Select files ──────────────────────────────────────
        selected_files = select_files(
            combined, subgraph, named_node_ids, max_files, query
        )

        if not selected_files:
            return f'No relevant code found for "{query}"'

        # ── Step 6: Flow tracing ────────────────────────────────────────
        flow_text = ""
        path_node_ids: set[str] = set()
        if opts.include_flow and len(named_node_ids) >= 2:
            flow_result = find_flow_chain(
                [node for node, _ in named_seeds], self._traverser
            )
            flow_text = format_flow_chain(flow_result.chain)
            path_node_ids = flow_result.path_node_ids

        # ── Step 7: Blast radius ────────────────────────────────────────
        blast_text = ""
        if opts.include_blast_radius:
            entry_nodes = [
                subgraph.nodes[rid] for rid in subgraph.roots if rid in subgraph.nodes
            ][:5]
            blast_text = compute_blast_radius(entry_nodes, self._traverser, query)

        # ── Step 8: Build output ────────────────────────────────────────
        lines: list[str] = [
            format_header(query, len(subgraph.nodes), len(combined)),
        ]

        if blast_text:
            lines.append(blast_text)

        # Relationships
        if opts.include_relationships:
            rel_text = format_relationships(subgraph.edges, subgraph)
            if rel_text:
                lines.append(rel_text)

        # Source code section
        lines.append("### Source Code")
        lines.append("")
        lines.append(
            "> The code below is **verbatim, current on-disk source** — "
            "line-numbered, byte-for-byte identical to what the Read tool "
            "returns. Treat each block as a Read you have already performed: "
            "do not Read a file shown here."
        )
        lines.append("")

        total_chars = len("\n".join(lines))
        files_included = 0
        any_trimmed = False
        remaining_files: list[tuple[str, list[Node]]] = []

        for file_path in selected_files:
            # Get nodes for this file
            file_nodes = [
                n for n in subgraph.nodes.values() if n.file_path == file_path
            ]
            if not file_nodes:
                continue

            lang = get_file_language(subgraph, file_path)

            # Check if whole file fits
            if self._file_provider.file_exists(file_path):
                content = self._file_provider.read_file(file_path)
                if content is not None:
                    file_line_count = len(content.split("\n"))
                    file_char_count = len(content)
                else:
                    remaining_files.append((file_path, file_nodes))
                    continue
            else:
                remaining_files.append((file_path, file_nodes))
                continue

            whole_file_max_chars = (
                budget.max_chars_per_file * _WHOLE_FILE_MAX_CHARS_FACTOR
            )
            if (
                file_line_count <= _WHOLE_FILE_MAX_LINES
                and file_char_count <= whole_file_max_chars
            ):
                # Whole file shortcut
                source = extract_whole_file(self._file_provider, file_path)
                if source is None:
                    remaining_files.append((file_path, file_nodes))
                    continue

                section = format_source_section(
                    file_path,
                    file_nodes,
                    source,
                    lang,
                    budget.max_symbols_in_header,
                )

                # Check budget
                is_necessary = any(
                    n.id in entry_node_ids or n.id in named_node_ids for n in file_nodes
                )
                if (
                    not is_necessary
                    and total_chars + len(section) > budget.max_output_chars * 0.9
                ):
                    remaining_files.append((file_path, file_nodes))
                    any_trimmed = True
                    continue

                lines.append(section)
                total_chars += len(section)
                files_included += 1
            else:
                # Large file — check skeletonization first, then cluster
                file_lines = content.split("\n") if content else []

                # ── Skeletonization path ─────────────────────────────────
                # When the file is a "god-file" (many named/entry methods
                # whose bodies exceed per-file budget), switch to per-symbol
                # rendering: priority methods get full body, rest get
                # signature only.  This prevents large files from eating
                # the entire output budget (issue #32).
                if should_skeletonize(
                    file_nodes,
                    path_node_ids,
                    named_node_ids,
                    unique_named_node_ids,
                    entry_node_ids,
                    file_lines,
                    budget.max_chars_per_file,
                ):
                    source, tag = render_skeletonized(
                        file_nodes,
                        file_lines,
                        path_node_ids,
                        named_node_ids,
                        unique_named_node_ids,
                        entry_node_ids,
                        budget.max_chars_per_file,
                    )
                    if not source:
                        remaining_files.append((file_path, file_nodes))
                        continue

                    section = format_source_section(
                        file_path,
                        file_nodes,
                        source,
                        lang,
                        budget.max_symbols_in_header,
                        tag=tag,
                    )

                    is_necessary = any(
                        n.id in entry_node_ids or n.id in named_node_ids
                        for n in file_nodes
                    )
                    if (
                        not is_necessary
                        and total_chars + len(section) > budget.max_output_chars * 0.9
                    ):
                        remaining_files.append((file_path, file_nodes))
                        any_trimmed = True
                        continue

                    lines.append(section)
                    total_chars += len(section)
                    files_included += 1
                    continue

                # ── Cluster-based extraction (default) ───────────────────
                clusters = cluster_nodes_in_file(
                    file_nodes,
                    node_importance,
                    gap_threshold=budget.gap_threshold,
                    file_line_count=file_line_count,
                )

                if not clusters:
                    remaining_files.append((file_path, file_nodes))
                    continue

                # Rank clusters by importance, select within per-file budget
                ranked = sorted(clusters, key=lambda c: c.importance, reverse=True)
                file_budget = min(
                    budget.max_chars_per_file,
                    max(0, budget.max_output_chars - total_chars - 200),
                )

                selected_clusters = select_clusters_within_budget(ranked, file_budget)

                source = extract_source_with_line_numbers(
                    self._file_provider, file_path, selected_clusters
                )
                if not source:
                    remaining_files.append((file_path, file_nodes))
                    continue

                all_symbols: list[Node] = []
                for c in selected_clusters:
                    all_symbols.extend(c.symbols)

                # Re-add filtered envelope nodes that are named/entry symbols
                # so they appear in the output header (e.g., the class name
                # the user searched for), even though their line span was too
                # large to include in clustering.  Prepend them so they appear
                # first in the header and aren't truncated by max_symbols_in_header.
                cluster_symbol_ids = {s.id for s in all_symbols}
                header_envelopes: list[Node] = []
                for n in file_nodes:
                    if n.id not in cluster_symbol_ids and (
                        n.id in entry_node_ids or n.id in named_node_ids
                    ):
                        header_envelopes.append(n)
                all_symbols = header_envelopes + all_symbols

                section = format_source_section(
                    file_path,
                    all_symbols,
                    source,
                    lang,
                    budget.max_symbols_in_header,
                )

                is_necessary = any(
                    n.id in entry_node_ids or n.id in named_node_ids
                    for n in all_symbols
                )
                if (
                    not is_necessary
                    and total_chars + len(section) > budget.max_output_chars * 0.9
                ):
                    remaining_files.append((file_path, file_nodes))
                    any_trimmed = True
                    continue

                if len(selected_clusters) < len(clusters):
                    any_trimmed = True

                lines.append(section)
                total_chars += len(section)
                files_included += 1

        # Remaining files list
        if remaining_files:
            lines.append(format_remaining_files(remaining_files))

        # Completeness signal
        completeness = format_completeness_signal(files_included, any_trimmed)
        if completeness:
            lines.append("")
            lines.append(completeness)

        # Flow section (placed right before Source Code for readability)
        if flow_text:
            for i, line in enumerate(lines):
                if line.strip().startswith("### Source Code"):
                    lines.insert(i, "")
                    lines.insert(i, flow_text)
                    break

        # Final assembly
        output = "\n".join(lines)

        # Hard ceiling — avoid MCP externalization (~25K)
        _TRUNC_MSG = (
            "\n\n... (output truncated to budget; the source above is "
            "complete and verbatim — treat it as already Read. For "
            "any area not covered, run another explore with the "
            "specific names — do NOT Read these files.)"
        )
        hard_ceiling = min(int(budget.max_output_chars * 1.5), 25_000)
        if len(output) > hard_ceiling:
            trunc_msg = (
                _TRUNC_MSG
                if len(_TRUNC_MSG) < hard_ceiling
                else "\n... (output truncated to budget)"
            )
            if len(trunc_msg) >= hard_ceiling:
                return trunc_msg[:hard_ceiling]

            # Reserve room for truncation message
            ceiling = hard_ceiling - len(trunc_msg)
            # Cut at file section boundary
            cut = output[:ceiling]
            last_section = cut.rfind("\n#### ")
            boundary = last_section if last_section > ceiling * 0.5 else cut.rfind("\n")
            output = cut[:boundary] if boundary > 0 else cut
            output += trunc_msg

        return output
