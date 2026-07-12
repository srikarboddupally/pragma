# Learning Phase 0 & Phase 1 — line by line

> A study guide for understanding **every file** we wrote in Phase 0 (foundation) and
> Phase 1 (data contracts), why each line is there, and how to internalize the patterns by
> re-writing them yourself.
>
> Read this with the actual files open side by side. The goal is not to memorize — it's to
> understand the *decision* behind each line so you can reproduce it from scratch.

---

## 0. How to use this document

There are three passes. Do them in order, don't skip:

1. **Read pass** — read each section here with the real file open. After each file, close this
   doc and explain the file out loud (or in writing) in your own words. If you can't, re-read.
2. **Blank-page pass** — open an empty file and try to re-write it from memory. Check against
   the original. The diff *is* your study list.
3. **Mutation pass** — deliberately change something (add a field, break a constraint) and
   predict what fails. Then run it and see if you were right. This is where understanding sticks.

A few symbols you'll see repeated, worth knowing up front:

- `from __future__ import annotations` — makes all type hints *strings* evaluated lazily.
  Lets you write `dict | None` and forward-reference types on Python 3.11 without import-order
  pain. Put it at the top of every module; treat it as boilerplate.
- `Mapped[...]` / `mapped_column(...)` — SQLAlchemy 2.0's typed way to declare a DB column.
- `Field(default_factory=list)` — Pydantic's way to say "default to a *new empty* list per
  instance" (never `= []`, which would share one list across all instances — a classic bug).

---

## PART 1 — The mental model (read before any code)

Pragma has two halves that never block each other (this is **CQRS**):

- **Cold path (write):** sources → ingest → embed → cluster → extract skills. Slow, runs on
  Celery workers. Phase 0 sets up the Celery app; Phase 2+ fills it in.
- **Hot path (read):** an HTTP/MCP call → query pre-computed data → respond in < 500ms.
  Phase 0 sets up the FastAPI app; Phase 3+ fills it in.

**Phase 0** builds the empty skeleton both halves stand on: config, logging, DB connection,
the app objects, and the container setup. Nothing "does" anything yet — but everything has a
home. **Phase 1** defines the *shapes of the data* (Pydantic models = in-memory contracts;
ORM tables = database contracts) and the *first migration* that stamps those tables into
Postgres. After Phase 1, the system knows what a `Document`, `Chunk`, `Skill`, and audit row
*are* — it just can't produce them yet.

Two ideas to hold onto:

- **A "contract" is a shape that two pieces of code agree on.** The connector promises to
  hand the chunker a `Document`; the chunker never asks "is this from Slack?" That promise is
  the `Document` model. Change the model and you change the promise everywhere at once.
- **Pydantic models vs ORM tables are two different things for two different worlds.** Pydantic
  = data moving through Python (validated, serialized to JSON). ORM = data sitting in Postgres
  (columns, indexes, constraints). They overlap in fields but are *not* the same object, and
  that's deliberate.

---

## PART 2 — Phase 0, file by file

### 2.1 `pyproject.toml` — the project's identity card

This is the single source of truth for *what the project is* and *what it depends on*.

- `[build-system]` — tells pip how to build/install the package. `setuptools` is the standard,
  boring choice. You rarely touch this.
- `[project]` — name, version, `requires-python = ">=3.11"` (we use 3.11+ syntax like `X | Y`).
- `dependencies = [...]` — the runtime libraries. Note they're **unpinned** (no `==1.2.3`).
  For an app you'd normally pin for reproducibility; here we keep it loose for early dev and
  generate a pinned `requirements.txt` for Docker. Each entry maps to a real capability:
  `fastapi`/`uvicorn` (hot path), `celery[redis]` (cold path), `sqlalchemy[asyncio]`+`asyncpg`+
  `pgvector` (DB + vectors), `anthropic`/`voyageai` (LLM + embeddings), `argon2-cffi` (key
  hashing), `cryptography` (Fernet), `mcp` (the server protocol).
- `[project.optional-dependencies] dev` — tools only needed to *develop*, not to *run*:
  `pytest`, `ruff`, `testcontainers[postgres]` (real ephemeral DB for tests), `respx` (mock HTTP).
  Installed via `pip install -e ".[dev]"`. The `-e` means "editable" — your source changes take
  effect without reinstalling.
- `[tool.ruff] line-length = 100` and `select = [...]` — the linter config. The selected rule
  families matter: `E/F` (errors), `I` (import sorting — this is what bit us earlier in env.py),
  `UP` (modernize syntax), `B` (bug-prone patterns), `ASYNC` (async footguns).
- `[tool.pytest.ini_options] asyncio_mode = "auto"` — the line that lets you write
  `async def test_...` without decorating each one. Without it, async tests silently don't run.

**Why it matters:** when you `pip install -e ".[dev]"`, *this file* is what gets read. The
config (ruff/pytest) living here too means "one file describes the whole project."

### 2.2 `app/config.py` — typed settings, loaded once

The pattern: **never read `os.environ` scattered around your code.** Read it once, into a typed
object, and pass that around.

- `EMBEDDING_DIMS` dict — maps provider name → vector dimension. This is the *one place* that
  knows voyage=1024, openai=1536. Everything else asks `settings.embedding_dim`.
- `class Settings(BaseSettings)` — pydantic-settings reads each field from the matching env var
  (case-insensitive: `DATABASE_URL` → `database_url`). If a required field is missing and has no
  default, the app **fails to boot loudly** instead of crashing later with a confusing error.
- `model_config = SettingsConfigDict(env_file=".env", extra="ignore", ...)` — load a local
  `.env` file in dev; `extra="ignore"` means unknown env vars don't crash it.
- Infrastructure fields (`database_url`, `redis_url`) have **localhost defaults** so the app
  boots with zero setup. Secret fields default to `""` — present but empty, only needed when you
  actually use that provider.
- `embedding_provider: Literal["voyage", "openai", "local"]` — `Literal` means Pydantic
  *rejects* any other value at load time. A typo in the env var becomes a startup error, not a
  3am bug.
- `@property embedding_dim` — computed, not stored: "given the chosen provider, what's the dim?"
- `@lru_cache def get_settings()` — `lru_cache` makes this a **singleton**: the first call builds
  `Settings()`, every later call returns the same object. The note about `cache_clear()` exists
  because tests change env vars and need to force a rebuild.

**The lesson:** configuration is a *typed, validated, cached* object — not a pile of `os.getenv`.

### 2.3 `app/logging.py` — logging that can't leak secrets

- `_SECRET_FIELDS` — the names of settings whose *values* must never appear in a log line.
- `_make_patcher()` — builds a function that loguru runs on every log record. It grabs the
  actual secret values from settings, and if any appears in a message, replaces it with
  `***REDACTED***`. Note `(v := getattr(settings, f))` — the walrus operator both fetches and
  filters: only non-empty secrets get added to the scrub list.
- `configure_logging()` — `logger.remove()` clears loguru's default handler (so we don't double
  log), then adds our own to stderr with a colored format. **`diagnose=False` is a security
  line, not a style one:** loguru's "diagnose" feature expands local variables into tracebacks —
  which could dump a secret-holding variable into your logs. We turn it off.

**The lesson:** treat logs as a place secrets can leak, and build the guardrail once, centrally.
But the docstring is honest: it's a *safety net*, not permission to log credentials.

### 2.4 `app/db/base.py` — the ORM's root object

Tiny but load-bearing.

- `class Base(DeclarativeBase): pass` — every ORM table subclasses this. SQLAlchemy collects
  all tables onto `Base.metadata`, which is what Alembic reads to know "what tables should exist."
- The docstring says "keep this import-light." Reason: Alembic imports `Base` to do its work; if
  this module pulled in heavy stuff, every migration command would get slow. A clean seam.

### 2.5 `app/db/session.py` — how the app talks to Postgres

This is the heart of the data layer. Three things live here: the engine, the session factory,
and the `transaction()` helper.

- `engine = create_async_engine(url, pool_pre_ping=True)` — the engine owns the connection pool.
  `pool_pre_ping` sends a tiny "are you alive?" before handing out a connection, so a DB restart
  doesn't hand you a dead socket.
- `AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)` — a *factory* that
  makes sessions. `expire_on_commit=False` means objects stay usable after commit (otherwise
  accessing an attribute post-commit triggers a surprise reload).
- The **second engine** (`_serializable_engine` / `SerializableSessionLocal`) is pinned to
  `isolation_level="SERIALIZABLE"`. This exists for the dedup race: two workers inserting the
  same document at once. SERIALIZABLE makes Postgres detect the conflict and abort one, so we
  never double-insert. Most writes don't need this (it's stricter and can force retries), so it's
  a *separate* factory you opt into.
- `get_session()` — an async generator for FastAPI's dependency injection: yields a session, and
  the `async with` guarantees it's closed even if the handler raises. Note: **no implicit
  transaction** — reads don't need one.
- `transaction(*, serializable=False)` — the *only* sanctioned way to do multi-statement writes.
  `async with session.begin()` commits on clean exit and rolls back on any exception. This is the
  thing CLAUDE.md means by "all chunk/document writes are atomic": dedup's delete-then-reinsert
  happens inside one `transaction()` so it can't half-succeed.

**The lesson:** centralize DB access patterns so correctness (atomicity, isolation) is decided in
*one* file, not re-decided (and gotten wrong) at every call site.

### 2.6 `app/main.py` — the FastAPI app factory

- `create_app()` is a **factory function**, not a module-level app. Why a function? Tests can
  build a fresh app; you can configure it before returning. It calls `configure_logging()` first
  so logging is set up before anything logs.
- The `/health` endpoint returns `{"status": "ok"}`. Boring on purpose: load balancers and
  `docker-compose` healthchecks hit it to know the app is alive.
- `app = create_app()` at the bottom — uvicorn imports this (`app.main:app`).
- The comment about routers and the **hard rule** ("nothing mounted here may trigger cold-path
  work") is the architectural spine: the hot path reads pre-computed data, never kicks off Celery.

### 2.7 `app/worker.py` — the Celery app

- `celery_app = Celery("pragma", broker=redis, backend=redis)` — the broker is the queue (where
  tasks wait); the backend stores results. Both are Redis here.
- `task_acks_late=True` — a task is acknowledged *after* it finishes, not when picked up. If a
  worker crashes mid-task, the task isn't lost — it requeues. Important for long ingest jobs.
- `worker_prefetch_multiplier=1` — each worker grabs one task at a time. Prevents a worker from
  hoarding a batch of slow tasks while others sit idle ("fair dispatch").
- The commented `beat_schedule` and `autodiscover_tasks` — placeholders. The pattern: write the
  skeleton with the future wired up but disabled, so Phase 2 just uncomments.
- The junior note about `asyncio.run(...)` is the key gotcha: Celery is sync, our code is async,
  so each task crosses into async land *exactly once*. Scattering event loops is a real bug source.

### 2.8 `Dockerfile` — how the backend becomes a container

- `FROM python:3.11-slim` — small base image with Python 3.11.
- The `apt-get install build-essential` line installs a C compiler. Needed because `hdbscan`,
  `asyncpg`, and `argon2` have native (C) extensions that compile on install. `rm -rf
  /var/lib/apt/lists/*` afterward keeps the image small.
- `COPY requirements.txt` *then* `pip install` *then* `COPY . .` — this ordering is a **Docker
  layer-caching trick**: dependencies change rarely, so that layer is cached; your source changes
  often but doesn't bust the (slow) install layer.
- `CMD [...]` — the default command (run uvicorn). `docker-compose.yml` overrides it per service.

### 2.9 `docker-compose.yml` — the whole local stack

- `db` uses `pgvector/pgvector:pg16` — Postgres 16 *with the vector extension preinstalled*. A
  plain `postgres` image wouldn't have it. A named volume `pgdata` persists data across restarts.
  The `healthcheck` (`pg_isready`) lets other services wait until the DB is actually accepting
  connections.
- `redis` — the broker/cache, with its own `ping` healthcheck.
- `api` / `worker` / `beat` — all three `build: ./backend` (same image), differing only in
  `command`: uvicorn, celery worker, celery beat. This is CQRS made literal — same code, three
  process roles.
- `environment:` overrides `DATABASE_URL`/`REDIS_URL` to use the service names `db`/`redis` as
  hostnames (Docker's internal DNS), not `localhost`.
- `depends_on: condition: service_healthy` — don't start the app until DB+Redis pass their
  healthchecks. (Note `beat` only needs redis, not the db.)

### 2.10 `tests/conftest.py` — the test harness

- The `_TEST_ENV` dict + `os.environ.setdefault(...)` loop sets fake credentials **before**
  `app.config` is imported. Order matters: settings read env at import time, so the fake env must
  exist first. `setdefault` means "only if not already set" — so a real CI env can override.
- `get_settings.cache_clear()` — because settings may have been cached during collection, force a
  rebuild with the test env.
- `pg_url` fixture — spins up a *real* ephemeral pgvector Postgres via testcontainers, and
  **skips the test** (doesn't fail) if Docker isn't available. That's why your unit suite stays
  green on a machine without Docker.
- `migrated_pg_url` fixture — takes that fresh DB and runs `alembic upgrade head` against it, so
  DB tests run on the real schema. `cfg.set_main_option("sqlalchemy.url", pg_url)` is exactly the
  hook that `env.py` was modified to honor.

**The lesson (CLAUDE.md §4.10):** mock only external HTTP. Test DB behavior against a real DB,
because SERIALIZABLE conflicts and RLS *don't exist* in a mock.

---

## PART 3 — Phase 1, file by file

Phase 1 is "data contracts." Two layers: **Pydantic models** (`app/models/`) describe data in
Python; **ORM tables** (`app/db/tables.py`) describe data in Postgres; the **migration**
(`0001_init.py`) creates those tables for real.

### 3.1 `app/models/document.py` — the most important abstraction

- `compute_document_id(source, source_id)` — a sha256 of `"{source}:{source_id}"`. Deterministic:
  the same Slack thread always hashes to the same id, so re-syncing updates rather than
  duplicates. (sha256 here is for a *stable id*, not security.)
- `compute_content_hash(content)` — sha256 of the content. The dedup layer compares this to skip
  re-processing unchanged documents.
- `class Document(BaseModel)` — the canonical shape every connector must produce. Fields:
  identity (`id`, `source`, `doc_type`), content (`title`, `content` — *cleaned plaintext*,
  no markdown), people (`author`, `participants`), time (`created_at`/`updated_at`), `url`,
  `content_hash`, a free-form `metadata` dict for source-specific extras, and `trace_id` for
  end-to-end tracing. **No connector-specific fields** — that's what makes "add a source" an
  O(1) change.
- `participants: list[str] = Field(default_factory=list)` — note `default_factory`, not `= []`.
- `class Chunk` — a slice of a document. Carries enough denormalized context (`doc_title`,
  `doc_url`, `author`) to render a search result without a join, plus `position`/`total_chunks`.
- `class EmbeddedChunk` — a `Chunk` *plus* its `embedding` vector, the `model` name, and a
  timestamp. **`model` is stored deliberately**: you must only ever compare vectors from the same
  model, so every vector remembers where it came from.

### 3.2 `app/models/skill.py` — structured, safe conditions

- `ConditionOp = Literal["==","!=",">","<",">=","<=","in","not_in"]` — the *only* allowed
  operators. A closed set you can evaluate safely.
- `SkillCondition` — `{field, op, value, then_action}`. This is the big security decision
  (CLAUDE.md §4.4): a condition is **data**, not a string like `"amount > 500"` to be `eval`'d.
  There's nothing to inject. `field` names a key in the guardrail context; `value` is what to
  compare against; `then_action` is the human-readable consequence.
- `SkillSource` — provenance for a skill: which doc, url, author, date, source. Skills are
  *sourced* — every claim points back to evidence.
- `Skill` — the extractor's output. `confidence: Confidence` is a Literal of high/medium/low,
  and the comment hammers the rule: **computed mechanically in extractor.py, never asked of the
  LLM** (LLMs overestimate their own confidence). `superseded_by` + `version` implement the
  versioning chain; `contradictions` are surfaced, never hidden.

### 3.3 `app/models/guardrail.py` — request, decision, and typed errors

- `GuardrailRequest` — what an agent submits: who (`agent_id`, `workspace_id`), what (`tool_name`,
  `proposed_params`), the `skill_id` it's acting under, and `context` (the *actual values* the
  conditions get checked against).
- `ConditionResult` — one condition's evaluation: the condition, the bool `result`, and the
  `then_action` if it fired.
- `GuardrailDecision` — the pipeline's verdict: `outcome` (approved/rejected/escalated),
  `failed_check`, `reason`, `risk_tier`, `requires_human`, all the `condition_results`, and the
  `idempotency_key`.
- `GuardrailRejection` / `GuardrailEscalation` — **typed exceptions**, each storing `check` and
  `reason`. The convention (CLAUDE.md §Error handling): internal guardrail functions *raise*
  these, never return error dicts. The pipeline catches them and maps to a `GuardrailDecision`.
  Raising-not-returning means a check *cannot* be silently ignored.

### 3.4 `app/models/audit.py` — the audit entry shape

- `AuditLogEntry` mirrors the `audit_log` table. A row is written on **every** outcome
  (approved/rejected/escalated) and on execution success/failure. Nullable fields
  (`failed_check`, `error`, `result`, `executed_at`) capture whichever path happened.
- This is the in-memory shape; the *immutability* lives in the DB (RLS, next).

### 3.5 `app/db/tables.py` — the 8 ORM tables

This is where Pydantic-world meets Postgres-world. SQLAlchemy 2.0 style: each column is
`name: Mapped[type] = mapped_column(...)`.

- `_DIM = get_settings().embedding_dim` — the vector columns size themselves from config (1024
  for voyage). One knob, set in Phase 0, flows here.
- **`Workspace`** — the tenant. `id` is a string PK; everything else hangs off `workspace_id`.
- **`SourceConnection`** — one connected source per workspace. `UniqueConstraint("workspace_id",
  "source")` means "you can't connect Slack twice." `credentials` is JSONB holding a
  *Fernet-encrypted* payload (never plaintext). UUID primary key with `default=uuid.uuid4`.
- **`Document`** — `UniqueConstraint("source","source_id")` is the dedup anchor at the DB level.
  Note `metadata_: Mapped[dict] = mapped_column("metadata", JSONB, ...)` — the Python attribute is
  `metadata_` (trailing underscore) because `metadata` is reserved by SQLAlchemy's `Base`, but the
  *column* is named `metadata`. A small but important gotcha.
- **`Chunk`** — `doc_id` is a FK with `ondelete="CASCADE"`: delete a document, its chunks vanish
  automatically. `embedding: Mapped[list[float]] = mapped_column(Vector(_DIM))` is the pgvector
  column. `embedding_model` defaults to `"voyage-3.5"` so every row records its model.
  `near_duplicate_of` is a **self-referential FK** (`ForeignKey("chunks.id")`) — near-dupes link
  to the original instead of storing a copy.
- **`Skill`** — `skill_id` string PK; `trigger_embedding` is another vector column (so you can
  vector-search skills by trigger). `steps`/`conditions`/`contradictions`/`sources` are JSONB
  (they're lists of structured objects). `superseded_by` self-FKs to `skills.skill_id` for the
  version chain.
- **`AgentPermission`** — which agent may call which tool, with a `max_amount` cap and
  `allowed_scopes`. `UniqueConstraint("workspace_id","agent_id","tool_name")` = one grant per
  (agent, tool) per workspace.
- **`AuditLog`** — the big one. `request_id` is `unique=True`, and `idempotency_key` is
  `unique=True` — that uniqueness is what makes the **insert-first idempotency** pattern work
  (CLAUDE.md §4.5): you insert the row; if the key already exists the insert fails and you return
  the prior result instead of re-executing. `executed` is a non-null bool.
- **`WorkspaceApiKey`** — `key_prefix` (indexed, the lookup handle) + `secret_hash` (the argon2
  hash). The plaintext key is shown once at creation and never stored (CLAUDE.md §4.7).

**The lesson:** constraints (`UniqueConstraint`, FKs, `ondelete`) push correctness *into the
database*, where it holds even if application code has a bug. Read every `__table_args__` as a
sentence: "you cannot ___."

### 3.6 `app/db/migrations/versions/0001_init.py` — stamping it into Postgres

ORM tables describe the *desired* schema; a migration is the *executable steps* to get there.

- Top metadata: `revision = "0001_init"`, `down_revision = None` (this is the first migration).
  Alembic chains migrations by these IDs.
- **Hand-written, not autogenerated** — the docstring explains why: no DB existed to generate
  against, and Alembic autogenerate *cannot* express three things we need: the pgvector extension,
  HNSW indexes, and RLS policies. So we wrote it by hand and must keep it in sync with `tables.py`.
- `upgrade()`:
  - `op.execute("CREATE EXTENSION IF NOT EXISTS vector")` — must run before any `Vector` column.
  - `op.create_table(...)` for each of the 8 tables — mirrors `tables.py` column-for-column.
  - `op.create_index(...)` for plain btree indexes (e.g. `ix_chunks_doc_id`).
  - `op.execute("CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)")` — the **HNSW**
    vector index, written as raw SQL because Alembic has no helper for it. HNSW (not IVFFlat) is
    CLAUDE.md §4.3: better recall, handles incremental inserts, no `lists` tuning.
  - The **RLS block** on `audit_log` is the append-only enforcement:
    - `ENABLE ROW LEVEL SECURITY` turns RLS on.
    - `FORCE ROW LEVEL SECURITY` makes it apply *even to the table owner* — without FORCE the
      owner bypasses it.
    - `deny_update` / `deny_delete` policies `USING (false)` — no row ever matches, so UPDATE and
      DELETE affect zero rows.
    - `allow_read` / `allow_insert` `USING/WITH CHECK (true)` — reads and inserts are fine.
    - **Crucial caveat:** superusers bypass RLS entirely. So the *application* must connect as a
      non-superuser role for this to bite. That's exactly what the RLS test does (creates a
      `NOSUPERUSER` role and asserts UPDATE/DELETE affect 0 rows).
- `downgrade()` — drops everything in *reverse* dependency order (children before parents, then
  the extension). A migration you can't reverse is a migration you can't trust.

**The lesson:** the migration is the *real* schema; the ORM is how the app talks to it. They must
agree, and when autogenerate can't express something (extensions, vector indexes, RLS), you write
raw SQL and own keeping it in sync.

### 3.7 The Phase 1 tests (what "done" means)

- `tests/models/test_*.py` — pure unit tests: build each model, assert validation works
  (e.g. a bad `confidence` or `op` is rejected, `default_factory` lists are independent). No DB.
- `tests/db/test_schema.py` — runs against the real migrated Postgres (`migrated_pg_url`):
  - `test_chunk_round_trip_and_vector_search` — insert a workspace→document→chunk with a 1024-dim
    vector, then `ORDER BY embedding <=> '...'` and assert the chunk comes back. Proves pgvector +
    the HNSW index + the dimension all line up.
  - `test_audit_log_is_append_only_under_rls` — `SET ROLE` to a `NOSUPERUSER` role, INSERT a row
    (works), UPDATE and DELETE it (both affect **0 rows** thanks to the deny policies), then RESET
    ROLE and confirm the row is unchanged. This is the test that proves the immutability claim.

"Phase 1 done" = these pass (the two DB ones skip cleanly without Docker).

---

## PART 4 — Best practices, distilled from this code

These are the transferable lessons. Each one shows up multiple times above.

1. **Read config once into a typed object.** `Settings(BaseSettings)` + `@lru_cache`. Never
   `os.getenv` scattered around. Typos become startup errors via `Literal`.
2. **Centralize a risky pattern in one place.** Atomic writes live in `transaction()`. Secret
   redaction lives in one patcher. Embedding dimension lives in one dict. Fix it once, correct
   everywhere.
3. **Push correctness into the database.** Unique constraints, FKs with `ondelete`, and RLS hold
   even when app code is buggy. The DB is your last line of defense — use it.
4. **Make illegal states unrepresentable.** Structured `SkillCondition` instead of an eval'd
   string. `Literal` operator sets. A `Document` with no connector-specific fields. If the type
   system forbids the bad case, you can't hit it.
5. **Fail loud and early.** Missing required config → no boot. Bad enum → validation error.
   Guardrail failure → *raised* typed exception, never a swallowed error dict.
6. **Factories over globals.** `create_app()` and `async_sessionmaker` let tests build fresh,
   isolated instances.
7. **Test the real thing for DB logic, mock only HTTP.** You can't test RLS or a SERIALIZABLE
   race against a mock. Skip-don't-fail when Docker is absent keeps the unit suite portable.
8. **Comment the *why*, not the *what*.** Notice the good comments explain decisions (`FORCE` and
   superusers, HNSW vs IVFFlat, `default_factory`). The code already says what; comments earn
   their place by saying why.
9. **Set up the skeleton with the future wired but disabled.** The commented Celery beat schedule,
   the router include comments — Phase 2 uncomments instead of rediscovering.
10. **Keep seams clean and import-light.** `base.py` is tiny so Alembic loads fast. The
    `Document` contract decouples connectors from everything downstream.

---

## PART 5 — Learn by re-writing (the drill that actually works)

Reading code you didn't write feels like understanding. Re-writing it proves it. Do this in
order; each step is harder.

### Drill A — Narrate (15 min/file)
For each file, write 2–3 sentences: *what it does, why it exists, what breaks if it's deleted.*
If you can't answer "what breaks if deleted," you don't understand it yet — re-read that section.

### Drill B — Fill in the blanks (per file)
Copy a file into a scratch location, delete the *bodies* (keep signatures and docstrings), and
re-write the bodies from memory. Suggested order (easy → hard):
1. `base.py`, `main.py`, `worker.py` (small, structural)
2. `config.py`, `logging.py` (patterns, no DB)
3. `models/document.py` → `skill.py` → `guardrail.py` → `audit.py` (contracts)
4. `db/session.py` (engines, the transaction helper)
5. `db/tables.py` (the 8 tables — do one table at a time)
6. `migrations/0001_init.py` (the payoff: turn the ORM into SQL by hand)

Check each against the original. **The diff is your syllabus** — study exactly what you missed.

### Drill C — Mutation (predict, then verify)
Make a change, *predict the outcome in writing*, then run and check. Examples worth doing:
- Change `embedding_provider` default to `"openai"`. What is `embedding_dim` now? Why would the
  existing migration (hard-coded 1024) now be *wrong*? (This teaches §4.2.)
- Add a required field to `Document` with no default. Which tests fail, and what's the error?
- Remove the `UniqueConstraint("source","source_id")` from `documents`. What dedup guarantee did
  you just lose?
- In the migration, change `FORCE ROW LEVEL SECURITY` to just `ENABLE`. Does the RLS test still
  pass? (It should — until you run it as the *owner*. Think about why.)
- Replace `Field(default_factory=list)` with `= []` on a model, create two instances, append to
  one. What happens to the other? (The classic shared-mutable-default bug.)

### Drill D — Rebuild Phase 1 from Phase 0 (capstone)
Delete your local `app/models/`, `tables.py`, and the migration (you have them in git — this is
safe). Re-create all of Phase 1 from scratch, using only the **Key Data Contracts** section of
CLAUDE.md and your notes. When `pytest` is green again, you understand Phase 1.

### How to verify your work
From `backend/` with the venv active:
```bash
ruff check app/ && ruff format --check app/   # style + imports
pytest -q                                       # unit tests (DB tests skip w/o Docker)
```
Green = you matched the contracts. Red = read the failure; it's telling you which assumption was
wrong.

---

## Appendix — mini-glossary

- **CQRS** — Command Query Responsibility Segregation. Writes and reads are separate code paths
  that scale independently. Here: Celery (write) vs FastAPI (read).
- **ORM** — Object-Relational Mapper. Python classes ↔ DB tables (SQLAlchemy).
- **Migration** — a versioned, executable change to the DB schema (Alembic).
- **RLS** — Row-Level Security. Postgres policies that decide, per row, who can do what. We use it
  to make `audit_log` append-only.
- **HNSW** — Hierarchical Navigable Small World, a vector index for fast nearest-neighbor search.
- **pgvector** — the Postgres extension adding the `vector` column type and `<=>` distance ops.
- **Idempotency** — doing an operation twice has the same effect as once. We get it from a unique
  DB constraint on `idempotency_key`.
- **Fernet** — symmetric encryption (from `cryptography`) used to encrypt stored credentials.
- **argon2** — a password-hashing algorithm; we hash API-key secrets with it.
- **Singleton** — exactly one instance shared everywhere; `@lru_cache` on `get_settings()`.
- **Factory** — a function that builds a configured object (`create_app()`, `async_sessionmaker`).
</content>
</invoke>
