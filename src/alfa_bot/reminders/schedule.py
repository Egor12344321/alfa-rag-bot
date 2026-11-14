from datetime import datetime

from alfa_bot.contracts import ReminderIntent


def next_occurrence(intent: ReminderIntent, *, after: datetime) -> datetime | None:
    """Python 3: once/daily/weekly по локальному календарю, выход aware UTC.

    Зафиксировать DST и политику пропусков. Не заменять календарное правило
    прибавлением 24 часов к UTC. Проверить время в прошлом и неизвестную зону.
    """
    raise NotImplementedError("Реализовать расчёт расписания")
