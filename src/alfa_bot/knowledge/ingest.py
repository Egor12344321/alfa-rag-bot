import argparse
import asyncio
import json
from pathlib import Path

from alfa_bot.infra.config import load_config
from alfa_bot.infra.embeddings import LocalEmbeddings
from alfa_bot.knowledge.ingestion import build_index


def main() -> None:
    config = load_config()
    parser = argparse.ArgumentParser(description="Собрать индекс из public-only JSONL")
    parser.add_argument("--chunks", required=True, type=Path)
    parser.add_argument("--catalog", type=Path, help="JSONL карточек курсов и материалов")
    parser.add_argument("--index", type=Path, default=config.knowledge_index)
    parser.add_argument("--backend", choices=["bm25", "hybrid"], default="bm25")
    parser.add_argument("--model", default="intfloat/multilingual-e5-small")
    args = parser.parse_args()
    result = asyncio.run(
        build_index(
            chunks=args.chunks,
            catalog=args.catalog,
            root=args.index,
            embeddings=LocalEmbeddings(args.model) if args.backend == "hybrid" else None,
            article_url_template=config.article_url_template,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
