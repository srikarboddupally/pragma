# Pragma — CLAUDE.md
> Company intelligence, executable. Knowledge layer + agent action layer for B2B SaaS support teams.
>
> This file is the **working guide** for anyone (human or AI) building Pragma. It is loaded into context every session, so it stays lean and authoritative. The exhaustive, per-function build plan lives in [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md). The product spec / pitch lives in [`PRAGMA.md`](PRAGMA.md).

---

## ⚠️ Reality check (read first)

**Nothing is built yet.** The repository currently contains only `PRAGMA.md` (the spec) and this file. There is no `backend/`, no code, no git history. The "Current v1 Status" that previously appeared here was **aspirational** — it described an intended end state, not what exists on disk. We are starting from an empty repo.

Treat the [Build Plan](#7-build-plan--per-phase) as the source of truth for *what to do next*. Update the [status board](#8-status-board) as real code lands.

---

## 1. What Pragma is (1-minute version)

Pragma ingests company knowledge (Slack, GitHub, Notion) → normalizes → chunks → embeds into pgvector → clusters with HDBSCAN → extracts structured **skills** (sourced, versioned, executable procedures). A second layer exposes **action tools** (`issue_refund`, `update_ticket`) behind a **five-check guardrail pipeline**. Everything is served over a unified **MCP server**.

Two pipelines (this is the whole architecture in one idea — **CQRS**):
- **Write path (cold):** Sources → Ingestion → Vector store + Skills extractor. Runs on Celery workers. Latency-insensitive, CPU-heavy, can be slow.
- **Read path (hot):** MCP/HTTP call → Query API → Vector store / Skills table. Must be **< 500ms**. Reads pre-computed data only. **Never** blocks on write-path work.

> **Why CQRS matters (junior note):** "Command Query Responsibility Segregation" just means *the code that writes and the code that reads are completely separate and scale independently*. A 3-year Slack backfill (write) must never make a customer's live search (read) slow. We enforce this with a hard rule (see [Architecture Rules](#5-architecture-rules)).

---

## 2. Repository layout (monorepo: backend + frontend)

We use a **monorepo** with a clear backend/frontend split. One repo, two deployable apps, shared docs. This is the standard senior layout for a product with a Python API and a React dashboard: atomic commits across the stack, one CI config, no version-skew between client and server.

```
pragma/
├── backend/                     # Python service (all of v1's value lives here)
│   ├── app/
│   │   ├── main.py              # FastAPI entry point (app factory)
│   │   ├── worker.py            # Celery app + beat schedule
│   │   ├── config.py            # pydantic-settings, fail-fast on missing env
│   │   ├── logging.py           # loguru setup + secret-redaction filter
│   │   ├── api/                 # HOT PATH — read-only HTTP handlers
│   │   │   ├── deps.py          #   auth (hashed API keys), workspace resolution
│   │   │   ├── search.py        #   POST /api/v1/search
│   │   │   ├── skill.py         #   POST /api/v1/skill  (+ list/get/history)
│   │   │   ├── ask.py           #   POST /api/v1/ask    (skills-first, RAG fallback)
│   │   │   ├── actions.py       #   POST /api/v1/actions/* (→ guardrail pipeline)
│   │   │   ├── approvals.py     #   human approval queue endpoints
│   │   │   ├── audit.py         #   GET  /api/v1/audit (read-only view)
│   │   │   └── sources.py       #   OAuth connect / list / sync / status
│   │   ├── providers/           # External-service abstractions (swap + mock here)
│   │   │   ├── llm.py           #   LLMClient — Claude (anthropic SDK)
│   │   │   └── embeddings.py    #   EmbeddingProvider — Voyage (default) | OpenAI | local
│   │   ├── ingestion/           # COLD PATH
│   │   │   ├── connectors/
│   │   │   │   ├── base.py      #   ABC: watermark sync, backpressure, circuit breaker
│   │   │   │   ├── slack.py
│   │   │   │   ├── github.py
│   │   │   │   └── notion.py    #   poll-only
│   │   │   ├── normalizer.py    #   any raw object → Document  (the key abstraction)
│   │   │   ├── chunker.py       #   per-doc_type sizing
│   │   │   └── embedder.py      #   batched, model-tracked
│   │   ├── dedup/dedup.py       # content-hash skip + near-dup link + atomic upsert
│   │   ├── skills/              # COLD PATH (the differentiator)
│   │   │   ├── clusterer.py     #   HDBSCAN over workspace embeddings
│   │   │   ├── extractor.py     #   LLM → Skill (structured outputs); mechanical confidence
│   │   │   └── versioner.py     #   material-diff → superseded_by chain
│   │   ├── guardrails/          # CP PATH (correctness-critical)
│   │   │   ├── pipeline.py      #   five sequential checks → GuardrailDecision
│   │   │   ├── conditions.py    #   SAFE structured condition evaluator (no eval)
│   │   │   ├── permissions.py   #   agent permission grants
│   │   │   └── idempotency.py   #   insert-first, DB-unique-constraint based
│   │   ├── actions/
│   │   │   ├── registry.py      #   TOOL_REGISTRY + RISK_TIERS + execute_tool()
│   │   │   ├── stripe_refund.py #   issue_refund (Stripe Idempotency-Key + metadata)
│   │   │   └── ticket.py        #   update_ticket_status
│   │   ├── audit/log.py         # append-only writer (writes on EVERY outcome)
│   │   ├── approvals/queue.py   # Redis-backed human approval queue
│   │   ├── mcp/server.py        # Unified MCP server (official `mcp` SDK, streamable HTTP)
│   │   ├── models/              # Pydantic v2 schemas (the data contracts)
│   │   │   ├── document.py      #   Document, Chunk, EmbeddedChunk
│   │   │   ├── skill.py         #   Skill, SkillCondition, SkillSource
│   │   │   ├── guardrail.py     #   GuardrailRequest, GuardrailDecision, exceptions
│   │   │   └── audit.py         #   AuditLogEntry
│   │   ├── tasks/               # Celery tasks (thin wrappers over cold-path modules)
│   │   │   ├── ingest.py
│   │   │   └── skills.py
│   │   └── db/
│   │       ├── session.py       #   async SQLAlchemy engine, session, transaction()
│   │       ├── tables.py        #   ORM tables
│   │       └── migrations/      #   Alembic (generated; see "never hand-edit" rule)
│   ├── tests/                   # mirrors app/; pytest-asyncio
│   ├── pyproject.toml           # deps + ruff + pytest config (source of truth)
│   ├── requirements.txt         # generated from pyproject (CLAUDE commands use this)
│   └── alembic.ini
├── frontend/                    # React + Vite + Tailwind dashboard (v1: thin)
│   └── (sources status, skills list, audit log viewer)
├── docs/
│   ├── BUILD_PLAN.md            # exhaustive per-function build plan
│   └── skills-extractor-spec.md # (to be written when Phase 4 starts)
├── docker-compose.yml           # postgres+pgvector, redis, api, worker, beat, frontend
└── .env.example
```

> All `backend` commands below run **from `backend/`** (the Python project root). The frontend runs from `frontend/`.

---

## 3. Commands

```bash
# --- backend (run from ./backend) ---
pip install -r requirements.txt          # or: pip install -e ".[dev]"

uvicorn app.main:app --reload --port 8000          # API (hot path)
celery -A app.worker worker --loglevel=info -c 4    # ingestion workers (cold path)
celery -A app.worker beat --loglevel=info           # scheduled sync jobs

alembic upgrade head                                # apply migrations
alembic revision --autogenerate -m "description"    # generate a migration (then review it)

pytest -v                                            # all tests
pytest tests/dedup/test_dedup.py -v                  # one file
ruff check app/ && ruff format app/                  # lint + format (line length 100)

# --- frontend (run from ./frontend) ---
npm install
npm run dev

# --- full local stack (run from repo root) ---
docker compose up -d
python backend/scripts/seed_workspace.py --workspace-id=test_ws_001
```

---

## 4. Senior Engineering Review — decisions & changes from the spec

`PRAGMA.md` is an excellent product spec, but several of its code snippets are illustrative, vendor-specific, or not production-grade. Below is every deviation we are adopting, **with the reasoning spelled out** (this section is written for a junior dev — it explains *why*, not just *what*). When in doubt, follow this section over `PRAGMA.md`.

### 4.1 Consolidate generation on Claude; use structured outputs
- **Spec:** `gpt-4o-mini` for extraction + `claude-sonnet` for RAG + OpenAI for auto-titles. Three vendors.
- **Change:** All LLM generation goes through **one** `LLMClient` (`app/providers/llm.py`) backed by the official `anthropic` SDK.
  - **Skill extraction & auto-titles:** `claude-haiku-4-5` (cheap, fast, great at structured JSON).
  - **RAG answers:** `claude-sonnet-4-6` (better cited prose); escalate to `claude-opus-4-8` only if quality demands it.
  - Model IDs are exact strings: `claude-haiku-4-5`, `claude-sonnet-4-6`, `claude-opus-4-8`. Never append date suffixes.
- **Why:** One vendor = one SDK, one API key, one set of failure modes, one rate-limit pool to reason about. Mixed-vendor stacks multiply operational surface for zero benefit at our stage.
- **Bigger win — structured outputs:** The spec extracts skills with `response_format={"type": "json_object"}` (OpenAI-only, and it does *not* enforce your schema — the model can still emit the wrong shape). Claude's structured outputs **guarantee** the response matches your Pydantic `Skill` schema. Use `client.messages.parse(..., output_config={"format": ...})` (or the `messages.parse()` helper with a Pydantic model). This removes a whole class of "the LLM returned malformed JSON" bugs.
- **Junior note:** "Structured outputs" = you hand the model your schema and the API constrains its output to match it. You get a validated object back, not a string you have to `json.loads()` and pray over.

### 4.2 Embeddings: Voyage AI by default, behind a provider interface
- **Spec:** `text-embedding-3-small` (OpenAI), 1536 dims, hardcoded.
- **Change:** All embedding goes through `EmbeddingProvider` (`app/providers/embeddings.py`). **Default to Voyage AI** (`voyage-3.5`), which is the embeddings provider Anthropic officially recommends (Anthropic has no first-party embedding model). OpenAI `text-embedding-3-small` and a local model remain swappable implementations.
- **Why abstract it:** The dedup near-dup check, the re-embedding migration strategy, and test mocking all become trivial when there's a single seam. Hardcoding `openai.embeddings.create(...)` across the codebase is the thing you regret in month 2.
- **Dimension discipline:** The `chunks.embedding` column is `VECTOR(N)` where **N must match the chosen model** (`voyage-3.5` = 1024; `text-embedding-3-small` = 1536). Pick the provider in Phase 0, set `N` in the first migration, and store `embedding_model` on every row. Switching models later = re-embed + new migration. Always compare same-model embeddings only.

### 4.3 pgvector index: HNSW, not IVFFlat
- **Spec:** `ivfflat ... WITH (lists = 100)`.
- **Change:** Use an **HNSW** index (`USING hnsw (embedding vector_cosine_ops)`).
- **Why:** IVFFlat needs a representative amount of data present *before* you build it to get good recall, and you must tune `lists`. Our store is continuously updated (incremental ingest), which is exactly IVFFlat's weak spot. HNSW gives better recall/latency, needs no `lists` tuning, and handles incremental inserts gracefully. Move to a dedicated vector DB (Qdrant) only when p95 > 200ms under load or chunk count > ~50M (the spec's own threshold).

### 4.4 Conditions are structured data, not evaluated strings
- **Spec:** `safe_eval(condition.if_condition, context)` where `if_condition` is a string like `"amount > 500"` produced by the LLM.
- **Change:** Represent a condition as structured data: `{field: "amount", op: ">", value: 500}`. Evaluate with a tiny deterministic evaluator in `app/guardrails/conditions.py` over a whitelisted operator set.
- **Why:** Evaluating an LLM-produced string as code is a code-injection hole, even "safely." Structured conditions are data — there is nothing to inject, they're trivially testable, and they serialize cleanly to the `skills.conditions` JSONB column. This is the kind of thing that decides whether an enterprise's security team signs.

### 4.5 Idempotency: insert-first, rely on the DB constraint
- **Spec:** `check_idempotency(key)` then execute (check-then-act).
- **Change:** Insert the audit/idempotency row **first** under the `UNIQUE(idempotency_key)` constraint; if the insert conflicts, return the original result without re-executing. Also pass the same key to **Stripe's native `Idempotency-Key` header**.
- **Why:** Check-then-act has a race: two concurrent retries both "check" (miss), both execute → double refund. The unique constraint makes the database the arbiter — exactly the SERIALIZABLE/`ON CONFLICT` technique the spec already uses for dedup. Belt-and-suspenders with Stripe's own idempotency means even a bug in our layer can't double-charge.

### 4.6 Tenant isolation is a first-class concern
- **Gap in spec:** Only `audit_log` has row-level security. Everything else relies on remembering to add `WHERE workspace_id = ...`.
- **Change:** All data access goes through a thin repository layer that **always** injects `workspace_id`. Plan Postgres RLS per-tenant for v2.
- **Why:** A cross-tenant data leak (workspace A's Slack showing up in workspace B's search) is the single fastest way to kill a B2B company. Make it structurally hard to forget the filter.

### 4.7 API keys are hashed at rest
- **Spec:** `Authorization: Bearer pk_live_{workspace_id}_{secret}`, secret implicitly stored.
- **Change:** Store only an **argon2/bcrypt hash** of the secret; compare hashes on auth. The plaintext key is shown to the customer once at creation and never persisted.
- **Why:** Same reason you never store raw passwords. A DB dump must not hand an attacker live API keys.

### 4.8 MCP: use the official SDK + streamable HTTP, not hand-rolled endpoints
- **Spec:** custom `GET /mcp/tools` + `POST /mcp/call`.
- **Change:** Implement the server with the official **`mcp` Python SDK** over its standard transport (streamable HTTP). Knowledge tools open; action tools route through the guardrail pipeline server-side.
- **Why:** MCP is a standard. Customer agents (Claude, others) expect the standard handshake/transport. Reinventing the wire protocol means every client needs a custom shim — defeating the point of exposing MCP at all.

### 4.9 Keep Celery, but be deliberate about the async boundary
- We keep **Celery + Redis** (matches the spec, and it's battle-tested) but note the friction: Celery tasks are sync; our pipeline code is async. Each task runs its async work via a single `asyncio.run(...)` entry; don't scatter event loops. (`arq` is a cleaner async-native alternative if we ever revisit — out of scope for v1.)

### 4.10 Tests: real Postgres for DB logic, mocks only for HTTP
- **Change:** DB-correctness tests (dedup race, SERIALIZABLE conflict, pgvector search, RLS) run against a **real ephemeral Postgres** (testcontainers / the compose DB). Mock **only** external HTTP APIs (Claude, Voyage, Stripe, Slack, GitHub, Notion).
- **Why:** You cannot test a SERIALIZABLE write-write conflict or a pgvector `<=>` query against a mock — the behavior you care about lives in Postgres. Mocking the DB here would test nothing real.

### 4.11 Other production hygiene (apply throughout)
- **Per-workspace rate limiting** (Redis token bucket) to enforce plan limits and protect the hot path.
- **Credential encryption** of `source_connections.credentials` with Fernet (`PRAGMA_ENCRYPTION_KEY`); note KMS/Secrets Manager for v2.
- **`trace_id` propagation** end-to-end: Document → Chunk → Skill source → GuardrailRequest → AuditLogEntry. Wire an OpenTelemetry exporter in v2; the IDs exist from day 1.
- **HDBSCAN scaling cliff:** clustering *all* workspace embeddings each run is ~O(n²). Fine for v1 (tens of thousands of chunks). Mitigate later with incremental clustering / `approximate_predict` for new points. Don't pre-optimize now — just know the cliff is there.

---

## 5. Architecture Rules — NEVER violate these

- **Hot path never triggers cold path work.** `/api/v1/search`, `/skill`, `/ask` must never enqueue Celery jobs, embed new documents, or trigger extraction. They read pre-computed data only. (There is a test that asserts this.)
- **All chunk/document writes are atomic.** Delete-then-reinsert on document update happens inside one transaction via `db.session.transaction()`. Never split it across two calls.
- **Audit log is append-only.** Never add UPDATE/DELETE on `audit_log`. RLS enforces this at the DB level — do not work around it. Write an audit row on **every** guardrail outcome (approved / rejected / escalated).
- **Action tools always go through `guardrails.pipeline.evaluate()`.** Never call a tool executor directly. The pipeline is not optional.
- **`skill_id` is required on every action tool call**, enforced at the MCP layer (not just documented). An agent must call `get_skill` before acting.
- **HDBSCAN / clustering runs on the cold path only (Celery).** Never in a request handler.
- **Confidence is computed mechanically**, never asked of the LLM (it overestimates). See `extractor.py::compute_confidence`.
- **Every data query is workspace-scoped.** Go through the repository layer; never write a raw cross-workspace query.

---

## 6. Conventions

### Python
- Python 3.11+. `async`/`await` throughout — no sync DB calls in async handlers.
- Pydantic v2 for all schemas (`model_validate`, not `parse_obj`).
- Type hints on every function signature.
- `ruff` lint + format, line length 100. Run before committing.
- `loguru` for logging (with the redaction filter), not stdlib `logging`.

### Naming
- DB tables: snake_case plural (`chunks`, `audit_log`, `agent_permissions`).
- Pydantic models: PascalCase (`GuardrailRequest`, `SkillCondition`).
- Celery tasks: snake_case verb-noun (`ingest_document`, `cluster_workspace`).
- MCP tools: snake_case verb-noun (`search_knowledge`, `issue_refund`).

### Error handling
- Raise typed exceptions (`GuardrailRejection`, `GuardrailEscalation`) — never return error dicts from internal functions.
- Wrap all external API calls (Stripe, Claude, Voyage, connectors) in try/except: log with `workspace_id` + `trace_id`, write a failed `AuditLogEntry` where applicable, then re-raise.
- Circuit-breaker trips must log `workspace_id` and `source` — never swallow them.

### Testing
- Tests in `backend/tests/`, mirroring `app/`. `pytest-asyncio`.
- Mock **only** external HTTP APIs. DB logic tests use a real ephemeral Postgres.
- Dedup tests **must** cover the concurrent-insert race (two workers, same doc).
- Guardrail tests **must** cover all five checks independently and combined.

---

## 7. Build Plan — per phase

Build order is dependency-correct: **0 → 1 → 2 → 3 → 4 → 5 → 6.** Each phase leaves the tree green (lint + tests pass). The **full per-function specification** — signatures, responsibilities, edge cases, and test lists for every function in every phase — is in [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md). Summary:

- **Phase 0 — Foundation:** repo skeleton, `pyproject.toml`, ruff/pytest config, `config.py`, `logging.py`, `db/session.py`, app factory, Celery app, `docker-compose.yml`, `.env.example`, test fixtures (autouse external mocks). *Done when:* `ruff`, `alembic upgrade head`, `pytest`, and `uvicorn` all succeed on an empty app.
- **Phase 1 — Data contracts:** all Pydantic models + SQLAlchemy tables + first migration (with `CREATE EXTENSION vector`, HNSW index, unique constraints, audit_log RLS added in the migration body). *Done when:* migration applies and an RLS test confirms UPDATE/DELETE on `audit_log` are rejected.
- **Phase 2 — Ingestion (cold):** `normalizer` → `chunker` → `embedder` → `dedup`, connector base + Slack/GitHub/Notion, Celery ingest tasks. *Done when:* a seeded workspace ingests end-to-end; dedup race test passes.
- **Phase 3 — Retrieval (hot):** `vector_store.similarity_search`, `/search`, `/ask` (skills-first + RAG fallback), API-key auth. *Done when:* p95 search < 500ms locally; hot-path-no-cold-work test passes.
- **Phase 4 — Skills (cold):** `clusterer` (HDBSCAN), `extractor` (structured outputs + mechanical confidence), `versioner`, hourly task, `/skill`. *Done when:* skills generate with sourced evidence and version on change.
- **Phase 5 — Guardrails + actions + audit (CP):** five-check `pipeline`, structured `conditions`, `permissions`, insert-first `idempotency`, `registry` + `stripe_refund`, append-only `audit/log`, approval queue. *Done when:* all-five-checks tests pass; double-call returns the original result without re-executing.
- **Phase 6 — MCP + E2E:** official MCP server (knowledge open, actions gated, `skill_id` enforced), `sources` OAuth endpoints, thin dashboard, end-to-end test (`get_skill` → `issue_refund` → guardrail → mock Stripe → audit row).

---

## 8. Status board

Update this as real code lands. Everything is **not started** until the file exists and its tests pass.

| Phase | Item | Status |
|---|---|---|
| 0 | Repo skeleton, config, logging, db session, app factory, docker-compose | ☐ not started |
| 1 | Pydantic models + ORM tables + first migration (+RLS) | ☐ not started |
| 2 | Normalizer, chunker, embedder, dedup | ☐ not started |
| 2 | Slack / GitHub / Notion connectors + Celery ingest | ☐ not started |
| 3 | Vector store + `/search` + `/ask` + auth | ☐ not started |
| 4 | HDBSCAN clusterer, extractor, versioner, `/skill` | ☐ not started |
| 5 | Guardrail pipeline, conditions, permissions, idempotency | ☐ not started |
| 5 | `issue_refund` (Stripe), audit log (RLS), approval queue | ☐ not started |
| 6 | MCP server, sources OAuth, dashboard, E2E test | ☐ not started |

**Next task:** Phase 0 — scaffold `backend/` (see [`docs/BUILD_PLAN.md` → Phase 0](docs/BUILD_PLAN.md)).

---

## 9. Key Data Contracts — do not break these

### Document (normalizer output; input to everything downstream)
Every connector emits this. No downstream stage imports connector-specific types — this is the abstraction that keeps adding a source O(1) instead of O(N×stages).
```python
# app/models/document.py — Document
id, source, doc_type, title, content, author, participants,
created_at, updated_at, url, content_hash, metadata, trace_id
```

### Skill (extractor output)
```python
# app/models/skill.py — Skill
skill_id, skill_name, trigger, confidence ("high"|"medium"|"low"),
steps, conditions, contradictions, sources, last_updated,
superseded_by, cluster_id, version
```
`conditions` are structured (`{field, op, value, then_action}`), not strings. `confidence` is computed mechanically in `extractor.py`.

### GuardrailDecision (outcome of the five-check pipeline)
```python
# app/models/guardrail.py — GuardrailDecision
outcome ("approved"|"rejected"|"escalated"),
failed_check, reason, risk_tier, requires_human,
condition_results, idempotency_key
```

---

## 10. Files — never edit directly
- `backend/app/db/migrations/*` — generate with `alembic revision --autogenerate`, then **review and add** what autogen misses (pgvector extension, HNSW index, RLS policies) in the new migration's `upgrade()`. **Never hand-edit an already-applied migration** — write a new one.
- `backend/app/mcp/server.py` — tool signatures are the public API contract; discuss before changing.
- `audit_log` schema — any change needs a new migration; never alter existing columns.

---

## 11. Environment variables

```bash
DATABASE_URL=postgresql+asyncpg://...
REDIS_URL=redis://localhost:6379/0
ANTHROPIC_API_KEY=sk-ant-...          # Claude: extraction, RAG, auto-titles
VOYAGE_API_KEY=...                    # embeddings (default provider)
# OPENAI_API_KEY=sk-...               # only if EMBEDDING_PROVIDER=openai
EMBEDDING_PROVIDER=voyage             # voyage | openai | local
STRIPE_SECRET_KEY=sk_test_...
SLACK_CLIENT_ID=...
SLACK_CLIENT_SECRET=...
GITHUB_APP_ID=...
GITHUB_APP_PRIVATE_KEY=...            # PEM, newlines as \n
NOTION_CLIENT_ID=...
NOTION_CLIENT_SECRET=...
PRAGMA_ENCRYPTION_KEY=...             # Fernet key for credential encryption at rest
```
Never log any of these (the loguru redaction filter helps, but don't rely on it alone). Never commit `.env`. Keep `.env.example` with placeholders.
