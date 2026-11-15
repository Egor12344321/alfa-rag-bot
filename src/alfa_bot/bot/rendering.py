"""Безопасный Telegram HTML: ссылки только из проверенных DTO материалов."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from alfa_bot.contracts import Answer, ReminderDraft, ReminderView


@dataclass(frozen=True)
class Part:
    text: str
    url: str | None = None


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _take(text: str, units: int) -> tuple[str, str]:
    used = 0
    end = 0
    for char in text:
        size = 2 if ord(char) > 0xFFFF else 1
        if used + size > units:
            break
        used += size
        end += 1
    if end < len(text):
        boundary = max(text.rfind("\n", 0, end), text.rfind(" ", 0, end))
        if boundary >= max(1, end // 2):
            end = boundary + 1
    return text[:end], text[end:]


def html_chunks(parts: list[Part], *, limit: int = 3900) -> list[str]:
    """Разбиваем видимый текст, не разрезая теги, сущности или emoji."""
    if limit < 2:
        raise ValueError("Лимит должен вмещать хотя бы один emoji")
    chunks = []
    current = []
    length = 0
    for part in parts:
        remaining = part.text
        while remaining:
            # Обычная ссылка переносится целиком, если не помещается в остаток.
            if (
                part.url
                and utf16_length(remaining) <= limit
                and (length and length + utf16_length(remaining) > limit)
            ):
                chunks.append("".join(current))
                current, length = [], 0
            prefix, tail = _take(remaining, limit - length)
            if not prefix:
                chunks.append("".join(current))
                current, length = [], 0
                continue
            rendered = escape(prefix)
            if part.url:
                rendered = f'<a href="{escape(part.url, quote=True)}">{rendered}</a>'
            current.append(rendered)
            length += utf16_length(prefix)
            remaining = tail
            if remaining or length == limit:
                chunks.append("".join(current))
                current, length = [], 0
    if current:
        chunks.append("".join(current))
    return chunks


def answer_parts(answer: Answer) -> list[Part]:
    result = []
    position = 0
    for marker in re.finditer(r"\[([1-9][0-9]*)\]", answer.text):
        result.append(Part(answer.text[position : marker.start()]))
        number = int(marker.group(1))
        url = answer.sources[number - 1].url if number <= len(answer.sources) else None
        result.append(Part(marker.group(0), url))
        position = marker.end()
    result.append(Part(answer.text[position:]))
    return result


def recommendation_parts(recommendation) -> list[Part]:
    material = recommendation.material
    kind = "Курс" if material.source_type == "course" else "Материал"
    access = (
        "Платно"
        if material.is_paid is True
        else "Бесплатно"
        if material.is_paid is False
        else "Условия доступа не указаны"
    )
    if material.price:
        access += " · " + material.price
    return [
        Part(f"{kind} по вашей задаче: "),
        Part(material.title, material.url),
        Part(f"\n{recommendation.reason}\n{access}"),
    ]


FREQUENCIES = {
    "once": "один раз",
    "daily": "ежедневно",
    "weekly": "еженедельно",
    "monthly": "ежемесячно",
}
STATUSES = {"active": "включено", "paused": "отключено", "done": "выполнено"}


def _local(value: datetime, timezone: str) -> datetime:
    try:
        return value.astimezone(ZoneInfo(timezone))
    except (ZoneInfoNotFoundError, ValueError):
        return value.astimezone(UTC)


def draft_parts(draft: ReminderDraft) -> list[Part]:
    local = _local(draft.starts_at, draft.timezone)
    result = [
        Part(
            f"Создать напоминание?\n\n{draft.text}\n"
            f"Когда: {local:%d.%m.%Y %H:%M} ({draft.timezone})\n"
            f"Повтор: {FREQUENCIES.get(draft.frequency, draft.frequency)}\n\n"
            "Нажмите «Подтвердить», чтобы сохранить."
        )
    ]
    if draft.material_url:
        result.extend([Part("\nМатериал: "), Part("открыть", draft.material_url)])
    return result


def reminder_parts(reminder: ReminderView, notice: str = "") -> list[Part]:
    local = _local(reminder.next_run_at, reminder.timezone)
    text = (notice + "\n\n") if notice else ""
    text += (
        f"{reminder.text}\n"
        f"Ближайшее время: {local:%d.%m.%Y %H:%M} ({reminder.timezone})\n"
        f"Повтор: {FREQUENCIES.get(reminder.frequency, reminder.frequency)}\n"
        f"Статус: {STATUSES.get(reminder.status, reminder.status)}"
    )
    result = [Part(text)]
    if reminder.material_url:
        result.extend([Part("\nМатериал: "), Part("открыть", reminder.material_url)])
    return result
