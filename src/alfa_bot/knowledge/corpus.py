"""Подготовка опубликованных материалов к индексации."""

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal

from bs4 import BeautifulSoup
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from alfa_bot.contracts import MaterialAccess, MaterialKind
from alfa_bot.knowledge.ingestion import _rows
from alfa_bot.knowledge.models import Document, Material
from alfa_bot.knowledge.parsing import clean_text

SourceType = Literal["article", "document", "glossary", "course"]


class PublishedSource(BaseModel):
    """Строка подготовленного JSONL или результат выборки из дампа."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_id: str = Field(min_length=1, max_length=150)
    source_type: SourceType
    title: str = Field(min_length=1)
    description: str = ""
    body: str = ""
    url: str | None = None
    access: MaterialAccess
    price: str | None = None
    is_active: StrictBool = False
    is_public: StrictBool = False
    is_closed: StrictBool = False


@dataclass(frozen=True)
class CorpusChunk:
    document: Document
    material: Material


def read_published_jsonl(path: Path) -> list[PublishedSource]:
    result = []
    for line, row in _rows(path):
        try:
            result.append(PublishedSource.model_validate(row))
        except ValidationError as error:
            fields = ", ".join(
                ".".join(str(item) for item in problem["loc"]) for problem in error.errors()
            )
            raise ValueError(f"{path.name}:{line}: проверьте поля {fields}") from None
    return result


def plain_text(value: str) -> str:
    if re.search(r"<(?:/?[A-Za-z][^>]*|![^>]*)>", value):
        soup = BeautifulSoup(value, "html.parser")
        for tag in soup.select("script,style,noscript,template"):
            tag.decompose()
        for tag in soup.select("h1,h2,h3,h4,h5,h6,p,div,li,br,tr"):
            tag.insert_after("\n")
        value = soup.get_text(" ")
    lines = (line.strip() for line in clean_text(value).splitlines())
    return "\n".join(filter(None, lines))


def prepare_corpus(
    sources: list[PublishedSource], *, chunk_size: int, overlap: int
) -> list[CorpusChunk]:
    if not 0 <= overlap < chunk_size:
        raise ValueError("Перекрытие должно быть меньше размера чанка.")
    splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name="o200k_base",
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        disallowed_special=(),
    )
    result = []
    seen = set()
    kinds = {"course": MaterialKind.COURSE, "article": MaterialKind.ARTICLE}
    for source in sorted(sources, key=lambda item: (item.source_type, item.source_id)):
        if not source.is_active or not source.is_public or source.is_closed:
            continue
        title = plain_text(source.title)
        if not title or (source.source_type == "course" and not source.url):
            continue
        key = f"{source.source_type}:{source.source_id}"
        if key in seen:
            raise ValueError(f"Повтор ID материала: {key}")
        seen.add(key)
        description = plain_text(source.description)
        material = Material(
            id=key,
            title=title,
            description=description[:600],
            url=source.url,
            kind=kinds.get(source.source_type, MaterialKind.OTHER).value,
            access=source.access.value,
            price=source.price,
            source_type=source.source_type,
            source_id=source.source_id,
        )
        if material.card() is not None:
            catalog_text = "\n".join(filter(None, (title, material.description)))
            result.append(
                CorpusChunk(Document(f"catalog:{key}", key, catalog_text, "catalog"), material)
            )

        # Курсы и материалы с непроверенным доступом дают только карточки.
        if source.source_type == "course" or source.access != MaterialAccess.FREE:
            continue
        body = plain_text(source.body)
        if not body and not description:
            continue
        parts = splitter.split_text("\n\n".join(filter(None, (title, description, body))))
        result.extend(
            CorpusChunk(Document(f"{key}:{i}", key, part, "knowledge"), material)
            for i, part in enumerate(parts)
        )
    if not result:
        raise ValueError("Нет допущенных публичных материалов для индекса.")
    return result


def chunk_metadata(material: Material) -> dict:
    return {**material.to_dict(), "is_paid": material.access == MaterialAccess.PAID}


def chunk_fingerprint(chunks: list[CorpusChunk], model_name: str) -> str:
    digest = sha256(model_name.encode("utf-8"))
    for chunk in chunks:
        data = [
            chunk.document.id,
            chunk.document.purpose,
            chunk.document.text,
            chunk_metadata(chunk.material),
        ]
        digest.update(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()
