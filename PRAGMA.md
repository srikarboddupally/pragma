# Pragma
## Company Intelligence, Executable.

> "Your company's institutional knowledge — ingested, structured, and executable by AI agents.  
> Not a search box. Not a chatbot. A memory layer that acts."

**Built by:** Sai Srikar Boddupally  
**Stack:** Python · FastAPI · PostgreSQL · pgvector · Redis · Celery · PyTorch  
**Stage:** Pre-seed / YC S26 target  
**Category:** YC RFS — Company Brain × Software for Agents  

---

## Table of Contents

1. [What Pragma Is](#1-what-pragma-is)
2. [The Problem](#2-the-problem)
3. [What We Are Not](#3-what-we-are-not)
4. [System Architecture](#4-system-architecture)
5. [Feature Specifications](#5-feature-specifications)
   - 5.1 [Ingestion Pipeline](#51-ingestion-pipeline)
   - 5.2 [Normalization Layer](#52-normalization-layer)
   - 5.3 [Chunking Engine](#53-chunking-engine)
   - 5.4 [Embedding Pipeline](#54-embedding-pipeline)
   - 5.5 [Deduplication Layer](#55-deduplication-layer)
   - 5.6 [Skills File Generator](#56-skills-file-generator)
   - 5.7 [Vector Store](#57-vector-store)
   - 5.8 [Guardrail Layer](#58-guardrail-layer)
   - 5.9 [Action Layer / Tool Registry](#59-action-layer--tool-registry)
   - 5.10 [Unified MCP Server](#510-unified-mcp-server)
   - 5.11 [Query API](#511-query-api)
   - 5.12 [Audit Log](#512-audit-log)
6. [Database Schema](#6-database-schema)
7. [System Design Decisions](#7-system-design-decisions)
8. [API Reference](#8-api-reference)
9. [Implementation Roadmap](#9-implementation-roadmap)
10. [Tech Stack](#10-tech-stack)
11. [Competitive Positioning](#11-competitive-positioning)
12. [Business Model](#12-business-model)
13. [YC Application Narrative](#13-yc-application-narrative)

---

## 1. What Pragma Is

Pragma is the intelligence layer that sits between a company's scattered institutional knowledge and the AI agents trying to act on it.

Every B2B SaaS company above 20 people has the same problem: critical knowledge — how decisions are made, how edge cases are handled, what the actual policy is versus what the wiki says — lives inside Slack threads, GitHub PR comments, Notion pages, and the heads of three senior people. When someone new joins, or when an AI agent is trying to resolve a support ticket, that knowledge is effectively unreachable.

Pragma solves this in two connected layers:

**Layer 1 — Company Brain (the knowing layer)**  
Pragma continuously ingests Slack, GitHub, Notion, and Linear. It normalizes every message, thread, PR, doc, and comment into a canonical document format, chunks and embeds them into a vector store, and — critically — runs a clustering and extraction pipeline that converts thousands of scattered, redundant mentions of a topic into a single structured "skill": a sourced, versioned, confidence-scored, executable procedure.

**Layer 2 — Agent Execution (the doing layer)**  
Pragma exposes a unified MCP server with two categories of tools: knowledge tools (semantic search, skill lookup, RAG) and action tools (issue refund, update ticket, send notification). Every action tool routes through a guardrail layer — five sequential checks: skill match, condition evaluation, permission scope, risk threshold, idempotency — before anything executes. Every execution writes to an append-only audit log.

The result: an AI agent that doesn't just find the refund policy — it issues the refund, following the company's exact sourced procedure, with full traceability, under the correct approval threshold, exactly once.

---

## 2. The Problem

### The knowledge problem

A 30-person SaaS company has 3 years of Slack history, 800 GitHub PRs with comment threads, 200 Notion pages (half outdated), and Linear tickets going back to founding. Somewhere in that corpus is the answer to almost every question a support agent, a new hire, or an AI system will ever need to ask.

But:
- **It is not searchable by meaning.** Keyword search returns noise. "Refund" matches 4,000 Slack messages, none of which directly state the policy.
- **It is not structured.** The policy exists as fragments across 40 threads, some of which contradict each other because the policy changed in March.
- **It is not executable.** Even if an AI finds the right answer, "the manager needs to approve over $500" is text it has to interpret, not a rule it can evaluate.
- **It decays.** The Notion page from 2022 still says to use the old Stripe flow. Three support engineers learned the new flow in a Slack thread in April. The wiki was never updated.

### The agent problem

AI agents in 2026 can take real actions: update records, issue refunds, send messages, modify tickets. The tooling (MCP, function calling, tool use) is mature. The problem is trust.

A company cannot deploy an AI that takes real actions without:
1. Knowing the AI is following the company's actual approved procedure (not hallucinating one)
2. Knowing every action taken can be reviewed, explained, and if necessary reversed
3. Knowing the AI cannot take an action it is not permitted to take (risk threshold enforcement)
4. Knowing the same action cannot accidentally run twice (idempotency)

None of these are solved by LLMs alone. They require infrastructure.

### Why now

- The MCP protocol has standardized how agents discover and call tools
- Vector databases and embedding APIs are cheap enough for per-company deployments
- YC's S26 RFS explicitly identifies both "Company Brain" and "Software for Agents" as high-priority unsolved categories
- Multiple YC-backed teams (Cerenovus W26, Promptless S26, Corvera S26) are attacking adjacent problems — the category is validated, the exact wedge (support/CS execution with guardrails) is not yet claimed

---

## 3. What We Are Not

This matters because the positioning is precise.

| Product | What it does | What Pragma does differently |
|---|---|---|
| **Glean** | Enterprise search across SaaS tools | We extract executable skills, not search results. We act, not just retrieve. |
| **Notion AI / Confluence AI** | Q&A over a specific knowledge base | We ingest *all* company communication, not just docs. We surface contradictions. |
| **Microsoft Copilot** | Retrieval-augmented generation over M365 | We produce structured skills with sourced evidence and confidence scores, not generated prose. |
| **Cerenovus (YC S26)** | Company brain for executive decisions | We target support/CS workflows — high-frequency, measurable, action-oriented. |
| **Promptless (YC S26)** | Auto-updated documentation | Our output is agent-executable skills, not human-readable docs. |
| **Intercom Fin / Decagon** | AI support agents | We are the knowledge *underneath* support agents, not a competitor to them. |

Pragma is **infrastructure**, not an interface. It makes every other AI agent smarter and safer.

---

## 4. System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        DATA SOURCES                                  │
│   Slack ──── GitHub ──── Notion ──── Linear (later)                 │
└────────────────────────┬────────────────────────────────────────────┘
                         │  (OAuth per workspace, webhook + poll)
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    INGESTION PIPELINE                                │
│                                                                      │
│  Connector → Normalizer → Chunker → Embedder                        │
│      │                                  │                            │
│  Circuit breaker          Content hash dedup check                  │
│  Backpressure queue       Delete-then-reinsert on change            │
│  Watermark sync           SERIALIZABLE isolation                    │
└────────────────────────┬────────────────────────────────────────────┘
                         │
           ┌─────────────┴─────────────┐
           ▼                           ▼
┌──────────────────┐       ┌─────────────────────────┐
│   VECTOR STORE   │       │   SKILLS FILE GENERATOR  │
│  (pgvector)      │       │                          │
│  chunks +        │       │  HDBSCAN clustering      │
│  embeddings +    │       │  LLM extraction          │
│  metadata        │       │  Versioning + contradictions│
└──────────┬───────┘       └────────────┬─────────────┘
           │                            │
           └─────────────┬──────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      QUERY API                                       │
│                                                                      │
│  search_knowledge ── get_skill ── ask (RAG)                         │
│  (hot path — <500ms, no heavy computation)                          │
└────────────────────────┬────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    GUARDRAIL LAYER                                   │
│                                                                      │
│  1. Skill match check                                                │
│  2. Condition evaluation (if/then rules from skill)                 │
│  3. Permission scope check (agent_id × tool × threshold)            │
│  4. Risk tier routing (auto-approve or escalate to human)           │
│  5. Idempotency check (hash-based, prevents double execution)       │
└────────────────────────┬────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    ACTION LAYER                                      │
│                                                                      │
│  issue_refund ── update_ticket ── send_notification ── ...          │
│  (v1: one real action — issue_refund via Stripe)                    │
└────────────────────────┬────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    AUDIT LOG                                         │
│  (append-only, immutable at DB level, full trace_id chain)          │
└─────────────────────────────────────────────────────────────────────┘
                         ▲
                         │
┌─────────────────────────────────────────────────────────────────────┐
│                 UNIFIED MCP SERVER                                   │
│  Knowledge tools (open) + Action tools (gated)                      │
│  The interface customer agents call                                  │
└─────────────────────────────────────────────────────────────────────┘
```

### Two pipelines, one product

**Write path (cold, async, background):**  
Sources → Ingestion → Vector store + Skills extractor  
Runs on Celery workers. Latency-insensitive. Can be slow. CPU-heavy.

**Read path (hot, synchronous, <500ms):**  
MCP tool call → Query API → Vector store / Skills file → Response  
Runs on FastAPI. Latency-sensitive. Must never block on write-path work.

This is CQRS applied to the knowledge layer: read and write paths are completely separated, each can scale independently.

---

## 5. Feature Specifications

---

### 5.1 Ingestion Pipeline

**What it does:**  
Pulls raw data from each configured source on a schedule (webhook-driven where available, polling fallback). Produces a stream of raw source objects — Slack messages, GitHub PR objects, Notion page content — and hands them to the Normalizer.

**What every sub-component does:**

**OAuth install flow**  
Each source requires a workspace-level OAuth install. The customer clicks "Connect Slack," authorizes the Pragma Slack app with read scopes (`channels:history`, `users:read`, `files:read`), and the bot token is stored encrypted per workspace. This is the customer's first meaningful action — getting here with no friction is a priority for v1.

**Webhook registration**  
After OAuth, register webhooks where the source supports them (Slack Events API, GitHub App webhooks). Webhooks push new events in real time, eliminating polling latency for new content. For sources with limited webhook support (Notion), fall back to scheduled polling every 15 minutes.

**Watermark-based incremental sync**  
Store `last_synced_at` per (workspace, source). On each sync cycle, only fetch content created or updated after the watermark. Never do full re-ingestion after the initial load. Initial sync of a large workspace (3 years of Slack) uses the backpressure-controlled job queue to avoid overwhelming the pipeline.

**Circuit breaker per connector**  
Each source connector wraps its API calls in a circuit breaker (fail_max=3 consecutive failures, reset_timeout=60s). If Slack's API returns 5xx three times in a row, the Slack connector's breaker opens. Only the Slack connector pauses — GitHub and Notion continue unaffected. When the breaker resets, the connector retries from its last watermark.

```python
from circuitbreaker import circuit

@circuit(failure_threshold=3, recovery_timeout=60)
async def pull_slack_channel(workspace_id: str, channel_id: str, since: datetime):
    resp = await slack_client.conversations_history(
        channel=channel_id,
        oldest=since.timestamp(),
        limit=200
    )
    return resp["messages"]
```

**Backpressure on the Celery queue**  
The connector checks queue depth before emitting jobs. If `queue.depth() > MAX_QUEUE_DEPTH` (default: 10,000), the connector sleeps for a backoff period before emitting more. This prevents the initial sync of a large workspace from flooding the queue and causing OOM on workers.

```python
async def emit_with_backpressure(jobs: list[IngestJob]):
    for job in jobs:
        while queue.depth() > MAX_QUEUE_DEPTH:
            await asyncio.sleep(BACKPRESSURE_SLEEP_S)
        queue.enqueue(job)
```

**When to use each sync mode:**

| Trigger | Use case | Implementation |
|---|---|---|
| Webhook event | New Slack message, new GitHub PR, new comment | Process immediately, enqueue single-doc job |
| Scheduled poll (15min) | Notion pages (no webhook), edited Slack messages | Watermark query, batch enqueue |
| Manual re-sync | Customer requests full refresh, schema migration | Full re-ingest with dedup — skip unchanged docs |
| On source connect | First-time OAuth install | Bulk historical ingest with backpressure |

**How to design with maximum intelligence:**  
The connector should preserve source-specific metadata at ingest time even if the normalizer doesn't use it today. Slack message reactions (emoji counts) can later signal "this message was important / endorsed by the team" — a signal for skills confidence scoring. GitHub PR review approvals signal authority of the commenter. Don't discard these at ingestion; store them in the raw metadata column.

---

### 5.2 Normalization Layer

**What it does:**  
Converts every raw source object — regardless of its origin format — into a single canonical `Document` schema. Every downstream stage only ever sees `Document`. No downstream stage needs to know what source the content came from.

**Why this is the most important abstraction in the system:**  
Without a canonical schema, adding a fourth source (Linear, email, Loom transcripts) requires modifying the chunker, the embedder, the dedup logic, and the skills extractor. With the canonical schema, adding a source is one new connector that emits `Document` objects — everything downstream is unchanged. This is the difference between O(N) and O(N×M) complexity as sources grow.

**The Document schema:**

```python
from pydantic import BaseModel
from datetime import datetime

class Document(BaseModel):
    id: str                    # hash(source + source_id) — stable across re-syncs
    source: str                # "slack" | "github" | "notion" | "linear"
    doc_type: str              # "thread" | "pr" | "page" | "issue" | "comment"
    title: str                 # human-readable summary (auto-extracted if absent)
    content: str               # cleaned plaintext — no markdown syntax, no HTML
    author: str                # display name or username
    participants: list[str]    # all people in the thread/PR/doc
    created_at: datetime
    updated_at: datetime
    url: str                   # deep link back to the original
    content_hash: str          # sha256(content) — for dedup
    metadata: dict             # source-specific extras preserved for future use
    trace_id: str              # propagates through every downstream stage
```

**Content cleaning rules:**  
- Strip all markdown syntax (the embedding model doesn't need `**bold**` — it needs `bold`)
- Strip all HTML tags
- Collapse consecutive whitespace
- Remove bot messages, automated messages, CI/CD notifications (by author pattern matching)
- For Slack: reassemble threaded replies in chronological order as a single content block, prefixed with `{author}: {message}` per turn
- For GitHub PRs: concatenate title + description + all review comments with author attribution
- For Notion: recursively fetch all block children, concatenate in document order

**Auto-extracted title:**  
If the source has no natural title (e.g., a Slack thread), extract one:  
`title = llm.complete(f"In 8 words or fewer, what is this about: {content[:500]}")`  
Cache this — never re-extract unless content changes.

---

### 5.3 Chunking Engine

**What it does:**  
Splits each `Document.content` into overlapping windows suitable for embedding. Produces `Chunk` objects that carry the full document context in their metadata.

**Why chunking matters for retrieval quality:**  
A document is rarely what you want to retrieve — a specific part of it is. A 3,000-word Notion page about the company's billing policies contains 20 distinct answerable facts. If you embed the whole page, retrieval matches "billing" but returns the entire page as context, diluting the relevant answer. Chunks let retrieval be precise.

**The chunk size decision:**

| Chunk size | Effect | Problem |
|---|---|---|
| 128 tokens | Highly precise retrieval | Fragments — no context for the embedding to be meaningful |
| 512 tokens | Sweet spot: context-rich, precise | — |
| 2048 tokens | High context, low precision | One bad sentence tanks the whole chunk's relevance |

Use 512 tokens, 50-token overlap (≈10%), sentence-boundary aware splitting.

**Implementation:**

```python
from langchain.text_splitter import RecursiveCharacterTextSplitter

splitter = RecursiveCharacterTextSplitter(
    chunk_size=512,         # tokens (approximate — use character proxy: 512*4=2048 chars)
    chunk_overlap=50,       # overlap window
    separators=["\n\n", "\n", ". ", " ", ""],   # preference order
    length_function=token_count,
)

def chunk_document(doc: Document) -> list[Chunk]:
    raw_chunks = splitter.split_text(doc.content)
    return [
        Chunk(
            id=f"{doc.id}_{i}",
            doc_id=doc.id,
            source=doc.source,
            doc_type=doc.doc_type,
            content=chunk_text,
            doc_title=doc.title,
            doc_url=doc.url,
            author=doc.author,
            created_at=doc.created_at,
            position=i,
            total_chunks=len(raw_chunks),
            trace_id=doc.trace_id,
        )
        for i, chunk_text in enumerate(raw_chunks)
    ]
```

**Chunk metadata is as important as chunk content:**  
When a retrieval result is returned to a user or agent, the chunk metadata determines whether they can trust and act on it. A chunk that says "refund approved" is useless without knowing it came from a Slack message from the CEO in 2026, not from a new hire's question in 2023. Always store and return: `source`, `doc_type`, `author`, `created_at`, `url`.

**Dynamic chunk sizing by doc type:**  
Not all content is equal. Use different chunk sizes per doc_type:

| doc_type | Chunk size | Reason |
|---|---|---|
| `thread` (Slack) | 256 tokens | Short conversational turns — small chunks preserve individual responses |
| `pr` (GitHub) | 512 tokens | Medium — PR descriptions are paragraph-sized |
| `page` (Notion) | 768 tokens | Long-form — needs more context to be meaningful |
| `issue` (Linear/Jira) | 256 tokens | Short by nature |

---

### 5.4 Embedding Pipeline

**What it does:**  
Converts each `Chunk.content` into a dense vector representation using an embedding model. Stores the vector alongside the chunk metadata in pgvector. Enables semantic (meaning-based) retrieval at query time.

**Model selection:**

| Model | Dimensions | Cost | Quality | Recommendation |
|---|---|---|---|---|
| `text-embedding-3-small` | 1536 | $0.02/1M tokens | Very good | **Default for v1** |
| `text-embedding-3-large` | 3072 | $0.13/1M tokens | Best OpenAI | Upgrade when precision matters for enterprise |
| `nomic-embed-text` | 768 | Free (local) | Good | Privacy-sensitive customers who won't send data to OpenAI |

**Batch embedding for cost efficiency:**

```python
async def embed_chunks(chunks: list[Chunk]) -> list[EmbeddedChunk]:
    # OpenAI allows up to 2048 inputs per request — batch aggressively
    BATCH_SIZE = 512
    results = []
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i:i+BATCH_SIZE]
        response = await openai.embeddings.create(
            input=[c.content for c in batch],
            model="text-embedding-3-small"
        )
        for chunk, embedding_data in zip(batch, response.data):
            results.append(EmbeddedChunk(
                chunk=chunk,
                embedding=embedding_data.embedding,
                model="text-embedding-3-small",
                embedded_at=datetime.utcnow()
            ))
    return results
```

**Embedding the query vs embedding the chunks:**  
At query time, embed the query using the **same model** that embedded the chunks. A query embedded with `text-embedding-3-large` against chunks embedded with `text-embedding-3-small` produces meaningless similarity scores. Store the model name alongside every chunk so this constraint is enforced.

**Re-embedding strategy:**  
When a better model becomes available, don't re-embed everything immediately. Embed new documents with the new model, keep old with the old model, and run a background job to gradually re-embed existing chunks. Track `embedding_model` on every chunk row. Filter searches to only compare same-model embeddings.

---

### 5.5 Deduplication Layer

**What it does:**  
Prevents the same content from appearing multiple times in the vector store — whether from re-syncing unchanged documents, from documents being updated, or from the same content arriving through two different source paths.

**Three distinct dedup problems:**

**Problem 1: Re-syncing an unchanged document**  
Most common. The 15-minute Notion poll sees a page that hasn't changed. Do not re-chunk, re-embed, or re-insert.

```python
existing_hash = db.get_doc_hash(doc.id)
new_hash = sha256(doc.content.encode()).hexdigest()

if existing_hash == new_hash:
    return SkipResult(reason="unchanged")

# Content changed — proceed to full re-ingest
```

**Problem 2: Document was updated — old chunks are stale**  
If the hash doesn't match, old chunks must be removed before new ones are inserted. Stale chunks mean retrieval returns outdated answers alongside current ones — confusing, undetectable, and corrosive to trust.

```python
async def reingest_document(doc: Document):
    async with db.transaction():    # atomic — no window of inconsistency
        db.delete_chunks(where={"doc_id": doc.id})
        new_chunks = chunk_document(doc)
        embeddings = await embed_chunks(new_chunks)
        db.insert_chunks(embeddings)
        db.upsert(
            table="documents",
            values={"id": doc.id, "content_hash": new_hash, ...},
            conflict_target=["source", "source_id"]
        )
```

**Problem 3: Same content arriving from two sources**  
Example: a GitHub PR description is pasted into a Slack thread. Both connectors ingest it. Two nearly identical chunk sets appear in the vector store, both retrieved for the same query, doubling confidence scores falsely.

Detection: after embedding, before inserting, check cosine similarity against existing chunks. If a chunk has similarity > 0.97 with an existing chunk from a different source, mark it as a near-duplicate and link rather than insert.

```python
async def check_near_duplicate(embedding: list[float], doc_id: str) -> str | None:
    result = db.execute("""
        SELECT id, doc_id, 1 - (embedding <=> $1::vector) AS similarity
        FROM chunks
        WHERE doc_id != $2
          AND 1 - (embedding <=> $1::vector) > 0.97
        LIMIT 1
    """, embedding, doc_id)
    return result[0]["id"] if result else None
```

**Race condition protection:**  
Two Celery workers could simultaneously determine that the same document is new and both attempt to insert. The database-level unique constraint on `(source, source_id)` ensures exactly-once insertion even under concurrent workers:

```sql
CREATE UNIQUE INDEX ON documents(source, source_id);
-- application uses: INSERT ... ON CONFLICT (source, source_id) DO UPDATE
-- second worker's insert hits the constraint and loses cleanly
```

This is the same class of correctness problem as concurrent withdrawal in a payments engine, solved with the same tool: database-enforced uniqueness, not application-level locking.

---

### 5.6 Skills File Generator

**What it does:**  
This is Pragma's most differentiated feature. After chunks are stored, the skills generator runs a background clustering and extraction pipeline over them to produce "skills" — structured, executable, sourced procedures distilled from all the evidence in the knowledge base.

A chunk says: *"We usually approve refunds if they ask nicely, just check with Sarah for big ones" (Slack, March 2024)*  
A skill says: *"Issue refund if within 30 days. If amount > $500, require manager approval in #finance-approvals. Step 1: verify Stripe. Step 2: check window. Step 3: issue + tag reason. Step 4: reply to customer."*

**Why skills are not the same as retrieved chunks:**  
A chunk is evidence. A skill is a decision. An AI agent can follow a skill — it has defined steps, conditions, and sources. An AI agent receiving a chunk must still interpret it, infer the rule, and potentially hallucinate a procedure that doesn't match company practice.

**Stage 1: HDBSCAN clustering over the embedding space**

```python
import hdbscan
import numpy as np

def cluster_chunks(embeddings: np.ndarray) -> np.ndarray:
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=3,         # need at least 3 mentions to be a pattern
        min_samples=2,              # noise tolerance
        metric="euclidean",
        cluster_selection_method="eom"
    )
    labels = clusterer.fit_predict(embeddings)
    # labels == -1 are noise (one-off mentions) — never become skills
    return labels
```

Why HDBSCAN over k-means: you do not know how many distinct "procedures" exist in a company's knowledge base. HDBSCAN is density-based and does not require k. It also assigns noise labels to outlier chunks — a single mention of a topic never produces a skill, preventing low-evidence hallucinations.

**Stage 2: LLM extraction per cluster**

```python
EXTRACTION_PROMPT = """
You are analyzing {n} excerpts from a company's internal communications, all related to the same topic.

Your job: identify if these excerpts consistently describe a procedure or decision pattern.

Rules:
- If they are consistent: extract the skill.
- If they contradict each other: flag the contradiction explicitly in the contradictions field.
- If there is no clear pattern: set confidence to "low" and leave steps empty.
- NEVER invent a procedure that isn't directly supported by the evidence.

Excerpts:
{chunks_text}

Output valid JSON matching this exact schema:
{schema}
"""

async def extract_skill(cluster_chunks: list[Chunk]) -> Skill | None:
    chunks_text = "\n---\n".join([
        f"[{c.source} | {c.author} | {c.created_at.date()}]\n{c.content}"
        for c in cluster_chunks
    ])
    response = await llm.complete(
        EXTRACTION_PROMPT.format(
            n=len(cluster_chunks),
            chunks_text=chunks_text,
            schema=SKILL_SCHEMA_JSON
        ),
        temperature=0.1,    # low temperature — we want deterministic extraction, not creative
        response_format={"type": "json_object"}
    )
    return Skill.model_validate_json(response.content)
```

**The Skill schema:**

```python
class SkillCondition(BaseModel):
    if_condition: str       # "amount > 500"
    then_action: str        # "requires manager approval in #finance-approvals"

class SkillSource(BaseModel):
    doc_id: str
    url: str
    author: str
    date: str
    source: str

class Skill(BaseModel):
    skill_id: str                           # skl_{uuid4().hex[:8]}
    skill_name: str                         # "Billing refund approval"
    trigger: str                            # "when a customer requests a refund"
    confidence: Literal["high","medium","low"]  # derived from source count + consistency
    steps: list[str]                        # imperative, ordered procedure steps
    conditions: list[SkillCondition]        # branching rules
    contradictions: list[str]              # flagged disagreements — never hide these
    sources: list[SkillSource]             # every document that contributed
    last_updated: datetime
    superseded_by: str | None              # points to newer skill_id if replaced
    cluster_id: str                        # which cluster produced this
    version: int                           # increment on each update
```

**Confidence scoring (mechanical, not LLM-assessed):**

```python
def compute_confidence(cluster_chunks: list[Chunk], contradictions: list[str]) -> str:
    source_count = len({c.source for c in cluster_chunks})   # distinct sources
    doc_count = len({c.doc_id for c in cluster_chunks})       # distinct documents
    has_contradictions = len(contradictions) > 0

    if doc_count >= 3 and source_count >= 2 and not has_contradictions:
        return "high"
    elif doc_count >= 2 and not has_contradictions:
        return "medium"
    else:
        return "low"
```

Never ask the LLM to rate its own confidence — it systematically overestimates. Derive confidence mechanically from evidence count and consistency. This is the signal customers trust because it has a traceable definition.

**Skill versioning on re-extraction:**  
When new chunks land in an existing skill's cluster, re-run extraction for that cluster only. Diff the new output against the stored skill. If steps changed materially, version it: increment `version`, set the old skill's `superseded_by` to the new `skill_id`, preserve full history. Never overwrite silently.

```python
async def update_skill_if_changed(cluster_id: str, new_chunks: list[Chunk]):
    existing_skill = db.get_skill_by_cluster(cluster_id)
    new_skill = await extract_skill(new_chunks)

    if skills_materially_differ(existing_skill, new_skill):
        new_skill.version = existing_skill.version + 1
        new_skill.skill_id = f"skl_{uuid4().hex[:8]}"
        db.update(existing_skill.skill_id, superseded_by=new_skill.skill_id)
        db.insert(new_skill)
    # else: no change, no write
```

**When to run extraction:**  
- Not on every chunk insert (too expensive and creates churn)
- On a schedule: once per hour, check for clusters with new chunks since last extraction run
- On manual trigger: customer can request re-extraction after a major knowledge update
- Never block the query API on extraction — it runs entirely on the cold path (Celery)

---

### 5.7 Vector Store

**What it does:**  
Stores chunk embeddings alongside metadata for similarity-based retrieval. The foundation of the semantic search capability.

**Why pgvector over a dedicated vector database (for v1):**

The core argument is transactional consistency. The dedup layer's delete-then-reinsert must be atomic: if the worker crashes between deleting old chunks and inserting new ones, the database must roll back the entire operation, not leave a half-updated state. With pgvector, all of this happens inside a single PostgreSQL transaction. With Pinecone or Qdrant, your document metadata lives in Postgres and your vectors live in a separate system — a crash leaves them permanently inconsistent with no way to detect or repair it.

Move to Qdrant when: query latency > 200ms under production load, or chunk count > 50M, whichever comes first.

**Schema:**

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE chunks (
    id              TEXT PRIMARY KEY,
    doc_id          TEXT NOT NULL,
    source          TEXT NOT NULL,
    doc_type        TEXT NOT NULL,
    content         TEXT NOT NULL,
    doc_title       TEXT,
    doc_url         TEXT,
    author          TEXT,
    created_at      TIMESTAMPTZ,
    position        INTEGER,
    total_chunks    INTEGER,
    embedding       VECTOR(1536),
    embedding_model TEXT DEFAULT 'text-embedding-3-small',
    workspace_id    TEXT NOT NULL,
    trace_id        TEXT,
    inserted_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX ON chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);   -- sqrt(n_rows) is the rule of thumb for lists count

CREATE INDEX ON chunks(workspace_id, source, doc_type);
CREATE INDEX ON chunks(doc_id);
```

**Query pattern:**

```sql
SELECT
    id, doc_id, content, doc_title, doc_url, author, created_at, source, doc_type,
    1 - (embedding <=> $1::vector) AS similarity
FROM chunks
WHERE
    workspace_id = $2
    AND embedding_model = 'text-embedding-3-small'   -- enforce same-model comparison
    AND ($3::text IS NULL OR source = $3)            -- optional source filter
ORDER BY embedding <=> $1::vector
LIMIT $4;
```

---

### 5.8 Guardrail Layer

**What it does:**  
A sequential five-check pipeline that every proposed agent action must pass before executing. Sits between the agent's decision and the tool's execution. If any check fails, the action is rejected or escalated — never silently skipped.

**Why the guardrail layer is the product's most important safety property:**  
The moment Pragma lets an agent take real actions (not just retrieve text), a bad action has real-world consequences: a wrong refund, a wrong ticket status, a wrong notification sent to a customer. The guardrail layer is what separates "AI that does things in production" from "AI that does things in demos." It is also the feature that makes Pragma deployable to enterprises with legal departments.

**The five checks:**

**Check 1: Skill match**  
Does a sourced, high- or medium-confidence skill cover this situation? If no skill exists for the requested action, reject immediately. This prevents the agent from invoking an action based on its own invented reasoning rather than company-validated procedure.

```python
async def check_skill_match(tool_name: str, workspace_id: str) -> Skill | None:
    skill = db.get_skill_for_tool(tool_name, workspace_id)
    if not skill or skill.confidence == "low":
        raise GuardrailRejection(
            check="skill_match",
            reason=f"No validated skill found for '{tool_name}'. Human approval required."
        )
    return skill
```

**Check 2: Condition evaluation**  
Evaluate the skill's `conditions` array against the actual values in the request. This is deterministic evaluation of structured if/then rules — not LLM judgment. An LLM interpreting "does this meet the conditions?" will sometimes get it wrong. A Python `eval` of `amount > 500` against the request's actual amount will not.

```python
def evaluate_conditions(skill: Skill, context: dict) -> list[ConditionResult]:
    results = []
    for condition in skill.conditions:
        # evaluate condition.if_condition against context safely
        result = safe_eval(condition.if_condition, context)
        results.append(ConditionResult(
            condition=condition.if_condition,
            result=result,
            then_action=condition.then_action if result else None
        ))
    return results
```

**Check 3: Permission scope**  
Does this specific agent identity have an explicit grant for this tool, up to this threshold?

```python
async def check_permission(agent_id: str, tool_name: str, context: dict) -> Permission:
    permission = db.get_permission(agent_id, tool_name)
    if not permission:
        raise GuardrailRejection(check="permission", reason="No permission grant found.")

    if "amount" in context and context["amount"] > permission.max_amount:
        raise GuardrailEscalation(
            check="permission",
            reason=f"Amount ${context['amount']} exceeds agent limit ${permission.max_amount}."
        )
    return permission
```

**Check 4: Risk tier routing**  
Every action tool is configured with a risk tier. Low-risk actions (update a ticket status, send an internal notification) auto-approve. High-risk actions (any refund, anything touching financial records) route to the human approval queue. The agent doesn't wait — it receives `{"status": "pending_approval", "request_id": "..."}` and the human approval happens asynchronously.

```python
RISK_TIERS = {
    "update_ticket_status": "low",
    "send_internal_notification": "low",
    "issue_refund": "high",
    "modify_billing_record": "high",
}

def check_risk_tier(tool_name: str, context: dict) -> Literal["approve", "escalate"]:
    tier = RISK_TIERS.get(tool_name, "high")  # default high if unknown
    if tier == "low":
        return "approve"
    # high-risk always escalates regardless of condition results
    return "escalate"
```

**Check 5: Idempotency**  
Has this exact action already been executed? Hash the action's identity and look it up before executing.

```python
def compute_idempotency_key(agent_id: str, tool_name: str, params: dict) -> str:
    canonical = json.dumps(
        {"agent_id": agent_id, "tool": tool_name, "params": params},
        sort_keys=True
    )
    return sha256(canonical.encode()).hexdigest()

async def check_idempotency(key: str) -> ActionRecord | None:
    return db.get_action_by_idempotency_key(key)
    # if found: return original result, do not re-execute
    # if not found: proceed
```

**GuardrailRequest and GuardrailDecision schemas:**

```python
class GuardrailRequest(BaseModel):
    request_id: str
    agent_id: str
    workspace_id: str
    skill_id: str | None
    tool_name: str
    proposed_params: dict
    context: dict           # actual values to evaluate conditions against

class GuardrailDecision(BaseModel):
    request_id: str
    outcome: Literal["approved", "rejected", "escalated"]
    failed_check: str | None
    reason: str
    risk_tier: str
    requires_human: bool
    condition_results: list[ConditionResult]
    idempotency_key: str
```

---

### 5.9 Action Layer / Tool Registry

**What it does:**  
Maintains the registry of callable action tools — each wrapping a real external system (Stripe, Intercom, Linear, etc.) — and provides the execution logic that runs after guardrail approval.

**Scope of v1:**  
Build one real action end-to-end: `issue_refund` via Stripe. This forces you to build the full pipeline (guardrail → execution → audit log) with real money on the line, which validates every safety property properly. Fake actions in demos do not surface real failure modes.

**Why not a generic "connect any system" framework:**  
Building a universal connector for every possible write API requires handling every system's auth, rate limits, error codes, retry semantics, and idempotency behavior. That is a multi-year platform engineering project. Build one action that works perfectly and prove the pipeline. Add actions one at a time after that.

**Tool execution pattern:**

```python
async def execute_tool(tool_name: str, params: dict, decision: GuardrailDecision) -> dict:
    executor = TOOL_REGISTRY[tool_name]
    try:
        result = await executor(params)
        await audit_log.write(AuditLogEntry(
            request_id=decision.request_id,
            tool_name=tool_name,
            params=params,
            decision=decision,
            executed=True,
            executed_at=datetime.utcnow(),
            result=result
        ))
        return result
    except Exception as e:
        await audit_log.write(AuditLogEntry(
            ...executed=False, error=str(e)
        ))
        raise

TOOL_REGISTRY = {
    "issue_refund": stripe_issue_refund,
    "update_ticket_status": intercom_update_ticket,
}
```

**The `issue_refund` implementation:**

```python
async def stripe_issue_refund(params: dict) -> dict:
    refund = await stripe.refunds.create(
        payment_intent=params["payment_intent_id"],
        amount=int(params["amount"] * 100),     # Stripe uses cents
        reason=params.get("reason", "requested_by_customer"),
        metadata={
            "pragma_request_id": params["request_id"],
            "skill_id": params["skill_id"],
            "agent_id": params["agent_id"],
        }
    )
    return {
        "status": "succeeded",
        "refund_id": refund.id,
        "amount": refund.amount / 100,
        "currency": refund.currency,
    }
```

Note the metadata attached to the Stripe refund: `pragma_request_id`, `skill_id`, `agent_id`. This means every refund in your customer's Stripe dashboard is traceable back to the exact Pragma skill and agent that issued it. This is the kind of detail that closes enterprise deals.

---

### 5.10 Unified MCP Server

**What it does:**  
Exposes all of Pragma's capabilities — knowledge retrieval and action execution — through a single MCP server interface. Customer agents (Claude, GPT-4, custom agents) connect once and have access to both capability layers through standard MCP tool calls.

**Why one MCP server:**  
A customer building an AI support agent doesn't want to connect two separate servers ("Pragma Knowledge" and "Pragma Actions"). One server, clean tool list, clear distinction between open knowledge tools and gated action tools. The distinction is enforced server-side via the guardrail layer — the agent doesn't need to know.

**Full tool registry:**

```python
# Knowledge tools — unrestricted, no guardrail
@mcp.tool(name="search_knowledge")
async def search_knowledge(query: str, source_filter: str = None, top_k: int = 5):
    """
    Semantic search across all ingested company knowledge.
    Returns top-k chunks with source URLs, authors, and timestamps.
    Use when: looking up any company information, policies, decisions.
    """

@mcp.tool(name="get_skill")
async def get_skill(situation: str):
    """
    Returns the matching executable skill for a described situation.
    Returns: steps, conditions, confidence, sources, contradictions.
    Use when: about to take an action and need the validated procedure.
    """

@mcp.tool(name="ask")
async def ask(question: str):
    """
    RAG endpoint. Embeds question, retrieves context, returns cited answer.
    Use when: needing a synthesized answer with source citations.
    """

# Action tools — all route through guardrail layer
@mcp.tool(name="issue_refund")
async def issue_refund(
    customer_id: str,
    payment_intent_id: str,
    amount: float,
    reason: str,
    skill_id: str,          # must be provided — agent must have called get_skill first
    agent_id: str,
):
    """
    Issues a refund via the configured payment processor.
    Routes through guardrail layer: skill match, condition check, permission, risk, idempotency.
    Returns: approved (with result), rejected (with reason), or escalated (with request_id).
    Use when: a customer refund has been validated and should be executed.
    """

@mcp.tool(name="update_ticket_status")
async def update_ticket_status(ticket_id: str, new_status: str, agent_id: str):
    """
    Updates a support ticket status in the configured ticketing system.
    Low risk tier — typically auto-approved if permission exists.
    """

@mcp.tool(name="request_human_approval")
async def request_human_approval(request_id: str, summary: str):
    """
    Called automatically by the guardrail layer on escalation.
    Routes to the human approval queue.
    Not typically called directly by agents.
    """
```

**The agent's expected call sequence for an action:**

```
1. search_knowledge("refund policy")   ← understand the landscape
2. get_skill("customer requests refund")   ← get the validated procedure
3. [agent evaluates skill conditions against context]
4. issue_refund(customer_id=..., amount=..., skill_id="skl_7c91e2", ...)   ← act
5. [guardrail layer runs internally]
6. → response: {status: "completed", refund_id: "re_..."} or {status: "pending_approval", ...}
```

Requiring `skill_id` as an explicit parameter to action tools enforces that the agent cannot invoke an action without first retrieving a skill. This is the architectural guarantee that agents always act on validated company knowledge, not on their own reasoning.

---

### 5.11 Query API

**What it does:**  
The fast-path read layer. Handles all incoming knowledge queries with sub-500ms response time. Never blocks on write-path work.

**Hot path discipline:**  
The query API must never trigger: embedding of new documents, skills extraction, clustering, or any Celery job. It only reads from the vector store and skills table — both pre-computed by the write path. This is the critical performance boundary.

**Endpoints:**

```python
# POST /api/v1/search
class SearchRequest(BaseModel):
    workspace_id: str
    query: str
    source_filter: str | None = None
    doc_type_filter: str | None = None
    top_k: int = 5
    min_similarity: float = 0.6

async def search(req: SearchRequest) -> SearchResponse:
    query_embedding = await embed_single(req.query)   # ~30ms
    chunks = db.similarity_search(                     # ~50ms with ivfflat index
        embedding=query_embedding,
        workspace_id=req.workspace_id,
        source=req.source_filter,
        doc_type=req.doc_type_filter,
        top_k=req.top_k,
        min_similarity=req.min_similarity
    )
    return SearchResponse(chunks=chunks, query_embedding_model="text-embedding-3-small")

# POST /api/v1/skill
class SkillRequest(BaseModel):
    workspace_id: str
    situation: str

async def get_skill(req: SkillRequest) -> SkillResponse:
    # embed the situation, find the nearest skill's trigger embedding
    situation_embedding = await embed_single(req.situation)
    skill = db.find_nearest_skill(situation_embedding, req.workspace_id)
    return SkillResponse(skill=skill)

# POST /api/v1/ask
class AskRequest(BaseModel):
    workspace_id: str
    question: str
    use_skills_first: bool = True   # check skills before falling back to raw chunks

async def ask(req: AskRequest) -> AskResponse:
    # 1. Try skills layer first (structured, higher quality)
    if req.use_skills_first:
        skill = await get_skill(SkillRequest(workspace_id=req.workspace_id, situation=req.question))
        if skill and skill.confidence in ("high", "medium"):
            return AskResponse(
                answer=skill_to_prose(skill),
                source_type="skill",
                skill=skill,
                sources=skill.sources
            )
    # 2. Fall back to RAG over raw chunks
    chunks = await search(SearchRequest(workspace_id=req.workspace_id, query=req.question))
    answer = await llm.complete(RAG_PROMPT.format(question=req.question, chunks=chunks))
    return AskResponse(answer=answer, source_type="rag", chunks=chunks)
```

**Response time budget:**

| Step | Target latency |
|---|---|
| Query embedding | 30ms |
| pgvector similarity search | 50ms |
| Skills table lookup | 10ms |
| LLM generation (RAG fallback) | 300-400ms |
| **Total (skills path)** | **< 100ms** |
| **Total (RAG path)** | **< 500ms** |

---

### 5.12 Audit Log

**What it does:**  
Records every action taken through the action layer — including rejected and escalated actions — in an immutable, append-only table. Every record carries the full causal chain: which skill authorized it, which conditions matched, which agent requested it, what the tool returned.

**Why immutability is enforced at the database level, not just the application level:**  
An application-level "don't update the audit log" convention can be broken by any engineer with database access. Row-level security policies that prevent UPDATE and DELETE on the audit table cannot be bypassed from the application layer. This is the difference between a trustworthy audit trail and a mutable log that a customer's legal team will not rely on.

```sql
CREATE TABLE audit_log (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    request_id      TEXT NOT NULL UNIQUE,
    workspace_id    TEXT NOT NULL,
    agent_id        TEXT NOT NULL,
    skill_id        TEXT,
    tool_name       TEXT NOT NULL,
    params          JSONB NOT NULL,
    context         JSONB NOT NULL,
    outcome         TEXT NOT NULL,       -- "approved" | "rejected" | "escalated"
    failed_check    TEXT,
    rejection_reason TEXT,
    risk_tier       TEXT,
    idempotency_key TEXT UNIQUE,
    executed        BOOLEAN NOT NULL,
    executed_at     TIMESTAMPTZ,
    result          JSONB,
    error           TEXT,
    trace_id        TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- Immutability enforced at DB level
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY deny_update ON audit_log FOR UPDATE USING (false);
CREATE POLICY deny_delete ON audit_log FOR DELETE USING (false);

-- Read access for the application
CREATE POLICY allow_read ON audit_log FOR SELECT USING (true);
CREATE POLICY allow_insert ON audit_log FOR INSERT WITH CHECK (true);
```

**The audit log as a product feature (not just compliance):**  
Surface the audit log in the customer dashboard. Let customers filter by agent, by tool, by outcome, by date. This makes Pragma's decision-making transparent and explainable. An enterprise buyer asking "how do I explain this to my legal team" gets a clear answer: every action taken by Pragma is logged, immutable, searchable, and traceable to a specific sourced company procedure. No competitor in this space currently offers this.

---

## 6. Database Schema

Full PostgreSQL schema for all tables:

```sql
-- Workspaces
CREATE TABLE workspaces (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Source connections per workspace
CREATE TABLE source_connections (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id    TEXT REFERENCES workspaces(id),
    source          TEXT NOT NULL,          -- "slack" | "github" | "notion"
    credentials     JSONB NOT NULL,         -- encrypted OAuth tokens
    last_synced_at  TIMESTAMPTZ,
    config          JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(workspace_id, source)
);

-- Canonical documents
CREATE TABLE documents (
    id              TEXT PRIMARY KEY,       -- hash(source + source_id)
    workspace_id    TEXT REFERENCES workspaces(id),
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,          -- native ID from the source
    doc_type        TEXT NOT NULL,
    title           TEXT,
    author          TEXT,
    url             TEXT,
    content_hash    TEXT NOT NULL,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ,
    ingested_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source, source_id)
);

-- Chunks with embeddings
CREATE TABLE chunks (
    id              TEXT PRIMARY KEY,
    doc_id          TEXT REFERENCES documents(id) ON DELETE CASCADE,
    workspace_id    TEXT NOT NULL,
    source          TEXT NOT NULL,
    doc_type        TEXT NOT NULL,
    content         TEXT NOT NULL,
    doc_title       TEXT,
    doc_url         TEXT,
    author          TEXT,
    created_at      TIMESTAMPTZ,
    position        INTEGER,
    total_chunks    INTEGER,
    embedding       VECTOR(1536),
    embedding_model TEXT DEFAULT 'text-embedding-3-small',
    near_duplicate_of TEXT,                -- FK to another chunk if near-dup detected
    trace_id        TEXT,
    inserted_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ON chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists=100);
CREATE INDEX ON chunks(workspace_id, doc_type, source);
CREATE INDEX ON chunks(doc_id);

-- Skills
CREATE TABLE skills (
    skill_id        TEXT PRIMARY KEY,
    workspace_id    TEXT REFERENCES workspaces(id),
    cluster_id      TEXT NOT NULL,
    skill_name      TEXT NOT NULL,
    trigger_text    TEXT NOT NULL,
    trigger_embedding VECTOR(1536),
    confidence      TEXT NOT NULL,
    steps           JSONB NOT NULL,         -- list[str]
    conditions      JSONB NOT NULL,         -- list[{if, then}]
    contradictions  JSONB NOT NULL,         -- list[str]
    sources         JSONB NOT NULL,         -- list[SkillSource]
    version         INTEGER DEFAULT 1,
    superseded_by   TEXT REFERENCES skills(skill_id),
    last_updated    TIMESTAMPTZ DEFAULT NOW(),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ON skills USING ivfflat (trigger_embedding vector_cosine_ops);
CREATE INDEX ON skills(workspace_id, confidence);

-- Agent permission grants
CREATE TABLE agent_permissions (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id    TEXT REFERENCES workspaces(id),
    agent_id        TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    max_amount      NUMERIC,                -- for financial tools
    allowed_scopes  JSONB DEFAULT '[]',
    granted_by      TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(workspace_id, agent_id, tool_name)
);

-- Audit log (immutable — see RLS policies above)
CREATE TABLE audit_log (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    request_id      TEXT NOT NULL UNIQUE,
    workspace_id    TEXT NOT NULL,
    agent_id        TEXT NOT NULL,
    skill_id        TEXT,
    tool_name       TEXT NOT NULL,
    params          JSONB NOT NULL,
    context         JSONB NOT NULL,
    outcome         TEXT NOT NULL,
    failed_check    TEXT,
    rejection_reason TEXT,
    risk_tier       TEXT,
    idempotency_key TEXT UNIQUE,
    executed        BOOLEAN NOT NULL,
    executed_at     TIMESTAMPTZ,
    result          JSONB,
    error           TEXT,
    trace_id        TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW() NOT NULL
);
```

---

## 7. System Design Decisions

Each architectural decision maps to a specific failure mode it prevents. This section exists to answer "why is it designed this way" for every non-obvious choice.

### CAP theorem applied asymmetrically

The knowledge layer (ingestion, vector store) is **AP** — available and partition-tolerant. A Slack message taking 15 minutes to appear in search results is acceptable. The system remains responsive even if one source connector is down.

The action layer (guardrail checks, idempotency, audit log) is **CP** — consistent and partition-tolerant. Under a network partition, the system rejects or escalates actions rather than risk inconsistency. A double-refund is not acceptable even under partition. The CAP choice is asymmetric because the cost of being wrong is asymmetric.

### CQRS for the read/write split

The ingestion pipeline (write path) is CPU-heavy, bursty, and latency-insensitive. The query API (read path) is latency-sensitive (< 500ms) and must remain available during large ingestion runs. Running them on separate worker pools means a large initial sync (10M Slack messages) does not degrade query performance for active users.

### SERIALIZABLE isolation for dedup

Two Celery workers running simultaneously could both determine that the same document is "new" and attempt concurrent inserts. PostgreSQL SERIALIZABLE isolation detects the write-write conflict and rolls back one of them. This is the same correctness technique used in the payout engine for concurrent withdrawal attempts: database-enforced ordering, not application-level locking which can fail under race conditions.

### pgvector for atomicity across metadata and vectors

The delete-then-reinsert operation that handles document updates must be atomic. Old chunks must be removed and new chunks inserted in a single transaction — if the worker crashes mid-operation, the entire operation rolls back. This cannot be guaranteed across Postgres (metadata) and a separate vector database (embeddings). pgvector keeps both in the same transactional boundary.

### HDBSCAN for skill cluster discovery

Company knowledge does not arrive in a fixed number of topics. HDBSCAN discovers the natural cluster structure without requiring a pre-specified k, and critically assigns noise labels to low-frequency mentions rather than forcing them into a cluster. This prevents one-off mentions of a topic from inflating the evidence base of a skill, which would artificially raise confidence scores.

### Append-only audit log with RLS enforcement

An audit log that can be modified post-hoc by the application is not an audit log — it is a mutable record. PostgreSQL row-level security policies that deny UPDATE and DELETE cannot be bypassed from application code. This provides a guarantee that is meaningful to a legal or compliance team: every record in the audit log reflects what actually happened, unmodified, period.

### Idempotency key on all action tool calls

The combination of: AI agent retrying on timeout + non-idempotent tool execution = real-world duplicate actions. The idempotency key is computed from `hash(agent_id + tool_name + target_id + params)` and stored before execution. If the same key arrives again, the original result is returned without re-executing. This is the same class of problem as duplicate payout on network retry — solved identically.

---

## 8. API Reference

### Authentication

All API requests require a workspace API key in the header:  
`Authorization: Bearer pk_live_{workspace_id}_{secret}`

### Knowledge endpoints

```
POST /api/v1/search
POST /api/v1/skill
POST /api/v1/ask
GET  /api/v1/skills                   # list all skills for workspace
GET  /api/v1/skills/{skill_id}        # get specific skill with full history
GET  /api/v1/skills/{skill_id}/history # version history
```

### Action endpoints

```
POST /api/v1/actions/issue_refund
POST /api/v1/actions/update_ticket
POST /api/v1/approvals/{request_id}/approve
POST /api/v1/approvals/{request_id}/reject
GET  /api/v1/approvals/pending
```

### Audit endpoints

```
GET /api/v1/audit                     # paginated log, filterable
GET /api/v1/audit/{request_id}        # single record with full trace
GET /api/v1/audit/export              # JSONL export for compliance
```

### Ingestion management

```
POST /api/v1/sources/connect          # OAuth install flow trigger
GET  /api/v1/sources                  # list connected sources
POST /api/v1/sources/{id}/sync        # manual re-sync trigger
GET  /api/v1/sources/{id}/status      # sync status, last_synced_at, error state
```

### MCP server

```
GET  /mcp/tools                       # list all available tools
POST /mcp/call                        # standard MCP tool call endpoint
```

---

## 9. Implementation Roadmap

### v1 — Weeks 1–6: The full pipeline, narrow scope

**Goal:** End-to-end working system for one customer type (engineering/support teams at 10–30 person SaaS companies), two sources, one real action.

**Week 1–2: Ingestion foundation**
- [ ] Postgres setup with pgvector extension
- [ ] Slack connector: OAuth install, `conversations.list`, `conversations.history`, `conversations.replies`
- [ ] GitHub connector: GitHub App, PRs + issues + comments, `since`-based incremental
- [ ] Normalizer: `Document` schema, content cleaning, auto-title extraction
- [ ] Celery + Redis: async job queue, basic workers
- [ ] Dedup: content hash check, unique constraint, delete-then-reinsert on change

**Week 3: Embedding and retrieval**
- [ ] Chunker: `RecursiveCharacterTextSplitter`, per-doc-type chunk sizes
- [ ] Embedder: OpenAI `text-embedding-3-small`, batch embedding, model tracking
- [ ] pgvector: schema, `ivfflat` index, similarity search query
- [ ] `/api/v1/search` endpoint: embed query, search, return ranked chunks with metadata
- [ ] Basic `/api/v1/ask` endpoint: RAG over chunks

**Week 4: Skills extractor**
- [ ] HDBSCAN clustering over workspace embeddings (scheduled Celery task)
- [ ] LLM extraction prompt: structured JSON output with schema enforcement
- [ ] Confidence scoring: mechanical derivation from source count and contradictions
- [ ] Skill versioning: diff detection, `superseded_by` chain
- [ ] `/api/v1/skill` endpoint: embed situation, find nearest skill trigger

**Week 5: Guardrail + action layer**
- [ ] GuardrailRequest/Decision models
- [ ] Five checks implemented: skill match, condition eval, permission, risk tier, idempotency
- [ ] Stripe `issue_refund` action: real Stripe API integration, metadata tagging
- [ ] Human approval queue: Redis-backed, webhook notification on escalation
- [ ] Append-only audit log: RLS policies, full record on every guardrail outcome

**Week 6: MCP server + integration**
- [ ] MCP server: all knowledge tools (open) + action tools (gated)
- [ ] `skill_id` enforcement on action tool calls
- [ ] Basic workspace dashboard: source status, skills list, audit log view
- [ ] End-to-end test: agent calls `get_skill` → calls `issue_refund` → guardrail → Stripe → audit log
- [ ] Closed beta deployed for design partners

**v1 success criteria:**
- Three design partners with real workspaces ingested
- Skills generated with > 60% human-rated accuracy
- At least one real `issue_refund` executed through the full pipeline in a staging environment
- Query API p95 latency < 500ms

---

### v2 — Weeks 7–12: Design partner feedback, first paid customers

**Goal:** Ship what design partners tell you they need. Get to $1K MRR.

**From ex-Meta contact feedback:**
- [ ] Retrieval quality improvements: re-ranking layer over initial similarity search
- [ ] Query understanding: classify query intent before choosing search strategy

**From ex-Amazon contact feedback:**
- [ ] Runbook-style skill display: numbered steps, explicit "who does what" attribution
- [ ] On-call integration: PagerDuty / Opsgenie trigger as an action tool

**From ex-LinkedIn contact feedback:**
- [ ] Graph layer: link related skills (a refund skill references the billing policy skill)
- [ ] Usage analytics: which skills are retrieved most, which are never used

**General:**
- [ ] Notion connector (third source)
- [ ] Contradiction resolution workflow: customer can review and dismiss flagged contradictions
- [ ] Pricing page + Stripe billing integration (self-serve payment)
- [ ] Slack app for human approval queue (approve/reject from Slack, not dashboard)

---

### v3 — Month 4+: Expansion and YC application

**Goal:** 10 paying customers, strong MRR trajectory, clear retention signal.

- [ ] Linear/Jira connector (fourth source)
- [ ] Additional action tools: `update_ticket_status` (Intercom), `send_customer_email`, `escalate_to_human`
- [ ] Workspace analytics dashboard: resolution time, skill accuracy rate, action approval rate
- [ ] Multi-agent support: different agents with different permission profiles in the same workspace
- [ ] Enterprise auth: SSO, audit log export (JSONL + CSV), data residency options
- [ ] YC S27 application: live product, paying customers, clear wedge, technical depth demonstrated

---

## 10. Tech Stack

### Backend

| Component | Technology | Reason |
|---|---|---|
| API framework | FastAPI | Async-native, fast, Pydantic integration, your existing stack |
| Task queue | Celery + Redis | Your existing stack; battle-tested for the async ingestion pipeline |
| ORM | SQLAlchemy (async) | Mature, pgvector support via `sqlalchemy-pgvector` |
| Vector store | PostgreSQL + pgvector | Transactional consistency, no additional infra, sufficient for v1 |
| Cache | Redis | Celery broker, rate limit tracking, approval queue |
| Embeddings | OpenAI `text-embedding-3-small` | Fast, cheap, sufficient quality |
| LLM (extraction) | `gpt-4o-mini` | Fast, cheap, good at structured JSON extraction |
| LLM (RAG) | `claude-sonnet-4-6` | Better at nuanced, cited answers than GPT-4o-mini |
| Clustering | HDBSCAN (`hdbscan` Python library) | Density-based, no fixed k, noise labeling |
| Circuit breaker | `circuitbreaker` Python library | Lightweight, decorator-based |

### Infrastructure

| Component | Technology | Reason |
|---|---|---|
| Hosting | Railway (v1) | Zero DevOps overhead, Postgres included, fast to deploy |
| Migrations | Alembic | Standard SQLAlchemy migration tool |
| CI/CD | GitHub Actions | Already in your workflow from existing projects |
| Secrets | Railway env vars (v1), AWS Secrets Manager (v2+) | Start simple |
| Monitoring | Sentry (errors) + Railway metrics (infra) | Minimum viable observability for v1 |
| Distributed tracing | OpenTelemetry (add in v2) | Trace_id chain is already built in; wire up the exporter later |

### Frontend

| Component | Technology | Reason |
|---|---|---|
| Dashboard | React + Vite | Fast to build, you know it |
| Styling | Tailwind CSS | Rapid iteration |
| Charts (analytics) | Recharts | Simple, well-documented |

---

## 11. Competitive Positioning

### Primary differentiators

**1. Skills over search results**  
Every competitor in this space — Glean, Notion AI, Copilot, Cerenovus — returns documents or generated prose. Pragma returns structured, executable procedures with sourced evidence, confidence scores, and version history. This is a different product, not a better search engine.

**2. Action layer with auditability**  
No knowledge-retrieval product in this space has an action layer with a guardrail pipeline and an immutable audit log. This is the feature that enables AI agents to take real actions in production rather than just provide information. It is also the feature that an enterprise legal department needs before signing.

**3. Contradiction detection**  
Pragma surfaces cases where the company's own knowledge base contains conflicting information. No competitor does this. It is simultaneously a feature ("you can trust our answers because we tell you when we're uncertain") and a product insight for the customer ("your team has an unresolved disagreement about this procedure that you didn't know about").

**4. Execution provenance**  
Every action taken through Pragma's action layer is traceable to a specific sourced skill, which is traceable to specific company documents. The Stripe refund has a `pragma_skill_id` in its metadata. This is the difference between "AI did something" and "AI followed your company's validated procedure, here's the receipt."

### Competitive landscape

| Company | Focus | Our differentiation |
|---|---|---|
| Glean | Enterprise search | We extract skills + enable actions. Search is table stakes. |
| Microsoft Copilot | M365 retrieval + generation | We're source-agnostic, we produce executable skills, we have an audit trail. |
| Cerenovus (YC S26) | Executive decision support | We target high-frequency support/CS workflows, not strategic decisions. |
| Promptless (YC S26) | Auto-updated documentation | Our output is agent-executable, not human-readable. |
| Corvera (YC S26) | CPG vertical knowledge layer | Different vertical, different output format. |
| Decagon / Intercom Fin | AI support agents | We are their knowledge layer, not their competitor. |

---

## 12. Business Model

### Pricing

**Starter — $299/mo**  
- Up to 2 sources connected
- 50,000 chunks stored
- Knowledge tools (search, skill lookup, ask)
- Action tools disabled
- 1 workspace

**Growth — $799/mo**  
- Up to 4 sources
- 500,000 chunks
- All knowledge tools + action tools
- Guardrail layer with human approval queue
- Full audit log
- Up to 5 agent identities

**Enterprise — custom**  
- Unlimited sources, chunks, agents
- SSO + SCIM
- Data residency options
- SLA + dedicated support
- Compliance audit exports

### Unit economics at scale

The primary variable cost is the embedding API. At `text-embedding-3-small` pricing ($0.02/1M tokens), embedding 500,000 chunks of 512 tokens each costs approximately $5.12 on initial ingest. Re-embedding only changed documents means incremental cost is near-zero after initial sync. LLM costs (skills extraction) are batch jobs running once per hour on changed clusters — negligible at < 100 API calls per workspace per day.

At $299/mo per customer, LLM cost per customer is < $10/mo at current rates. 30× gross margin on infrastructure before any operational overhead.

---

## 13. YC Application Narrative

### The problem in one sentence

Every SaaS company above 20 people has a critical knowledge problem: the real procedures, policies, and decisions live in Slack threads, GitHub comments, and senior people's heads — inaccessible to AI agents that need to act on them.

### Why now

The infrastructure to build this has converged in 2025-2026: cheap embeddings, standardized MCP protocol, battle-tested vector databases, and AI agents capable of taking real actions. YC's own S26 RFS identifies both "Company Brain" and "Software for Agents" as high-priority categories. Multiple funded teams are building adjacent products (Cerenovus, Promptless, Corvera), validating the macro thesis without claiming the specific wedge — support/CS execution with auditability — that Pragma occupies.

### Why us

The system we've built is not a retrieval product with a skills layer bolted on. The skills file generator, the guardrail pipeline, the append-only audit log, and the CAP-asymmetric architecture are each non-obvious choices with specific failure modes they prevent. The payout engine work — SERIALIZABLE isolation for concurrent writes, event sourcing for the audit trail, idempotency via DB-level unique constraints — is directly applicable to the action layer's correctness requirements. Most teams building in this space don't have this systems background; they have retrieval quality and product taste, but not the production-correctness intuitions that make an action-taking system trustworthy.

### Traction target for YC application

- 3 design partners with real workspaces ingested and skills generated (Month 1–2)
- 5 paying customers at $299+/mo (Month 3–4)
- One enterprise pilot at $2K+/mo (Month 4–5)
- Clear retention: customers who ingested are still using at 60-day mark

### What we're asking for

YC acceptance, $500K on standard terms. Primary use: hire one engineer to accelerate the connector layer and dashboard, and extend the action tool registry to 5 tools (covering the top support workflow actions at the typical SaaS company).

---

*Pragma — built by Sai Srikar Boddupally, Hyderabad, India*  
*saisrikar.boddupally@gmail.com | github.com/srikarboddupally*  
*Version 1.0 — June 2026*
