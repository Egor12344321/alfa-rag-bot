"""Full prepared-index import/search against a dedicated PostgreSQL database."""

from pathlib import Path

import pytest
from sqlalchemy import text

from alfa_bot.domain import Profile
from alfa_bot.knowledge.bundle import import_bundle, validate_bundle
from alfa_bot.knowledge.repository import EmbeddingCompatibilityError, KnowledgeRepository
from alfa_bot.knowledge.sqlalchemy_pool import SQLAlchemyPool


@pytest.mark.asyncio
async def test_full_bundle_import_is_idempotent_and_retrieval_uses_same_model(db):
    bundle = validate_bundle(Path(__file__).resolve().parents[1] / "private_data/prepared")
    run_id = await import_bundle(db, bundle)
    assert await import_bundle(db, bundle) == run_id
    repository = KnowledgeRepository(SQLAlchemyPool(db.engine))
    model = bundle.manifest.embedding
    index = next(i for i, c in enumerate(bundle.chunks) if c.purpose == "knowledge")
    found = await repository.retrieve_by_vector(
        bundle.vectors[index],
        embedding_model=model.model,
        embedding_revision=model.revision,
    )
    assert found.run_id == run_id
    assert found.evidence[0].distance < 0.00001
    assert found.evidence[0].text == bundle.chunks[index].text
    index = next(i for i, c in enumerate(bundle.chunks) if c.purpose == "catalog")
    cards = await repository.recommend_by_vector(
        bundle.vectors[index],
        profile=Profile(),
        run_id=run_id,
        exclude=(),
        limit=3,
        embedding_model=model.model,
        embedding_revision=model.revision,
    )
    assert cards and cards[0].score > 0.999
    with pytest.raises(EmbeddingCompatibilityError):
        await repository.retrieve_by_vector(
            bundle.vectors[index],
            embedding_model="another model",
            embedding_revision=model.revision,
        )
    async with db.engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM kb.chunks")) == 4219
        assert await connection.scalar(text("SELECT count(*) FROM kb.materials")) == 1425
