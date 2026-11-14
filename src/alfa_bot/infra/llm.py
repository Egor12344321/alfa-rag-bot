"""DeepSeek Chat Completions: ограниченные повторы и проверка JSON по схеме."""

import asyncio
import json
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Безопасная для логирования ошибка без тела запроса и ключа API."""


class LLM(Protocol):
    async def complete(self, *, system: str, payload: dict, schema: type[T]) -> T: ...


class DeepSeekLLM:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-flash",
        base_url: str = "https://api.deepseek.com",
        timeout: float = 45,
        client: httpx.AsyncClient | None = None,
    ):
        self._key = api_key
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def complete(self, *, system: str, payload: dict, schema: type[T]) -> T:
        instruction = (
            system
            + "\nВерни только JSON по этой JSON Schema:\n"
            + json.dumps(
                schema.model_json_schema(),
                ensure_ascii=False,
            )
        )
        messages = [
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        for attempt in range(2):
            try:
                response = await self._client.post(
                    self._url,
                    headers={"Authorization": "Bearer " + self._key},
                    json={
                        "model": self.model,
                        "messages": messages,
                        "response_format": {"type": "json_object"},
                        "thinking": {"type": "disabled"},
                        "max_tokens": 3500,
                    },
                )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt == 0:
                        await asyncio.sleep(0.5)
                        continue
                if response.is_error:
                    raise LLMError(f"LLM HTTP {response.status_code}")
                choice = response.json()["choices"][0]
                if choice.get("finish_reason") != "stop":
                    raise ValueError("Incomplete response")
                return schema.model_validate_json(choice["message"]["content"])
            except httpx.RequestError:
                if attempt:
                    raise LLMError("LLM connection failed") from None
            except (ValidationError, ValueError, KeyError, IndexError, TypeError):
                if attempt:
                    raise LLMError("LLM returned invalid JSON") from None
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Предыдущий ответ некорректен. Верни полный JSON строго по схеме."
                        ),
                    }
                )
        raise LLMError("LLM request failed")
