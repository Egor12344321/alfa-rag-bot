"""Единственный LLM-провайдер — DeepSeek; ответ всегда проверяется по Pydantic."""

import asyncio
import json
from typing import Protocol, TypeVar

import httpx
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ValidationError

from alfa_bot.config import Settings

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Не содержит тела запроса, персональных данных и API-ключа."""


class LLMPort(Protocol):
    async def generate(self, messages: list[BaseMessage], *, schema: type[T]) -> str: ...


class DeepSeekLLM:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._owned = client is None
        self.client = client or httpx.AsyncClient(timeout=settings.llm_timeout)

    async def close(self):
        if self._owned:
            await self.client.aclose()

    async def generate(self, messages: list[BaseMessage], *, schema: type[T]) -> str:
        roles = {"system": "system", "human": "user", "ai": "assistant"}
        body = [{"role": roles[item.type], "content": str(item.content)} for item in messages]
        schema_text = "\nВерни только JSON по JSON Schema:\n" + json.dumps(
            schema.model_json_schema(),
            ensure_ascii=False,
        )
        if body and body[0]["role"] == "system":
            body[0]["content"] += schema_text
        else:
            body.insert(0, {"role": "system", "content": schema_text})
        retries = self.settings.llm_retries
        for attempt in range(retries + 1):
            try:
                response = await self.client.post(
                    self.settings.deepseek_base_url.rstrip("/") + "/chat/completions",
                    headers={
                        "Authorization": "Bearer "
                        + self.settings.deepseek_api_key.get_secret_value(),
                    },
                    json={
                        "model": self.settings.deepseek_model,
                        "messages": body,
                        "response_format": {"type": "json_object"},
                        "thinking": {"type": "disabled"},
                        "max_tokens": self.settings.llm_max_tokens,
                    },
                )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < retries:
                        await asyncio.sleep(min(0.5 * (2**attempt), 3))
                        continue
                if response.is_error:
                    raise LLMError(f"DeepSeek HTTP {response.status_code}")
                choice = response.json()["choices"][0]
                if choice.get("finish_reason") != "stop":
                    raise ValueError("Незавершённый JSON")
                content = choice["message"]["content"]
                schema.model_validate_json(content)
                return content
            except httpx.RequestError:
                if attempt == retries:
                    raise LLMError("DeepSeek connection failed") from None
                await asyncio.sleep(min(0.5 * (2**attempt), 3))
            except (ValidationError, ValueError, KeyError, IndexError, TypeError):
                if attempt == retries:
                    raise LLMError("DeepSeek returned invalid JSON") from None
                body.append(
                    {
                        "role": "user",
                        "content": (
                            "Верни полный JSON строго по переданной схеме, без пустого ответа."
                        ),
                    }
                )
        raise LLMError("DeepSeek request failed")
