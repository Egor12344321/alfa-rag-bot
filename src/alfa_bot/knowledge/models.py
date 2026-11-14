"""Внутренние типы knowledge. Telegram использует только MaterialCard."""

from dataclasses import asdict, dataclass

from alfa_bot.contracts import MaterialAccess, MaterialCard, MaterialKind


@dataclass(frozen=True)
class Material:
    id: str
    title: str
    description: str
    url: str | None
    kind: str
    access: str
    price: str | None = None

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


@dataclass(frozen=True)
class Document:
    id: str
    material_id: str
    text: str
    purpose: str  # knowledge — текст для ответа, catalog — только рекомендации.


@dataclass(frozen=True)
class SearchHit:
    document: Document
    material: Material
    score: float
