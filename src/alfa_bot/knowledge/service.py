from uuid import UUID

from alfa_bot.contracts import MaterialCard, Profile, SearchResult, SourceKey
from alfa_bot.infra.embeddings import Embeddings
from alfa_bot.knowledge.repository import KnowledgeRepository


class KnowledgeService:
    def __init__(self, repository: KnowledgeRepository, embeddings: Embeddings):
        self.repository = repository
        self.embeddings = embeddings

    async def retrieve(
        self,
        query: str,
        *,
        limit: int = 6,
        run_id: UUID | None = None,
        external_context: bool = True,
    ) -> SearchResult:
        raise NotImplementedError("Python 1: поиск допущенных knowledge-фрагментов")

    async def recommend(
        self,
        query: str,
        profile: Profile,
        *,
        run_id: UUID,
        exclude: tuple[SourceKey, ...] = (),
        limit: int = 1,
    ) -> tuple[MaterialCard, ...]:
        raise NotImplementedError("Python 1: подбор активных публичных карточек")

    async def get_card(
        self,
        source: SourceKey,
        *,
        run_id: UUID | None = None,
    ) -> MaterialCard | None:
        raise NotImplementedError("Python 1: карточка по исходному ключу и версии")
