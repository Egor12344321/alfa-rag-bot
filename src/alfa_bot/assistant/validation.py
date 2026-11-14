from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from alfa_bot.assistant.schemas import ReminderFields
from alfa_bot.contracts import ReminderIntent


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
