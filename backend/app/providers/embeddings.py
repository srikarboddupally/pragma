"""Embedding providers — the single seam between Pragma and whatever embeds our text.

Everything that needs vectors (the cold-path ``embedder``, the hot-path ``embed_query``,
dedup's near-duplicate check, test doubles) goes through the ``EmbeddingProvider`` protocol,
so the concrete model is swappable in exactly one place (CLAUDE.md §4.2). Voyage AI
(``voyage-3.5``) is the default — Anthropic's officially recommended embeddings provider,
since Anthropic ships no first-party embedding model.

**Dimension discipline** (CLAUDE.md §4.2): each provider's ``dim`` is intrinsic to its model
and MUST equal the pgvector column dimension the schema was built with
(``settings.embedding_dim`` → the migration's ``EMBEDDING_DIM``). ``get_embedder`` checks this
on construction so a provider/config mismatch fails loudly at startup instead of silently
writing wrong-width vectors. Switching providers is therefore a re-embed + new migration, never
a live swap.

Provider SDKs are imported lazily inside ``embed`` so importing this module needs neither the
packages installed nor an API key present — which keeps it trivially mockable in tests and lets
the app boot without embedding credentials until something actually embeds.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol, runtime_checkable

from app.config import get_settings


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Structural interface every embedder satisfies.

    A ``Protocol`` (not an ABC) so a plain test double exposing ``model``/``dim``/``embed``
    counts as a provider without inheriting anything.
    """

    model: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text, order-preserving."""
        ...


class VoyageEmbeddings:
    """Default provider — Voyage ``voyage-3.5`` (1024-dim)."""

    model = "voyage-3.5"
    dim = 1024

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or get_settings().voyage_api_key
        self._client = None  # lazily created on first embed

    def _get_client(self):  # noqa: ANN202 - third-party client type
        if self._client is None:
            import voyageai

            self._client = voyageai.AsyncClient(api_key=self._api_key)
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        result = await self._get_client().embed(texts, model=self.model)
        return result.embeddings


class OpenAIEmbeddings:
    """Alternate provider — OpenAI ``text-embedding-3-small`` (1536-dim)."""

    model = "text-embedding-3-small"
    dim = 1536

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or get_settings().openai_api_key
        self._client = None

    def _get_client(self):  # noqa: ANN202 - third-party client type
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=self._api_key)
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = await self._get_client().embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in resp.data]


class LocalEmbeddings:
    """Placeholder for a self-hosted model (e.g. ``nomic-embed-text``, 768-dim).

    Declared so the factory can resolve ``EMBEDDING_PROVIDER=local`` and so the dimension is on
    record, but not wired — running a local model pulls a heavy dependency we don't need until
    there's a reason to self-host. ``embed`` raises until then.
    """

    model = "nomic-embed-text"
    dim = 768

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError(
            "Local embeddings are not wired yet — set EMBEDDING_PROVIDER=voyage (or openai)."
        )


_PROVIDERS: dict[str, type[EmbeddingProvider]] = {
    "voyage": VoyageEmbeddings,
    "openai": OpenAIEmbeddings,
    "local": LocalEmbeddings,
}


@lru_cache
def get_embedder() -> EmbeddingProvider:
    """Return the configured embedding provider (cached singleton).

    Keyed on ``settings.embedding_provider``. Cached so the hot path reuses one lazily-created
    client instead of rebuilding it per request. Call ``get_embedder.cache_clear()`` in tests
    after changing the provider (mirrors ``get_settings``).
    """
    settings = get_settings()
    provider = _PROVIDERS[settings.embedding_provider]()
    if provider.dim != settings.embedding_dim:
        raise RuntimeError(
            f"embedding dimension mismatch: provider {provider.model!r} is {provider.dim}-dim "
            f"but settings.embedding_dim is {settings.embedding_dim} — the pgvector column and "
            "the active model disagree (CLAUDE.md §4.2)."
        )
    return provider
