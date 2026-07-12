from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from app.providers.llm import LLMClient


@pytest.fixture
def mock_openai_resp():
    mock_resp = AsyncMock()
    mock_resp.choices = [AsyncMock(message=AsyncMock(content="This is the answer."))]
    return mock_resp


@pytest.mark.asyncio
async def test_complete_returns_resp_text(mock_openai_resp):
    with patch("openai.AsyncOpenAI") as mock_client_cls:
        mock_client = mock_client_cls.return_value

        mock_client.chat.completions.create = AsyncMock(return_value=mock_openai_resp)

        client = LLMClient()

        res = await client.complete("What is Pragma?")

        assert res == "This is the answer."


@pytest.mark.asyncio
async def test_complete_uses_default_model_when_none_passed(mock_openai_resp):
    with patch("openai.AsyncOpenAI") as mock_openai_client:
        mock_client = mock_openai_client.return_value
        mock_client.chat.completions.create = AsyncMock(return_value=mock_openai_resp)

        client = LLMClient()
        await client.complete("test prompt")

        _, kwargs = mock_client.chat.completions.create.call_args
        assert kwargs["model"] == client._default_model


@pytest.mark.asyncio
async def test_complete_respects_explicit_model_override(mock_openai_resp):
    """A caller can override the model per-call — proves the parameter isn't ignored."""
    with patch("openai.AsyncOpenAI") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.chat.completions.create = AsyncMock(return_value=mock_openai_resp)

        client = LLMClient()
        await client.complete("test prompt", model="deepseek/deepseek-r1:free")

        _, kwargs = mock_client.chat.completions.create.call_args
        assert kwargs["model"] == "deepseek/deepseek-r1:free"


class _Widget(BaseModel):
    """Minimal schema to prove extract_json forwards it and returns a validated instance."""

    name: str
    qty: int


@pytest.mark.asyncio
async def test_extract_json_forwards_schema_and_returns_validated_model():
    widget = _Widget(name="bolt", qty=3)
    completion = AsyncMock()
    completion.choices = [AsyncMock(message=AsyncMock(parsed=widget))]

    with patch("openai.AsyncOpenAI") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.beta.chat.completions.parse = AsyncMock(return_value=completion)

        client = LLMClient()
        result = await client.extract_json("make a widget", _Widget)

        # The validated model comes back unchanged...
        assert result is widget
        # ...and the schema was handed to the API as response_format (structured outputs).
        _, kwargs = mock_client.beta.chat.completions.parse.call_args
        assert kwargs["response_format"] is _Widget


@pytest.mark.asyncio
async def test_extract_json_raises_when_no_parsed_content():
    """parsed=None (a refusal, or a model that ignores the schema) must fail loud, not silently."""
    completion = AsyncMock()
    completion.choices = [AsyncMock(message=AsyncMock(parsed=None))]

    with patch("openai.AsyncOpenAI") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.beta.chat.completions.parse = AsyncMock(return_value=completion)

        client = LLMClient()
        with pytest.raises(ValueError, match="structured outputs"):
            await client.extract_json("make a widget", _Widget)
