from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from app.config import get_settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
TModel = TypeVar("TModel", bound=BaseModel)


class LLMClient:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:

        settings = get_settings()
        self._api_key = api_key or settings.openrouter_api_key
        self._default_model = model or settings.llm_model
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=self._api_key, base_url=OPENROUTER_BASE_URL)
        return self._client

    async def complete(self, prompt: str, model: str | None = None) -> str:
        response = await self._get_client().chat.completions.create(
            model=model or self._default_model,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""

    async def extract_json(
        self, prompt: str, schema: type[TModel], model: str | None = None
    ) -> TModel:
        completion = await self._get_client().beta.chat.completions.parse(
            model=model or self._default_model,
            messages=[{"role": "user", "content": prompt}],
            response_format=schema,
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise ValueError(
                f"LLM returned no schema-valid content for {schema.__name__} "
                f"(model {model or self._default_model!r} may not support structured outputs)."
            )
        return parsed
