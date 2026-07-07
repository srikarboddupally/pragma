"""Hybrid retrieval — sparse (Postgres FTS) + dense fusion → rerank → MMR diversification.

Hot-path module (CLAUDE.md §5): read-only, never enqueues work, workspace-scoped on every
query. All I/O enters through injected callables (``Searcher``, ``Reranker``); fusion and
diversification are pure functions, so ranking behavior is unit-testable without a database.

Pipeline::

    dense_search (pgvector, injected) ──┐
                                        ├─→ rrf_fuse ─→ [:rerank_n] ─→ rerank ─→ mmr_select
    sparse_search (Postgres FTS) ───────┘

Stage order is deliberate: rerank runs BEFORE MMR because MMR's relevance term should use
the best relevance estimate available (the cross-encoder's, not the fusion score); MMR runs
LAST because diversity is a property of the final presented set, not of intermediate pools.
Each ambiguous design decision is explained at its site below — the short version:

- Fusion is Reciprocal Rank Fusion (rank-based), not weighted score combination, because
  FTS and cosine scores live on incomparable scales and — with a reranker downstream —
  fusion only has to be a *recall gate*, not a precision ranker.
- Sparse retrieval is Postgres full-text search, not an in-process BM25 index, because the
  index must live where the data lives (tenant scoping in SQL, no per-worker in-memory
  corpus, no hot/cold-path coupling for invalidation).
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import tables

# RRF's k dampens the advantage of rank 1 over rank 2 (1/(k+1) vs 1/(k+2)): small k makes
# fusion top-heavy, large k flattens it. 60 is the published default (Cormack et al. 2009)
# and is empirically stable; with a reranker downstream its exact value barely matters.
RRF_K = 60

# Rerank window: ~3x the final k (5-8 for RAG context), so the cross-encoder can rescue
# fusion mistakes without blowing the latency budget. Cross-encoder cost is linear in N —
# at N=20 one batched rerank call is ~100-150ms, which fits the <500ms hot-path p95
# alongside query embedding + retrieval; N=50 would eat the whole budget for a marginal
# recall gain (fusion already ranks plausible candidates high).
DEFAULT_RERANK_N = 20

# MMR lambda: relevance-dominant, diversity as a near-duplicate tiebreaker. At 0.7, an
# EXACT duplicate (sim=1.0) of an already-selected chunk survives only if its relevance
# edge over the alternative exceeds (1-λ)/λ ≈ 0.43 (normalized) — so near-dupes get
# displaced by any reasonably relevant alternative — while a merely topically-similar
# chunk (sim≈0.6) needs just a ~0.26 edge, so corroborating detail chunks survive.
# λ=0.5 would mean exact dupes NEVER win (over-diversifies away corroboration); λ=0.9
# barely penalizes duplicates at all.
DEFAULT_MMR_LAMBDA = 0.7


@dataclass(frozen=True)
class Candidate:
    """One retrieved chunk moving through the pipeline.

    ``score`` is stage-local: retriever score → RRF score after fusion → reranker score
    after reranking. Only its *relative order within one stage* is ever compared.
    """

    chunk_id: str
    content: str
    doc_id: str = ""
    source: str = ""
    doc_title: str | None = None
    doc_url: str | None = None
    embedding: list[float] | None = None  # reused by MMR's diversity term (no extra compute)
    score: float = 0.0


class Searcher(Protocol):
    """Any retriever: up to ``limit`` candidates for a workspace-scoped query.

    Dense search (pgvector) is injected behind this seam — it decouples ranking logic from
    storage and lets tests drive fusion with controlled result lists.
    """

    def __call__(self, query: str, workspace_id: str, limit: int) -> Awaitable[list[Candidate]]: ...


class Reranker(Protocol):
    """Cross-encoder reranker: returns candidates re-scored against the query.

    Contract: the returned candidates carry NEW ``score`` values (the cross-encoder's).
    The concrete client (hosted rerank API / local model) is a providers/ concern.
    """

    def rerank(self, query: str, candidates: Sequence[Candidate]) -> Awaitable[list[Candidate]]: ...


async def keyword_search(
    session: AsyncSession, query: str, workspace_id: str, limit: int = 30
) -> list[Candidate]:
    """Sparse retrieval via Postgres full-text search (the production ``sparse_search``).

    Why Postgres FTS and not an in-process BM25 index: an in-memory index needs the whole
    workspace corpus in the API process, rebuilt on every ingest, duplicated per uvicorn
    worker, and stale in between — coupling the hot path to cold-path invalidation, which
    the CQRS rule forbids. FTS lives with the data: transactionally consistent with chunk
    writes, and tenant isolation is a WHERE clause. The tradeoff — ``ts_rank_cd`` is not
    true BM25 (no IDF saturation) — is absorbed downstream: sparse retrieval only needs to
    *find* exact-match candidates; the reranker fixes their ordering.

    ``websearch_to_tsquery`` over ``plainto_``: never raises on arbitrary user input and
    supports quoted phrases — exactly the literal-error-string case that motivates sparse
    retrieval. (A generated tsvector column + GIN index is a later migration; unindexed
    FTS is fine at v1 volume.)

    Bind ``session`` at the call site (e.g. ``functools.partial``) to satisfy ``Searcher``.
    """
    ts_query = func.websearch_to_tsquery("english", query)
    ts_vector = func.to_tsvector("english", tables.Chunk.content)
    rank = func.ts_rank_cd(ts_vector, ts_query)
    stmt = (
        select(tables.Chunk, rank.label("rank"))
        .where(
            tables.Chunk.workspace_id == workspace_id,  # tenant isolation, always
            tables.Chunk.near_duplicate_of.is_(None),  # canonical chunks only
            ts_vector.bool_op("@@")(ts_query),
        )
        .order_by(rank.desc(), tables.Chunk.id)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        Candidate(
            chunk_id=chunk.id,
            content=chunk.content,
            doc_id=chunk.doc_id,
            source=chunk.source,
            doc_title=chunk.doc_title,
            doc_url=chunk.doc_url,
            embedding=list(chunk.embedding) if chunk.embedding is not None else None,
            score=float(score),
        )
        for chunk, score in rows
    ]


def rrf_fuse(result_lists: Sequence[Sequence[Candidate]], *, k: int = RRF_K) -> list[Candidate]:
    """Fuse ranked result lists with Reciprocal Rank Fusion: score(d) = Σ 1/(k + rank).

    Why RRF and not a weighted score combination (α·dense + (1-α)·sparse): the two score
    scales are incomparable — FTS rank scores are unbounded and corpus/length-dependent,
    cosine is bounded — so weighted fusion needs per-query min-max normalization whose
    endpoints shift with every result set; an α tuned on one query type misbehaves on
    another. RRF uses only ranks: scale-free, no normalization machinery, one stable
    parameter. Its weakness (discarding score magnitudes) is neutralized by the reranker,
    which re-scores the fused pool anyway — fusion is a recall gate here.

    A chunk found by BOTH retrievers sums two reciprocal terms (rank 3 + rank 4 ≈ 0.031)
    and outranks a single-list rank 1 (≈ 0.016): cross-retriever agreement is evidence.
    """
    scores: dict[str, float] = {}
    representative: dict[str, Candidate] = {}
    for results in result_lists:
        for rank, cand in enumerate(results, start=1):
            scores[cand.chunk_id] = scores.get(cand.chunk_id, 0.0) + 1.0 / (k + rank)
            kept = representative.get(cand.chunk_id)
            # Prefer the variant that carries an embedding — MMR needs it later.
            if kept is None or (kept.embedding is None and cand.embedding is not None):
                representative[cand.chunk_id] = cand
    fused = [replace(representative[cid], score=score) for cid, score in scores.items()]
    fused.sort(key=lambda c: (-c.score, c.chunk_id))  # chunk_id tiebreak → deterministic
    return fused


def mmr_select(
    candidates: Sequence[Candidate], k: int, *, lambda_: float = DEFAULT_MMR_LAMBDA
) -> list[Candidate]:
    """Select k results by Maximal Marginal Relevance: relevance minus redundancy.

    Greedy: each step takes argmax over ``λ·rel(d) − (1−λ)·max_sim(d, selected)``. The
    first pick has no redundancy penalty, so MMR never displaces the reranked #1 result.

    Why MMR at all when dedup links near-duplicates at ingest: ``near_duplicate_of`` only
    catches cross-source pairs above 0.97 similarity. Same-source paraphrases (0.85-0.97)
    and overlapping chunks of the same document (the chunker overlaps by construction)
    still crowd a top-k — MMR handles that residue at query time.

    Relevance scores are min-max normalized within the candidate set so they share a [0,1]
    scale with cosine similarity. That per-set normalization is safe HERE (unlike in
    fusion): it happens within a single stage where only relative order matters, not
    across two retrievers whose lists must be made comparable.
    """
    pool = list(candidates)
    if k <= 0 or not pool:
        return []
    rels = _min_max_normalize([c.score for c in pool])
    remaining: list[tuple[Candidate, float]] = list(zip(pool, rels, strict=True))
    selected: list[Candidate] = []
    while remaining and len(selected) < k:
        best_index = 0
        best_value = float("-inf")
        for i, (cand, rel) in enumerate(remaining):
            redundancy = max((_cosine(cand.embedding, s.embedding) for s in selected), default=0.0)
            value = lambda_ * rel - (1.0 - lambda_) * redundancy
            if value > best_value:
                best_index, best_value = i, value
        selected.append(remaining.pop(best_index)[0])
    return selected


async def hybrid_search(
    query: str,
    workspace_id: str,
    *,
    dense_search: Searcher,
    sparse_search: Searcher,
    k: int = 8,
    fetch_limit: int = 30,
    rerank_n: int = DEFAULT_RERANK_N,
    reranker: Reranker | None = None,
    mmr_lambda: float = DEFAULT_MMR_LAMBDA,
) -> list[Candidate]:
    """Full hybrid pipeline: retrieve (parallel) → fuse → rerank top-N → MMR-select top-k.

    ``fetch_limit=30`` per retriever: enough overlap evidence for fusion while keeping both
    queries cheap; the fused union is truncated to ``rerank_n`` anyway. Candidates below
    the rerank window are dropped — if fusion ranked a chunk past 20, it was not credibly
    making a top-8. With ``reranker=None`` the fused RRF order feeds MMR directly.
    """
    dense, sparse = await asyncio.gather(
        dense_search(query, workspace_id, fetch_limit),
        sparse_search(query, workspace_id, fetch_limit),
    )
    fused = rrf_fuse([dense, sparse])
    pool = fused[:rerank_n]
    if reranker is not None:
        pool = await reranker.rerank(query, pool)
    return mmr_select(pool, k, lambda_=mmr_lambda)


def _min_max_normalize(scores: Sequence[float]) -> list[float]:
    lo, hi = min(scores), max(scores)
    if hi == lo:
        return [1.0] * len(scores)  # all equally relevant → MMR degrades to pure diversity
    return [(s - lo) / (hi - lo) for s in scores]


def _cosine(a: Sequence[float] | None, b: Sequence[float] | None) -> float:
    """Cosine similarity; 0.0 when either side is missing (unknown ≠ redundant).

    Pure Python on purpose: the MMR pool is ≤ rerank_n=20 vectors of ~1024 dims, a few
    hundred thousand float ops — numpy would add a dependency to save microseconds.
    """
    if a is None or b is None:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
