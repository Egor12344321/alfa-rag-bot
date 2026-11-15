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

from .configured_embeddings import ConfiguredEmbedder
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
        description="Import explicitly approved public materials into the working kb schema"
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
    from sqlalchemy.engine import make_url

    from alfa_bot.config import Settings
    from alfa_bot.db import Database
    from alfa_bot.knowledge.sqlalchemy_pool import SQLAlchemyPool

    settings = Settings()
    source_dsn = os.environ.get("ALFA_SOURCE_DSN")
    if not source_dsn and settings.source_database_url:
        source_dsn = (
            make_url(settings.source_database_url)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )
    target_url = os.environ.get("ALFA_DATABASE_URL") or settings.database_url
    target_url = str(
        make_url(target_url)
        .set(drivername="postgresql+psycopg")
        .render_as_string(hide_password=False)
    )
    if not source_dsn:
        raise ValueError("Set ALFA_SOURCE_DSN to the restored source database")

    try:
        import psycopg
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
        raise RuntimeError(
            "No verified materials passed the ingestion policy; no run was published"
        )

    embedder = ConfiguredEmbedder(settings)
    database = Database(target_url)
    pool = SQLAlchemyPool(database.engine)
    try:
        from alfa_bot.runtime import require_migrations

        await require_migrations(database)
        async with pool.connection() as target:
            cursor = await target.execute("SELECT current_database()")
            target_db = (await cursor.fetchone())[0]
        expected_target_db = os.getenv("ALFA_TARGET_DATABASE") or make_url(target_url).database
        if target_db != expected_target_db:
            raise RuntimeError(f"Target DB is {target_db!r}; expected {expected_target_db!r}")
        async with pool.connection() as target:
            source = await asyncio.to_thread(lambda: _source_database_name(psycopg, source_dsn))
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

    finally:
        await database.close()

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
    with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as connection:
        return connection.execute("SELECT current_database()").fetchone()[0]


def main() -> None:
    args = _parser().parse_args()
    try:
        factory = _load_factory(args.mapping)
        exit_code = asyncio.run(_run(factory))
    except Exception as exc:
        print(
            f"ingest failed: {type(exc).__name__}; check configuration, database and model",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
