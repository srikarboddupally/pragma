# Pragma — Build Plan (per-function)

This is the exhaustive, function-by-function plan referenced by [`../CLAUDE.md`](../CLAUDE.md). It exists so a junior developer (or an AI agent) can build Pragma without re-deriving the design each session. Read the [Senior Engineering Review](../CLAUDE.md#4-senior-engineering-review--decisions--changes-from-the-spec) in CLAUDE.md first — every deviation from `PRAGMA.md` is justified there.

**How to read this:** each phase lists files, and for each file the functions with their signature, what they do, edge cases to handle, and the tests to write. Signatures are guidance — match the real `Document`/`Skill` types once `models/` exists. Build in order; keep the tree green (lint + tests) after each phase. **Build one file at a time. Read and understand each file before requesting the next — this plan describes what to build, not permission to generate a whole phase in one pass.**

Legend: 🟥 correctness-critical · 🔌 external call (must be mockable) · ⏱ hot path (<500ms, no cold work) · ❄ cold path (Celery only) · 🆕 pattern added in this revision.

---

## Engineering standards (cross-cutting — applies from Phase 2 onward)

These are the production patterns layered onto the existing design. They don't replace any prior decision (SERIALIZABLE dedup, HNSW over ivfflat, mechanical confidence scoring, insert-first idempotency, RLS on `audit_log`, structured LLM outputs, circuit breakers, backpressure) — they extend it.

### API design conventions 🆕
- **Versioning**: all routes under `/api/v1/...`. A breaking change ships as `/api/v2/...` alongside, never a silent mutation of `v1`.
- **Error format**: every non-2xx response is [RFC 7807 Problem Details](https://www.rfc-editor.org/rfc/rfc7952) — `{"type": "...", "title": "...", "status": 4xx, "detail": "...", "instance": "...", "trace_id": "..."}`. One FastAPI exception handler maps every raised exception (`GuardrailRejection`, `GuardrailEscalation`, validation errors) to this shape. No endpoint hand-rolls its own error body.
- **Pagination**: any list endpoint (`GET /skills`, `GET /audit-log`) uses cursor pagination (`?cursor=<opaque>&limit=50`), never offset. Offset pagination degrades badly on a growing `audit_log` table and can skip/duplicate rows under concurrent writes.
- **Idempotency-Key header**: every state-changing action endpoint (`POST /api/v1/actions/execute`) accepts a client-supplied `Idempotency-Key` header, separate from the internal `compute_idempotency_key` used for guardrail dedup. This lets a client safely retry a timed-out request without knowing whether the first attempt landed.

### Distributed systems patterns 🆕
- **Transactional outbox** (replaces bare `task.delay()` calls from inside a DB transaction): when `reingest_document` commits a `documents`/`chunks` write that should also trigger a downstream task (e.g. re-cluster skills), the task isn't enqueued directly — a row is written to an `outbox` table **in the same transaction**. A separate lightweight poller (or Postgres `LISTEN/NOTIFY`) reads unpublished outbox rows and enqueues the actual Celery task, then marks them published. This closes the classic **dual-write problem**: without it, a process can crash after committing the DB write but before enqueuing the task (silent data loss) or after enqueueing but before committing (task runs against data that was never actually saved).
- **Dead-letter queue**: Celery tasks (`ingest_document`, `sync_source`, `cluster_workspace`) get `autoretry_for=(Exception,)`, `retry_backoff=True`, `retry_backoff_max=600`, `retry_jitter=True`, `max_retries=5`. On final failure, the task and its error land in a `failed_tasks` table (not silently dropped) for manual replay or alerting. Exponential backoff **with jitter** specifically avoids the thundering-herd retry pattern where every failed task in a batch retries at the exact same moment.
- **At-least-once + idempotent consumers**: every task above assumes it may run more than once for the same input (Celery's own retry, a redelivered message, an operator re-triggering). This is why `reingest_document`'s hash-skip and `UNIQUE(source, source_id)` upsert matter beyond "nice to have" — they're what makes at-least-once delivery *safe* to build on, rather than needing exactly-once delivery (which distributed systems generally can't cheaply guarantee).
- **Saga-style compensation** (Phase 5): the guardrail pipeline's five checks are sequential and each can fail independently. If `execute_tool` succeeds but the audit write fails, that's a partial-failure state. Formalize: `execute_tool` writes a `pending` audit row *before* calling the executor, then updates it to `succeeded`/`failed` after. A reconciliation job (cron, low frequency) finds `pending` rows older than N minutes and either confirms completion with the downstream system or marks them `unknown` for human review. Never silently drop a partial failure.

### Observability 🆕
- **Distributed tracing**: the `trace_id` field already threaded through `Document`, `Chunk`, `GuardrailRequest`, and `AuditLogEntry` becomes a real OpenTelemetry trace ID, not just a log-correlation string. Instrument with `opentelemetry-instrumentation-fastapi` and `opentelemetry-instrumentation-celery` so a single ingested document's trace spans the API request, the Celery task, the embedding call, and the DB write — one trace, one view, across the hot/cold boundary.
- **Metrics**: export Prometheus metrics via `prometheus-fastapi-instrumentator` — request latency histograms (to actually verify the <500ms hot-path budget rather than eyeballing logs), Celery queue depth (feeds the existing `MAX_QUEUE_DEPTH` backpressure check), circuit breaker state per connector (open/closed/half-open), and dedup outcome counters (unchanged/updated/near-dup-linked).
- **Structured logs stay as-is** (loguru + redaction), but every log line inside a request/task now includes `trace_id` for correlation with the trace above.

### Resilience 🆕
- **Liveness vs readiness**: `GET /health` splits into `GET /health/live` (process is up — always 200 unless the process is dead) and `GET /health/ready` (DB pool + Redis reachable — 503 if not). A container orchestrator restarts on a failed liveness check but only stops *routing traffic* on a failed readiness check — conflating the two causes unnecessary restarts during a transient DB blip.
- **Graceful shutdown**: on `SIGTERM`, the API stops accepting new connections but finishes in-flight requests (FastAPI/uvicorn `--timeout-graceful-shutdown`); Celery workers finish their current task before exiting (`worker_shutdown` signal), rather than dropping a half-processed ingestion mid-write.
- **Connection pool sizing**: `create_async_engine(..., pool_size=10, max_overflow=5, pool_timeout=30)` — sized explicitly rather than left at defaults, since an under-sized pool silently serializes requests that should be concurrent, and an over-sized one can exhaust Postgres's own `max_connections` under multiple worker processes.

---

## Phase 0 — Foundation & scaffolding ✅ COMPLETE

*Goal: an installable, lint-clean, type-checked skeleton that boots empty and migrates an empty DB. No features.*

Built and verified: `pyproject.toml`, `config.py`, `logging.py`, `db/session.py`, `main.py` (app factory + `/health`), `worker.py`, Alembic wiring, `docker-compose.yml`, `conftest.py` with mocked externals, `.env.example`.

**🆕 Retrofit before Phase 2 starts:**
- Split `/health` into `/health/live` and `/health/ready` per the Resilience standard above.
- Add `opentelemetry-instrumentation-fastapi` init to `main.py`'s `create_app()`.
- These are small, additive changes to a file you already understand — don't rewrite `main.py`, extend it.

---

## Phase 1 — Data contracts (models + schema) ✅ COMPLETE

*Everything downstream depends on these.*

Built and verified: `models/document.py` (`Document`, `Chunk`, `EmbeddedChunk`, `compute_document_id`, `compute_content_hash`), `db/session.py` (async engine, `transaction()` context manager), `db/tables.py`, first migration.

**🆕 Retrofit before Phase 2 starts:**
- **HNSW tuning, made explicit** (was implicit in the original plan): `CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)`. `m` controls graph connectivity (higher = better recall, more memory); `ef_construction` controls index build quality (higher = slower build, better recall). These are reasonable production defaults for a corpus in the low millions of chunks — don't tune further until you have real recall/latency numbers to tune against.
- **Outbox table**: add `outbox(id, aggregate_id, event_type, payload jsonb, created_at, published_at nullable)` to the same migration series. This is what Phase 2's dedup writes will insert into transactionally, per the Distributed systems standard.
- **`failed_tasks` table**: `(id, task_name, args jsonb, error text, failed_at, retry_count)` for the dead-letter pattern.

---

## Phase 2 — Ingestion write path (cold) ❄

*Build the normalizer first (it defines the contract everything targets), then the pure transforms, then dedup, then connectors.*

### `backend/app/providers/embeddings.py` 🔌
- `class EmbeddingProvider(Protocol)`: `async def embed(texts: list[str]) -> list[list[float]]`; `model: str`; `dim: int`.
- `class VoyageEmbeddings(EmbeddingProvider)` (default, `voyage-3.5`), `class OpenAIEmbeddings`, `class LocalEmbeddings`. Factory `get_embedder() -> EmbeddingProvider` keyed on `settings.EMBEDDING_PROVIDER`.
- *Test:* factory returns the configured provider; `embed()` is mocked.

### `backend/app/providers/llm.py` 🔌
- `class LLMClient`: wraps the `anthropic` SDK.
  - `async def extract_json(prompt, schema: type[BaseModel], model="claude-haiku-4-5") -> BaseModel` 🟥 — structured outputs, schema-guaranteed.
  - `async def complete(prompt, model="claude-sonnet-4-6") -> str` — for RAG answers.
- *Test:* mocked; schema passed through; validated model returned.

### `backend/app/ingestion/normalizer.py`
- `async def normalize(raw: dict, source: str) -> Document` — dispatch to per-source normalizers. No downstream stage imports connector types.
- Per-source helpers: `_normalize_slack`, `_normalize_github`, `_normalize_notion`. Strip markdown/HTML, collapse whitespace, drop bot/CI messages, reassemble Slack threads chronologically, concat GitHub title+desc+comments, recurse Notion blocks.
- `async def ensure_title(doc, llm) -> Document` — LLM title only if none exists; cached.
- *Tests:* recorded raw payloads → expected `Document`; bot messages dropped; thread order; title caching.

### `backend/app/ingestion/chunker.py`
- `CHUNK_SIZES = {"thread":256, "pr":512, "page":768, "issue":256}`; 10% overlap; `RecursiveCharacterTextSplitter`.
- `def chunk_document(doc: Document) -> list[Chunk]` — pure function, full metadata carried forward, `position`/`total_chunks` set.
- **🆕 Chunk ID**: `id = sha256(f"{doc.id}:{chunk_content}")` — content-hash, not position-based, so a chunking-algorithm change doesn't silently invalidate stable IDs (see CLAUDE.md decision log).
- *Tests:* per-doc_type sizing; overlap; metadata carried; empty/short content.

### `backend/app/ingestion/embedder.py` 🔌
- `async def embed_chunks(chunks, embedder) -> list[EmbeddedChunk]` — batched, records `model`/`embedded_at`.
- `async def embed_query(text, embedder) -> list[float]` ⏱ — single embed, **same model** as chunks, used by hot-path search.
- *Tests:* batching boundaries; model recorded; mocked provider.

### `backend/app/dedup/dedup.py` 🟥
- `async def reingest_document(doc, embedder)` 🟥:
  1. Hash skip: stored `content_hash == new` → `SkipResult("unchanged")`.
  2. Changed: **`ON CONFLICT (id) DO UPDATE`** upsert at plain `READ COMMITTED` for the single-row `documents` write (a `UNIQUE` constraint already makes this race-proof — no SERIALIZABLE needed for a single-table upsert). Reserve `transaction(serializable=True)` for the multi-table chunk-replace step (delete old chunks by `doc_id` + insert new, atomically) where write-skew across two tables is the real risk.
  3. Near-dup: `async def check_near_duplicate(embedding, doc_id, workspace_id) -> str | None` — workspace-scoped HNSW query, similarity > 0.97 against other-source chunks → link via `near_duplicate_of`.
  4. **🆕 Outbox write**: inside the same transaction as the chunk replace, insert an `outbox` row (`event_type="chunks_updated"`) instead of calling `cluster_workspace.delay()` directly.
- *Tests:* 🟥 concurrent-insert race (two workers, same doc → exactly one row, via the UNIQUE constraint, not SERIALIZABLE); unchanged-skip; changed → atomic old-gone/new-present; near-dup linked not duplicated; outbox row present after commit.

### `backend/app/ingestion/connectors/base.py`
- `class Connector(ABC)`: `source: str`; `async def sync(workspace_id, since) -> AsyncIterator[dict]`.
- `@circuit(failure_threshold=3, recovery_timeout=60)` around API calls; `async def emit_with_backpressure(jobs)`; watermark read/write.
- *Tests:* breaker isolates one failing source; backpressure sleeps when queue deep.

### `backend/app/ingestion/connectors/slack.py` / `github.py` / `notion.py` 🔌
- Slack: OAuth token, thread assembly, reactions preserved in `metadata`. GitHub: GitHub App, `since`-incremental, review approvals in `metadata`. Notion: poll-only, recursive blocks.
- *Tests:* recorded fixtures; incremental `since`; mocked SDKs.

### `backend/app/tasks/ingest.py` ❄ 🆕 (dead-letter + outbox poller)
- `@celery_app.task(autoretry_for=(Exception,), retry_backoff=True, retry_backoff_max=600, retry_jitter=True, max_retries=5) def ingest_document(workspace_id, raw, source)` — on final failure, write to `failed_tasks` via `on_failure` handler, don't just let Celery discard it.
- `@celery_app.task def sync_source(workspace_id, source)` — watermark → connector.sync → backpressure-emit. Beat: every 15 min.
- `@celery_app.task def publish_outbox()` — polls unpublished `outbox` rows (or driven by `LISTEN/NOTIFY`), enqueues the real task, marks `published_at`. Beat: every few seconds, or event-driven.

**Phase 2 acceptance:** seeded workspace ingests end-to-end; dedup race + atomicity tests pass; a task that fails 5 times lands in `failed_tasks`, not silently dropped; outbox rows publish reliably even if the enqueue step crashes and retries.

---

## Phase 3 — Retrieval read path (hot) ⏱

*Strict <500ms, read-only, no cold work.*

### `backend/app/db/vector_store.py` ⏱
- `async def similarity_search(embedding, workspace_id, *, source=None, doc_type=None, top_k=5, min_similarity=0.6) -> list[ChunkHit]` — workspace-scoped, same-embedding-model filter, `ORDER BY embedding <=> $1 LIMIT k`.
- *Test:* ranked hits; same-model filter enforced; min_similarity respected (real Postgres).
- **Implements the `Searcher` protocol below** — `hybrid_search` takes this as an injected dependency rather than importing it directly, so retrieval logic stays decoupled from the storage backend (see `retrieval/hybrid.py`).

### `backend/app/retrieval/hybrid.py` ⏱ 🆕 (hybrid retrieval — designed via Fable 5, verified by hand)
*Pure dense search misses exact-match queries (a literal error string, "PR #47"); pure keyword search misses semantic intent. This module fuses both, reranks for precision, then diversifies with MMR. Hot path — <500ms p95, no cold-path coupling, workspace-scoped on every query.*

- `@dataclass(frozen=True) class Candidate`: `chunk_id, doc_id, content, embedding, score, source, doc_title, doc_url`.
- `class Searcher(Protocol)` / `class Reranker(Protocol)` — injected dependencies. `vector_store.similarity_search` implements `Searcher`; a concrete reranker client is a later `providers/` addition, not scaffolded yet. `reranker=None` skips that stage.
- `async def keyword_search(session, query, workspace_id, limit) -> list[Candidate]` — the production sparse searcher: Postgres full-text search, `websearch_to_tsquery('english', query)` + `ts_rank_cd` against `chunks.content`, filtered `WHERE workspace_id = ... AND near_duplicate_of IS NULL`.
  - **🆕 Why Postgres FTS, not an in-process BM25 index** (e.g. `rank_bm25`): an in-process index needs the whole workspace corpus in API-process memory, rebuilt on every ingest, duplicated per uvicorn worker, stale between rebuilds — that couples the hot path to cold-path invalidation, which the CQRS separation (Engineering standards, above) forbids. Postgres FTS lives where the data already lives: transactionally consistent, workspace scoping is a `WHERE` clause, zero new dependencies. Honest tradeoff: `ts_rank_cd` isn't true BM25 (no IDF saturation) — accepted, because fusion + rerank downstream fix ordering; FTS's only job here is recall.
  - `websearch_to_tsquery` over `plainto_tsquery` specifically: safe on arbitrary user input, supports quoted phrases — the exact "PR #47" / literal-error-string case this module exists for.
  - **Deferred, not blocking v1**: a generated `tsvector` column + GIN index (a schema migration, separate reviewable step). Without it, FTS sequential-scans — fine at current volume; revisit once real query latency data exists, not preemptively.
- `def rrf_fuse(result_lists: list[list[Candidate]], *, k: int = 60) -> list[Candidate]` 🟥 — pure. Reciprocal Rank Fusion: `score(d) = Σ 1/(k + rank)` across the lists a candidate appears in.
  - **🆕 Why RRF, not weighted score combination** (`α·dense + (1−α)·bm25`): dense (cosine, bounded 0–1) and sparse (FTS rank, unbounded, corpus-dependent) scores live on incomparable scales — weighted combination needs per-query min-max normalization, and that normalization is unstable (min/max shift per query, so a tuned `α` doesn't transfer across query types). RRF uses only ranks — scale-free, no normalization machinery, one well-studied default parameter (`k=60`).
  - **The key insight**: with a reranker downstream, fusion is a recall gate, not a precision ranker — its only job is getting true positives into the top-N window; the reranker re-scores them anyway. RRF's weakness (discards score magnitude) is neutralized by that division of labor; weighted fusion's weakness (fragile normalization) is not.
  - Bonus, verified property: a candidate found by *both* retrievers accumulates two reciprocal terms and outranks a candidate found by only one at rank 1 — cross-retriever agreement is itself evidence of relevance. Test asserts this directly.
- `def mmr_select(candidates: list[Candidate], k: int, *, lambda_: float = 0.7) -> list[Candidate]` 🟥 — pure. Iteratively picks `argmax λ·relevance(d) − (1−λ)·max_sim(d, already_selected)`.
  - **🆕 Why λ = 0.7** (verify this arithmetic yourself before trusting it — see worked example below): for an exact duplicate (`sim=1.0`) to still outrank a fresh candidate, its relevance edge must exceed `(1−λ)/λ`. At λ=0.7 that's ≈0.43 — a near-duplicate needs to be ~43% more relevant to survive, so exact dupes get displaced by almost any reasonable alternative, while a *topically* similar corroborating chunk (`sim≈0.6`) only needs a ~0.26 edge to stay. λ=0.5 → threshold =1.0 (over-diversifies, dupes essentially never win, even legitimate corroboration gets excluded). λ=0.9 → threshold ≈0.11 (barely penalizes dupes, they sneak through).
  - **Why MMR at all, given dedup already links near-duplicates at ingest**: `near_duplicate_of` only catches cross-source pairs above 0.97 similarity. Same-source paraphrases (0.85–0.97) and same-document overlapping chunks (the chunker's own 10% overlap, by construction) still crowd results. MMR is the hot-path cleanup for that residue.
  - Implementation: relevance scores min-max normalized *within the candidate set* to share a [0,1] scale with cosine similarity — safe here (unlike in fusion) because only relative order within one stage matters, not cross-query comparability. Reuses stored chunk embeddings for the similarity term, no extra compute. First pick is always the top-ranked candidate (penalty is 0 with nothing yet selected) — MMR never displaces the top result, only diversifies what follows it.
- `async def hybrid_search(query, workspace_id, *, dense_search: Searcher, sparse_search: Searcher, k=8, fetch_limit=30, rerank_n=20, reranker: Reranker | None = None, mmr_lambda=0.7) -> list[Candidate]` ⏱ — orchestrates the pipeline: `dense_search` and `sparse_search` run concurrently (`asyncio.gather`) → `rrf_fuse` → `[:rerank_n]` → `reranker` (optional) → `mmr_select` → top-`k`. Rerank happens *before* MMR (MMR's relevance term needs the best available estimate); diversification happens last (diversity is a property of the final presented set, not an intermediate one).
  - **🆕 Rerank window `rerank_n=20`**: final `k` for RAG context is ~5–8; window is ~3× that so reranking can rescue fusion mistakes, while cost stays linear and bounded — at N=20, one batched cross-encoder call (Voyage rerank API, same vendor as embeddings, or a small local model) costs roughly 100–150ms, fitting the 500ms budget alongside embed+retrieve. N=50 would consume the whole budget. Candidates fusion ranked past the window are dropped — if they weren't credibly making top-8, extending the window doesn't change that.
- *Tests* (`backend/tests/retrieval/test_hybrid.py`): keyword-heavy query → chunk found only by sparse lands in final top-k; semantic query → chunk found only by dense lands in final top-k; both-lists candidate outranks a single-list rank-1 candidate (RRF agreement property, verified numerically); MMR — two near-identical-embedding top candidates → only one selected, a diverse runner-up promoted; rerank window — fake reranker sees exactly `rerank_n` candidates, `reranker=None` path works; edge cases (empty retrievals → `[]`, `k` > pool size doesn't crash); one real-Postgres test seeding chunks across two workspaces, asserting FTS matches an exact phrase and returns only the caller's workspace (exercises the tenant-isolation rule end-to-end).
- No new dependencies. `vector_store.py` and this module share the `Searcher` protocol — building this before `vector_store.py` exists is intentional (dependency inversion), not a gap; `vector_store.similarity_search` slots in as `dense_search` once built.

### `backend/app/api/deps.py`
- `async def require_workspace(authorization: Header) -> Workspace` — `Bearer pk_live_{workspace_id}_{secret}`, argon2 hash lookup, 401 on mismatch.
- **🆕 Rate limit, made concrete**: Redis token bucket — `ALLOWANCE = tokens_remaining()`; refill `rate` tokens/sec up to `burst` capacity; reject with `429` + `Retry-After` header (part of the RFC 7807 body) when empty. Per-workspace, keyed on `workspace_id`, not per-IP (a workspace behind NAT shouldn't be penalized as one client).
- *Test:* valid key passes; tampered secret 401; cross-workspace key blocked; burst exhausted → 429 with correct `Retry-After`.

### `backend/app/api/search.py` ⏱
- `POST /api/v1/search` — `embed_query` → `hybrid_search` (dense via `vector_store.similarity_search`, sparse via `retrieval.hybrid.keyword_search`) → chunks + metadata + `query_embedding_model`. No Celery, no new-doc embedding.
- *Test:* 🟥 asserts zero Celery enqueue calls (mock the queue); p95 budget sanity via the Prometheus histogram, now accounting for the fusion+MMR overhead on top of raw vector search.

### `backend/app/api/ask.py` ⏱
- `POST /api/v1/ask` — skills-first, else RAG via `LLMClient.complete` with citation prompt. Returns `source_type` + sources.
- *Test:* skills path returns skill; fallback calls LLM (mocked), cites chunks.

**Phase 3 acceptance:** local p95 search < 500ms, verified by the metrics histogram, not eyeballed; hot-path-no-cold-work test passes; rate limiter enforces per-workspace burst correctly.

---

## Phase 4 — Skills extractor (cold) ❄

### `backend/app/skills/clusterer.py` ❄
- `def cluster_chunks(embeddings) -> np.ndarray` — HDBSCAN (`min_cluster_size=3, min_samples=2, metric="euclidean", cluster_selection_method="eom"`). `-1` = noise, never a skill.
- `async def load_workspace_embeddings(workspace_id) -> tuple[list[Chunk], np.ndarray]`.
- **🆕 `async def load_workspace_interaction_embeddings(workspace_id) -> tuple[list[Interaction], np.ndarray]`** — same clustering machinery, second input source: embeddings of past `/ask` queries, not just ingested chunks. Repeated, semantically-similar questions from different users are themselves a signal a skill should exist, even when no single source document states the pattern outright. This is what makes skill generation continuous — Pragma learns from its own use, not only from what was ingested once.
- *Tests:* cluster shape on synthetic embeddings; noise excluded; interaction clustering keyed on query embedding, not answer embedding (the recurring question shape is the signal, not any one answer's exact wording).

### `backend/app/models/interaction.py` 🆕
- `class Interaction(BaseModel)`: `id, workspace_id, user_id, query, query_embedding, answer, source_type: Literal["skill","rag"], cited_chunk_ids: list[str], feedback: Literal["up","down"] | None, created_at, trace_id`.
- Written by `api/ask.py` on every request — Pragma's own answers become raw material for future skill extraction, not just an audit record. `feedback` is optional, set later via `POST /api/v1/interactions/{id}/feedback` if the UI collects it — feedback strengthens or weakens an interaction's later contribution to a skill cluster, but is never required for clustering to run.

### `backend/app/db/tables.py` 🆕 addition
- `class InteractionORM`: mirrors `Interaction` above. `workspace_id` FK, `user_id` string, index on `(workspace_id, created_at)` for the digest/clustering sweep. Migration: new revision, not an edit to `0001_init` — same discipline as the earlier `workspace_id`-on-`chunks` fix.

### `backend/app/skills/extractor.py` 🟥 ❄ 🔌
- `async def extract_skill(cluster_chunks, llm) -> Skill | None` — `llm.extract_json(..., schema=Skill, model="claude-haiku-4-5")`, low temperature.
- **🆕 `async def extract_skill_from_interactions(cluster_interactions, llm) -> Skill | None`** — same extraction shape, different source: given a cluster of semantically-similar past queries (+ their answers, if consistent), synthesize a skill the same way. Resulting `Skill.sources` are tagged `source_kind: "interaction"` (see `models/skill.py` below) — always distinguishable from document-derived skills, so an auditor can tell "this came from what people asked" versus "this came from a document someone wrote."
- `def compute_confidence(cluster_chunks, contradictions) -> Literal["high","medium","low"]` 🟥 — **mechanical**, never LLM-decided (distinct sources + distinct docs + no contradictions).
- **🆕 `def compute_interaction_confidence(cluster_interactions) -> Literal["high","medium","low"]`** 🟥 — mechanical, mirrors the document version: distinct *users* asking (not just distinct queries) + no conflicting past answers in the cluster + a minimum cluster size. One user repeatedly asking the same thing is NOT high confidence — that's one person's curiosity, not a team pattern; the same question from several distinct users is.
- *Tests:* consistent → skill with sources; contradictory → flagged; sparse → low; confidence provably mechanical; interaction-derived confidence requires distinct users, not just distinct queries.

### `backend/app/models/skill.py` 🆕 amendment
- `class SkillSource(BaseModel)`: add `source_kind: Literal["document", "interaction"] = "document"` alongside the existing `doc_id, url, author, date, source` fields — every skill's provenance is now explicitly typed, not just implied by which pipeline produced it.

### `backend/app/skills/versioner.py` ❄
- `def skills_materially_differ(old, new) -> bool`.
- `async def update_skill_if_changed(cluster_id, new_chunks, llm)` — version bump + `superseded_by` chain on material change; no-op otherwise. **Unchanged in shape** for interaction-derived skills — same versioning discipline applies regardless of source kind.
- *Tests:* material change → new version + chain; no change → no write.

### `backend/app/tasks/skills.py` ❄
- `@celery_app.task def cluster_workspace(workspace_id)` — triggered by the outbox's `chunks_updated` event (not a bare hourly beat alone — event-driven re-clustering is more responsive, keep the hourly beat as a safety-net sweep for any missed events).
- **🆕 `@celery_app.task def cluster_workspace_interactions(workspace_id)`** — separate daily beat task (not outbox-triggered, since interactions accumulate gradually rather than arriving in discrete ingest events). Clusters recent `Interaction` rows, calls `extract_skill_from_interactions` for any new/changed cluster.

### `backend/app/api/skill.py` ⏱
- `POST /api/v1/skill` (nearest `trigger_embedding`); `GET /skills` (cursor-paginated); `GET /skills/{id}`, `GET /skills/{id}/history`.
- *Test:* nearest-skill lookup; history returns version chain; pagination cursor stable under concurrent inserts.

### `backend/app/api/ask.py` ⏱ 🆕 amendment
- Every call writes an `Interaction` row after the response is composed — a fast, dedicated insert, not the outbox, since this is an observation, not an action requiring guardrails, and must never add latency to the hot-path response itself.
- *Test:* an `/ask` call produces exactly one `Interaction` row with the right `source_type` and cited chunk IDs.

**Phase 4 acceptance:** skills generate with sourced evidence from BOTH ingested documents and accumulated interaction history; re-extraction versions correctly regardless of source kind; document-triggered clustering fires within seconds of an ingest, interaction clustering runs on its own daily cadence.

---

## Phase 5 — Guardrails + actions + audit (CP path) 🟥

### `backend/app/guardrails/conditions.py` 🟥
- `def evaluate_condition(cond: SkillCondition, context: dict) -> bool` — whitelisted ops only, no `eval`. Unknown field/op → typed error.
- *Tests:* each operator; missing field; type mismatch; never executes arbitrary code.

### `backend/app/guardrails/permissions.py` 🟥
- `async def check_permission(agent_id, tool_name, context) -> Permission` — no grant → `GuardrailRejection`; over-threshold → `GuardrailEscalation`.
- **🆕 Expiring, task-scoped grants**: `agent_permissions` gains `expires_at: TIMESTAMPTZ NULL`, `lease_reason: TEXT NULL`, `granted_for_task_id: STRING NULL`. `check_permission` adds a mandatory clause — `expires_at IS NULL OR expires_at > now()` — an expired lease is treated identically to no grant at all (`GuardrailRejection`), not a special error path. A standing (non-expiring) grant is still `expires_at = NULL`; leases are the exception, not the default, so nothing about today's static grants breaks.
- **🆕 `async def request_temporary_permission(agent_id, tool_name, task_id, duration, requested_by) -> Permission`** — creates a `pending` lease row; always routes through `GuardrailEscalation` for human approval, never auto-grants. A lease request is itself an escalation, not a new decision path — reuses the existing escalation machinery rather than inventing a second one.
- **🆕 Inherited (on-behalf-of) permissions**: `GuardrailRequest.context` gains an optional `on_behalf_of_user_id`. When set, `check_permission` computes the *intersection* of the agent's own grant AND that user's current access from a new `user_tool_access` table — an agent acting "as" a user can never do more than that user currently could, even if the agent's own static grant is broader. If the user's access has since been revoked, the action is rejected even with a valid, unexpired agent-level lease — the human's *current* access is always the ceiling.
- *Tests:* missing grant rejects; over-threshold escalates; within-threshold passes; expired lease rejects identically to no grant; lease request always escalates, never silently grants; on-behalf-of check correctly intersects agent grant with the named user's live access, not either alone.

### `backend/app/db/tables.py` 🆕 amendment (Phase 5)
- `AgentPermission` gains `expires_at`, `lease_reason`, `granted_for_task_id` (all nullable — additive migration, new revision, existing rows unaffected).
- **🆕 `class UserToolAccess`**: `id, workspace_id, user_id, tool_name, scope, source_of_truth (e.g. "slack_channel_membership"), last_verified_at`. Populated/refreshed by connectors where a real source-of-truth exists (e.g. a Slack connector periodically re-checking channel membership) — this table is Pragma's own record of what a *human* can currently do, which `on_behalf_of` checks read from. Stale beyond a configurable window (`last_verified_at` too old) is treated as "unknown" and fails closed, same posture as an expired lease.

### `backend/app/guardrails/idempotency.py` 🟥
- `def compute_idempotency_key(agent_id, tool_name, params) -> str` — sha256 of canonical JSON.
- `async def claim_or_get(key, ...) -> ActionRecord | None` 🟥 — insert-first under `UNIQUE(idempotency_key)`; conflict → return original, never re-execute.
- *Tests:* first call claims; concurrent duplicate returns original without re-executing.

### `backend/app/guardrails/pipeline.py` 🟥
- `async def evaluate(req: GuardrailRequest) -> GuardrailDecision` — five checks in order: skill match → condition eval → permission → risk tier → idempotency. Each raises typed exceptions mapped to a decision.
- *Tests:* each check independently; ordering (early failure short-circuits); escalation path; idempotent replay.

### `backend/app/actions/registry.py` 🟥
- `TOOL_REGISTRY: dict[str, Callable]`, `RISK_TIERS: dict[str, "low"|"high"]` (default high).
- `async def execute_tool(tool_name, params, decision) -> dict` 🟥 — **saga-style**: write `AuditLogEntry(status="pending")` *before* calling the executor; update to `succeeded`/`failed` after; on exception, mark `failed` with the error and re-raise. A separate `reconcile_pending_actions()` sweep (cron, e.g. every 10 min) finds `pending` rows older than a threshold and flags them `unknown` for human review — never leaves a silently-stuck action.

### `backend/app/api/actions.py` 🟥
- `POST /api/v1/actions/execute` — accepts client `Idempotency-Key` header (distinct from internal guardrail idempotency); runs `pipeline.evaluate` → `execute_tool`; RFC 7807 error body on rejection/escalation.
- *Test:* approved path executes and audits; rejected path returns typed 4xx with `failed_check`; duplicate `Idempotency-Key` returns the original response without re-executing.

### `backend/app/api/audit.py`
- `GET /api/v1/audit-log` — cursor-paginated, workspace-scoped, read-only (RLS already blocks UPDATE/DELETE at the DB level from Phase 1).
- *Test:* pagination stable; cross-workspace isolation enforced.

**Phase 5 acceptance:** guardrail pipeline correctly gates every action; idempotent replay never re-executes; a crashed `execute_tool` mid-flight is caught by reconciliation, not lost; expired leases and revoked-user access both fail closed; on-behalf-of actions never exceed the named user's current real access.

---

## Phase 6 — Frontend + external surface

*Deferred until Phases 2–5 are real and tested against actual ingested data — a frontend against a fake backend teaches nothing about whether retrieval quality is good.*

- React + Vite, hitting `/api/v1/search` and `/api/v1/ask` directly.
- MCP server surface: expose `search`, `ask`, and `execute_action` as MCP tools so external agents (Claude, others) can call Pragma directly — this is the same guardrail pipeline, just a different transport in front of it, not a parallel code path.

**Phase 6 acceptance:** a real user can ask a question in the UI and get a cited answer sourced from their actual ingested Slack/GitHub/Notion data.

---

## Phase 7 — Long-horizon tasks, agent coordination, proactive digests 🟥

*Everything here is new architecture, not an extension of an existing file — deliberately sequenced after Phase 6, since all three depend on the full guardrail pipeline and a real, working UI already existing. None of these bypass the five-check pipeline; they wrap it.*

### `backend/app/db/tables.py` 🆕 addition
- `class AgentTask`: `id, workspace_id, agent_id, goal, status: Literal["pending","in_progress","paused","completed","failed"], context: JSONB, on_behalf_of_user_id, created_at, updated_at, last_checkpoint_at`.
- `class AgentTaskStep`: `id, task_id (FK), step_number, action_taken, audit_log_id (FK), status, created_at` — every step of a long-horizon task is still an individual, fully guardrail-checked action; this table only tracks *sequence and state across* already-guarded steps, it never grants a step anything the pipeline wouldn't have approved on its own.
- `AuditLog` gains `task_id: STRING NULL` — any action taken as part of a long-horizon task traces back to it.
- `class AgentHandoff`: `id, from_agent_id, to_agent_id, task_id (FK), subtask, status: Literal["pending","accepted","completed","failed"], result: JSONB, created_at, completed_at`.
- `class Digest`: `id, workspace_id, digest_type, content, created_at, delivered_at, channel`.

### `backend/app/actions/long_horizon.py` 🟥 ❄
- `async def start_task(workspace_id, agent_id, goal, on_behalf_of_user_id) -> AgentTask` — creates the task row, status `pending`.
- `async def execute_step(task_id, tool_name, params) -> AgentTaskStep` 🟥 — **every step runs through the normal `guardrails/pipeline.py` `evaluate()` exactly as a standalone action would.** Long-horizon persistence adds sequencing and checkpointing on top of the pipeline; it does not create a second, less-checked path. If `on_behalf_of_user_id` is set on the parent task, every step's permission check re-verifies that user's *current* `UserToolAccess` — access can be revoked mid-task, and the very next step must respect that, not just the task's starting state.
- `async def checkpoint(task_id, context: dict)` — writes `context` and `last_checkpoint_at`; on worker crash, `resume_task(task_id)` reloads from the last checkpoint rather than restarting from `goal` alone.
- *Tests:* 🟥 a step that would be rejected standalone is also rejected mid-task (no privilege escalation via task wrapping); revoking `UserToolAccess` mid-task blocks the next step even though earlier steps succeeded; crash-and-resume continues from the last checkpoint, not from scratch.

### `backend/app/actions/handoff.py` 🟥
- A handoff is itself a registered tool (`tool_name = "handoff_to_agent"`) — it goes through the same `guardrails/pipeline.py` evaluation as any other action, including risk tier. **🆕 Risk tier rule**: handoffs to an external, non-Pragma-controlled agent (e.g. an external coding assistant) default to `"high"` risk tier regardless of the task's own tier — crossing a trust boundary is inherently riskier than an internal step.
- `async def request_handoff(task_id, to_agent_id, subtask, decision) -> AgentHandoff` — creates a `pending` `AgentHandoff` row after guardrail approval.
- `async def accept_result(handoff_id, result: dict)` — **the returned result is NOT trusted blindly.** It re-enters the guardrail pipeline as a new proposed action (the receiving agent's output is itself gated before being merged back into the parent task) — a handed-off agent's output is treated with exactly the same skepticism as a first-party action, never auto-accepted.
- *Tests:* handoff to an external agent is forced to high risk tier even if requested as low; a handoff result that would fail guardrail evaluation on its own is rejected on re-entry, not silently merged.

### `backend/app/tasks/digests.py` ❄
- `@celery_app.task def generate_digest(workspace_id, digest_type)` — queries chunks/interactions/skills changed since the last digest for this workspace, calls `LLMClient.complete` to synthesize a short "what changed, what's worth attention" summary. Configurable cadence per workspace (extends the existing Celery beat pattern from Phase 2's `sync_source`).
- **🆕 Delivery is a real action, not a bare message send**: posting a digest (e.g. to Slack) is a registered tool (`tool_name = "post_digest"`, default risk tier `"low"`) and goes through the guardrail pipeline like any other action — a digest is a state-changing, externally-visible action (it posts something, on the workspace's behalf, unprompted), and treating it as guardrail-exempt because it "just" summarizes would be a real gap, not a shortcut worth taking.
- *Tests:* a digest with nothing changed since the last run produces no post (no empty/noise digests); digest delivery is present in `audit_log` like any other executed action.

**Phase 7 acceptance:** a multi-step task can run across real time with checkpointing and resumability, never bypassing per-step guardrails; a handoff to another agent is risk-appropriately gated both on request and on result re-entry; digests are generated on a real cadence and delivered through the same audit-logged action path as everything else.

---

## Compliance & security certification track (non-engineering, tracked separately)

*Not a code phase — SOC 2 Type II and GDPR readiness are audit, policy, and process work, not features to build. Tracked here so it isn't a surprise blocker when a real enterprise or regulated-industry deal is on the table (per the finance/medical strategic angle already noted for Pragma).*

- **SOC 2 Type II** — requires documented security policies, formal access-control review process, incident response procedures, and typically 6-12 months of collected evidence before certification is possible. `audit_log`'s existing RLS-enforced immutability is a genuine head start here (real, structural evidence of tamper-resistant logging), but certification needs the surrounding *policy and process* documentation, not just the schema.
- **GDPR — a real, currently unresolved architectural tension worth stating plainly**: `audit_log`'s RLS policy deliberately blocks UPDATE and DELETE, by design, for audit integrity. GDPR's right-to-erasure requires that personal data be deletable on request. These two requirements conflict if raw PII (a user's name, email) ever lands directly in `audit_log.params` or `.context`. Resolution direction, not yet implemented: pseudonymize user-identifying fields in `audit_log` (store a stable pseudonym/reference ID, not raw PII), with the real identity mapping held in a *separate*, genuinely erasable table — "erasure" then means deleting the mapping, not mutating the immutable log itself. This needs a real design pass before any customer's actual personal data flows through the audit path, not a retrofit after the fact.
- Both tracks are prerequisites for closing deals with regulated-industry customers specifically — worth revisiting once Pragma has a real enterprise prospect, not before, but the GDPR/immutability tension above should inform `audit_log`'s schema decisions now, while it's still cheap to change.
