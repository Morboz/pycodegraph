"""ExploreEngine — main orchestrator for LLM-oriented code exploration."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ..context.builder import ContextBuilder
from ..types import (
    ExploreOptions,
    ExploreOutputBudget,
    FindRelevantContextOptions,
    Node,
)
from .blast_radius import compute_blast_radius
from .clustering import (
    FileCluster,
    cluster_nodes_in_file,
    extract_source_with_line_numbers,
    extract_whole_file,
    get_file_language,
    score_files,
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
from .seeding import seed_named_symbols

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
    ) -> None:
        self._project_root = project_root
        self._queries = queries
        self._traverser = traverser
        self._searcher = searcher
        # Reuse ContextBuilder for the initial subgraph
        self._context_builder = ContextBuilder(
            project_root, queries, traverser, searcher
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

        # ── Step 4: Score files ─────────────────────────────────────────
        entry_node_ids = set(subgraph.roots) | named_node_ids
        file_scores = score_files(subgraph, named_node_ids, entry_node_ids)

        # ── Step 5: Select files ────────────────────────────────────────
        selected_files = select_files(
            file_scores, subgraph, named_node_ids, max_files, query
        )

        if not selected_files:
            return f'No relevant code found for "{query}"'

        # ── Step 6: Flow tracing ────────────────────────────────────────
        flow_text = ""
        if opts.include_flow and len(named_node_ids) >= 2:
            chain = find_flow_chain(named_node_ids, self._traverser)
            flow_text = format_flow_chain(chain)

        # ── Step 7: Blast radius ────────────────────────────────────────
        blast_text = ""
        if opts.include_blast_radius:
            entry_nodes = [
                subgraph.nodes[rid] for rid in subgraph.roots if rid in subgraph.nodes
            ][:5]
            blast_text = compute_blast_radius(entry_nodes, self._traverser, query)

        # ── Step 8: Build output ────────────────────────────────────────
        lines: list[str] = [
            format_header(query, len(subgraph.nodes), len(file_scores)),
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
            abs_path = os.path.join(self._project_root, file_path)
            if os.path.exists(abs_path):
                try:
                    with open(abs_path) as f:
                        content = f.read()
                    file_line_count = len(content.split("\n"))
                    file_char_count = len(content)
                except (OSError, UnicodeDecodeError):
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
                source = extract_whole_file(self._project_root, file_path)
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
                # Cluster-based extraction
                clusters = cluster_nodes_in_file(
                    file_nodes,
                    named_boosts,
                    gap_threshold=budget.gap_threshold,
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

                selected_clusters: list[FileCluster] = []
                projected = 0
                for cluster in ranked:
                    # Rough size estimate
                    est = (cluster.end_line - cluster.start_line + 1) * 60
                    if not selected_clusters or projected + est <= file_budget:
                        selected_clusters.append(cluster)
                        projected += est

                source = extract_source_with_line_numbers(
                    self._project_root, file_path, selected_clusters
                )
                if not source:
                    remaining_files.append((file_path, file_nodes))
                    continue

                all_symbols: list[Node] = []
                for c in selected_clusters:
                    all_symbols.extend(c.symbols)

                section = format_source_section(
                    file_path,
                    all_symbols,
                    source,
                    lang,
                    budget.max_symbols_in_header,
                )

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

        # Flow section (placed after header but before source for readability)
        if flow_text:
            # Insert flow after header
            header_end = 4  # After the 4-line header
            lines.insert(header_end, "")
            lines.insert(header_end + 1, flow_text)

        # Final assembly
        output = "\n".join(lines)

        # Hard ceiling — avoid MCP externalization (~25K)
        hard_ceiling = min(int(budget.max_output_chars * 1.5), 25_000)
        if len(output) > hard_ceiling:
            # Cut at file section boundary
            cut = output[:hard_ceiling]
            last_section = cut.rfind("\n#### ")
            boundary = (
                last_section if last_section > hard_ceiling * 0.5 else cut.rfind("\n")
            )
            if boundary > 0:
                output = cut[:boundary]
            output += (
                "\n\n... (output truncated to budget; the source above is "
                "complete and verbatim — treat it as already Read. For "
                "any area not covered, run another explore with the "
                "specific names — do NOT Read these files.)"
            )

        return output
