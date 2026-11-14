import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from alfa_bot.assistant.schemas import ReminderFields
from alfa_bot.contracts import ReminderIntent

_IN_MINUTES = re.compile(
    r"\bнапомни(?:те)?\s+через\s+([1-9][0-9]{0,3})\s+минут(?:у|ы)?\b", re.IGNORECASE,
)


def relative_minutes(text: str) -> int | None:
    match = _IN_MINUTES.search(text)
    return int(match[1]) if match else None


def resolve_relative_minutes(
    minutes: int, timezone: str | None, *, now: datetime,
) -> datetime | None:
    if not timezone:
        return None
    try:
        target = now.astimezone(UTC) + timedelta(minutes=minutes)
        return target.astimezone(ZoneInfo(timezone)).replace(tzinfo=None)
    except (ZoneInfoNotFoundError, ValueError, OverflowError):
        return None


def validate_reminder(fields: ReminderFields, *, now: datetime, material_url: str | None):
    """Не угадывает пропущенные дату/время/пояс; возвращает Intent или вопрос."""
    missing = []
    if not fields.text or not fields.text.strip():
        missing.append("о чём напомнить")
    if fields.local_datetime is None:
        missing.append("дату и время")
    if not fields.timezone:
        missing.append("часовой пояс (например, МСК)")
    if missing:
        return None, "Уточните, пожалуйста: " + ", ".join(missing) + "."
    local = fields.local_datetime
    if local.tzinfo is not None:
        return None, "Укажите местные дату и время, а часовой пояс — отдельно."
    try:
        zone = ZoneInfo(fields.timezone)
        candidate = local.replace(tzinfo=zone)
        utc = candidate.astimezone(UTC)
        if utc.astimezone(zone).replace(tzinfo=None) != local:
            return None, "Это время пропускается при переводе часов. Укажите другое время."
        if local.replace(tzinfo=zone, fold=1).utcoffset() != candidate.utcoffset():
            return None, "Это время встречается дважды при переводе часов. Укажите другое время."
        if utc <= now.astimezone(UTC):
            return None, "Указанное время уже прошло. На какую будущую дату и время напомнить?"
    except (ZoneInfoNotFoundError, ValueError, OverflowError):
        return None, "Уточните часовой пояс: например, МСК или Europe/Moscow."
    return ReminderIntent(
        text=fields.text.strip(),
        local_datetime=local,
        timezone=fields.timezone,
        frequency=fields.frequency or "once",
        material_url=material_url,
    ), None
