"""Вычисление моментов запуска задач и форматирование расписания."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

DAY_NAMES = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def parse_hhmm(value: str) -> time:
    hours, _, minutes = value.strip().partition(":")
    return time(int(hours), int(minutes or 0))


def latest_occurrence(at: str, days, now: datetime) -> datetime | None:
    """Последний момент запуска по расписанию, не позже `now`."""
    start = parse_hhmm(at)
    for back in range(8):
        day = now.date() - timedelta(days=back)
        if day.weekday() in days:
            moment = datetime.combine(day, start)
            if moment <= now:
                return moment
    return None


def next_occurrence(at: str, days, now: datetime) -> datetime | None:
    """Ближайший момент запуска по расписанию, строго после `now`."""
    start = parse_hhmm(at)
    for forward in range(8):
        day = now.date() + timedelta(days=forward)
        if day.weekday() in days:
            moment = datetime.combine(day, start)
            if moment > now:
                return moment
    return None


def format_days(days) -> str:
    selected = sorted(set(days))
    if selected == list(range(7)):
        return "Каждый день"
    if selected == list(range(5)):
        return "Будни"
    if selected == [5, 6]:
        return "Выходные"
    if not selected:
        return "—"
    return ", ".join(DAY_NAMES[d] for d in selected)


def format_duration(minutes: int) -> str:
    if minutes <= 0:
        return "Не закрывать"
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours} ч {mins} мин"
    if hours:
        return f"{hours} ч"
    return f"{mins} мин"


def format_moment(moment: datetime, today: date) -> str:
    delta = (moment.date() - today).days
    clock = moment.strftime("%H:%M")
    if delta == 0:
        return f"сегодня {clock}"
    if delta == 1:
        return f"завтра {clock}"
    if delta == -1:
        return f"вчера {clock}"
    return f"{DAY_NAMES[moment.weekday()]} {moment.strftime('%d.%m')} {clock}"
