from dataclasses import field
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import ConfigDict
from pydantic.dataclasses import dataclass

DTO_CONFIG = ConfigDict(extra="forbid", strict=True)


class ReplyType(StrEnum):
    ANSWER = "answer"
    REMINDER = "reminder"
    CLARIFICATION = "clarification"
    OUT_OF_SCOPE = "out_of_scope"


# Вид материала для подписи на карточке в Telegram.
class MaterialKind(StrEnum):
    ARTICLE = "article"
    COURSE = "course"
    OTHER = "other"


# Условия доступа из каталога; UNKNOWN означает, что достоверных сведений нет.
class MaterialAccess(StrEnum):
    FREE = "free"
    PAID = "paid"
    UNKNOWN = "unknown"


@dataclass(frozen=True, config=DTO_CONFIG)
class MaterialCard:
    """Материал из каталога, а не ссылка, придуманная LLM.

    description объясняет, чем полезен материал. price — строка из каталога
    с валютой; None, если достоверной цены нет. UNKNOWN не означает FREE.
    """

    title: str  # Название материала из каталога.
    url: str
    kind: MaterialKind
    access: MaterialAccess = MaterialAccess.UNKNOWN
    description: str = ""
    price: str | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("У материала должно быть название.")
        try:
            parsed = urlsplit(self.url)
            valid_url = (
                parsed.scheme in {"http", "https"}
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and not any(char.isspace() for char in self.url)
            )
            parsed.port  # Также проверяем корректность порта.
        except ValueError:
            valid_url = False
        if not valid_url:
            raise ValueError("Ссылка на материал должна быть HTTP(S) URL без логина и пароля.")
        if self.price is not None and not self.price.strip():
            raise ValueError("Неизвестную цену передавайте как None.")


@dataclass(frozen=True, config=DTO_CONFIG)
class Answer:
    """Метка [1] в тексте указывает на первый элемент sources."""

    text: str
    sources: list[MaterialCard] = field(default_factory=list)
    recommendations: list[MaterialCard] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Ответ должен содержать текст.")


# Полные данные для подготовки черновика; assistant сам напоминание в БД не создаёт.
@dataclass(frozen=True, config=DTO_CONFIG)
class ReminderIntent:
    """Полностью собранные данные: местная дата без tzinfo и часовой пояс IANA."""

    text: str  # О чём напомнить пользователю.
    local_datetime: datetime  # Местные дата и время без tzinfo.
    timezone: str  # Часовой пояс IANA, например Europe/Moscow.
    frequency: str = "once"  # once / daily / weekly / monthly.
    material_url: str | None = None  # Необязательная ссылка в сообщении-напоминании.


# Единый результат обработки сообщения; комбинацию полей проверяет __post_init__.
@dataclass(frozen=True, config=DTO_CONFIG)
class Reply:
    """Результат assistant; заполняется только полезная нагрузка выбранного типа.

    ANSWER: answer; REMINDER: reminder; CLARIFICATION: text.
    OUT_OF_SCOPE: только type, стандартную подсказку показывает Telegram.
    Если данных для напоминания не хватает, возвращается CLARIFICATION.
    REMINDER возвращается после сбора всех полей; запись в БД делает бот
    только после подтверждения пользователем.
    """

    type: ReplyType
    text: str = ""  # Заполняется только для CLARIFICATION.
    answer: Answer | None = None  # Заполняется только для ANSWER.
    reminder: ReminderIntent | None = None  # Заполняется только для REMINDER.

    def __post_init__(self) -> None:
        if self.type == ReplyType.ANSWER:
            valid = self.answer is not None and self.reminder is None and not self.text
        elif self.type == ReplyType.REMINDER:
            valid = self.reminder is not None and self.answer is None and not self.text
        elif self.type == ReplyType.CLARIFICATION:
            valid = bool(self.text.strip()) and self.answer is None and self.reminder is None
        else:
            valid = not self.text and self.answer is None and self.reminder is None
        if not valid:
            raise ValueError(f"Поля Reply не соответствуют типу {self.type.value}.")


# Интерфейс assistant: бот вызывает handle через DI, получая готовый Reply.
class AssistantPort(Protocol):
    async def handle(
        self,
        *,
        telegram_user_id: int,  # Telegram ID отправителя, не UUID из общей БД.
        text: str,  # Текст текущего сообщения пользователя.
        now: datetime,  # Текущее время в UTC с tzinfo для разбора относительных дат.
    ) -> Reply:
        """now — текущее время с часовым поясом, передаваемое ботом в UTC.

        Классификация, поиск и контекст уточнений принадлежат assistant.
        telegram_user_id — ключ диалога в текущем режиме личных сообщений;
        это не UUID пользователя из общей БД. Telegram не хранит историю для LLM.
        """
        ...
