from typing import Protocol


class LLM(Protocol):
    async def generate(self, *, system: str, context: str, question: str) -> str: ...


class UnconfiguredLLM:
    """Python 2: подключить выбранный SDK, timeout и ограниченные повторы."""

    async def generate(self, *, system: str, context: str, question: str) -> str:
        raise NotImplementedError("Python 2: подключить генератор без передачи лишних данных")
