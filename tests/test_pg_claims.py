"""PostgreSQL validation for the Summary Claims overlay (ADR-0004 slice 2).

Mirrors ``tests/test_claims.py`` against the PostgreSQL backend so the claim
store + FTS retrieval path is held to the same contract as the SQLite tracer
bullet (#94). All assertions are on observable behaviour through the
``CodeGraph`` public-API seam — no SQL or FTS-internal assertions.

Requires a running PG instance. Skips all tests if unavailable.

Usage:
    FORMSY_PG_DSN="host=localhost port=5433 dbname=ai user=admin password=admin" \\
        pytest tests/test_pg_claims.py
"""

from __future__ import annotations

import os
import shutil

import pytest

psycopg = pytest.importorskip("psycopg")

from pycodegraph import CodeGraph  # noqa: E402
from pycodegraph.types import ClaimGrounding, Node, SummaryClaim  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PG_DSN = os.environ.get(
    "FORMSY_PG_DSN",
    "host=localhost port=5433 dbname=ai user=admin password=admin",
)
TEST_DB = "codegraph_claims_test"


def _build_sa_url(dsn: str, dbname: str) -> str:
    parts: dict[str, str] = {}
    for token in dsn.split():
        k, _, v = token.partition("=")
        parts[k] = v
    host = parts.get("host", "localhost")
    port = parts.get("port", "5432")
    user = parts.get("user", "postgres")
    password = parts.get("password", "")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{dbname}"


TEST_DB_URL = _build_sa_url(PG_DSN, TEST_DB)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _pg_available() -> bool:
    try:
        with psycopg.connect(PG_DSN, autocommit=True) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


pg_available = pytest.mark.skipif(
    not _pg_available(), reason="PostgreSQL not available"
)


@pytest.fixture(scope="module")
def _pg_db():
    """Create the test database once per module; drop on teardown."""
    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
        conn.execute(f"CREATE DATABASE {TEST_DB}")
    yield
    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            [TEST_DB],
        )
        conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")


@pytest.fixture
def pg_codegraph(_pg_db, tmp_path):
    """A PG-backed CodeGraph on an empty project; claims cleared per test."""
    tmp_root = str(tmp_path)
    cg = CodeGraph.init(tmp_root, config_overrides={"db_url": TEST_DB_URL})
    cg.clear_claims()
    yield cg
    cg.clear_claims()
    cg.close()
    shutil.rmtree(tmp_root, ignore_errors=True)


@pytest.fixture
def pg_codegraph_indexed(_pg_db, tmp_path, create_python_project):
    """A PG-backed CodeGraph with the synthetic Python project indexed."""
    root = create_python_project()
    cg = CodeGraph.init(
        root,
        config_overrides={
            "db_url": TEST_DB_URL,
            "root_dir": root,
            "include": ["**/*.py"],
        },
    )
    cg.index_all()
    cg.clear_claims()
    yield cg
    cg.clear_claims()
    cg.close()


# ---------------------------------------------------------------------------
# Tests — mirror tests/test_claims.py one-for-one
# ---------------------------------------------------------------------------


@pg_available
class TestClaimsTracer:
    """Tracer bullet: load one claim, retrieve it by a stemmed paraphrase."""

    def test_load_claim_then_stemmed_search_returns_bundle(self, pg_codegraph):
        cg = pg_codegraph
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


@pg_available
class TestClearClaims:
    """clear_claims() removes all claims and their grounding spans."""

    def test_clear_then_search_returns_empty(self, pg_codegraph):
        cg = pg_codegraph
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


@pg_available
class TestClaimGroundings:
    """A claim carries multiple grounding spans with distinct relations."""

    def test_multiple_grounding_spans_with_different_relations(self, pg_codegraph):
        cg = pg_codegraph
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

    def test_claim_with_no_grounding_is_retrievable(self, pg_codegraph):
        cg = pg_codegraph
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


@pg_available
class TestClaimTypeFilter:
    """search_claims_fts honors an optional claim_type filter."""

    def test_claim_type_filter_restricts_results(self, pg_codegraph):
        cg = pg_codegraph
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


@pg_available
class TestClaimSearchLimit:
    """search_claims_fts respects the limit parameter."""

    def test_limit_caps_number_of_results(self, pg_codegraph):
        cg = pg_codegraph
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


@pg_available
class TestClaimSearchEdgeCases:
    """Edge cases: empty store and empty query."""

    def test_searching_empty_store_returns_empty_list(self, pg_codegraph):
        cg = pg_codegraph

        assert cg.search_claims_fts("anything") == []

    def test_empty_or_whitespace_query_does_not_error(self, pg_codegraph):
        cg = pg_codegraph
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


@pg_available
class TestClaimsPersistence:
    """Loaded claims survive closing and reopening the CodeGraph."""

    def test_claims_persist_across_reopen(self, tmp_path, _pg_db):
        tmp_root = str(tmp_path)
        cg = CodeGraph.init(tmp_root, config_overrides={"db_url": TEST_DB_URL})
        try:
            cg.clear_claims()
            cg.load_claims(
                [
                    SummaryClaim(
                        claim_type="behavior_contract",
                        # 'decompressed' stems to 'decompress' under PG's
                        # 'english' (Snowball) config, as does the query
                        # 'decompression' — exercising the stemmer.
                        claim_text="the cache stores decompressed payloads",
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

        reopened = CodeGraph.open(tmp_root)
        try:
            hits = reopened.search_claims_fts("decompression")
        finally:
            reopened.clear_claims()
            reopened.close()

        assert len(hits) == 1
        hit = hits[0]
        assert "decompress" in hit.claim_text
        assert len(hit.groundings) == 1
        span = hit.groundings[0]
        assert span.file_path == "lib/cache.py"
        assert (span.start_line, span.end_line) == (10, 20)


@pg_available
class TestClaimsDoNotPolluteNodeSearch:
    """Symbol search() stays Node-only; claims never leak into it."""

    def test_node_search_excludes_claims_and_still_finds_nodes(
        self, pg_codegraph_indexed
    ):
        cg = pg_codegraph_indexed
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
