"""Shared boundary between source extraction, indexing, and retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

from alfa_bot.domain import AccessType as AccessType
from alfa_bot.domain import Evidence as Evidence
from alfa_bot.domain import MaterialCard as MaterialCard
from alfa_bot.domain import Profile as Profile
from alfa_bot.domain import SearchResult as SearchResult
from alfa_bot.domain import SourceKey as SourceKey

MaterialKind = Literal["article", "document", "term", "course", "webinar"]


@dataclass(frozen=True)
class TopicInput:
    """A source topic that has been independently cleared for publication."""

    source_id: str
    title: str


@dataclass(frozen=True)
class SourceBlock:
    """An ordered source content block; content may contain HTML from the dump."""

    block_id: str
    order: int
    content: str


@dataclass(frozen=True)
class MaterialInput:
    """Normalized, policy-screened source record for one ingestion run."""

    source_table: str
    source_id: str
    kind: MaterialKind
    title: str
    url: str | None
    blocks: tuple[SourceBlock, ...]
    access_type: AccessType = "unknown"
    summary: str = ""
    answer_allowed: bool = False
    recommend_allowed: bool = False
    external_allowed: bool = False
    source_modified_at: datetime | None = None
    provenance: dict[str, object] = field(default_factory=dict)
    price: Decimal | None = None
    currency: str | None = None
    price_note: str | None = None
    reason_features: tuple[str, ...] = ()
    topics: tuple[TopicInput, ...] = ()
    recommendations: tuple[SourceKey, ...] = ()


@dataclass(frozen=True)
class ChunkInput:
    """A bounded text fragment that retains its source block lineage."""

    chunk_no: int
    text: str
    section_path: str | None
    source_blocks: tuple[str, ...]
    token_count: int
    purpose: Literal["knowledge", "catalog"] = "knowledge"
