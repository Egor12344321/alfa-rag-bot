import asyncio
from typing import Protocol

from alfa_bot.contracts import MaterialAccess, MaterialCard, MaterialKind
from alfa_bot.infra.embeddings import Embeddings, LocalEmbeddings
from alfa_bot.knowledge.models import SearchHit
from alfa_bot.knowledge.repository import KnowledgeRepository


class KnowledgePort(Protocol):
    async def retrieve(self, query: str, *, limit: int | None = None) -> list[SearchHit]: ...

    async def recommend(
        self,
        query: str,
        *,
        exclude: set[str] | None = None,
        limit: int = 2,
        kind: str = "any",
        access: str = "any",
    ) -> list[SearchHit]: ...

    async def get_card(self, material_id: str) -> MaterialCard | None: ...



class KnowledgeService:
    def __init__(self, repository: KnowledgeRepository, embeddings: Embeddings | None = None):
        self.repository = repository
        self.embeddings = embeddings
        if repository.model_name and embeddings is None:
            self.embeddings = LocalEmbeddings(repository.model_name)
        if embeddings and repository.model_name != embeddings.model_name:
            raise ValueError("Модель запросов должна совпадать с моделью индекса.")

    async def _search(self, query: str, *, purpose: str) -> list[SearchHit]:
        if not query.strip():
            return []
        vector = await self.embeddings.embed_query(query) if self.embeddings else None
        ranking = await asyncio.to_thread(
            self.repository.rank,
            query,
            purpose=purpose,
            query_vector=vector,
        )
        return [
            SearchHit(
                document=self.repository.documents[i],
                material=self.repository.materials[self.repository.documents[i].material_id],
                score=score,
            )
            for i, score in ranking
        ]

    async def retrieve(self, query: str, *, limit: int | None = None) -> list[SearchHit]:
        limit = 5 if limit is None else limit
        if not 1 <= limit <= 20:
            raise ValueError("limit должен быть от 1 до 20")
        counts = {}
        result = []
        for hit in await self._search(query, purpose="knowledge"):
            key = hit.material.id
            if counts.get(key, 0) >= 2:
                continue
            counts[key] = counts.get(key, 0) + 1
            result.append(hit)
            if len(result) == limit:
                break
        return result

    async def recommend(
        self,
        query: str,
        *,
        exclude: set[str] | None = None,
        limit: int = 2,
        kind: str = "any",
        access: str = "any",
    ) -> list[SearchHit]:
        if not 1 <= limit <= 10:
            raise ValueError("limit должен быть от 1 до 10")
        hits = await self._search(query, purpose="catalog")
        result = []
        for hit in hits:
            mat = hit.material
            if exclude and mat.id in exclude:
                continue
            if kind != "any" and mat.kind != MaterialKind(kind).value:
                continue
            if access != "any" and mat.access != MaterialAccess(access).value:
                continue
            if mat.card() is not None:
                result.append(hit)
        return result[:limit]

    async def get_card(self, material_id: str) -> MaterialCard | None:
        material = self.repository.materials.get(material_id)
        return material.card() if material else None
