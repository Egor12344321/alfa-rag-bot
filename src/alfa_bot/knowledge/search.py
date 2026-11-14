import argparse
import asyncio
from pathlib import Path

from alfa_bot.infra.config import load_config
from alfa_bot.knowledge.repository import KnowledgeRepository
from alfa_bot.knowledge.service import KnowledgeService


async def search(args):
    service = KnowledgeService(KnowledgeRepository.load(args.index))
    hits = await service.retrieve(args.query, limit=5)
    for i, hit in enumerate(hits, start=1):
        print(f"{i}. {hit.material.title}\n   {hit.material.url}\n   {hit.document.id}")
        print(hit.document.text[:350] + "\n")
    if not hits:
        print("Подходящие фрагменты не найдены.")


def main():
    parser = argparse.ArgumentParser(description="Проверить поиск без Telegram и API LLM")
    parser.add_argument("query")
    parser.add_argument("--index", type=Path, default=load_config().knowledge_index)
    asyncio.run(search(parser.parse_args()))


if __name__ == "__main__":
    main()
