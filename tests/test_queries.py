"""QueryBuilder unit tests — SQLite in-memory, no project indexing."""

from __future__ import annotations

from sqlalchemy import create_engine, event

from pycodegraph.db.queries import QueryBuilder
from pycodegraph.db.tables import metadata
from pycodegraph.types import Edge, EdgeKind, Language, Node, NodeKind


def _node(node_id: str, name: str, file_path: str = "a.py") -> Node:
    return Node(
        id=node_id,
        kind=NodeKind.FUNCTION,
        name=name,
        qualified_name=name,
        file_path=file_path,
        language=Language.PYTHON,
        start_line=1,
        end_line=1,
        start_column=0,
        end_column=10,
        updated_at=0,
    )


def _queries() -> tuple[QueryBuilder, object]:
    engine = create_engine("sqlite:///:memory:")
    conn = engine.connect()
    metadata.create_all(conn)
    return QueryBuilder(conn), conn


class TestGetOutgoingEdgesForSources:
    def test_empty_source_ids_returns_empty_without_query(self):
        queries, conn = _queries()
        try:
            sql: list[str] = []

            @event.listens_for(conn, "before_cursor_execute")
            def _count(conn, cursor, statement, parameters, context, executemany):
                sql.append(statement)

            assert queries.get_outgoing_edges_for_sources([]) == []
            assert sql == []
        finally:
            conn.close()

    def test_returns_union_of_per_source_outgoing_edges(self):
        queries, conn = _queries()
        try:
            queries.insert_nodes(
                [_node("a", "a"), _node("b", "b"), _node("c", "c"), _node("t", "t")]
            )
            queries.insert_edges(
                [
                    Edge(source="a", target="t", kind=EdgeKind.CALLS),
                    Edge(source="b", target="t", kind=EdgeKind.IMPORTS),
                    Edge(source="c", target="t", kind=EdgeKind.REFERENCES),
                ]
            )

            batched = queries.get_outgoing_edges_for_sources(["a", "b"])
            per_id = queries.get_outgoing_edges("a") + queries.get_outgoing_edges("b")

            batched_pairs = sorted((e.source, e.target, e.kind) for e in batched)
            per_id_pairs = sorted((e.source, e.target, e.kind) for e in per_id)
            assert batched_pairs == per_id_pairs
            assert batched_pairs == [
                ("a", "t", EdgeKind.CALLS),
                ("b", "t", EdgeKind.IMPORTS),
            ]
        finally:
            conn.close()

    def test_kind_filter_excludes_other_edge_kinds(self):
        queries, conn = _queries()
        try:
            queries.insert_nodes([_node("a", "a"), _node("t", "t")])
            queries.insert_edges(
                [
                    Edge(source="a", target="t", kind=EdgeKind.CALLS),
                    Edge(source="a", target="t", kind=EdgeKind.IMPORTS),
                ]
            )

            result = queries.get_outgoing_edges_for_sources(
                ["a"], kinds=[EdgeKind.IMPORTS.value]
            )
            assert [e.kind for e in result] == [EdgeKind.IMPORTS]
        finally:
            conn.close()

    def test_chunks_source_ids_and_returns_every_matching_edge(self):
        queries, conn = _queries()
        try:
            sources = [f"s{i}" for i in range(5)]
            queries.insert_nodes([_node("t", "t")] + [_node(s, s) for s in sources])
            queries.insert_edges(
                [Edge(source=s, target="t", kind=EdgeKind.CALLS) for s in sources]
            )

            sql: list[str] = []

            @event.listens_for(conn, "before_cursor_execute")
            def _count(conn, cursor, statement, parameters, context, executemany):
                sql.append(" ".join(statement.split()))

            result = queries.get_outgoing_edges_for_sources(sources, chunk_size=2)
            assert sorted(e.source for e in result) == sources
            edge_selects = [s for s in sql if "FROM edges" in s]
            assert len(edge_selects) == 3  # 5 ids / chunk_size 2
        finally:
            conn.close()
