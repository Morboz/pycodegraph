"""Dataflow Slice — bounded BFS over Dataflow Edges (consumer-side API).

Built on the storage layer from issue #86 (``dataflow_edges`` table +
``QueryBuilder`` dataflow methods). This module turns those raw line-range
rows into a :class:`DataflowSlice` of :class:`StatementRef` /
:class:`DataflowSliceEdge` objects suitable for LLM consumption.
"""

from __future__ import annotations

from collections import defaultdict, deque

from ..db.queries import QueryBuilder
from ..fs import FileProvider
from ..types import (
    DataflowEdge,
    DataflowSlice,
    DataflowSliceEdge,
    StatementRef,
)

# A statement's identity: (file_path, start_line, end_line).
Coord = tuple[str, int, int]


class DataflowSlicer:
    """Bounded BFS over Dataflow Edges starting from a seed statement.

    Constructed by ``CodeGraph._create_components``; inject where needed.
    """

    def __init__(self, queries: QueryBuilder, file_provider: FileProvider) -> None:
        self._queries = queries
        self._file_provider = file_provider

    def slice(
        self,
        file_path: str,
        line: int,
        variable: str | None = None,
        direction: str = "both",
        max_depth: int = 10,
    ) -> DataflowSlice:
        """Return the Dataflow Slice rooted at ``(file_path, line)``.

        Args:
            file_path: File containing the seed statement.
            line: A line inside the seed statement.
            variable: Optional filter — restrict the slice to one variable.
            direction: ``"forward"`` (outgoing edges), ``"backward"``
                (incoming edges), or ``"both"``.
            max_depth: Maximum BFS hop count from the seed.
        """
        seed_edges = self._queries.get_dataflow_edges_by_statement(file_path, line)
        if variable is not None:
            seed_edges = [e for e in seed_edges if e.variable == variable]
        if not seed_edges:
            return DataflowSlice()

        seed_coords = self._seed_coords(seed_edges, line)
        universe = self._bfs_universe(seed_edges, variable)

        forward_adj, backward_adj = self._build_adjacency(universe)
        reached, traversed = self._bfs(
            seed_coords, forward_adj, backward_adj, direction, max_depth
        )

        statements_by_coord = self._build_statements(reached, universe)
        self._fill_source_text(statements_by_coord)
        edges = self._build_edges(traversed, statements_by_coord)
        statements = [statements_by_coord[c] for c in sorted(reached)]
        seed = statements_by_coord[sorted(seed_coords)[0]] if seed_coords else None

        return DataflowSlice(statements=statements, edges=edges, seed=seed)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _source_coord(e: DataflowEdge) -> Coord:
        return (e.file_path, e.source_start_line, e.source_end_line)

    @staticmethod
    def _target_coord(e: DataflowEdge) -> Coord:
        return (e.file_path, e.target_start_line, e.target_end_line)

    @staticmethod
    def _contains(coord: Coord, line: int) -> bool:
        return coord[1] <= line <= coord[2]

    def _seed_coords(self, seed_edges: list[DataflowEdge], line: int) -> set[Coord]:
        """Statements whose span contains ``line`` — the BFS starting points."""
        coords: set[Coord] = set()
        for e in seed_edges:
            src = self._source_coord(e)
            tgt = self._target_coord(e)
            if self._contains(src, line):
                coords.add(src)
            if self._contains(tgt, line):
                coords.add(tgt)
        return coords

    def _bfs_universe(
        self, seed_edges: list[DataflowEdge], variable: str | None
    ) -> list[DataflowEdge]:
        """Full edge set for the seed's function(s) — the graph to traverse."""
        function_ids = {e.function_id for e in seed_edges}
        universe: list[DataflowEdge] = []
        for fid in function_ids:
            universe.extend(self._queries.get_dataflow_edges_by_function(fid))
        if variable is not None:
            universe = [e for e in universe if e.variable == variable]
        return universe

    @staticmethod
    def _build_adjacency(universe: list[DataflowEdge]):
        """Forward (source→target) and backward (target→source) adjacency."""
        forward: dict[Coord, list[tuple[DataflowEdge, Coord]]] = defaultdict(list)
        backward: dict[Coord, list[tuple[DataflowEdge, Coord]]] = defaultdict(list)
        for e in universe:
            src = (e.file_path, e.source_start_line, e.source_end_line)
            tgt = (e.file_path, e.target_start_line, e.target_end_line)
            forward[src].append((e, tgt))
            backward[tgt].append((e, src))
        return forward, backward

    def _bfs(
        self,
        seed_coords: set[Coord],
        forward: dict[Coord, list[tuple[DataflowEdge, Coord]]],
        backward: dict[Coord, list[tuple[DataflowEdge, Coord]]],
        direction: str,
        max_depth: int,
    ) -> tuple[set[Coord], list[DataflowEdge]]:
        do_forward = direction in ("forward", "both")
        do_backward = direction in ("backward", "both")

        reached: set[Coord] = set(seed_coords)
        traversed: list[DataflowEdge] = []
        seen: set[tuple] = set()
        queue: deque[tuple[Coord, int]] = deque((c, 0) for c in seed_coords)

        while queue:
            coord, depth = queue.popleft()
            if depth >= max_depth:
                continue
            neighbors: list[tuple[DataflowEdge, Coord]] = []
            if do_forward:
                neighbors.extend(forward.get(coord, ()))
            if do_backward:
                neighbors.extend(backward.get(coord, ()))
            for e, nbr in neighbors:
                key = (
                    e.file_path,
                    e.source_start_line,
                    e.source_end_line,
                    e.target_start_line,
                    e.target_end_line,
                    e.variable,
                )
                if key not in seen:
                    seen.add(key)
                    traversed.append(e)
                if nbr not in reached:
                    reached.add(nbr)
                    queue.append((nbr, depth + 1))

        return reached, traversed

    def _build_statements(
        self, reached: set[Coord], universe: list[DataflowEdge]
    ) -> dict[Coord, StatementRef]:
        coord_func: dict[Coord, str] = {}
        for e in universe:
            coord_func[self._source_coord(e)] = e.function_id
            coord_func[self._target_coord(e)] = e.function_id
        return {
            coord: StatementRef(
                file_path=coord[0],
                start_line=coord[1],
                end_line=coord[2],
                function_name=self._function_name(coord_func.get(coord)),
            )
            for coord in reached
        }

    @staticmethod
    def _function_name(function_id: str | None) -> str | None:
        """Derive a readable function name from a ``file.py::Name`` id."""
        if not function_id or "::" not in function_id:
            return None
        return function_id.split("::", 1)[1].replace("::", ".")

    def _fill_source_text(self, statements_by_coord: dict[Coord, StatementRef]) -> None:
        """Fill ``source_text`` on each statement, one file read per file."""
        by_file: dict[str, list[StatementRef]] = defaultdict(list)
        for coord, ref in statements_by_coord.items():
            by_file[coord[0]].append(ref)
        for file_path, refs in by_file.items():
            content = self._file_provider.read_file(file_path)
            if content is None:
                continue  # file absent or unreadable — leave source_text None
            lines = content.splitlines()
            for ref in refs:
                ref.source_text = "\n".join(lines[ref.start_line - 1 : ref.end_line])

    @staticmethod
    def _build_edges(
        traversed: list[DataflowEdge],
        statements_by_coord: dict[Coord, StatementRef],
    ) -> list[DataflowSliceEdge]:
        edges: list[DataflowSliceEdge] = []
        for e in traversed:
            src = statements_by_coord[
                (e.file_path, e.source_start_line, e.source_end_line)
            ]
            tgt = statements_by_coord[
                (e.file_path, e.target_start_line, e.target_end_line)
            ]
            edges.append(DataflowSliceEdge(source=src, target=tgt, variable=e.variable))
        return edges
