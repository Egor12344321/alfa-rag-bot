"""Подключения базы знаний, используемые ботом и консолью."""

import asyncio

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alfa_bot.infra.config import Settings
from alfa_bot.infra.embeddings import OllamaEmbeddings
from alfa_bot.knowledge.repository import KnowledgeRepository
from alfa_bot.knowledge.retrieval import PostgresKnowledge
from alfa_bot.knowledge.service import KnowledgePort, KnowledgeService


async def open_knowledge(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient,
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
    await service.ensure_ready()
    return service
