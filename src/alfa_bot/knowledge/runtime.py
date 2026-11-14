"""Подключения базы знаний, используемые ботом и консолью."""

import asyncio
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alfa_bot.infra.config import Settings
from alfa_bot.infra.embeddings import OllamaEmbeddings
from alfa_bot.knowledge.models import KnowledgeUnavailable
from alfa_bot.knowledge.repository import KnowledgeRepository
from alfa_bot.knowledge.retrieval import PostgresKnowledge
from alfa_bot.knowledge.service import KnowledgePort, KnowledgeService

logger = logging.getLogger(__name__)


async def open_knowledge(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient,
    *,
    require_index: bool = True,
) -> KnowledgePort:
    if settings.knowledge_backend == "legacy":
        index = await asyncio.to_thread(KnowledgeRepository.load, settings.knowledge_index)
        return KnowledgeService(index)
    service = PostgresKnowledge(
        sessions,
        OllamaEmbeddings(settings.embedding_model, client, timeout=settings.embedding_timeout),
        minimum_score=settings.kb_score_threshold,
        catalog_minimum_score=settings.kb_catalog_score_threshold,
        top_k=settings.kb_top_k,
    )
    try:
        await service.ensure_ready()
    except KnowledgeUnavailable as error:
        if require_index:
            raise
        logger.warning("Knowledge search unavailable at startup: %s", error)
    return service
