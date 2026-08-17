"""A tiny 5-field cron parser — just enough for `slipbox_schedule`.

The plugin does not run the schedule (the host's cron does); it only needs to
report when the three configured jobs fire next.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}


class InvalidCron(ValueError):
    pass


def _value(token: str) -> int:
    token = token.strip().lower()
    if token in _NAMES:
        return _NAMES[token]
    try:
        return int(token)
    except ValueError as exc:
        raise InvalidCron(f"bad cron value: {token!r}") from exc


def _field(spec: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise InvalidCron("empty cron field")
        step = 1
        if "/" in part:
            part, _, raw_step = part.partition("/")
            step = _value(raw_step)
            if step <= 0:
                raise InvalidCron(f"bad cron step: {raw_step!r}")
        if part in ("*", "?"):
            start, end = low, high
        elif "-" in part.lstrip("-"):
            start_raw, _, end_raw = part.partition("-")
            start, end = _value(start_raw), _value(end_raw)
        else:
            start = end = _value(part)
        if start > end or start < low or end > high:
            raise InvalidCron(f"cron field out of range: {part!r}")
        values.update(range(start, end + 1, step))
    return values


def parse(expression: str) -> tuple[set[int], ...]:
    parts = (expression or "").split()
    if len(parts) != 5:
        raise InvalidCron(f"expected 5 cron fields, got {len(parts)}: {expression!r}")
    return tuple(_field(p, low, high) for p, (low, high) in zip(parts, FIELD_RANGES))


def _day_matches(day: date, doms: set[int], months: set[int], dows: set[int]) -> bool:
    if day.month not in months:
        return False
    dom_restricted = doms != set(range(1, 32))
    dow_restricted = dows != set(range(0, 7))
    dow = (day.weekday() + 1) % 7  # cron: Sunday = 0
    if dom_restricted and dow_restricted:
        return day.day in doms or dow in dows
    if dom_restricted:
        return day.day in doms
    if dow_restricted:
        return dow in dows
    return True


def next_run(expression: str, after: datetime | None = None,
             horizon_days: int = 400) -> datetime | None:
    """First firing strictly after `after` (default: now), or None within horizon."""
    minutes, hours, doms, months, dows = parse(expression)
    start = (after or datetime.now()).replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = start.date()
    for _ in range(horizon_days):
        if _day_matches(day, doms, months, dows):
            for hour in sorted(hours):
                for minute in sorted(minutes):
                    candidate = datetime.combine(day, time(hour, minute))
                    if candidate >= start:
                        return candidate
        day += timedelta(days=1)
    return None


def describe(expression: str) -> dict:
    """Expression plus its next firing — or the parse error, never raising."""
    try:
        upcoming = next_run(expression)
    except InvalidCron as exc:
        return {"cron": expression, "error": str(exc)}
    return {
        "cron": expression,
        "next_run": upcoming.isoformat(timespec="minutes") if upcoming else None,
    }
