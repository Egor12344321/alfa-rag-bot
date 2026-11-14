"""Ручная проверка AssistantPort без Telegram. Нужны PostgreSQL и ключ DeepSeek."""

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime

from alfa_bot.assistant.repository import AssistantRepository
from alfa_bot.assistant.service import AssistantService
from alfa_bot.infra.config import load_config
from alfa_bot.infra.db import Database
from alfa_bot.infra.llm import DeepSeekLLM
from alfa_bot.knowledge.repository import KnowledgeRepository
from alfa_bot.knowledge.service import KnowledgeService


async def run(telegram_user_id: int):
    config = load_config()
    if not config.database_url or not config.deepseek_api_key.get_secret_value():
        raise ValueError("Заполните DATABASE_URL и DEEPSEEK_API_KEY в .env")
    knowledge = KnowledgeService(KnowledgeRepository.load(config.knowledge_index))
    database = Database(config.database_url)
    llm = DeepSeekLLM(
        api_key=config.deepseek_api_key.get_secret_value(),
        model=config.deepseek_model,
        base_url=config.deepseek_base_url,
        timeout=config.llm_timeout,
    )
    try:
        repo = AssistantRepository(database.sessions)
        await repo.init_db()
        assistant = AssistantService(repo, knowledge, llm)
        print("/exit — выйти; /new — очистить тестовый диалог; /cancel — отменить уточнение")
        while True:
            try:
                text = await asyncio.to_thread(input, "Вы: ")
            except EOFError:
                break
            if text.strip() == "/exit":
                break
            reply = await assistant.handle(
                telegram_user_id=telegram_user_id,
                text=text,
                now=datetime.now(UTC),
            )
            print(json.dumps(asdict(reply), ensure_ascii=False, default=str, indent=2))
    finally:
        await llm.close()
        await database.close()


def main():
    parser = argparse.ArgumentParser(description="Проверить assistant без Telegram")
    parser.add_argument("--user-id", required=True, type=int, help="Отдельный тестовый Telegram ID")
    asyncio.run(run(parser.parse_args().user_id))


if __name__ == "__main__":
    main()
