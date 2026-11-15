from calendar import monthrange
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SUPPORTED_FREQUENCIES = ("once", "daily", "weekly", "monthly")


class ScheduleError(ValueError):
    """Некорректные параметры или непредставимая дата расписания."""


def validate_frequency(frequency: str) -> None:
    if frequency not in SUPPORTED_FREQUENCIES:
        raise ScheduleError("Допустимые повторы: once, daily, weekly, monthly.")


def _get_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ScheduleError("Неизвестный часовой пояс.") from None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ScheduleError("Ожидалась дата со сведениями о часовом поясе.")
    try:
        return value.astimezone(UTC)
    except OverflowError:
        raise ScheduleError("Дата выходит за допустимый диапазон.") from None


def _local_to_utc(value: datetime, zone: ZoneInfo) -> datetime | None:
    # При осеннем переводе часов выбираем первое из двух одинаковых времён.
    local = value.replace(tzinfo=zone, fold=0)
    try:
        result = local.astimezone(UTC)
        restored = result.astimezone(zone).replace(tzinfo=None)
    except OverflowError:
        raise ScheduleError("Дата выходит за допустимый диапазон.") from None

    # Несуществующее местное время не проходит преобразование туда и обратно.
    if restored != value:
        return None
    return result


def calculate_first_run(*, local_datetime: datetime, timezone_name: str, now: datetime) -> datetime:
    """Проверить местную дату первого запуска и вернуть её в UTC."""
    if local_datetime.tzinfo is not None:
        raise ScheduleError("Передайте местные дату и время без tzinfo.")

    now_utc = _as_utc(now)
    zone = _get_timezone(timezone_name)
    first_run = _local_to_utc(local_datetime, zone)
    if first_run is None:
        raise ScheduleError("Такого местного времени нет из-за перевода часов.")
    if first_run <= now_utc:
        raise ScheduleError("Дата и время напоминания должны быть в будущем.")
    return first_run


def _occurrence(anchor: datetime, frequency: str, step: int) -> datetime:
    if frequency == "daily":
        return anchor + timedelta(days=step)
    if frequency == "weekly":
        return anchor + timedelta(weeks=step)

    # Каждый месяц считаем от исходной даты, чтобы 31-е не смещалось на 28-е.
    year, month_index = divmod(anchor.year * 12 + anchor.month - 1 + step, 12)
    month = month_index + 1
    day = min(anchor.day, monthrange(year, month)[1])
    return anchor.replace(year=year, month=month, day=day)


def calculate_next_run(
    *,
    starts_at: datetime,
    frequency: str,
    timezone_name: str,
    after: datetime,
) -> datetime | None:
    """Найти срабатывание строго после after; вернуть UTC или None для once.

    starts_at — неизменная опорная дата первого запуска.
    Повторы с несуществующим местным временем пропускаются.
    Неоднозначное местное время используется один раз, с fold=0.
    """
    validate_frequency(frequency)
    start_utc = _as_utc(starts_at)
    after_utc = _as_utc(after)
    zone = _get_timezone(timezone_name)

    if start_utc > after_utc:
        return start_utc
    if frequency == "once":
        return None

    try:
        anchor = start_utc.astimezone(zone).replace(tzinfo=None)
        after_local = after_utc.astimezone(zone).replace(tzinfo=None)
    except OverflowError:
        raise ScheduleError("Дата выходит за допустимый диапазон.") from None

    # Сразу переходим к нужному периоду, не перебирая все пропущенные даты.
    days = (after_local.date() - anchor.date()).days
    if frequency == "daily":
        step = max(0, days)
    elif frequency == "weekly":
        step = max(0, days // 7)
    else:
        months = (after_local.year - anchor.year) * 12 + after_local.month - anchor.month
        step = max(0, months)

    while True:
        try:
            candidate = _occurrence(anchor, frequency, step)
        except (OverflowError, ValueError):
            raise ScheduleError("Следующая дата выходит за допустимый диапазон.") from None
        candidate_utc = _local_to_utc(candidate, zone)
        if candidate_utc is not None and candidate_utc > after_utc:
            return candidate_utc
        step += 1
