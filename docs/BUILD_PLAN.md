# Pragma — Build Plan (per-function)

This is the exhaustive, function-by-function plan referenced by [`../CLAUDE.md`](../CLAUDE.md). It exists so a junior developer (or an AI agent) can build Pragma without re-deriving the design each session. Read the [Senior Engineering Review](../CLAUDE.md#4-senior-engineering-review--decisions--changes-from-the-spec) in CLAUDE.md first — every deviation from `PRAGMA.md` is justified there.

**How to read this:** each phase lists files, and for each file the functions with their signature, what they do, edge cases to handle, and the tests to write. Signatures are guidance — match the real `Document`/`Skill` types once `models/` exists. Build in order; keep the tree green (lint + tests) after each phase.

Legend: 🟥 correctness-critical · 🔌 external call (must be mockable) · ⏱ hot path (<500ms, no cold work) · ❄ cold path (Celery only).

---

## Phase 0 — Foundation & scaffolding

*Goal: an installable, lint-clean, type-checked skeleton that boots empty and migrates an empty DB. No features.*

### `backend/pyproject.toml`
- Dependencies: `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]`, `asyncpg`, `pgvector`, `alembic`, `celery[redis]`, `redis`, `pydantic`, `pydantic-settings`, `loguru`, `anthropic`, `voyageai`, `stripe`, `slack_sdk`, `PyGithub` (or `githubkit`), `notion-client`, `hdbscan`, `numpy`, `langchain-text-splitters`, `cryptography` (Fernet), `argon2-cffi`, `circuitbreaker`, `mcp`. Dev: `pytest`, `pytest-asyncio`, `ruff`, `testcontainers[postgres]`, `respx`/`httpx` for HTTP mocks.
- `[tool.ruff]` line-length 100. `[tool.pytest.ini_options]` `asyncio_mode = "auto"`.
- Provide a way to export `requirements.txt` (so the documented `pip install -r requirements.txt` works): `pip-compile` or a committed export.

### `backend/app/config.py`
- `class Settings(BaseSettings)` (pydantic-settings) with every var from CLAUDE.md §11, typed. `EMBEDDING_PROVIDER: Literal["voyage","openai","local"] = "voyage"`. `MAX_QUEUE_DEPTH: int = 10_000`, `BACKPRESSURE_SLEEP_S: float = 1.0`, `EMBEDDING_DIM: int` (derived from provider; 1024 voyage / 1536 openai).
- `@lru_cache def get_settings() -> Settings`. **Fail fast:** missing required env raises at import/startup, not at first use.
- *Test:* loading with a complete fake env succeeds; missing a required key raises.

### `backend/app/logging.py`
- `def configure_logging() -> None`: set up loguru sinks.
- `def _redact(record) -> bool` / patcher: scrub any value matching known secret keys (API keys, tokens, `PRAGMA_ENCRYPTION_KEY`) from log output.
- *Test:* a log line containing a fake `sk-ant-...` is redacted.

### `backend/app/db/session.py`
- `engine = create_async_engine(settings.DATABASE_URL, ...)`; `AsyncSessionLocal = async_sessionmaker(...)`.
- `async def get_session() -> AsyncIterator[AsyncSession]` — FastAPI dependency.
- `@asynccontextmanager async def transaction() -> AsyncIterator[AsyncSession]` 🟥 — opens a session, `BEGIN`, commits on success, rolls back on exception. Used by dedup.
- `def serializable_session()` helper that sets `isolation_level="SERIALIZABLE"` for dedup writes.
- *Test:* `transaction()` rolls back on raised exception (insert visible only on commit).

### `backend/app/main.py`
- `def create_app() -> FastAPI`: app factory; `configure_logging()`; register routers (empty for now); `GET /health` → `{"status": "ok"}`.
- *Test:* `/health` returns 200 via `httpx.AsyncClient`.

### `backend/app/worker.py`
- `celery_app = Celery("pragma", broker=REDIS_URL, backend=REDIS_URL)`; beat schedule stubs (15-min source poll, hourly skills extraction) pointing at not-yet-written tasks.

### `backend/alembic.ini` + `backend/app/db/migrations/env.py`
- Async Alembic wired to the ORM metadata (metadata import will be empty until Phase 1).

### `backend/docker-compose.yml` (or repo-root)
- Services: `db` (`pgvector/pgvector:pg16`), `redis`, `api`, `worker`, `beat`, `frontend`. Healthchecks; `api`/`worker` depend on `db` healthy.

### `backend/tests/conftest.py`
- Fixtures: event loop, fake `Settings`, a **real** ephemeral Postgres (testcontainers) with the `vector` extension, a transactional session that rolls back per test.
- **Autouse fixtures that mock external HTTP**: `anthropic`, `voyageai`, `stripe`, Slack/GitHub/Notion SDKs. No test ever hits a real external API.

### `.env.example`, `.gitignore`, `README.md`; `git init`
- `.env.example` mirrors CLAUDE.md §11 with placeholders. `git init` (repo is currently not a git repo).

**Phase 0 acceptance:** `ruff check`, `alembic upgrade head` (against compose/testcontainer DB), `pytest` (green, ~3 trivial tests), `uvicorn app.main:app` boots with `/health`.

---

## Phase 1 — Data contracts (models + schema)

*Everything downstream depends on these. Build before any feature.*

### `backend/app/models/document.py`
- `class Document(BaseModel)`: exact fields from `PRAGMA.md §5.2` — `id, source, doc_type, title, content, author, participants: list[str], created_at, updated_at, url, content_hash, metadata: dict, trace_id`.
- `class Chunk(BaseModel)`: `id, doc_id, source, doc_type, content, doc_title, doc_url, author, created_at, position, total_chunks, trace_id`.
- `class EmbeddedChunk(BaseModel)`: `chunk: Chunk, embedding: list[float], model: str, embedded_at: datetime`.
- Helpers: `Document.compute_id(source, source_id) -> str` (`sha256(source + source_id)`), `compute_content_hash(content) -> str`.

### `backend/app/models/skill.py`
- `class SkillCondition(BaseModel)`: `field: str, op: Literal["==","!=",">","<",">=","<=","in","not_in"], value, then_action: str`. **(structured, not a string — see CLAUDE.md §4.4)**
- `class SkillSource(BaseModel)`: `doc_id, url, author, date, source`.
- `class Skill(BaseModel)`: `skill_id, skill_name, trigger, confidence: Literal["high","medium","low"], steps: list[str], conditions: list[SkillCondition], contradictions: list[str], sources: list[SkillSource], last_updated, superseded_by: str | None, cluster_id, version: int`.

### `backend/app/models/guardrail.py`
- `class GuardrailRequest(BaseModel)`: `request_id, agent_id, workspace_id, skill_id: str | None, tool_name, proposed_params: dict, context: dict`.
- `class ConditionResult(BaseModel)`: `condition: SkillCondition, result: bool, then_action: str | None`.
- `class GuardrailDecision(BaseModel)`: `request_id, outcome: Literal["approved","rejected","escalated"], failed_check: str | None, reason, risk_tier, requires_human: bool, condition_results: list[ConditionResult], idempotency_key`.
- Exceptions: `class GuardrailRejection(Exception)` (`check`, `reason`), `class GuardrailEscalation(Exception)` (`check`, `reason`).

### `backend/app/models/audit.py`
- `class AuditLogEntry(BaseModel)`: mirrors the `audit_log` table columns (CLAUDE.md / `PRAGMA.md §6`).

### `backend/app/db/tables.py`
- SQLAlchemy ORM for `workspaces, source_connections, documents, chunks, skills, agent_permissions, audit_log` per `PRAGMA.md §6`, with these **changes**:
  - `chunks.embedding = mapped_column(Vector(settings.EMBEDDING_DIM))` (1024 voyage default).
  - `near_duplicate_of` FK column on `chunks`.
  - `documents`: `UNIQUE(source, source_id)`.
  - `source_connections.credentials` stored encrypted (see Phase 2 dedup/crypto helper).
  - API-key storage: a `workspace_api_keys` table (or column) holding an **argon2 hash**, not the raw secret.

### First migration `0001_init`
- Autogenerate, then add to `upgrade()` body (autogen won't catch these):
  - `op.execute("CREATE EXTENSION IF NOT EXISTS vector")`
  - HNSW index: `CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops)` 🟥 (not ivfflat — CLAUDE.md §4.3)
  - HNSW index on `skills.trigger_embedding`.
  - btree indexes: `chunks(workspace_id, doc_type, source)`, `chunks(doc_id)`, `skills(workspace_id, confidence)`.
  - audit_log RLS 🟥: `ENABLE ROW LEVEL SECURITY`; `deny_update`/`deny_delete` policies `USING (false)`; `allow_read`/`allow_insert`.

**Phase 1 acceptance:** migration applies cleanly; round-trip test inserts/reads a `chunks` row with a vector; RLS test confirms UPDATE and DELETE on `audit_log` are rejected at the DB level.

---

## Phase 2 — Ingestion write path (cold) ❄

*Build the normalizer first (it defines the contract everything targets), then the pure transforms, then dedup, then connectors.*

### `backend/app/providers/embeddings.py` 🔌
- `class EmbeddingProvider(Protocol)`: `async def embed(texts: list[str]) -> list[list[float]]`; `model: str`; `dim: int`.
- `class VoyageEmbeddings(EmbeddingProvider)` (default, `voyage-3.5`), `class OpenAIEmbeddings`, `class LocalEmbeddings`. Factory `get_embedder() -> EmbeddingProvider` keyed on `settings.EMBEDDING_PROVIDER`.
- *Test:* factory returns the configured provider; `embed()` is mocked.

### `backend/app/providers/llm.py` 🔌
- `class LLMClient`: wraps the `anthropic` SDK.
  - `async def extract_json(prompt: str, schema: type[BaseModel], model="claude-haiku-4-5") -> BaseModel` 🟥 — uses **structured outputs** (`messages.parse` / `output_config.format`) to guarantee schema-valid output. Used by the skills extractor and auto-titles.
  - `async def complete(prompt: str, model="claude-sonnet-4-6") -> str` — for RAG answers.
- *Test:* mocked; asserts the schema is passed through and a validated model is returned.

### `backend/app/ingestion/normalizer.py`
- `async def normalize(raw: dict, source: str) -> Document` — dispatch to per-source normalizers. **No downstream stage imports connector types.**
- Per-source helpers: `_normalize_slack`, `_normalize_github`, `_normalize_notion`. Apply cleaning rules (`PRAGMA.md §5.2`): strip markdown/HTML, collapse whitespace, drop bot/CI messages, reassemble Slack threads `"{author}: {msg}"` chronologically, concat GitHub title+desc+review comments with attribution, recurse Notion blocks in document order.
- `async def ensure_title(doc: Document, llm: LLMClient) -> Document` — if no natural title, generate one via `llm` (≤8 words) and cache; never re-extract unless content changes.
- *Tests:* recorded raw payloads → expected `Document`; bot messages dropped; thread reassembly order; title caching.

### `backend/app/ingestion/chunker.py`
- `CHUNK_SIZES = {"thread":256, "pr":512, "page":768, "issue":256}` (`PRAGMA.md §5.3`); 10% overlap; sentence-boundary aware via `RecursiveCharacterTextSplitter`.
- `def chunk_document(doc: Document) -> list[Chunk]` — carries full metadata onto each chunk; sets `position`, `total_chunks`. Pure function.
- *Tests:* per-doc_type sizing; overlap; metadata carried; empty/short content edge cases.

### `backend/app/ingestion/embedder.py` 🔌
- `async def embed_chunks(chunks: list[Chunk], embedder: EmbeddingProvider) -> list[EmbeddedChunk]` — batch (≤ provider max), record `model`, `embedded_at`.
- `async def embed_query(text: str, embedder) -> list[float]` ⏱ — single embedding for hot-path search; **same model** as chunks.
- *Tests:* batching boundaries; model recorded; mocked provider.

### `backend/app/dedup/dedup.py` 🟥
- `async def reingest_document(doc, embedder)` 🟥 — the three dedup problems (`PRAGMA.md §5.5`):
  1. Hash skip: if stored `content_hash == new` → `SkipResult("unchanged")`.
  2. Changed: inside one **SERIALIZABLE** `transaction()`: delete old chunks by `doc_id`, chunk+embed, insert, upsert `documents` `ON CONFLICT(source, source_id) DO UPDATE`.
  3. Near-dup: `async def check_near_duplicate(embedding, doc_id, workspace_id) -> str | None` — workspace-scoped HNSW query, similarity > 0.97 against other-source chunks → link via `near_duplicate_of` instead of inserting a second copy.
- `def compute_idempotency_for_insert(...)` — rely on `UNIQUE(source, source_id)`; the losing concurrent insert hits the constraint and is handled cleanly.
- `crypto.py` helper (here or `app/security/`): `encrypt(plaintext) / decrypt(token)` via Fernet for `source_connections.credentials`.
- *Tests:* 🟥 **concurrent-insert race** (two workers, same doc → exactly one row); unchanged-skip; changed → old chunks gone + new present, atomic (crash mid-op rolls back); near-dup linked not duplicated.

### `backend/app/ingestion/connectors/base.py`
- `class Connector(ABC)`: `source: str`; `async def sync(workspace_id, since) -> AsyncIterator[dict]` (abstract).
- Shared infra: `@circuit(failure_threshold=3, recovery_timeout=60)` wrapper around API calls (log `workspace_id`+`source` on trip); `async def emit_with_backpressure(jobs)` (sleep while `queue.depth() > MAX_QUEUE_DEPTH`); watermark read/write (`last_synced_at` per `(workspace, source)`).
- *Tests:* breaker opens after 3 failures and isolates one source; backpressure sleeps when queue deep.

### `backend/app/ingestion/connectors/slack.py` / `github.py` / `notion.py` 🔌
- Slack: OAuth bot token (decrypted from `source_connections`), `conversations.list/history/replies`, thread assembly, preserve reactions in `metadata` (future confidence signal). GitHub: GitHub App, PRs+issues+comments, `since`-incremental, preserve review approvals in `metadata`. Notion: poll-only, recursive block fetch.
- Each yields raw dicts → `normalize()`.
- *Tests:* recorded API fixtures; incremental `since` respected; mocked SDKs.

### `backend/app/tasks/ingest.py` ❄
- `@celery_app.task def ingest_document(workspace_id, raw, source)` — `asyncio.run(...)` → normalize → dedup.reingest. (Single event-loop entry per task — CLAUDE.md §4.9.)
- `@celery_app.task def sync_source(workspace_id, source)` — watermark query → connector.sync → backpressure-emit `ingest_document` jobs. Beat: every 15 min.

**Phase 2 acceptance:** a seeded workspace ingests end-to-end (raw → chunks in pgvector); dedup race + atomicity tests pass.

---

## Phase 3 — Retrieval read path (hot) ⏱

*Strict <500ms, read-only, no cold work.*

### `backend/app/db/vector_store.py` ⏱
- `async def similarity_search(embedding, workspace_id, *, source=None, doc_type=None, top_k=5, min_similarity=0.6) -> list[ChunkHit]` — parameterized SQL: filter `workspace_id` + `embedding_model` (same-model only), optional source/doc_type, `ORDER BY embedding <=> $1 LIMIT k`, compute `similarity = 1 - distance`. Goes through the workspace-scoped repository.
- *Test:* returns ranked hits; same-model filter enforced; min_similarity respected (real Postgres).

### `backend/app/api/deps.py`
- `async def require_workspace(authorization: Header) -> Workspace` — parse `Bearer pk_live_{workspace_id}_{secret}`, look up the **argon2 hash**, verify, return workspace. 401 on mismatch.
- Per-workspace rate limit dependency (Redis token bucket).
- *Test:* valid key passes; tampered secret 401; cross-workspace key cannot read another workspace.

### `backend/app/api/search.py` ⏱
- `POST /api/v1/search` (`SearchRequest` → `SearchResponse`): `embed_query` → `similarity_search` → return chunks + metadata + `query_embedding_model`. **No Celery, no new-doc embedding.**
- *Test:* 🟥 asserts no Celery task is enqueued (mock the queue, assert zero calls); p95 budget sanity.

### `backend/app/api/ask.py` ⏱
- `POST /api/v1/ask`: skills-first (`use_skills_first`) → if a high/medium skill matches, render `skill_to_prose`; else RAG over chunks via `LLMClient.complete` with a citation prompt. Returns `source_type` ("skill"|"rag") + sources.
- *Test:* skills path returns skill; fallback path calls LLM (mocked) and cites chunks.

**Phase 3 acceptance:** local p95 search < 500ms; hot-path-no-cold-work test passes.

---

## Phase 4 — Skills extractor (cold) ❄

*The differentiator.*

### `backend/app/skills/clusterer.py` ❄
- `def cluster_chunks(embeddings: np.ndarray) -> np.ndarray` — HDBSCAN (`min_cluster_size=3, min_samples=2, metric="euclidean", cluster_selection_method="eom"`). Label `-1` = noise → never becomes a skill.
- `async def load_workspace_embeddings(workspace_id) -> tuple[list[Chunk], np.ndarray]`.
- *Tests:* cluster shape on synthetic embeddings; noise excluded.

### `backend/app/skills/extractor.py` 🟥 ❄ 🔌
- `EXTRACTION_PROMPT` (`PRAGMA.md §5.6`): consistent → extract; contradictory → fill `contradictions`; no pattern → low confidence, empty steps; never invent.
- `async def extract_skill(cluster_chunks: list[Chunk], llm: LLMClient) -> Skill | None` — calls `llm.extract_json(..., schema=Skill, model="claude-haiku-4-5")` (**structured outputs**, temperature low). Conditions come back structured (`SkillCondition`).
- `def compute_confidence(cluster_chunks, contradictions) -> Literal["high","medium","low"]` 🟥 — **mechanical** (distinct sources + distinct docs + no contradictions). Never ask the LLM.
- *Tests:* consistent cluster → skill with sources; contradictory → flagged; sparse → low; confidence is mechanical (LLM mock can't influence it).

### `backend/app/skills/versioner.py` ❄
- `def skills_materially_differ(old: Skill, new: Skill) -> bool` — compare steps/conditions semantically.
- `async def update_skill_if_changed(cluster_id, new_chunks, llm)` — if changed: bump `version`, new `skill_id`, set old `superseded_by`, insert new; preserve history. Else: no write.
- *Tests:* material change → new version + chain; no change → no write.

### `backend/app/tasks/skills.py` ❄
- `@celery_app.task def cluster_workspace(workspace_id)` — hourly (beat): clusters with new chunks since last run → extract/update. Never blocks the API.

### `backend/app/api/skill.py` ⏱
- `POST /api/v1/skill` (`situation` → nearest `trigger_embedding`); `GET /skills`, `GET /skills/{id}`, `GET /skills/{id}/history`.
- *Test:* nearest-skill lookup; history returns version chain.

**Phase 4 acceptance:** skills generate with sourced evidence; re-extraction versions on material change.

---

## Phase 5 — Guardrails + actions + audit (CP path) 🟥

### `backend/app/guardrails/conditions.py` 🟥
- `def evaluate_condition(cond: SkillCondition, context: dict) -> bool` — deterministic over whitelisted ops; **no `eval`** (CLAUDE.md §4.4). Unknown field/op → safe failure (raise typed error).
- *Tests:* each operator; missing field; type mismatch; never executes arbitrary code.

### `backend/app/guardrails/permissions.py` 🟥
- `async def check_permission(agent_id, tool_name, context) -> Permission` — grant lookup; if `context["amount"] > permission.max_amount` → `GuardrailEscalation`; no grant → `GuardrailRejection`.
- *Tests:* missing grant rejects; over-threshold escalates; within-threshold passes.

### `backend/app/guardrails/idempotency.py` 🟥
- `def compute_idempotency_key(agent_id, tool_name, params) -> str` — sha256 of canonical (`sort_keys`) JSON.
- `async def claim_or_get(key, ...) -> ActionRecord | None` 🟥 — **insert-first**: try to insert the audit/idempotency row under `UNIQUE(idempotency_key)`; on conflict, fetch and return the original (do **not** re-execute). (CLAUDE.md §4.5.)
- *Tests:* first call claims; concurrent duplicate returns original without re-executing.

### `backend/app/guardrails/pipeline.py` 🟥
- `async def evaluate(req: GuardrailRequest) -> GuardrailDecision` — five checks **in order**, each raising typed exceptions caught and mapped to a decision (`PRAGMA.md §5.8`):
  1. **Skill match** — `get_skill_for_tool`; missing/low → reject.
  2. **Condition eval** — `evaluate_condition` over `req.context` → `condition_results`.
  3. **Permission** — `check_permission`.
  4. **Risk tier** — `RISK_TIERS.get(tool, "high")`; low → approve, high → escalate.
  5. **Idempotency** — `claim_or_get`; existing → return original outcome.
- Always produces a `GuardrailDecision`; the audit row is written by `execute_tool`/the action layer on **every** outcome.
- *Tests:* 🟥 each check independently (pass + fail); combined ordering (an early failure short-circuits later checks); escalation path; idempotent replay.

### `backend/app/actions/registry.py` 🟥
- `TOOL_REGISTRY: dict[str, Callable]`, `RISK_TIERS: dict[str, "low"|"high"]` (default high).
- `async def execute_tool(tool_name, params, decision) -> dict` 🟥 — run executor; write `AuditLogEntry` on success **and** on exception (`executed=False, error=...`) then re-raise.

### `backend/app/actions/stripe_refund.py` 🟥 🔌
- `async def stripe_issue_refund(params) -> dict` — `stripe.refunds.create(..., metadata={pragma_request_id, skill_id, agent_id})` and pass our idempotency key as Stripe's native `Idempotency-Key` header. try/except → failed audit → re-raise.
- *Tests:* metadata attached; Stripe idempotency key passed; failure writes failed audit then raises (mocked Stripe).

### `backend/app/actions/ticket.py` 🔌
- `async def update_ticket_status(params) -> dict` — low-risk tier.

### `backend/app/audit/log.py` 🟥
- `async def write(entry: AuditLogEntry) -> None` — append-only INSERT (RLS allows insert+select only). Never updates.
- *Test:* row written for approved / rejected / escalated; UPDATE/DELETE blocked by RLS.

### `backend/app/approvals/queue.py` 🔌
- Redis-backed queue: `enqueue(request_id, decision)`, `pending()`, `approve(request_id)`, `reject(request_id)`; notify on escalation. Agent receives `{"status":"pending_approval","request_id":...}` and does not block.

### `backend/app/api/actions.py` · `approvals.py` · `audit.py`
- The §8 endpoints from `PRAGMA.md` — all action endpoints route through `pipeline.evaluate()`.

**Phase 5 acceptance:** all-five-checks tests pass; a double `issue_refund` call returns the original result and executes Stripe exactly once; rejected/escalated actions still produce audit rows.

---

## Phase 6 — Unified MCP server + E2E

### `backend/app/mcp/server.py` 🟥
- Built with the official **`mcp` SDK** over streamable HTTP (CLAUDE.md §4.8).
- Knowledge tools (open): `search_knowledge`, `get_skill`, `ask`.
- Action tools (gated): `issue_refund`, `update_ticket_status`, `request_human_approval` — **require `skill_id`** as a parameter and route through `guardrails.pipeline.evaluate()`. Enforce `skill_id` presence at this layer (reject calls without it).
- *Test:* action without `skill_id` is rejected before any executor runs; action with valid skill → guardrail → executor.

### `backend/app/api/sources.py` 🔌
- OAuth connect / list / manual sync / status (`PRAGMA.md §8`). Store encrypted credentials.

### `backend/scripts/seed_workspace.py`
- Seed a workspace with synthetic Slack/GitHub docs so the whole pipeline runs without live sources.

### `frontend/` (thin v1)
- Source status, skills list, audit-log viewer (React + Vite + Tailwind). Read-only against the API.

### End-to-end test
- `get_skill("customer requests refund")` → `issue_refund(skill_id=...)` → guardrail (approve/escalate) → mock Stripe → assert an `audit_log` row with the full trace chain.

**Phase 6 acceptance:** the E2E test passes; the MCP server lists tools and enforces `skill_id`; the dashboard renders seeded data.

---

## Cross-cutting checklist (every phase)
- `trace_id` flows Document → Chunk → Skill source → GuardrailRequest → AuditLogEntry.
- One mock module per external service; never hit real APIs in tests.
- Every data query is workspace-scoped via the repository layer.
- New migrations only — never hand-edit applied ones; add pgvector/HNSW/RLS in the migration body.
- Keep the tree green (`ruff` + `pytest`) at the end of each phase before starting the next.
