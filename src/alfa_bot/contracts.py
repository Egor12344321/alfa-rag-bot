from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit


# ============================================================================
# Контракты интеграции Telegram-бота с assistant.
# Разработчик assistant реализует AssistantPort и возвращает объекты этих типов.
# Telegram-адаптер отображает Reply и передаёт ReminderIntent в ReminderService.
# Проверки __post_init__ отклоняют некорректные данные с ValueError.
# ============================================================================

# Вариант результата: определяет, какое поле Reply заполняет assistant.
class ReplyType(StrEnum):
    ANSWER = "answer"                  # Готовый ответ на вопрос: Reply.answer.
    REMINDER = "reminder"              # Собранное напоминание: Reply.reminder.
    CLARIFICATION = "clarification"    # Нужно уточнение: вопрос в Reply.text.
    OUT_OF_SCOPE = "out_of_scope"      # Сообщение вне темы: бот покажет подсказку.


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


# Данные одной карточки источника или рекомендации с кнопкой открытия.
@dataclass(frozen=True)
class MaterialCard:
    """Материал из каталога, а не ссылка, придуманная LLM.

    description объясняет, чем полезен материал. price — строка из каталога
    с валютой; None, если достоверной цены нет. UNKNOWN не означает FREE.
    """

    title: str  # Название материала из каталога.
    url: str  # HTTP(S) ссылка, которую бот поместит в кнопку.
    kind: MaterialKind
    access: MaterialAccess = MaterialAccess.UNKNOWN
    description: str = ""
    price: str | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("У материала должно быть название.")
        if not isinstance(self.kind, MaterialKind) or not isinstance(self.access, MaterialAccess):
            raise ValueError("Используйте MaterialKind и MaterialAccess.")
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


# Содержимое ответа на вопрос: текст, использованные источники и рекомендации.
@dataclass(frozen=True)
class Answer:
    """Готовый текст; sources использованы в ответе, recommendations — для изучения.

    Вопрос по теме без найденных материалов остаётся ANSWER: assistant сообщает
    об отсутствии данных в text и возвращает пустые списки материалов.
    """

    text: str  # Непустой текст ответа без Telegram-разметки.
    sources: list[MaterialCard] = field(default_factory=list)  # На чём основан ответ.
    recommendations: list[MaterialCard] = field(default_factory=list)  # Что изучить дальше.

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Ответ должен содержать текст.")
        if any(not isinstance(item, MaterialCard) for item in [*self.sources, *self.recommendations]):
            raise ValueError("Материалы передавайте как MaterialCard.")


# Полные данные для подготовки черновика; assistant сам напоминание в БД не создаёт.
@dataclass(frozen=True)
class ReminderIntent:
    """Полностью собранные данные: местная дата без tzinfo и часовой пояс IANA."""

    text: str  # О чём напомнить пользователю.
    local_datetime: datetime  # Местные дата и время без tzinfo.
    timezone: str  # Часовой пояс IANA, например Europe/Moscow.
    frequency: str = "once"  # once / daily / weekly / monthly.
    material_url: str | None = None  # Необязательная ссылка в сообщении-напоминании.


# Единый результат обработки сообщения; комбинацию полей проверяет __post_init__.
@dataclass(frozen=True)
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
        if not isinstance(self.type, ReplyType):
            raise ValueError("Используйте значение ReplyType.")
        if self.type == ReplyType.ANSWER:
            valid = isinstance(self.answer, Answer) and self.reminder is None and not self.text
        elif self.type == ReplyType.REMINDER:
            valid = isinstance(self.reminder, ReminderIntent) and self.answer is None and not self.text
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


# ================= Конец контрактов интеграции с assistant ===================
