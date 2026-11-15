"""Verify bundle corruption checks, wiring and migration/deployment consistency."""

import asyncio
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from alfa_bot.config import Settings
from alfa_bot.db import Database
from alfa_bot.knowledge.bundle import BundleError, validate_bundle
from alfa_bot.knowledge.repository import InvalidIngestion, _vector_literal
from alfa_bot.main import build_components
from alfa_bot.runtime import SCHEMA_REVISION

ROOT = Path(__file__).resolve().parents[1]


def bundle(root, vector=None):
    vector = vector if vector is not None else [1.0, 0.0]
    row = {
        "source_key": "article:a",
        "purpose": "knowledge",
        "chunk_no": 0,
        "text": "Текст.",
        "meta": {
            "id": "article:a",
            "source_type": "article",
            "source_id": "a",
            "title": "Заголовок",
            "url": "https://example.org/article",
            "is_paid": False,
        },
    }
    (root / "chunks.jsonl").write_text(json.dumps(row, ensure_ascii=False) + "\n")
    np.save(root / "embeddings.npy", np.array([vector], dtype=np.float32))
    files = {
        name: {
            "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest(),
            "bytes": (root / name).stat().st_size,
        }
        for name in ("chunks.jsonl", "embeddings.npy")
    }
    manifest = {
        "format_version": 1,
        "pipeline_version": "3.1",
        "embedding": {"model": "model", "revision": "a" * 40, "dimension": 2},
        "chunking": {"tokens": 100, "overlap": 0},
        "counts": {"materials": 1, "chunks": 1, "knowledge_chunks": 1, "catalog_chunks": 0},
        "files": files,
        "source_fingerprint": "b" * 64,
        "created_at": "2030-01-01T00:00:00Z",
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root


def test_valid_bundle_retains_vector_order_and_is_immutable(tmp_path):
    value = validate_bundle(bundle(tmp_path))
    assert value.chunks[0].text == "Текст."
    assert value.vectors[0].tolist() == [1.0, 0.0]
    assert not value.vectors.flags.writeable


@pytest.mark.parametrize("filename", ["chunks.jsonl", "embeddings.npy"])
def test_modified_bundle_bytes_are_rejected(tmp_path, filename):
    root = bundle(tmp_path)
    with (root / filename).open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(BundleError, match="SHA-256"):
        validate_bundle(root)


@pytest.mark.parametrize("vector", [[0.0, 0.0], [float("nan"), 0.0], [0.5, 0.0]])
def test_invalid_vectors_are_rejected_even_with_correct_checksum(tmp_path, vector):
    with pytest.raises(BundleError):
        validate_bundle(bundle(tmp_path, vector))


def test_actual_included_bundle_matches_default_model_signature():
    settings = Settings(_env_file=None)
    value = validate_bundle(ROOT / "private_data/prepared", settings)
    assert value.manifest.counts.materials == 1425
    assert len(value.chunks) == 4219


def test_wiring_is_lazy_and_accepts_old_asyncpg_url_without_loading_models(monkeypatch):
    from alfa_bot.knowledge.local_embeddings import LocalEmbeddings

    def fail(*args):
        raise AssertionError("Bootstrap should not download a model")

    monkeypatch.setattr(LocalEmbeddings, "_load", fail)
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://u:p@localhost/db")
    db = Database(settings.database_url)
    assistant, llm, knowledge = build_components(db, settings)
    assert type(assistant).__name__ == "IntegratedAssistant"
    assert type(knowledge).__name__ == "KnowledgeService"
    assert db.engine.dialect.driver == "psycopg"
    asyncio.run(llm.close())
    asyncio.run(db.close())


def test_migration_head_matches_startup_guard():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_current_head() == SCHEMA_REVISION


@pytest.mark.parametrize("vector", [[], [0.0] * 384, [float("nan"), 1.0]])
def test_repository_rejects_empty_zero_and_nan_vectors(vector):
    with pytest.raises(InvalidIngestion):
        _vector_literal(vector)
