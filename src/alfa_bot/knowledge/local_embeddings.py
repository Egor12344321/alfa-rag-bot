"""Локальные эмбеддинги BGE/Qwen/E5. Тексты не передаются embedding API."""

import asyncio
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from alfa_bot.config import Settings


@dataclass(frozen=True)
class EmbeddingSignature:
    model: str
    revision: str
    dimension: int


class EmbeddingsPort(Protocol):
    async def signature(self) -> EmbeddingSignature: ...
    async def embed_query(self, text: str) -> list[float]: ...
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def tokenizer(self): ...


class LocalEmbeddings:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._lock = threading.Lock()
        self._signature: EmbeddingSignature | None = None

    def _load(self):
        if self._model is None:
            import torch
            from sentence_transformers import SentenceTransformer

            # Избыток потоков на небольшом CPU замедляет обработку.
            if self.settings.embedding_device == "cpu":
                torch.set_num_threads(self.settings.embedding_threads)
            model = SentenceTransformer(
                self.settings.embedding_model,
                revision=self.settings.embedding_revision,
                device=self.settings.embedding_device,
                cache_folder=str(self.settings.embedding_cache_dir),
                trust_remote_code=False,
            )
            get_dimension = getattr(model, "get_embedding_dimension", None)
            if get_dimension is None:
                get_dimension = model.get_sentence_embedding_dimension
            dimension = get_dimension()
            if dimension != self.settings.embedding_dimension:
                raise ValueError("EMBEDDING_DIMENSION не совпадает с выбранной локальной моделью")
            if self._uses_e5_prefix():
                # E5 принимает максимум 512 токенов, включая префикс и спецтокены.
                prefix_tokens = len(model.tokenizer.encode("passage: ", add_special_tokens=False))
                special_tokens = model.tokenizer.num_special_tokens_to_add(pair=False)
                if (
                    self.settings.chunk_tokens + prefix_tokens + special_tokens
                    > model.max_seq_length
                ):
                    raise ValueError("Для E5 уменьшите CHUNK_TOKENS до 500: текст будет обрезан")
            first = model._first_module()
            config = getattr(getattr(first, "auto_model", None), "config", None)
            revision = getattr(config, "_commit_hash", None)
            if not revision:
                # Новые transformers не всегда сохраняют commit в config.
                # Читаем уже загруженный snapshot из кеша, без сетевого запроса.
                from huggingface_hub import try_to_load_from_cache

                cached = try_to_load_from_cache(
                    self.settings.embedding_model,
                    "config.json",
                    cache_dir=str(self.settings.embedding_cache_dir),
                    revision=self.settings.embedding_revision,
                )
                if isinstance(cached, str):
                    parts = Path(cached).parts
                    if "snapshots" in parts:
                        revision = parts[parts.index("snapshots") + 1]
            revision = revision or self.settings.embedding_revision
            self._signature = EmbeddingSignature(
                self.settings.embedding_model,
                revision,
                dimension,
            )
            # Не кешируем частично инициализированную модель при ошибке конфигурации.
            self._model = model
        return self._model

    def _get_signature(self):
        with self._lock:
            self._load()
            return self._signature

    async def signature(self) -> EmbeddingSignature:
        return await asyncio.to_thread(self._get_signature)

    def _get_tokenizer(self):
        with self._lock:
            return self._load().tokenizer

    async def tokenizer(self):
        return await asyncio.to_thread(self._get_tokenizer)

    def _uses_e5_prefix(self) -> bool:
        name = self.settings.embedding_model.casefold().rsplit("/", 1)[-1]
        return name.startswith(("e5-", "multilingual-e5-"))

    def _encode(self, texts: list[str], *, query: bool):
        with self._lock:
            model = self._load()
            inputs = texts
            if self._uses_e5_prefix():
                # Английские префиксы нужны E5 и для русскоязычных текстов.
                prefix = "query: " if query else "passage: "
                inputs = [prefix + text for text in texts]
            elif query and "qwen3-embedding" in self.settings.embedding_model.casefold():
                inputs = [
                    "Instruct: Retrieve business knowledge relevant to the question.\n"
                    "Query: " + text
                    for text in texts
                ]
            vectors = np.asarray(
                model.encode(
                    inputs,
                    batch_size=self.settings.embedding_batch_size,
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                ),
                dtype=np.float32,
            )
            if vectors.shape != (len(texts), self.settings.embedding_dimension):
                raise ValueError("Локальная модель вернула некорректную размерность")
            if not np.isfinite(vectors).all():
                raise ValueError("Локальная модель вернула нечисловые значения")
            if np.any(np.linalg.norm(vectors, axis=1) == 0):
                raise ValueError("Локальная модель вернула нулевой вектор")
            return vectors.tolist()

    async def embed_query(self, text: str) -> list[float]:
        return (await asyncio.to_thread(self._encode, [text], query=True))[0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._encode, texts, query=False)
