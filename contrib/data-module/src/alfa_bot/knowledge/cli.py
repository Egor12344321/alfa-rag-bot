"""Command line entrypoint for the reviewed, local knowledge import."""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
from collections.abc import Callable
from typing import Any

from .embeddings import E5Embedder
from .repository import KnowledgeRepository
from .service import KnowledgeService


def _load_factory(spec: str) -> Callable[[], Any]:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("Mapping factory must use module.path:factory_name syntax")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute)
    if not callable(factory):
        raise TypeError(f"Mapping factory {spec!r} is not callable")
    return factory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import explicitly approved public materials into assistant_db.kb"
    )
    parser.add_argument(
        "--mapping",
        default=os.getenv(
            "ALFA_MAPPING_FACTORY",
            "alfa_bot.knowledge.expanded_sources:build_mapping",
        ),
        help="local reviewed mapping factory (module.path:function); default: %(default)s",
    )
    return parser


async def _run(mapping_factory: Callable[[], Any]) -> int:
    source_dsn = os.environ.get("ALFA_SOURCE_DSN")
    target_dsn = os.environ.get("ALFA_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not source_dsn:
        raise ValueError("Set ALFA_SOURCE_DSN to the restored source database")
    if not target_dsn:
        raise ValueError("Set ALFA_DATABASE_URL (or DATABASE_URL) to the separate assistant_db")

    try:
        import psycopg
        from psycopg_pool import AsyncConnectionPool
    except ImportError as exc:
        raise RuntimeError("Install project dependencies with `pip install -e .`") from exc

    from .source import extract_materials

    mapping = mapping_factory()
    mapping.validate()

    def extract() -> Any:
        with psycopg.connect(
            source_dsn, options="-c default_transaction_read_only=on"
        ) as connection:
            return extract_materials(connection, mapping)

    extraction = await asyncio.to_thread(extract)
    if not extraction.materials:
        print(json.dumps(extraction.report.as_dict(), ensure_ascii=False, indent=2))
        raise RuntimeError("No verified materials passed the ingestion policy; no run was published")

    embedder = E5Embedder(
        revision=os.getenv("ALFA_EMBEDDING_REVISION") or "local-cache",
        local_files_only=os.getenv("ALFA_ALLOW_MODEL_DOWNLOAD", "0") != "1"
    )
    async with AsyncConnectionPool(
        conninfo=target_dsn,
        min_size=1,
        max_size=4,
        open=False,
    ) as pool:
        async with pool.connection() as target:
            cursor = await target.execute("SELECT current_database()")
            target_db = (await cursor.fetchone())[0]
        expected_target_db = os.getenv("ALFA_TARGET_DATABASE", "assistant_db")
        if target_db != expected_target_db:
            raise RuntimeError(
                f"Target DB is {target_db!r}; expected {expected_target_db!r}"
            )
        async with pool.connection() as target:
            source = await asyncio.to_thread(
                lambda: _source_database_name(psycopg, source_dsn)
            )
            if source == target_db:
                raise RuntimeError(
                    "Source and target DSNs resolve to the same database; refusing to write"
                )
        service = KnowledgeService(KnowledgeRepository(pool), embedder)
        run_id = await service.ingest(
            extraction.materials,
            mapping_version=mapping.mapping_version,
            pipeline_version=os.getenv("ALFA_PIPELINE_VERSION", "knowledge-v1"),
            policy_version=os.getenv("ALFA_POLICY_VERSION", "access-v1"),
        )

    print(
        json.dumps(
            {
                "run_id": str(run_id),
                "extraction": extraction.report.as_dict(),
                "materials_indexed": len(extraction.materials),
                "database": target_db,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _source_database_name(psycopg: Any, dsn: str) -> str:
    with psycopg.connect(
        dsn, options="-c default_transaction_read_only=on"
    ) as connection:
        return connection.execute("SELECT current_database()").fetchone()[0]


def main() -> None:
    args = _parser().parse_args()
    try:
        factory = _load_factory(args.mapping)
        exit_code = asyncio.run(_run(factory))
    except Exception as exc:
        print(f"ingest failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
