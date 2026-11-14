"""DTO базы знаний и ошибка недоступного индекса."""

from dataclasses import asdict

from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass

from alfa_bot.contracts import MaterialAccess, MaterialCard, MaterialKind


class KnowledgeUnavailable(RuntimeError):
    """Индекс или модель временно недоступны."""


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class Material:
    id: str
    title: str
    description: str
    url: str | None
    kind: str
    access: str
    price: str | None = None
    source_type: str | None = None
    source_id: str | None = None

    def card(self) -> MaterialCard | None:
        if not self.url:
            return None
        return MaterialCard(
            title=self.title,
            url=self.url,
            kind=MaterialKind(self.kind),
            access=MaterialAccess(self.access),
            description=self.description,
            price=self.price,
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class Document:
    id: str
    material_id: str
    text: str
    purpose: str


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class SearchHit:
    document: Document
    material: Material
    score: float = Field(allow_inf_nan=False)
