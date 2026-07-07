"""Hybrid retrieval behavior tests.

Fusion, rerank-window, and MMR tests are pure (fake retrievers — they test the ranking
math, which is exactly what fakes are for). ``keyword_search`` is DB logic, so it gets one
real-Postgres test (skips without Docker, per conftest) covering FTS matching AND tenant
isolation.
"""

from __future__ import annotations

from dataclasses import replace

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.retrieval.hybrid import (
    Candidate,
    hybrid_search,
    keyword_search,
    mmr_select,
    rrf_fuse,
)

DIM = 1024


def _cand(cid: str, *, score: float = 0.0, embedding: list[float] | None = None) -> Candidate:
    return Candidate(chunk_id=cid, content=f"content {cid}", embedding=embedding, score=score)


def _searcher(results: list[Candidate]):  # noqa: ANN202
    async def search(query: str, workspace_id: str, limit: int) -> list[Candidate]:
        return list(results)[:limit]

    return search


class RecordingReranker:
    """Reverses the pool and assigns fresh descending scores (the Reranker contract)."""

    def __init__(self) -> None:
        self.pool_sizes: list[int] = []

    async def rerank(self, query: str, candidates) -> list[Candidate]:  # noqa: ANN001
        self.pool_sizes.append(len(candidates))
        reordered = list(reversed(candidates))
        return [replace(c, score=float(len(reordered) - i)) for i, c in enumerate(reordered)]


# --- fusion behavior ----------------------------------------------------------------------


async def test_keyword_query_surfaces_sparse_only_chunk() -> None:
    """A chunk only BM25/FTS found (exact 'PR #47' match) must reach the final top-k."""
    exact = _cand("pr-47-exact", embedding=[1.0, 0.0])
    dense_only = [_cand(f"sem-{i}", embedding=[0.0, 1.0]) for i in range(5)]
    out = await hybrid_search(
        "PR #47",
        "ws1",
        dense_search=_searcher(dense_only),
        sparse_search=_searcher([exact]),
        k=3,
    )
    assert "pr-47-exact" in [c.chunk_id for c in out]


async def test_semantic_query_surfaces_dense_only_chunk() -> None:
    """Symmetric case: a chunk only dense search found must reach the final top-k."""
    semantic = _cand("semantic-hit", embedding=[1.0, 0.0])
    sparse_only = [_cand(f"kw-{i}", embedding=[0.0, 1.0]) for i in range(5)]
    out = await hybrid_search(
        "how do refunds work for enterprise customers",
        "ws1",
        dense_search=_searcher([semantic]),
        sparse_search=_searcher(sparse_only),
        k=3,
    )
    assert "semantic-hit" in [c.chunk_id for c in out]


def test_rrf_agreement_beats_single_list_rank_one() -> None:
    """Found-by-both (ranks 3+4) must outrank found-by-one at rank 1 — agreement is evidence."""
    both = _cand("both")
    dense = [_cand("d1"), _cand("d2"), both]
    sparse = [_cand("s1"), _cand("s2"), _cand("s3"), both]
    fused = rrf_fuse([dense, sparse])
    assert fused[0].chunk_id == "both"
    # 1/(60+3) + 1/(60+4) vs 1/(60+1) for the single-list rank-1 chunk.
    assert fused[0].score > next(c.score for c in fused if c.chunk_id == "d1")


def test_rrf_prefers_variant_with_embedding() -> None:
    """When the same chunk appears in both lists, keep the copy that has an embedding."""
    with_emb = _cand("x", embedding=[1.0, 0.0])
    without_emb = _cand("x")
    fused = rrf_fuse([[without_emb], [with_emb]])
    assert len(fused) == 1
    assert fused[0].embedding == [1.0, 0.0]


# --- MMR behavior -------------------------------------------------------------------------


def test_mmr_diversifies_near_duplicates() -> None:
    """Two near-identical chunks at the top: only one selected, diverse runner-up promoted."""
    same = [1.0, 0.0, 0.0]
    dup_a = _cand("dup-a", score=1.0, embedding=same)
    dup_b = _cand("dup-b", score=0.95, embedding=same)
    fresh = _cand("fresh", score=0.6, embedding=[0.0, 1.0, 0.0])
    filler = _cand("filler", score=0.0, embedding=[0.0, 0.0, 1.0])
    out = mmr_select([dup_a, dup_b, fresh, filler], 2)
    assert [c.chunk_id for c in out] == ["dup-a", "fresh"]


def test_mmr_lambda_one_is_pure_relevance() -> None:
    same = [1.0, 0.0]
    dup_a = _cand("dup-a", score=1.0, embedding=same)
    dup_b = _cand("dup-b", score=0.95, embedding=same)
    fresh = _cand("fresh", score=0.6, embedding=[0.0, 1.0])
    out = mmr_select([dup_a, dup_b, fresh], 2, lambda_=1.0)
    assert [c.chunk_id for c in out] == ["dup-a", "dup-b"]


def test_mmr_never_displaces_top_result() -> None:
    """First pick has no redundancy penalty → always the highest-relevance candidate."""
    top = _cand("top", score=5.0, embedding=[1.0, 0.0])
    other = _cand("other", score=1.0, embedding=[0.0, 1.0])
    assert mmr_select([other, top], 1)[0].chunk_id == "top"


# --- pipeline orchestration ---------------------------------------------------------------


async def test_reranker_sees_exactly_rerank_n_candidates() -> None:
    dense = [_cand(f"d-{i:02d}", embedding=[1.0]) for i in range(30)]
    reranker = RecordingReranker()
    out = await hybrid_search(
        "q",
        "ws1",
        dense_search=_searcher(dense),
        sparse_search=_searcher([]),
        k=5,
        rerank_n=20,
        reranker=reranker,
    )
    assert reranker.pool_sizes == [20]
    # The reranker's new ordering (reversed + rescored) drives the final selection.
    assert out[0].chunk_id == "d-19"


async def test_empty_retrievals_return_empty() -> None:
    out = await hybrid_search(
        "q", "ws1", dense_search=_searcher([]), sparse_search=_searcher([]), k=5
    )
    assert out == []


async def test_k_larger_than_pool_returns_all() -> None:
    dense = [_cand("a", embedding=[1.0, 0.0]), _cand("b", embedding=[0.0, 1.0])]
    out = await hybrid_search(
        "q", "ws1", dense_search=_searcher(dense), sparse_search=_searcher([]), k=10
    )
    assert len(out) == 2


# --- keyword_search against real Postgres (skips without Docker) ---------------------------


@pytest_asyncio.fixture
async def db_session(migrated_pg_url: str):  # noqa: ANN201
    engine = create_async_engine(migrated_pg_url)
    zero_vec = "[" + ",".join("0" for _ in range(DIM)) + "]"
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM chunks"))
        await conn.execute(text("DELETE FROM documents"))
        await conn.execute(
            text(
                "INSERT INTO workspaces (id, name) VALUES ('ws1','W1'), ('ws2','W2') "
                "ON CONFLICT DO NOTHING"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO documents (id, workspace_id, source, source_id, doc_type, "
                "content_hash) VALUES "
                "('d1','ws1','github','r#47','pr','h1'), "
                "('d2','ws2','github','r#47b','pr','h2')"
            )
        )
        for cid, doc, ws, content in [
            ("c1", "d1", "ws1", "PR #47 fixes the refund flow for enterprise plans"),
            ("c2", "d1", "ws1", "notes about the deployment schedule"),
            ("c3", "d2", "ws2", "PR #47 discussion in another workspace"),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO chunks (id, doc_id, workspace_id, source, doc_type, content, "
                    "embedding, embedding_model) VALUES "
                    "(:id, :doc, :ws, 'github', 'pr', :content, CAST(:emb AS vector), "
                    "'voyage-3.5')"
                ),
                {"id": cid, "doc": doc, "ws": ws, "content": content, "emb": zero_vec},
            )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_keyword_search_matches_tokens_and_scopes_workspace(db_session) -> None:  # noqa: ANN001
    hits = await keyword_search(db_session, "PR #47", "ws1", limit=10)
    ids = [c.chunk_id for c in hits]
    assert "c1" in ids  # exact-token match found
    assert "c2" not in ids  # non-matching content excluded
    assert "c3" not in ids  # other workspace NEVER leaks (tenant isolation)
    assert hits[0].embedding is not None  # embedding round-trips for MMR
