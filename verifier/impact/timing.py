"""
When does a part run out, and by when must someone act?

Packing time comes from the launch plan's shift table ('Saturday 06:00–22:00'), placed on the
days before launch. 'Now' is the time of the newest stock figures, not the machine clock, so the
answer is the same every time the same data is read. All times are the DC's clock as written.
"""

import re
from datetime import date, datetime, timedelta

_TIMES = re.compile(r"(\d{1,2}):(\d{2})\s*[–—-]\s*(\d{1,2}):(\d{2})")
_HOURS = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:h\b|hrs?\b|hours?\b|godz)", re.I)
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_moment(value: str | None) -> datetime | None:
    """An ISO timestamp from the records (always UTC 'Z' form) as a naive datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "")).replace(tzinfo=None)
    except ValueError:
        return None


def shift_windows(shifts: list[dict], launch_date: str | None) -> list[tuple[datetime, datetime]]:
    """
    Packing windows from rows like {'day': 'Saturday', 'shift': '06:00–22:00'}: each weekday is
    placed on its date within the 7 days up to and including launch day.
    """
    if not launch_date:
        return []
    launch = date.fromisoformat(launch_date)
    windows = []
    for shift in shifts:
        day = (shift.get("day") or "").strip().lower()
        match = _TIMES.search(shift.get("shift") or "")
        if day not in WEEKDAYS or not match:
            continue
        offset = (launch.weekday() - WEEKDAYS.index(day)) % 7
        on = launch - timedelta(days=offset)
        h1, m1, h2, m2 = map(int, match.groups())
        start = datetime(on.year, on.month, on.day, h1, m1)
        end = datetime(on.year, on.month, on.day, h2, m2)
        windows.append((start, end))
    return sorted(windows)


def hours_left(windows: list[tuple[datetime, datetime]], now: datetime) -> float:
    return sum(max((end - max(start, now)).total_seconds(), 0) for start, end in windows) / 3600


def runs_dry(
    windows: list[tuple[datetime, datetime]], now: datetime, packing_hours: float
) -> datetime | None:
    """The moment `packing_hours` of packing from `now` are used up; None if they last."""
    left = packing_hours
    for start, end in windows:
        begin = max(start, now)
        if begin >= end:
            continue
        available = (end - begin).total_seconds() / 3600
        if left <= available:
            return begin + timedelta(hours=left)
        left -= available
    return None


def lead_hours(texts: list[str]) -> float | None:
    """
    Total hours named in lead-time texts: 'loaded within 4 h' + 'about 9 hours driving'.

    Each text counts once, with its largest figure: bilingual documents state the same hours
    twice ('w 4 h / within 4 h').
    """
    per_text = []
    for text in texts:
        found = [float(m.replace(",", ".")) for m in _HOURS.findall(text or "")]
        if found:
            per_text.append(max(found))
    return sum(per_text) if per_text else None


def fmt(moment: datetime | None) -> str:
    return moment.strftime("%a %d %b %H:%M") if moment else "—"
