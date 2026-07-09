"""Integration tests for the Summary Claims semantic overlay (ADR-0004).

These tests drive the feature end-to-end through the ``CodeGraph`` public API
(``load_claims`` / ``clear_claims`` / ``search_claims_fts``) — the single seam
agreed for this feature. They assert on observable behaviour (which claims are
retrieved, which grounding spans are attached), never on SQL, raw tables, or
FTS internals.
"""

from __future__ import annotations

from pycodegraph import CodeGraph
from pycodegraph.types import ClaimGrounding, Node, SummaryClaim


class TestClaimsTracer:
    """Tracer bullet: load one claim, retrieve it by a stemmed paraphrase."""

    def test_load_claim_then_stemmed_search_returns_bundle(self, empty_codegraph):
        cg = empty_codegraph
        cg.load_claims(
            [
                SummaryClaim(
                    claim_type="behavior_contract",
                    claim_text="decompress=False preserves the raw compressed payload",
                    groundings=[
                        ClaimGrounding(
                            file_path="lib/urls.py",
                            start_line=142,
                            end_line=158,
                            relation="subject",
                        )
                    ],
                )
            ]
        )

        hits = cg.search_claims_fts("decompression")

        assert len(hits) == 1
        hit = hits[0]
        assert hit.claim_type == "behavior_contract"
        assert "decompress" in hit.claim_text
        assert len(hit.groundings) == 1
        span = hit.groundings[0]
        assert span.file_path == "lib/urls.py"
        assert span.start_line == 142
        assert span.end_line == 158
        assert span.relation == "subject"
        assert hit.score > 0


class TestClearClaims:
    """clear_claims() removes all claims and their grounding spans."""

    def test_clear_then_search_returns_empty(self, empty_codegraph):
        cg = empty_codegraph
        cg.load_claims(
            [
                SummaryClaim(
                    claim_type="behavior_contract",
                    claim_text="the decompressor caches decoded payloads",
                    groundings=[
                        ClaimGrounding(
                            file_path="lib/cache.py",
                            start_line=10,
                            end_line=20,
                            relation="subject",
                        )
                    ],
                )
            ]
        )
        assert cg.search_claims_fts("decompressor")  # something is indexed

        cg.clear_claims()

        assert cg.search_claims_fts("decompressor") == []


class TestClaimGroundings:
    """A claim carries multiple grounding spans with distinct relations."""

    def test_multiple_grounding_spans_with_different_relations(self, empty_codegraph):
        cg = empty_codegraph
        cg.load_claims(
            [
                SummaryClaim(
                    claim_type="behavior_contract",
                    claim_text="the retry loop backs off exponentially on timeouts",
                    groundings=[
                        ClaimGrounding(
                            file_path="lib/retry.py",
                            start_line=30,
                            end_line=44,
                            relation="subject",
                        ),
                        ClaimGrounding(
                            file_path="lib/retry.py",
                            start_line=88,
                            end_line=95,
                            relation="evidence",
                        ),
                    ],
                )
            ]
        )

        hits = cg.search_claims_fts("retry")

        assert len(hits) == 1
        spans = hits[0].groundings
        assert len(spans) == 2
        relations = {span.relation for span in spans}
        assert relations == {"subject", "evidence"}
        assert {(s.file_path, s.start_line, s.end_line) for s in spans} == {
            ("lib/retry.py", 30, 44),
            ("lib/retry.py", 88, 95),
        }

    def test_claim_with_no_grounding_is_retrievable(self, empty_codegraph):
        cg = empty_codegraph
        cg.load_claims(
            [
                SummaryClaim(
                    claim_type="architecture_component",
                    claim_text="the gateway is the single ingress for all public traffic",
                )
            ]
        )

        hits = cg.search_claims_fts("gateway")

        assert len(hits) == 1
        hit = hits[0]
        assert hit.claim_type == "architecture_component"
        assert hit.groundings == []


class TestClaimTypeFilter:
    """search_claims_fts honors an optional claim_type filter."""

    def test_claim_type_filter_restricts_results(self, empty_codegraph):
        cg = empty_codegraph
        cg.load_claims(
            [
                SummaryClaim(
                    claim_type="behavior_contract",
                    claim_text="the queue dispatches tasks to worker pools",
                ),
                SummaryClaim(
                    claim_type="architecture_component",
                    claim_text="the queue is backed by a durable log",
                ),
            ]
        )

        behavior_hits = cg.search_claims_fts("queue", claim_type="behavior_contract")

        assert {h.claim_type for h in behavior_hits} == {"behavior_contract"}
        assert all("queue" in h.claim_text for h in behavior_hits)


class TestClaimSearchLimit:
    """search_claims_fts respects the limit parameter."""

    def test_limit_caps_number_of_results(self, empty_codegraph):
        cg = empty_codegraph
        cg.load_claims(
            [
                SummaryClaim(
                    claim_type="behavior_contract",
                    claim_text=f"the cache serves key {i}",
                )
                for i in range(5)
            ]
        )

        hits = cg.search_claims_fts("cache", limit=2)

        assert len(hits) == 2


class TestClaimSearchEdgeCases:
    """Edge cases: empty store and empty query."""

    def test_searching_empty_store_returns_empty_list(self, empty_codegraph):
        cg = empty_codegraph

        assert cg.search_claims_fts("anything") == []

    def test_empty_or_whitespace_query_does_not_error(self, empty_codegraph):
        cg = empty_codegraph
        cg.load_claims(
            [
                SummaryClaim(
                    claim_type="behavior_contract",
                    claim_text="the scheduler coalesces periodic ticks",
                )
            ]
        )

        for blank_query in ("", "   ", "\t"):
            assert cg.search_claims_fts(blank_query) == []


class TestClaimsPersistence:
    """Loaded claims survive closing and reopening the CodeGraph."""

    def test_claims_persist_across_reopen(self, tmp_path):
        root = str(tmp_path)
        cg = CodeGraph.init(root)
        try:
            cg.load_claims(
                [
                    SummaryClaim(
                        claim_type="behavior_contract",
                        claim_text="the decompressor caches decoded payloads",
                        groundings=[
                            ClaimGrounding(
                                file_path="lib/cache.py",
                                start_line=10,
                                end_line=20,
                                relation="subject",
                            )
                        ],
                    )
                ]
            )
        finally:
            cg.close()

        reopened = CodeGraph.open(root)
        try:
            hits = reopened.search_claims_fts("decompression")
        finally:
            reopened.close()

        assert len(hits) == 1
        hit = hits[0]
        assert "decompress" in hit.claim_text
        assert len(hit.groundings) == 1
        span = hit.groundings[0]
        assert span.file_path == "lib/cache.py"
        assert (span.start_line, span.end_line) == (10, 20)


class TestClaimsDoNotPolluteNodeSearch:
    """Symbol search() stays Node-only; claims never leak into it."""

    def test_node_search_excludes_claims_and_still_finds_nodes(
        self, create_python_project, codegraph_from_project
    ):
        root = create_python_project()
        cg = codegraph_from_project(root)
        cg.load_claims(
            [
                SummaryClaim(
                    claim_type="behavior_contract",
                    claim_text="the floozle widget retries on transient errors",
                )
            ]
        )

        # Claim text does not surface through symbol search.
        assert cg.search("floozle") == []

        # Symbol search still returns real Nodes, not claims.
        node_hits = cg.search("User")
        assert node_hits
        assert all(isinstance(n, Node) for n in node_hits)
