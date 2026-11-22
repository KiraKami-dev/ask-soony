"""Packing clock: shift windows, pack rates and remaining copies, read from the portal files.

The launch plan workbook holds a shift table (rows like 'Saturday | 06:00–22:00') and an editions
table (product, packed, rate per hour). Sheets and columns are found by their headers, so a new
version of the plan is read without code changes. Packed copies come from the pack-out log when it
is newer than the plan. Nothing about Soony is written here.

A packed figure carries the time it was counted when its sheet states one ('status 2026-11-20
18:00', 'Saturday 13:00') or, for the pack-out log, the end of its last day. From then on the
lines are assumed to pack at their plan rate inside shifts (the plan's own assumption), so a status
from Saturday noon still says how far the cell is on Sunday. Parts shortfalls are separate: they
stop a line early (line_stops).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_TIMES = re.compile(r"(\d{1,2}):(\d{2})\s*[–—-]\s*(\d{1,2}):(\d{2})")
_STAMP_ISO = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{1,2}):(\d{2})")
_STAMP_DAY = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+(\d{1,2}):(\d{2})", re.I)
# The challenge brief's shifts, used only when no plan workbook states any: no night shift.
DEFAULT_SHIFTS = [("saturday", "06:00–22:00"), ("sunday", "06:00–20:00")]
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@dataclass
class EditionPlan:
    product: str
    label: str | None = None
    day_one: float | None = None
    packed: float | None = None
    rate_per_hour: float | None = None
    packed_as_of: datetime | None = None  # when `packed` was counted, if a source says so

    @property
    def remaining(self) -> float | None:
        if self.day_one is None or self.packed is None:
            return None
        return max(self.day_one - self.packed, 0)


@dataclass
class PackClock:
    windows: list[tuple[datetime, datetime]] = field(default_factory=list)
    editions: dict[str, EditionPlan] = field(default_factory=dict)

    @property
    def launch(self) -> datetime | None:
        """Trucks leave when the last shift ends."""
        return self.windows[-1][1] if self.windows else None

    def hours_left(self, now: datetime) -> float:
        return sum(max((e - max(s, now)).total_seconds(), 0) for s, e in self.windows) / 3600

    def shift_hours_between(self, a: datetime, b: datetime) -> float:
        return sum(max((min(e, b) - max(s, a)).total_seconds(), 0) for s, e in self.windows) / 3600

    def _base(self, now: datetime, ed: EditionPlan) -> datetime:
        """Where projections start: the time the packed figure was counted, if earlier than now."""
        return ed.packed_as_of if ed.packed_as_of and ed.packed_as_of < now else now

    def remaining_at(self, now: datetime, product: str | None) -> float | None:
        """Copies still to pack at `now`: the counted figure, less the plan rate for the shift
        hours since it was counted."""
        ed = self.editions.get(product or "")
        if not ed or ed.remaining is None:
            return None
        if not ed.rate_per_hour:
            return ed.remaining
        return max(ed.remaining - ed.rate_per_hour * self.shift_hours_between(self._base(now, ed), now), 0)

    def finishes(self, now: datetime, product: str | None) -> datetime | None:
        """When the last copy is packed at plan rate; None if the shifts run out first."""
        ed = self.editions.get(product or "")
        if not ed or not ed.rate_per_hour or ed.remaining is None:
            return None
        base = self._base(now, ed)
        done = self.after_packing_hours(base, ed.remaining / ed.rate_per_hour)
        return max(done, now) if done else None

    def after_packing_hours(self, now: datetime, hours: float) -> datetime | None:
        """Wall-clock time once `hours` of packing from `now` are used up; None if they outlast
        the last shift. Night gaps between shifts are skipped."""
        left = max(hours, 0.0)
        for start, end in self.windows:
            begin = max(start, now)
            if begin >= end:
                continue
            avail = (end - begin).total_seconds() / 3600
            if left <= avail:
                return begin + timedelta(hours=left)
            left -= avail
        return None

    def line_stops(self, now: datetime, product: str | None, copies_short: float | None):
        """When a line runs out if `copies_short` of the remaining copies cannot be packed: it packs
        the others at its plan rate, inside shifts, then stops. None if nothing is short or the
        rate is unknown."""
        ed = self.editions.get(product or "")
        if not ed or not copies_short or not ed.rate_per_hour or ed.remaining is None:
            return None
        packable = max(ed.remaining - copies_short, 0)
        return self.after_packing_hours(self._base(now, ed), packable / ed.rate_per_hour)

    def slack_hours(self, now: datetime, product: str | None) -> float | None:
        ed = self.editions.get(product or "")
        left = self.remaining_at(now, product)
        if not ed or not ed.rate_per_hour or left is None:
            return None
        return round(self.hours_left(now) - left / ed.rate_per_hour, 1)


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "")
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):  # '42.900' = 42,900
        s = s.replace(".", "")
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _rows(path: Path) -> dict[str, list[tuple]]:
    try:
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception:  # unreadable or missing workbook: no plan
        return {}
    out = {ws.title: [r for r in ws.iter_rows(values_only=True) if any(c is not None for c in r)]
           for ws in wb.worksheets}
    wb.close()
    return out


def _table(rows: list[tuple], *needles: str) -> tuple[dict[str, int], list[tuple]] | None:
    """The first header row that names every needle, with the rows below it."""
    for i, r in enumerate(rows):
        cells = [str(c or "").strip().lower() for c in r]
        if all(any(n in c for c in cells) for n in needles):
            return {c: j for j, c in enumerate(cells) if c}, rows[i + 1:]
    return None


def _col(header: dict[str, int], *needles: str) -> int | None:
    return next((j for c, j in header.items() if all(n in c for n in needles)), None)


def _stamp(text: str, launch: date | None, tz: ZoneInfo) -> datetime | None:
    """'status 2026-11-20 18:00' or 'Saturday 13:00' (the weekday of launch week) in a title."""
    m = _STAMP_ISO.search(text)
    if m:
        d = date.fromisoformat(m.group(1))
        return datetime(d.year, d.month, d.day, int(m.group(2)), int(m.group(3)), tzinfo=tz)
    m = _STAMP_DAY.search(text)
    if m and launch:
        on = launch - timedelta(days=(launch.weekday() - WEEKDAYS.index(m.group(1).lower())) % 7)
        return datetime(on.year, on.month, on.day, int(m.group(2)), int(m.group(3)), tzinfo=tz)
    return None


def default_windows(launch: date, tz: ZoneInfo) -> list[tuple[datetime, datetime]]:
    return _windows([("Day", "Shift"), *DEFAULT_SHIFTS], launch, tz)


def _windows(rows: list[tuple], launch: date | None, tz: ZoneInfo):
    found = _table(rows, "day", "shift")
    if not found or not launch:
        return []
    header, body = found
    dcol, scol = _col(header, "day"), _col(header, "shift")
    out = []
    for r in body:
        day = str(r[dcol] or "").strip().lower() if dcol is not None else ""
        m = _TIMES.search(str(r[scol] or "")) if scol is not None else None
        if day not in WEEKDAYS or not m:
            continue
        on = launch - timedelta(days=(launch.weekday() - WEEKDAYS.index(day)) % 7)
        h1, m1, h2, m2 = map(int, m.groups())
        start = datetime(on.year, on.month, on.day, h1, m1, tzinfo=tz)
        end = datetime(on.year, on.month, on.day, h2, m2, tzinfo=tz)
        out.append((start, end if end > start else end + timedelta(days=1)))
    return sorted(out)


def _read_csv(path: Path) -> list[dict]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def _set_packed(ed: EditionPlan, n: float, at: datetime | None) -> None:
    """Packed copies only grow, so the highest figure is the newest; it keeps its own stamp."""
    if ed.packed is None or n > ed.packed:
        ed.packed, ed.packed_as_of = n, at
    elif n == ed.packed and at and (ed.packed_as_of is None or at > ed.packed_as_of):
        ed.packed_as_of = at


def _read_editions(rows: list[tuple], editions: dict[str, EditionPlan],
                   launch: date | None = None, tz: ZoneInfo | None = None) -> None:
    """Rates and packed copies from any table that lists launch products by id."""
    for i, r in enumerate(rows):
        cells = [str(c or "").strip().lower() for c in r]
        pc = next((j for j, c in enumerate(cells) if c in ("product", "material", "product id")), None)
        if pc is None:
            continue
        header = {c: j for j, c in enumerate(cells) if c}
        title = " ".join(str(c or "") for row in rows[:i] for c in row).lower()
        at = _stamp(title, launch, tz) if tz else None
        rc = _col(header, "rate")
        kc = header.get("packed")
        if kc is None and "packed" in title:
            kc = header.get("qty")
        for row in rows[i + 1:]:
            pid = str(row[pc] or "").strip() if pc < len(row) else ""
            if pid not in editions:
                continue
            ed = editions[pid]
            if rc is not None and _num(row[rc]):
                ed.rate_per_hour = _num(row[rc])
            n = _num(row[kc]) if kc is not None else None
            if n is not None:
                _set_packed(ed, n, at)


def load(root: Path | None, tz: ZoneInfo) -> PackClock:
    if root is None:
        return PackClock()
    soony = root / "soony"
    products = _read_csv(soony / "master" / "products.csv")
    launch_products = {p["product_id"]: p for p in products if p.get("lifecycle") == "launch"}
    launch_dates = sorted(p.get("launch_date") for p in launch_products.values() if p.get("launch_date"))
    launch = date.fromisoformat(launch_dates[0]) if launch_dates else None

    editions: dict[str, EditionPlan] = {
        pid: EditionPlan(pid, p.get("name"), _num(p.get("day_one_qty"))) for pid, p in launch_products.items()
    }
    windows: list[tuple[datetime, datetime]] = []
    books = [b for d in ("context", "erp") if (soony / d).is_dir() for b in sorted((soony / d).glob("*.xlsx"))]
    for book in books:
        for rows in _rows(book).values():
            windows = windows or _windows(rows, launch, tz)
            _read_editions(rows, editions, launch, tz)
    if not windows and launch:
        windows = default_windows(launch, tz)
    # Packed copies only grow, so the highest figure across the plan, status sheets and the
    # pack-out log is the newest one. A log is complete up to the end of its last day.
    packed: dict[str, float] = {}
    last_day: dict[str, date] = {}
    for f in sorted((soony / "erp").glob("packout_log*.csv")) if (soony / "erp").is_dir() else []:
        for r in _read_csv(f):
            pid = r.get("product_id")
            if pid in editions:
                packed[pid] = packed.get(pid, 0) + (_num(r.get("units_packed")) or 0)
                try:
                    d = date.fromisoformat(str(r.get("date") or "")[:10])
                    last_day[pid] = max(last_day.get(pid, d), d)
                except ValueError:
                    pass
    for pid, n in packed.items():
        d = last_day.get(pid)
        at = datetime(d.year, d.month, d.day, tzinfo=tz) + timedelta(days=1) if d else None
        _set_packed(editions[pid], n, at)
    return PackClock(windows, editions)
