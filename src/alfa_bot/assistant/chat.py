"""Ручная проверка AssistantPort без Telegram."""

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime

import httpx

from alfa_bot.assistant.repository import AssistantRepository
from alfa_bot.assistant.service import AssistantService
from alfa_bot.infra.config import load_config
from alfa_bot.infra.db import Database
from alfa_bot.infra.llm import OllamaLLM
from alfa_bot.knowledge.runtime import open_knowledge


async def run(telegram_user_id: int):
    config = load_config()
    config.validate_runtime(telegram=False)
    database = Database(config.database_url)
    try:
        async with httpx.AsyncClient(
            base_url=config.ollama_base_url,
            timeout=config.llm_timeout,
            trust_env=False,
        ) as client:
            knowledge = await open_knowledge(config, database.sessions, client)
            llm = OllamaLLM(config.llm_model, client, context_length=config.ollama_context_length)
            assistant = AssistantService(AssistantRepository(database.sessions), knowledge, llm)
            print("/exit — выйти; /new — очистить тестовый диалог; /cancel — отменить уточнение")
            while True:
                try:
                    user_text = await asyncio.to_thread(input, "Вы: ")
                except EOFError:
                    break
                if user_text.strip() == "/exit":
                    break
                reply = await assistant.handle(
                    telegram_user_id=telegram_user_id,
                    text=user_text,
                    now=datetime.now(UTC),
                )
                print(json.dumps(asdict(reply), ensure_ascii=False, default=str, indent=2))
    finally:
        await database.close()


def main():
    parser = argparse.ArgumentParser(description="Проверить assistant без Telegram")
    parser.add_argument("--user-id", required=True, type=int, help="Отдельный тестовый Telegram ID")
    asyncio.run(run(parser.parse_args().user_id))


if __name__ == "__main__":
    main()
