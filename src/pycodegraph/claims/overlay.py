"""Storage and retrieval for the Summary Claims semantic overlay (ADR-0004).

This module owns claim-domain shaping — id generation, row building, and
assembling retrieved claims into :class:`ClaimHit` bundles with their
grounding spans. Raw SQL is delegated to :class:`QueryBuilder`.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from ..types import ClaimHit, SummaryClaim

if TYPE_CHECKING:
    from ..db.queries import QueryBuilder


class ClaimOverlay:
    """High-level overlay for Summary Claims over a CodeGraph database."""

    def __init__(self, queries: QueryBuilder) -> None:
        self._queries = queries

    def load_claims(self, claims: list[SummaryClaim]) -> None:
        if not claims:
            return
        claim_rows: list[dict] = []
        grounding_rows: list[dict] = []
        for claim in claims:
            claim_id = uuid.uuid4().hex
            claim_rows.append(
                {
                    "id": claim_id,
                    "claim_type": claim.claim_type,
                    "claim_text": claim.claim_text,
                }
            )
            for grounding in claim.groundings:
                grounding_rows.append(
                    {
                        "claim_id": claim_id,
                        "file_path": grounding.file_path,
                        "start_line": grounding.start_line,
                        "end_line": grounding.end_line,
                        "relation": grounding.relation,
                    }
                )
        self._queries.insert_claims(claim_rows, grounding_rows)

    def clear_claims(self) -> None:
        self._queries.delete_all_claims()

    def search_claims_fts(
        self, query: str, claim_type: str | None = None, limit: int = 20
    ) -> list[ClaimHit]:
        claim_rows = self._queries.search_claims_fts(query, claim_type, limit)
        if not claim_rows:
            return []
        groundings = self._queries.get_groundings_for_claims(
            [row["id"] for row in claim_rows]
        )
        return [
            ClaimHit(
                claim_text=row["claim_text"],
                claim_type=row["claim_type"],
                groundings=groundings.get(row["id"], []),
                score=row["score"],
            )
            for row in claim_rows
        ]
