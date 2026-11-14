"""Локальный E5; загрузка модели и вычисления вынесены из event loop."""

import asyncio
import threading
from typing import Protocol

import httpx
import numpy as np
from numpy.typing import NDArray


class Embeddings(Protocol):
    model_name: str

    async def embed_query(self, text: str) -> NDArray[np.float32]: ...
    async def embed_documents(self, texts: list[str]) -> NDArray[np.float32]: ...


class LocalEmbeddings:
    def __init__(self, model_name: str = "intfloat/multilingual-e5-small"):
        self.model_name = model_name
        self._model = None
        self._lock = threading.Lock()

    def _encode(self, texts: list[str], prefix: str) -> NDArray[np.float32]:
        with self._lock:
            if self._model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError:
                    raise RuntimeError('Установите pip install -e ".[semantic]"') from None
                self._model = SentenceTransformer(self.model_name, trust_remote_code=False)
                self._model.max_seq_length = 512
            return np.asarray(
                self._model.encode(
                    [prefix + text for text in texts],
                    batch_size=32,
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                    show_progress_bar=len(texts) > 32,
                ),
                dtype=np.float32,
            )

    async def embed_query(self, text: str) -> NDArray[np.float32]:
        return (await asyncio.to_thread(self._encode, [text], "query: "))[0]

    async def embed_documents(self, texts: list[str]) -> NDArray[np.float32]:
        return await asyncio.to_thread(self._encode, texts, "passage: ")


class EmbeddingError(RuntimeError):
    """Ошибка вычисления вектора без содержимого запроса."""


class OllamaEmbeddings:
    """Локальные эмбеддинги через HTTP API Ollama."""

    def __init__(self, model_name: str, client: httpx.AsyncClient, *, timeout: float = 180):
        self.model_name = model_name
        self.client = client
        self.timeout = timeout

    async def embed_documents(self, texts: list[str]) -> NDArray[np.float32]:
        try:
            response = await self.client.post(
                "/api/embed",
                json={"model": self.model_name, "input": texts, "truncate": False},
                timeout=self.timeout,
            )
            response.raise_for_status()
            vectors = np.asarray(response.json()["embeddings"], dtype=np.float32)
            if (
                vectors.ndim != 2
                or vectors.shape[0] != len(texts)
                or not np.isfinite(vectors).all()
                or not np.any(vectors, axis=1).all()
            ):
                raise ValueError("Invalid embedding shape or values")
            return vectors
        except (httpx.HTTPError, TypeError, KeyError, ValueError):
            raise EmbeddingError("Ollama не вернул корректные эмбеддинги.") from None

    async def embed_query(self, text: str) -> NDArray[np.float32]:
        return (await self.embed_documents([text]))[0]
