"""Validated import of existing JSONL/NumPy bundles into the contributed index."""

import argparse
import hashlib
import io
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from alfa_bot.config import Settings, load_config
from alfa_bot.contracts import Material
from alfa_bot.db import Database
from alfa_bot.knowledge.contracts import ChunkInput, MaterialInput, SourceBlock
from alfa_bot.knowledge.repository import IndexedMaterial, KnowledgeRepository
from alfa_bot.knowledge.sqlalchemy_pool import SQLAlchemyPool
from alfa_bot.runtime import configure_runtime, require_migrations, run_cli

FILES = ("chunks.jsonl", "embeddings.npy")


class BundleError(ValueError):
    """Ожидаемая ошибка пакета; сообщение не содержит исходных текстов и секретов."""


class BundleDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FileInfo(BundleDTO):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(gt=0)


class ModelInfo(BundleDTO):
    model: str = Field(min_length=1)
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    dimension: int = Field(gt=0, le=8192)


class ChunkingInfo(BundleDTO):
    tokens: int = Field(ge=100, le=2000)
    overlap: int = Field(ge=0, le=300)

    @model_validator(mode="after")
    def valid_overlap(self):
        if self.overlap >= self.tokens:
            raise ValueError("overlap должен быть меньше tokens")
        return self


class Counts(BundleDTO):
    materials: int = Field(gt=0)
    chunks: int = Field(gt=0, le=10_000_000)
    knowledge_chunks: int = Field(ge=0)
    catalog_chunks: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_totals(self):
        if self.knowledge_chunks + self.catalog_chunks != self.chunks:
            raise ValueError("Сумма назначений не совпадает с chunks")
        if self.materials > self.chunks:
            raise ValueError("Материалов больше, чем чанков")
        return self


class Manifest(BundleDTO):
    format_version: Literal[1] = 1
    pipeline_version: Literal["3.1"] = "3.1"
    embedding: ModelInfo
    chunking: ChunkingInfo
    counts: Counts
    files: dict[Literal["chunks.jsonl", "embeddings.npy"], FileInfo]
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: AwareDatetime
    sources: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def exact_files(self):
        if set(self.files) != set(FILES):
            raise ValueError("Нужны chunks.jsonl и embeddings.npy")
        return self


class PreparedChunk(BundleDTO):
    source_key: str = Field(min_length=1)
    purpose: Literal["knowledge", "catalog"]
    chunk_no: int = Field(ge=0)
    text: str = Field(min_length=1)
    meta: Material

    @model_validator(mode="after")
    def safe_content(self):
        if self.source_key != self.meta.id:
            raise ValueError("source_key не совпадает с Material.id")
        if not self.text.strip() or "\x00" in self.text:
            raise ValueError("Пустой текст или нулевой символ")
        if self.purpose == "knowledge":
            if self.meta.source_type == "course" or self.meta.is_paid is not False:
                raise ValueError("Только общедоступные некурсовые тексты разрешены для ответа")
        elif self.meta.source_type != "course":
            raise ValueError("В pipeline 3.1 отдельный catalog нужен только курсам")
        return self

    @property
    def key(self):
        return self.source_key, self.purpose, self.chunk_no


@dataclass(frozen=True)
class LoadedBundle:
    manifest: Manifest
    chunks: tuple[PreparedChunk, ...]
    vectors: np.ndarray
    bundle_id: str


def _checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bundle_id(manifest: Manifest) -> str:
    payload = manifest.model_dump(mode="json", exclude={"created_at", "sources"})
    return _checksum(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())


def _check_contents(chunks: list[PreparedChunk], vectors: np.ndarray, manifest: Manifest):
    expected = manifest.counts
    if vectors.dtype != np.dtype("float32") or vectors.shape != (
        expected.chunks,
        manifest.embedding.dimension,
    ):
        raise BundleError("embeddings.npy: ожидается float32 с размерностью из manifest.json")
    if not np.isfinite(vectors).all():
        raise BundleError("embeddings.npy содержит NaN или бесконечность")
    norms = np.linalg.norm(vectors, axis=1)
    if not np.allclose(norms, 1.0, rtol=0, atol=0.001):
        raise BundleError("embeddings.npy: векторы должны быть ненулевыми и нормализованными")
    if len(chunks) != expected.chunks:
        raise BundleError("Число строк chunks.jsonl не совпадает с manifest.json")
    keys = set()
    materials = {}
    groups = defaultdict(set)
    counts = Counter()
    for chunk in chunks:
        if chunk.key in keys:
            raise BundleError("В chunks.jsonl повторяется logical key чанка")
        keys.add(chunk.key)
        meta = chunk.meta.model_dump(mode="json")
        if chunk.source_key in materials and materials[chunk.source_key] != meta:
            raise BundleError("Для одного материала указаны разные метаданные")
        materials[chunk.source_key] = meta
        groups[(chunk.source_key, chunk.purpose)].add(chunk.chunk_no)
        counts[chunk.purpose] += 1
    if any(numbers != set(range(len(numbers))) for numbers in groups.values()):
        raise BundleError("Номера чанков каждого материала должны идти подряд с нуля")
    if (
        len(materials) != expected.materials
        or counts["knowledge"] != expected.knowledge_chunks
        or counts["catalog"] != expected.catalog_chunks
    ):
        raise BundleError("Состав материалов или назначений не совпадает с manifest.json")


def validate_bundle(root: Path, settings: Settings | None = None) -> LoadedBundle:
    """Читаем проверенные байты один раз: дальнейшее изменение файла не меняет импорт."""
    root = Path(root)
    try:
        manifest = Manifest.model_validate_json((root / "manifest.json").read_bytes())
    except (OSError, ValueError):
        raise BundleError("manifest.json отсутствует или имеет неподдерживаемый формат") from None
    if settings and (
        settings.embedding_model != manifest.embedding.model
        or settings.embedding_revision != manifest.embedding.revision
        or settings.embedding_dimension != manifest.embedding.dimension
    ):
        raise BundleError(
            "Модель пакета не совпадает с .env: перенесите EMBEDDING_MODEL, точный "
            "EMBEDDING_REVISION и EMBEDDING_DIMENSION из manifest.json"
        )
    payloads = {}
    for name in FILES:
        try:
            payload = (root / name).read_bytes()
        except OSError:
            raise BundleError(f"Не удалось прочитать {name}") from None
        info = manifest.files[name]
        if len(payload) != info.bytes or _checksum(payload) != info.sha256:
            raise BundleError(f"Размер или SHA-256 файла {name} не совпадает с manifest.json")
        payloads[name] = payload
    chunks = []
    for number, line in enumerate(payloads["chunks.jsonl"].splitlines(), start=1):
        try:
            chunks.append(PreparedChunk.model_validate_json(line))
        except ValueError:
            raise BundleError(f"Некорректная строка chunks.jsonl:{number}") from None
    try:
        vectors = np.load(io.BytesIO(payloads["embeddings.npy"]), allow_pickle=False)
    except (OSError, ValueError, EOFError):
        raise BundleError("embeddings.npy: ожидается безопасный массив NumPy, без pickle") from None
    if not isinstance(vectors, np.ndarray):
        raise BundleError("embeddings.npy должен содержать один массив NumPy")
    _check_contents(chunks, vectors, manifest)
    vectors.setflags(write=False)
    return LoadedBundle(manifest, tuple(chunks), vectors, _bundle_id(manifest))


async def import_bundle(database, bundle):
    """Keep vector order and model identity; import does not load a model."""
    grouped = defaultdict(list)
    for index, chunk in enumerate(bundle.chunks):
        grouped[chunk.source_key].append((index, chunk))
    records = []
    tables = {
        "article": "Articles",
        "document": "Documents",
        "glossary": "Glossary",
        "course": "Courses",
    }
    for items in grouped.values():
        meta = items[0][1].meta
        has_knowledge = any(c.purpose == "knowledge" for _, c in items)
        has_catalog = any(c.purpose == "catalog" for _, c in items)
        access = "paid" if meta.is_paid is True else "free" if meta.is_paid is False else "unknown"
        blocks = tuple(
            SourceBlock(f"bundle:{c.purpose}:{c.chunk_no}", c.chunk_no, c.text) for _, c in items
        )
        material = MaterialInput(
            source_table=tables[meta.source_type],
            source_id=meta.source_id,
            kind="term" if meta.source_type == "glossary" else meta.source_type,
            title=meta.title,
            url=meta.url,
            blocks=blocks,
            access_type=access,
            summary=meta.description or (items[0][1].text[:350] if has_knowledge else ""),
            answer_allowed=has_knowledge,
            recommend_allowed=has_catalog,
            external_allowed=has_knowledge,
            price_note=meta.price,
            provenance={
                "bundle_id": bundle.bundle_id,
                "token_count_unknown": True,
                "level": meta.level,
                "audience": meta.audience,
            },
        )
        chunks = tuple(
            ChunkInput(
                chunk_no=c.chunk_no,
                text=c.text,
                section_path=None,
                source_blocks=(f"bundle:{c.purpose}:{c.chunk_no}",),
                # Zero is unknown: the old format does not retain token counts.
                token_count=0,
                purpose=c.purpose,
            )
            for _, c in items
        )
        vectors = tuple(bundle.vectors[index].tolist() for index, _ in items)
        records.append(IndexedMaterial(material, chunks, vectors))
    model = bundle.manifest.embedding
    repository = KnowledgeRepository(SQLAlchemyPool(database.engine))
    return await repository.publish_run(
        records,
        source_fingerprint=bundle.manifest.source_fingerprint,
        embedding_model=model.model,
        embedding_revision=model.revision,
        pipeline_version="integrated-bundle-v1",
        mapping_version=bundle.bundle_id,
        policy_version="prepared-v3.1",
    )


async def _run(path):
    settings = load_config()
    bundle = validate_bundle(path, settings)
    database = Database(settings.database_url)
    try:
        await require_migrations(database)
        run_id = await import_bundle(database, bundle)
        print(
            json.dumps(
                {"run_id": str(run_id), "counts": bundle.manifest.counts.model_dump()},
                ensure_ascii=False,
            )
        )
    finally:
        await database.close()


def main():
    parser = argparse.ArgumentParser(
        description="Import prepared knowledge without embedding computation"
    )
    parser.add_argument("--bundle", type=Path, default=Path("private_data/prepared"))
    args = parser.parse_args()
    configure_runtime()
    run_cli(_run(args.bundle), operation="import_bundle")


if __name__ == "__main__":
    main()
