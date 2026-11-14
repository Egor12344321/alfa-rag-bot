
import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar

import httpx
import openai
from langchain_core.exceptions import OutputParserException
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, SecretStr, ValidationError

from alfa_bot.contracts import DependencyUnavailable

T = TypeVar("T", bound=BaseModel)


class LLM(Protocol):
    async def generate(
        self,
        *,
        system: str,
        context: str,
        question: str,
    ) -> str: ...


class StructuredLLM(LLM, Protocol):
    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
    ) -> T: ...


class UnconfiguredLLM:
    async def generate(
        self,
        *,
        system: str,
        context: str,
        question: str,
    ) -> str:
        raise DependencyUnavailable(
            "LLM не настроена: проверь LLM_API_KEY и LLM_MODEL в .env"
        )

    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
    ) -> T:
        raise DependencyUnavailable(
            "LLM не настроена: проверь LLM_API_KEY и LLM_MODEL в .env"
        )


class LangChainLLM:
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str,
        timeout_s: float = 25.0,
        max_concurrent: int = 5,
    ):
        if not model.strip():
            raise ValueError("Название модели не может быть пустым")

        if not api_key.strip():
            raise ValueError("LLM_API_KEY не может быть пустым")

        if not base_url.strip():
            raise ValueError("LLM_BASE_URL не может быть пустым")

        if timeout_s <= 0:
            raise ValueError("timeout_s должен быть больше 0")

        if max_concurrent < 1:
            raise ValueError("max_concurrent должен быть не меньше 1")

        self._timeout = timeout_s
        self._sem = asyncio.Semaphore(max_concurrent)

        self._chat = ChatOpenAI(
            model=model,
            api_key=SecretStr(api_key),
            base_url=base_url,
            temperature=0,
            timeout=timeout_s,
            max_retries=0,
        )

    def _is_retryable(self, error: Exception) -> bool:
        if isinstance(
            error,
            (
                TimeoutError,
                ConnectionError,
                httpx.TimeoutException,
                httpx.NetworkError,
                openai.APIConnectionError,
            ),
        ):
            return True

        if isinstance(error, openai.APIStatusError):
            return error.status_code == 429 or error.status_code >= 500

        return False

    async def _call(
        self,
        fn: Callable[[], Awaitable[Any]],
    ) -> Any:
        async with self._sem:
            for attempt in range(2):
                try:
                    return await asyncio.wait_for(
                        fn(),
                        timeout=self._timeout,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    if not self._is_retryable(error):
                        raise

                    if attempt == 1:
                        raise DependencyUnavailable(
                            "LLM временно недоступна"
                        ) from error

                    await asyncio.sleep(1)

        raise DependencyUnavailable("LLM временно недоступна")

    async def generate(
        self,
        *,
        system: str,
        context: str,
        question: str,
    ) -> str:
        user = f"{context}\n\n{question}" if context else question

        try:
            msg = await self._call(
                lambda: self._chat.ainvoke(
                    [
                        ("system", system),
                        ("human", user),
                    ]
                )
            )
        except DependencyUnavailable:
            raise
        except Exception as error:
            raise DependencyUnavailable(
                "Ошибка при генерации ответа LLM"
            ) from error

        if not isinstance(msg.content, str):
            raise DependencyUnavailable(
                "LLM вернула ответ в неподдерживаемом формате"
            )

        return msg.content

    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
    ) -> T:
        runnable = self._chat.with_structured_output(
            schema,
            method="function_calling",
        )

        try:
            result = await self._call(
                lambda: runnable.ainvoke(
                    [
                        ("system", system),
                        ("human", user),
                    ]
                )
            )

            if isinstance(result, schema):
                return result

            if not isinstance(result, dict):
                raise DependencyUnavailable(
                    "LLM вернула ответ в неподдерживаемом формате"
                )

            return schema.model_validate(result)

        except DependencyUnavailable:
            raise
        except (ValidationError, OutputParserException) as error:
            raise DependencyUnavailable(
                "LLM вернула некорректный структурированный ответ"
            ) from error
        except Exception as error:
            raise DependencyUnavailable(
                "Ошибка при получении структурированного ответа LLM"
            ) from error


def build_llm() -> StructuredLLM:
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    model = os.getenv("LLM_MODEL", "").strip()
    api_key = os.getenv("LLM_API_KEY", "").strip()

    if provider == "unconfigured":
        return UnconfiguredLLM()

    if provider != "openrouter":
        raise ValueError(f"Неподдерживаемый LLM_PROVIDER: {provider}")

    if not model:
        raise ValueError("Не задан LLM_MODEL")

    if not api_key:
        raise ValueError("Не задан LLM_API_KEY")

    try:
        timeout_s = float(os.getenv("LLM_TIMEOUT_S", "60"))
    except ValueError as error:
        raise ValueError("LLM_TIMEOUT_S должен быть числом") from error

    return LangChainLLM(
        model=model,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        timeout_s=timeout_s,
    )
