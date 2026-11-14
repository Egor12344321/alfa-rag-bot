"""Неизменяемый снимок JSON + NumPy; никакой десериализации pickle."""

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from alfa_bot.knowledge.models import Document, Material
from alfa_bot.knowledge.parsing import tokenize


class IndexError(ValueError):
    pass


class KnowledgeRepository:
    def __init__(
        self,
        materials: list[Material],
        documents: list[Document],
        *,
        vectors: np.ndarray | None = None,
        model_name: str | None = None,
    ):
        self.materials = {item.id: item for item in materials}
        self.documents = documents
        self.vectors = vectors
        self.model_name = model_name
        self.postings = defaultdict(list)
        self.lengths = []
        for index, doc in enumerate(documents):
            mat = self.materials[doc.material_id]
            words = tokenize(f"{mat.title} {mat.title} {mat.title} {doc.text}")
            self.lengths.append(len(words))
            for token, count in Counter(words).items():
                self.postings[token].append((index, count))
        self.avg_length = sum(self.lengths) / max(len(documents), 1) or 1
        if vectors is not None:
            if (
                vectors.ndim != 2
                or vectors.shape[0] != len(documents)
                or not np.isfinite(vectors).all()
            ):
                raise IndexError("Размер или значения векторов не соответствуют индексу.")

    @classmethod
    def load(cls, root: Path) -> "KnowledgeRepository":
        try:
            snapshot = (root / "CURRENT").read_text(encoding="utf-8").strip()
            if not snapshot or Path(snapshot).name != snapshot:
                raise ValueError("Invalid snapshot")
            folder = root / snapshot
            data = json.loads((folder / "index.json").read_text(encoding="utf-8"))
            if data["version"] != 1:
                raise ValueError("Unsupported version")
            vectors = None
            if data["model_name"]:
                vectors = np.load(folder / "vectors.npy", allow_pickle=False)
            return cls(
                [Material(**row) for row in data["materials"]],
                [Document(**row) for row in data["documents"]],
                vectors=vectors,
                model_name=data["model_name"],
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise IndexError("Индекс не загружен. Сначала выполните knowledge.ingest.") from error

    def lexical_scores(self, query: str, *, purpose: str) -> dict[int, float]:
        scores = defaultdict(float)
        for token in set(tokenize(query)):
            postings = self.postings.get(token, ())
            idf = math.log(1 + (len(self.documents) - len(postings) + 0.5) / (len(postings) + 0.5))
            for index, count in postings:
                if self.documents[index].purpose != purpose:
                    continue
                normalizer = count + 1.5 * (0.25 + 0.75 * self.lengths[index] / self.avg_length)
                scores[index] += idf * count * 2.5 / normalizer
        return dict(scores)

    def rank(
        self,
        query: str,
        *,
        purpose: str,
        query_vector: np.ndarray | None = None,
        semantic_threshold: float = 0.82,
    ) -> list[tuple[int, float]]:
        lexical = self.lexical_scores(query, purpose=purpose)
        lexical_rank = sorted(lexical, key=lexical.get, reverse=True)[:100]
        if query_vector is None or self.vectors is None:
            return [(i, lexical[i]) for i in lexical_rank]
        if query_vector.shape != (self.vectors.shape[1],):
            raise IndexError("Модель запроса отличается от модели индекса.")
        similarities = self.vectors @ query_vector
        dense_rank = sorted(
            (
                i
                for i, doc in enumerate(self.documents)
                if doc.purpose == purpose and similarities[i] >= semantic_threshold
            ),
            key=lambda i: float(similarities[i]),
            reverse=True,
        )[:100]
        # Reciprocal Rank Fusion: BM25 и cosine имеют разные шкалы.
        fused = defaultdict(float)
        for ranking in (lexical_rank, dense_rank):
            for position, index in enumerate(ranking, start=1):
                fused[index] += 1 / (60 + position)
        return sorted(fused.items(), key=lambda item: item[1], reverse=True)
