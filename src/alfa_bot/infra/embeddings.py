from typing import Protocol


class Embeddings(Protocol):
    async def embed_query(self, text: str) -> list[float]: ...
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class LocalEmbeddings:
    """Python 1: локальная модель, загрузка один раз, prefix и токенизация.

    SDK модели будет добавлен после выбора и фиксации версии. CPU-вычисления
    не должны блокировать asyncio event loop.
    """

    async def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError("Python 1: подключить локальную embedding-модель")

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("Python 1: батчевая векторизация разрешённых текстов")
