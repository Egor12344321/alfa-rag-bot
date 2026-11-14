"""Ручная проверка retrieval без Telegram и генерации."""

import argparse
import asyncio
from pathlib import Path

import httpx

from alfa_bot.infra.config import load_config
from alfa_bot.infra.db import Database
from alfa_bot.knowledge.repository import KnowledgeRepository
from alfa_bot.knowledge.runtime import open_knowledge
from alfa_bot.knowledge.service import KnowledgeService


async def search(args):
    config = load_config()
    if args.index or config.knowledge_backend == "legacy":
        service = KnowledgeService(KnowledgeRepository.load(args.index or config.knowledge_index))
        hits = await service.retrieve(args.query)
    else:
        config.validate_runtime(telegram=False)
        database = Database(config.database_url)
        try:
            async with httpx.AsyncClient(
                base_url=config.ollama_base_url,
                trust_env=False,
            ) as client:
                service = await open_knowledge(config, database.sessions, client)
                hits = await service.retrieve(args.query)
        finally:
            await database.close()
    for i, hit in enumerate(hits, start=1):
        print(
            f"{i}. {hit.material.title} ({hit.score:.3f})"
            f"\n   {hit.material.url}\n   {hit.document.id}"
        )
        print(hit.document.text[:350] + "\n")
    if not hits:
        print("Подходящие фрагменты не найдены.")


def main():
    parser = argparse.ArgumentParser(description="Проверить retrieval без Telegram и генерации")
    parser.add_argument("query")
    parser.add_argument("--index", type=Path, help="Путь к прежнему файловому индексу")
    asyncio.run(search(parser.parse_args()))


if __name__ == "__main__":
    main()
