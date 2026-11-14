"""Импорт подготовленного public-only article_chunks.jsonl и каталога JSONL."""

import json
import re
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import numpy as np

from alfa_bot.contracts import MaterialAccess, MaterialKind
from alfa_bot.infra.embeddings import Embeddings
from alfa_bot.knowledge.models import Document, Material
from alfa_bot.knowledge.parsing import clean_text


def _rows(path: Path):
    with path.open(encoding="utf-8-sig") as stream:
        for line_no, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("Ожидается JSON-объект")
                yield line_no, row
            except ValueError as error:
                raise ValueError(f"{path.name}:{line_no}: некорректный JSON") from error


def _allowed(row: dict, *, trusted_public: bool) -> bool:
    # В сыром каталоге public/active обязательны. Articles уже отфильтрованы парсером.
    public = row.get("is_public", trusted_public)
    active = row.get("is_active", trusted_public)
    return public is True and active is True and row.get("is_closed", False) is False


def read_sources(
    chunks: Path,
    catalog: Path | None,
    *,
    article_url_template: str,
) -> tuple[list[Material], list[Document]]:
    materials: dict[str, Material] = {}
    documents: list[Document] = []
    ids: set[str] = set()
    for line_no, row in _rows(chunks):
        if not _allowed(row, trusted_public=True):
            continue
        try:
            material_id = str(row["material_id"]).strip()
            doc_id = str(row["chunk_id"]).strip()
            title = clean_text(row["title"])
            text = clean_text(row["text"])
            if not all((material_id, doc_id, title, text)) or doc_id in ids:
                raise ValueError("Пустые поля или повтор chunk_id")
            if row.get("material_type", "article") != "article":
                raise ValueError("Курсы загружайте через --catalog")
            access = MaterialAccess(row.get("access", "free"))
            if access != MaterialAccess.FREE:
                continue  # Тело платного/неизвестного материала не попадёт в ответ.
            url = row.get("url")
            slug = row.get("url_slug", "")
            if not url and slug and re.fullmatch(r"[A-Za-z0-9_-]+", slug):
                url = article_url_template.format(slug=slug)
            material = Material(
                id=material_id,
                title=title,
                description=clean_text(row.get("description", "")),
                url=url,
                kind=MaterialKind.ARTICLE.value,
                access=access.value,
            )
            material.card()  # Проверка URL, если ссылка присутствует.
            if material_id in materials and materials[material_id] != material:
                raise ValueError("Разные карточки для одного material_id")
            materials[material_id] = material
            documents.append(Document(doc_id, material_id, text, "knowledge"))
            ids.add(doc_id)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise ValueError(f"{chunks.name}:{line_no}: {error}") from error
    if catalog:
        for line_no, row in _rows(catalog):
            if not _allowed(row, trusted_public=False):
                continue
            try:
                key = str(row["id"]).strip()
                if not key or key in materials:
                    raise ValueError("Пустой или повторяющийся id каталога")
                material = Material(
                    id=key,
                    title=clean_text(row["title"]),
                    description=clean_text(row.get("description", "")),
                    url=row["url"],
                    kind=MaterialKind(row.get("kind", "course")).value,
                    access=MaterialAccess(row.get("access", "unknown")).value,
                    price=row.get("price"),
                )
                if material.card() is None:
                    raise ValueError("Для рекомендации нужна ссылка")
                materials[key] = material
            except (KeyError, TypeError, ValueError, AttributeError) as error:
                raise ValueError(f"{catalog.name}:{line_no}: {error}") from error
    for material in materials.values():
        documents.append(
            Document(
                id="catalog:" + material.id,
                material_id=material.id,
                text=material.title + "\n" + material.description,
                purpose="catalog",
            )
        )
    if not documents:
        raise ValueError("Нет допущенных материалов для индекса.")
    return list(materials.values()), documents


async def build_index(
    *,
    chunks: Path,
    root: Path,
    catalog: Path | None = None,
    embeddings: Embeddings | None = None,
    article_url_template: str = "https://kurs.alfabank.ru/articles/{slug}/",
) -> dict:
    materials, documents = read_sources(chunks, catalog, article_url_template=article_url_template)
    root.mkdir(parents=True, exist_ok=True)
    snapshot = "snapshot-" + uuid4().hex
    folder = root / snapshot
    folder.mkdir()
    try:
        model_name = None
        if embeddings:
            mat = {item.id: item for item in materials}
            vectors = await embeddings.embed_documents(
                [mat[doc.material_id].title + "\n" + doc.text for doc in documents]
            )
            if (
                vectors.ndim != 2
                or vectors.shape[0] != len(documents)
                or not np.isfinite(vectors).all()
            ):
                raise ValueError("Некорректные векторы")
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            if np.any(norms == 0):
                raise ValueError("Нулевой вектор")
            np.save(folder / "vectors.npy", vectors / norms, allow_pickle=False)
            model_name = embeddings.model_name
        data = {
            "version": 1,
            "model_name": model_name,
            "materials": [m.to_dict() for m in materials],
            "documents": [asdict(d) for d in documents],
        }
        (folder / "index.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        pointer = root / ("CURRENT." + uuid4().hex + ".tmp")
        pointer.write_text(snapshot, encoding="utf-8")
        pointer.replace(root / "CURRENT")  # Старый снимок остаётся доступен до публикации.
    except BaseException:
        import shutil

        shutil.rmtree(folder)
        raise
    return {
        "materials": len(materials),
        "knowledge_chunks": sum(d.purpose == "knowledge" for d in documents),
        "catalog_documents": sum(d.purpose == "catalog" for d in documents),
        "backend": "hybrid" if embeddings else "bm25",
    }
