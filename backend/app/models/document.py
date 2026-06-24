"""Canonical document/chunk schemas — the most important abstraction in the system.

Every connector normalizes its raw objects into a ``Document``; no downstream stage ever
imports a connector-specific type. That keeps "add a new source" an O(1) change.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import BaseModel, Field


def compute_document_id(source: str, source_id: str) -> str:
    """Stable id for a source object — same across re-syncs. ``hash(source + source_id)``."""
    return hashlib.sha256(f"{source}:{source_id}".encode()).hexdigest()


def compute_content_hash(content: str) -> str:
    """Content fingerprint used by the dedup layer to skip unchanged documents."""
    return hashlib.sha256(content.encode()).hexdigest()


class Document(BaseModel):
    id: str  # compute_document_id(source, source_id)
    source: str  # "slack" | "github" | "notion" | "linear"
    doc_type: str  # "thread" | "pr" | "page" | "issue" | "comment"
    title: str
    content: str  # cleaned plaintext (no markdown/HTML)
    author: str
    participants: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    url: str
    content_hash: str
    metadata: dict = Field(default_factory=dict)  # source-specific extras preserved for later
    trace_id: str


class Chunk(BaseModel):
    id: str  # f"{doc_id}_{position}"
    doc_id: str
    source: str
    doc_type: str
    content: str
    doc_title: str | None = None
    doc_url: str | None = None
    author: str | None = None
    created_at: datetime | None = None
    position: int
    total_chunks: int
    trace_id: str


class EmbeddedChunk(BaseModel):
    chunk: Chunk
    embedding: list[float]
    model: str  # embedding model name — stored so we only ever compare same-model vectors
    embedded_at: datetime
