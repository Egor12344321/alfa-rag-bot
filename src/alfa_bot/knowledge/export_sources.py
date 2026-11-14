"""Сохраняет локальный JSONL для переноса публичного корпуса в Docker."""

import argparse
import asyncio
import logging
from pathlib import Path
from uuid import uuid4

from alfa_bot.infra.config import load_config
from alfa_bot.infra.db import Database
from alfa_bot.knowledge.source_postgres import read_source_database


async def export_sources(output: Path) -> None:
    config = load_config()
    if not config.source_database_url:
        raise ValueError("Задайте SOURCE_DATABASE_URL с восстановленным дампом.")
    database = Database(config.source_database_url)
    try:
        async with database.engine.connect() as connection:
            records = await read_source_database(connection, config.source_templates)
    finally:
        await database.close()
    if not records:
        raise ValueError("В дампе нет допущенных материалов.")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f".{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            for source in records:
                stream.write(source.model_dump_json())
                stream.write("\n")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    logging.getLogger(__name__).info("Exported %d published materials", len(records))


def main() -> None:
    config = load_config()
    logging.basicConfig(level=config.log_level)
    parser = argparse.ArgumentParser(description="Экспорт открытых материалов из локальной БД")
    parser.add_argument("--output", required=True, type=Path, help="Файл JSONL в private_data")
    asyncio.run(export_sources(parser.parse_args().output))


if __name__ == "__main__":
    main()
