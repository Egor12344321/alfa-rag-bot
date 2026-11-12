"""Shared boundary between source extraction, indexing, and retrieval."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID

AccessType = Literal["free", "paid", "subscription", "unknown"]
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


@dataclass(frozen=True)
class SourceKey:
    source_table: str
    source_id: str


@dataclass(frozen=True)
class Evidence:
    chunk_id: UUID
    material_id: UUID
    source: SourceKey
    title: str
    text: str
    url: str | None
    distance: float


@dataclass(frozen=True)
class SearchResult:
    run_id: UUID
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class Profile:
    """Recommendation-only preference fields; no access is inferred from them."""

    stage: str | None = None
    industry: str | None = None
    experience: str | None = None
    current_goal: str | None = None
    tax_regime: str | None = None
    free_only: bool = False


@dataclass(frozen=True)
class MaterialCard:
    """A short, access-screened public catalog card."""

    id: UUID
    run_id: UUID
    source: SourceKey
    kind: str
    title: str
    public_summary: str
    url: str
    access_type: AccessType
    price: Decimal | None = None
    currency: str | None = None
    price_note: str | None = None
    reason_features: tuple[str, ...] = ()


class Knowledge(Protocol):
    async def retrieve(
        self,
        query: str,
        *,
        limit: int = 6,
        run_id: UUID | None = None,
        external_context: bool = True,
    ) -> SearchResult: ...

    async def recommend(
        self,
        query: str,
        profile: Profile,
        *,
        run_id: UUID,
        exclude: tuple[SourceKey, ...] = (),
        limit: int = 1,
        source: SourceKey | None = None,
    ) -> tuple[MaterialCard, ...]: ...

    async def get_card(
        self, source: SourceKey, *, run_id: UUID | None = None
    ) -> MaterialCard | None: ...
