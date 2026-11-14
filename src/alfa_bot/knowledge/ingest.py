"""Команда импорта опубликованных материалов в индекс."""

import argparse
import asyncio
import json
import logging
from pathlib import Path

import httpx

from alfa_bot.infra.config import Settings, load_config
from alfa_bot.infra.db import Database
from alfa_bot.infra.embeddings import LocalEmbeddings, OllamaEmbeddings
from alfa_bot.knowledge.corpus import prepare_corpus, read_published_jsonl
from alfa_bot.knowledge.index_postgres import has_index, replace_index
from alfa_bot.knowledge.ingestion import build_index
from alfa_bot.knowledge.source_postgres import read_source_database

logger = logging.getLogger(__name__)


async def import_pgvector(
    settings: Settings,
    *,
    materials: Path | None,
    source_url: str,
    if_empty: bool,
    force: bool = False,
) -> dict:
    settings.validate_runtime(telegram=False)
    target = Database(settings.database_url)
    try:
        if if_empty and await has_index(target.sessions):
            return {"status": "unchanged"}
        if source_url:
            source = Database(source_url)
            try:
                async with source.engine.connect() as connection:
                    records = await read_source_database(connection, settings.source_templates)
            finally:
                await source.close()
        else:
            if materials is None:
                raise ValueError("Укажите путь к JSONL.")
            records = await asyncio.to_thread(read_published_jsonl, materials)
        chunks = await asyncio.to_thread(
            prepare_corpus,
            records,
            chunk_size=settings.chunk_size_tokens,
            overlap=settings.chunk_overlap_tokens,
        )
        logger.info("Prepared %d chunks from %d source rows", len(chunks), len(records))
        async with httpx.AsyncClient(base_url=settings.ollama_base_url, trust_env=False) as client:
            embeddings = OllamaEmbeddings(
                settings.embedding_model,
                client,
                timeout=settings.embedding_timeout,
            )
            return await replace_index(
                target.sessions, chunks, embeddings, if_empty=if_empty, force=force,
            )
    finally:
        await target.close()


def main() -> None:
    settings = load_config()
    logging.basicConfig(level=settings.log_level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="Собрать индекс из опубликованных материалов")
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--chunks", type=Path, help="Ранее подготовленный JSONL со статьями")
    sources.add_argument("--materials", type=Path, help="Публичные материалы в JSONL")
    sources.add_argument("--source-database-url", help="URL PostgreSQL с восстановленным дампом")
    parser.add_argument("--catalog", type=Path, help="JSONL каталога для --chunks")
    parser.add_argument("--index", type=Path, default=settings.knowledge_index)
    parser.add_argument("--backend", choices=["pgvector", "bm25", "hybrid"])
    parser.add_argument(
        "--model", default="intfloat/multilingual-e5-small", help="Модель legacy E5"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--if-empty", action="store_true", help="Импорт только в пустой индекс")
    mode.add_argument("--force", action="store_true", help="Пересчитать существующий индекс")
    args = parser.parse_args()
    backend = args.backend or ("bm25" if args.chunks else "pgvector")
    if backend == "pgvector":
        source_url = args.source_database_url or (
            settings.source_database_url if not args.materials else ""
        )
        if args.chunks or args.catalog or not (args.materials or source_url):
            parser.error("Для pgvector задайте --materials или SOURCE_DATABASE_URL.")
        result = asyncio.run(
            import_pgvector(
                settings,
                materials=args.materials,
                source_url=source_url,
                if_empty=args.if_empty,
                force=args.force,
            )
        )
    else:
        if not args.chunks or args.if_empty or args.force:
            parser.error("Для bm25/hybrid нужен --chunks.")
        result = asyncio.run(
            build_index(
                chunks=args.chunks,
                catalog=args.catalog,
                root=args.index,
                embeddings=LocalEmbeddings(args.model) if backend == "hybrid" else None,
                article_url_template=settings.article_url_template,
            )
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
