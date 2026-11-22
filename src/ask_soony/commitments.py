"""
Confirmed orders become inbound.

A decided card that asked a supplier for parts (an approved email option, a follow-up, a mail sent
outside the app in the same case), plus the supplier's replies on it, is a commitment: who sends
how many of which part, how far it is (offered, confirmed, loading, shipped, delivered), when it is
ready and when it reaches the site. Launch counts it like a shipment, but only the copies it can
still save: what arrives before the part runs dry counts in full, what arrives after counts only for
the packing hours left after it arrives, what arrives after the trucks counts for nothing.

Everything is read at runtime: replies are supplier-written data and are only parsed for numbers,
dates and a few status words (EN, PL, DE, KO, ZH); the model's reply facts (replies.facts, on the
card's latest reply) are used where the text bears them out. Transit comes from routes.csv and the
coordinates of ports and sites. A goods receipt or a new shipment row for the same supplier and
part supersedes the commitment, and a commitment whose reply names a shipment already on the list
only annotates that shipment, so nothing is counted twice.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from datetime import datetime, timedelta
from email.utils import parseaddr
from pathlib import Path
from typing import Any

SENT_KINDS = ("approve", "followup", "sent_outside")
STATUS_RANK = {
    "declined": 0,
    "offered": 1,
    "confirmed": 2,
    "loading": 3,
    "shipped": 4,
    "delivered": 5,
}
COUNTS = ("confirmed", "loading", "shipped")  # a commitment Launch counts
KIND = "late_commitment"

# ---------------------------------------------------------------- words (not Soony data)

_ASK = re.compile(
    r"\b(order|ordering|zamówien\w*|zamawia\w*|bestell\w*|auftrag|expedite|earlier|release|"
    r"deliver\w*|wysył\w*|dostaw\w*|liefer\w*)\b|订单|订购|주문|발주|提前|앞당",
    re.I,
)
_ORDER = re.compile(
    r"\b(order|zamówien\w*|zamawia\w*|bestell\w*|auftrag)\b|订单|订购|주문|발주", re.I
)
_NOT_ASK = re.compile(r"\border status\b|\bstatus of\b", re.I)
_CONFIRM = re.compile(
    r"\b(confirm\w*|we will release|we release|will ship|we ship|booked|potwierdzam\w*|"
    r"potwierdzamy|wydajemy|wyślemy|bestätig\w*|wir liefern)\b|确认|已安排|확인합니다|확정",
    re.I,
)
_CONDITIONAL = re.compile(
    r"\b(once (we|you|soony)|upon (receipt|your)|after (we )?receiv\w*|subject to|as soon as we "
    r"(receive|get)|if you confirm|po otrzymaniu|na podstawie państwa|po potwierdzeniu|"
    r"sobald wir|nach erhalt|vorbehaltlich)\b|收到.{0,12}后|받으면|접수되면|확인 후",
    re.I,
)
_REFUSE = re.compile(
    r"\b(cannot|can not|can't|unable|not able|not possible|nie możemy|nie jesteśmy w stanie|"
    r"nie ma możliwości|können nicht|nicht möglich)\b|无法|不能|불가|어렵습니다",
    re.I,
)
_LOADING = re.compile(
    r"\b(prepar\w* (the )?goods|goods (are )?being prepared|being loaded|loading (has )?"
    r"(started|begun)|loading is under way|przygotowani\w* towaru|załadunek trwa|"
    r"wird verladen|ware wird vorbereitet)\b|备货中|正在装|출고 준비|적재 중",
    re.I,
)
_SHIPPED = re.compile(
    r"\b(shipped|dispatched|has left|have left|departed|is on the (road|way)|handed (over )?to|"
    r"wysłan[aeoy]|wyjecha\w*|odjecha\w*|jest w drodze|versandt|abgefahren|unterwegs)\b|"
    r"已发货|已发运|已出发|발송되었|출발했|출고되었",
    re.I,
)
_NEGATION = re.compile(
    r"\b(not yet|do not yet|don't yet|no confirm\w*|cannot confirm|jeszcze nie|nie mam|"
    r"noch nicht|noch keine)\b|尚未|还没|아직",
    re.I,
)
_ARRIVE = re.compile(
    r"\b(arriv\w*|deliver\w*|eta|reach\w*|przyjazd\w*|przyjedzie|dotrze|dostaw\w*|"
    r"ankunft|ankommen|lieferung|geliefert|zustellung)\b|到达|到货|送达|抵达|도착|납품|입고",
    re.I,
)
_READY_WORD = re.compile(
    r"\b(load\w*|ready|dispatch\w*|ship\w*|załad\w*|wysył\w*|wydani\w*|gotow\w*|"
    r"verlad\w*|versand\w*|bereit)\b|装|发货|출고|선적|적재|준비",
    re.I,
)
_WORD_NUM = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "twelve": 12,
    "jednej": 1,
    "jedna": 1,
    "dwóch": 2,
    "dwie": 2,
    "trzech": 3,
    "czterech": 4,
    "pięciu": 5,
    "sześciu": 6,
    "ośmiu": 8,
    "ein": 1,
    "einer": 1,
    "zwei": 2,
    "drei": 3,
    "vier": 4,
    "fünf": 5,
    "sechs": 6,
    "acht": 8,
}
_HOURS = re.compile(
    r"(?:within|in|w ciągu|innerhalb(?: von)?|binnen)\s+(\d+(?:[.,]\d+)?|[a-ząćęłńóśźżü]+)\s*"
    r"(?:hours?|hrs?|h\b|godzin\w*|godziny|stunden?)"
    r"|(\d+(?:\.\d+)?)\s*(?:个)?(?:小时|시간)\s*(?:内|以内|이내|안에)",
    re.I,
)
_MONTHS = {
    **{
        m: i + 1
        for i, m in enumerate(
            "january february march april may june july august september october november "
            "december".split()
        )
    },
    **{m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())},
    **{
        m: i + 1
        for i, m in enumerate(
            "stycznia lutego marca kwietnia maja czerwca lipca sierpnia września października "
            "listopada grudnia".split()
        )
    },
    **{
        m: i + 1
        for i, m in enumerate(
            "januar februar märz april mai juni juli august september oktober november "
            "dezember".split()
        )
    },
}
_MONTH_RE = "|".join(sorted((re.escape(m) for m in _MONTHS), key=len, reverse=True))
_DATE_DM = re.compile(rf"\b(\d{{1,2}})\.?\s+({_MONTH_RE})\b", re.I)
_DATE_MD = re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})\b", re.I)
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{1,2}):(\d{2}))?")
_DATE_DOT = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})?(?!\d)")
_DATE_CJK = re.compile(r"(\d{1,2})\s*(?:月|월)\s*(\d{1,2})\s*(?:日|일)")
_TODAY = re.compile(r"\b(today|tonight|this evening|dziś|dzisiaj|heute)\b|今天|今日|오늘", re.I)
_TOMORROW = re.compile(r"\b(tomorrow|jutro|morgen)\b|明天|내일", re.I)
_TIME = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_MODAL = re.compile(
    r"\b(can|could|will|would|may|to|możemy|może|kann|können)\s+(be\s+|zostać\s+|werden\s+)?"
    r"(shipped|dispatched|wysłan\w*|versandt)",
    re.I,
)
_NUM = re.compile(r"(?<![\w./:-])(\d{1,3}(?:[,.   ]\d{3})+|\d+)(?![\w/-]|[.,:]\d)")
_UNIT_AFTER = re.compile(
    r"^\s*(?:(?:gb|mb|tb|p|cm|mm|kg|g|h|hours?|hrs?|godzin\w*|stunden|days?|dni|tage|min|km|"
    rf"{_MONTH_RE})\b|%|小时|시간|일|日|月|월)",
    re.I,
)
_MODE_WORDS = {
    "road": re.compile(
        r"\b(truck|lorry|van|road|ciężarów\w*|samochod\w*|bus\w*|lkw)\b|卡车|트럭", re.I
    ),
    "air": re.compile(r"\b(flight|air|awb|lot\w*|flug)\b|航班|空运|항공", re.I),
    "sea": re.compile(r"\b(vessel|ship|sea|container|statek|schiff)\b|海运|船|선박", re.I),
}
_VEHICLE = {"road": "truck", "air": "flight", "sea": "vessel", "rail": "train"}


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


# ---------------------------------------------------------------- reading one reply


def sentences(text: str) -> list[str]:
    """Sentences of a reply; a thousands separator ('8 000 szt.') or a decimal does not split."""
    flat = re.sub(r"\s+", " ", text or "")
    return [s.strip() for s in re.split(r"(?<=[.!?;。！？])\s+|\n", flat) if s.strip()]


def quantities(sentence: str) -> list[float]:
    """Quantities in a sentence: '8,000', '8 000', '36.000', '20000'; not order numbers, years,
    times, dates or measures ('128 GB', '4 hours', '24 November')."""
    out = []
    for m in _NUM.finditer(sentence):
        raw = m.group(1)
        digits = re.sub(r"\D", "", raw)
        if not digits or len(digits) > 7:
            continue
        if raw.isdigit() and (len(raw) > 6 or 1900 <= int(raw) <= 2100):
            continue
        if _UNIT_AFTER.match(sentence[m.end() :]):
            continue
        before = sentence[max(0, m.start() - 12) : m.start()].lower()
        if re.search(rf"({_MONTH_RE})\.?\s*$|\b(po|awb|nr|no\.?|#)\s*$", before, re.I):
            continue
        n = float(digits)
        if n >= 100:
            out.append(n)
    return out


def _at_end_of_day(day: datetime, windows: list[tuple[datetime, datetime]]) -> datetime:
    """A date without a time: counted at the end of that day's last shift (else 23:59), so a
    delivery 'on Sunday' never counts before the trucks."""
    ends = [e for s, e in windows if s.date() == day.date()]
    return max(ends) if ends else day.replace(hour=23, minute=59)


def dates(sentence: str, ref: datetime, windows=()) -> list[tuple[datetime, bool]]:
    """Dates in a sentence as (when, time_known), in the reply's time zone. A day without a time
    is taken at the end of its shift (see _at_end_of_day). Years default to the reply's."""
    tz = ref.tzinfo
    found: list[tuple[datetime, bool]] = []
    days: list[datetime] = []
    for m in _DATE_ISO.finditer(sentence):
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=tz)
        except ValueError:
            continue
        if m.group(4):
            found.append((d.replace(hour=int(m.group(4)), minute=int(m.group(5))), True))
        else:
            days.append(d)
    rest = _DATE_ISO.sub(" ", sentence)
    for m in _DATE_DM.finditer(rest):
        days.append((int(m.group(1)), _MONTHS[m.group(2).lower()]))  # type: ignore[arg-type]
    for m in _DATE_MD.finditer(rest):
        days.append((int(m.group(2)), _MONTHS[m.group(1).lower()]))  # type: ignore[arg-type]
    for m in _DATE_CJK.finditer(rest):
        days.append((int(m.group(2)), int(m.group(1))))  # type: ignore[arg-type]
    for m in _DATE_DOT.finditer(rest):
        if m.group(3) or not _TIME.fullmatch(m.group(0).rstrip(".")):
            try:
                days.append(
                    datetime(
                        int(m.group(3) or ref.year), int(m.group(2)), int(m.group(1)), tzinfo=tz
                    )
                )
            except ValueError:
                pass
    if _TODAY.search(rest):
        days.append(ref.replace(hour=0, minute=0, second=0, microsecond=0))
    if _TOMORROW.search(rest):
        days.append(ref.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
    clean = []
    for d in days:
        if isinstance(d, tuple):
            try:
                d = datetime(ref.year, d[1], d[0], tzinfo=tz)
            except ValueError:
                continue
        clean.append(d)
    times = [(int(m.group(1)), int(m.group(2))) for m in _TIME.finditer(rest)]
    if clean:
        for d in clean:
            if times and len(clean) == 1:
                found.append((d.replace(hour=times[-1][0], minute=times[-1][1]), True))
            else:
                found.append((_at_end_of_day(d, list(windows)), False))
    elif times and _ARRIVE.search(sentence):  # "arrives 19:30": today, or tomorrow once passed
        h, mi = times[-1]
        d = ref.replace(hour=h, minute=mi, second=0, microsecond=0)
        found.append((d if d >= ref else d + timedelta(days=1), True))
    return found


def ready_hours(sentence: str) -> float | None:
    """'loading within four hours', 'załadunek ... w ciągu 4 godzin', '4小时内': hours or None."""
    if not _READY_WORD.search(sentence):
        return None
    m = _HOURS.search(sentence)
    if not m:
        return None
    raw = (m.group(1) or m.group(2) or "").lower().replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return float(_WORD_NUM[raw]) if raw in _WORD_NUM else None


def read_reply(
    text: str,
    at: datetime,
    asked: float | None = None,
    windows=(),
    facts: dict | None = None,
    part: str | None = None,
    others: set[str] | frozenset[str] = frozenset(),
) -> dict:
    """
    What one supplier reply commits to: {quantity, arrival, arrival_known_time, arrival_quote,
    ready_hours, ready_quote, confirms, conditional, refuses, loading, shipped, mode, quote}.
    `facts`: the model's reading of this reply (replies.facts); its quantity is used only if the
    text states that number, its date only if the reply speaks of an arrival and the text gives
    no date itself. Sentences that name only other parts (`others`, not `part`) are left out.
    """
    sents = [
        s for s in sentences(text) if not (any(o in s for o in others) and not (part and part in s))
    ]
    text = " ".join(sents)
    stated = {q for s in sents for q in quantities(s)}
    arrivals = []
    for s in sents:
        if not _ARRIVE.search(s) or (_REFUSE.search(s) and not quantities(s)):
            continue
        for d, known in dates(s, at, windows):
            arrivals.append((d, known, s))
    arrival = max(arrivals, key=lambda x: x[0]) if arrivals else None
    quantity, quote = None, None
    if arrival and quantities(arrival[2]):
        quantity, quote = quantities(arrival[2])[0], arrival[2]
    if quantity is None and asked is not None and asked in stated:
        quantity = asked
        quote = next((s for s in sents if asked in quantities(s)), None)
    if quantity is None and facts and facts.get("quantity") is not None:
        try:
            q = float(facts["quantity"])
        except (TypeError, ValueError):
            q = None
        if q is not None and q in stated:
            quantity = q
            quote = next((s for s in sents if q in quantities(s)), None)
    if quantity is None:
        total = next(
            (
                s
                for s in sents
                if re.search(r"łącznie|in total|insgesamt|总共|총", s, re.I) and quantities(s)
            ),
            None,
        )
        first = total or next((s for s in sents if quantities(s)), None)
        if first:
            quantity, quote = quantities(first)[0], first
    if (
        arrival is None
        and facts
        and facts.get("new_date")
        and any(_ARRIVE.search(s) for s in sents)
    ):
        from ask_soony.api import parse_ts

        d = parse_ts(facts["new_date"])
        if d:
            known = bool(re.search(r"T\d{2}:\d{2}", str(facts["new_date"]))) and (
                d.hour or d.minute
            )
            arrival = (d if known else _at_end_of_day(d, list(windows)), bool(known), None)
    hours = next(((h, s) for s in sents if (h := ready_hours(s)) is not None), None)
    positive = [s for s in sents if not _NEGATION.search(s)]
    conditional = bool(_CONDITIONAL.search(text or ""))
    confirms = bool(any(_CONFIRM.search(s) for s in positive)) and not conditional
    if facts and facts.get("confirms") is True and not conditional:
        confirms = True
    refuses = bool(_REFUSE.search(text or ""))
    mode = next((m for m, rx in _MODE_WORDS.items() if rx.search(text or "")), None)
    return {
        "quantity": quantity,
        "arrival": arrival[0] if arrival else None,
        "arrival_known_time": arrival[1] if arrival else False,
        "arrival_quote": arrival[2] if arrival else None,
        "ready_hours": hours[0] if hours else None,
        "ready_quote": hours[1] if hours else None,
        "confirms": confirms or bool(arrival and quantity and not conditional and refuses),
        "conditional": conditional,
        "refuses": refuses,
        "loading": any(_LOADING.search(s) for s in positive),
        "shipped": any(_SHIPPED.search(_MODAL.sub(" ", s)) for s in positive),
        "mode": mode,
        "quote": quote,
    }


# ---------------------------------------------------------------- transit (routes.csv + geography)


def _km(a: dict, b: dict) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = (
        math.sin((la2 - la1) / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    )
    return 6371 * 2 * math.asin(math.sqrt(h))


def leg_hours(leg: dict, nodes: dict) -> tuple[float, str] | None:
    """Hours for one route leg and how they were found. Road legs with both ends on the map: the
    great-circle distance times a detour factor at a truck's average speed, plus the legal driver
    breaks (45 min per 4.5 h driving), never more than the route's transit days. Anything else:
    the route's transit days."""
    days = leg.get("transit_days")
    a, b = nodes.get(leg.get("origin") or ""), nodes.get(leg.get("destination") or "")
    name_a = (a or {}).get("name") or leg.get("origin")
    name = f"{name_a} to {(b or {}).get('name') or leg.get('destination')}"
    if leg.get("mode") == "road" and a and b:
        km = _km(a, b) * _env_f("ASK_ROAD_DETOUR", 1.25)
        drive = km / _env_f("ASK_ROAD_KMH", 70)
        breaks = math.floor(drive / 4.5) * 0.75
        hours = drive + breaks
        if days:
            hours = min(hours, days * 24)
        brk = (
            f", {math.floor(drive / 4.5)} driver break{'s' if math.floor(drive / 4.5) != 1 else ''}"
            if breaks
            else ""
        )
        return hours, f"{hours:.1f} h road ({km:,.0f} km {name}{brk})"
    if days:
        return (
            days * 24,
            f"{days:g} day{'s' if days != 1 else ''} {leg.get('mode') or 'transit'} "
            f"({name}, {leg.get('id')})",
        )
    return None


def transit(net: dict, supplier_id: str, dc: str | None, mode: str | None = None) -> dict | None:
    """The supplier's usual way to the site: the route its shipments take (most frequent, or the
    one in `mode`), plus the on-carriage leg when that route ends elsewhere. {hours, basis, mode,
    legs}; None when the data names no route."""
    legs = {leg["id"]: leg for leg in net.get("legs") or []}
    nodes = net.get("nodes") or {}
    used = Counter(
        str(r.get("route_id"))
        for r in net.get("shipments_raw") or []
        if str(r.get("supplier_id")) == supplier_id and r.get("route_id") in legs
    )
    if not used:
        return None
    choices = [legs[r] for r, _ in used.most_common()]
    if mode and any(c.get("mode") == mode for c in choices):
        choices = [c for c in choices if c.get("mode") == mode]
    first = choices[0]
    chain = [first]
    if dc and first.get("destination") != dc:
        on = next(
            (
                leg
                for leg in legs.values()
                if leg["origin"] == first["destination"] and leg["destination"] == dc
            ),
            None,
        )
        if on:
            chain.append(on)
    total, basis = 0.0, []
    for leg in chain:
        h = leg_hours(leg, nodes)
        if not h:
            return None
        total += h[0]
        basis.append(h[1])
    return {
        "hours": total,
        "basis": " + ".join(basis),
        "mode": first.get("mode"),
        "legs": [c["id"] for c in chain],
    }


def on_carriage(net: dict, dc: str | None, sentence: str | None) -> dict | None:
    """A stated arrival somewhere else ('Frankfurt arrival 23 November'): the leg from there to the
    site, when the sentence names that place and not the site itself."""
    if not dc or not sentence:
        return None
    nodes = net.get("nodes") or {}
    site = nodes.get(dc) or {}
    city = str(site.get("name") or "").split()[-1] if site.get("name") else ""
    if dc.lower() in sentence.lower() or (city and city.lower() in sentence.lower()):
        return None
    for leg in net.get("legs") or []:
        if leg.get("destination") != dc:
            continue
        place = str((nodes.get(leg["origin"]) or {}).get("name") or "").split()[0:1]
        if place and len(place[0]) > 3 and place[0].lower() in sentence.lower():
            h = leg_hours(leg, nodes)
            if h:
                return {"hours": h[0], "basis": h[1], "leg": leg["id"]}
    return None


# ---------------------------------------------------------------- building commitments


def _hm(dt: datetime | None, ref: datetime) -> str:
    if not dt:
        return "unknown"
    d = dt.astimezone(ref.tzinfo)
    return d.strftime("%H:%M") if d.date() == ref.date() else d.strftime("%a %d %b %H:%M")


def _day(dt: datetime, ref: datetime) -> str:
    d = dt.astimezone(ref.tzinfo)
    return "today" if d.date() == ref.date() else d.strftime("%a %d %b")


def _n(x: float | None) -> str:
    return f"{x:,.0f}" if x is not None else "unknown"


def _norm_subject(s: str | None) -> str:
    return re.sub(
        r"^\s*((re|aw|antw|sv|fw|fwd|odp)\s*:\s*)+", "", " ".join(str(s or "").split()), flags=re.I
    ).lower()


def _re(subject: str | None) -> str | None:
    """'Re: <subject>' once, keeping the supplier's wording and case."""
    if not subject:
        return None
    core = re.sub(
        r"^\s*((re|aw|antw|sv|odp)\s*:\s*)+", "", " ".join(str(subject).split()), flags=re.I
    )
    return f"Re: {core}"


def origin_of(decision_id: str, supplier_ids: set[str]) -> str:
    """The case a follow-up card belongs to: 'late-<supplier>-<case>' -> '<case>'."""
    if decision_id.startswith("late-"):
        rest = decision_id[5:]
        for sid in supplier_ids:
            if rest.startswith(sid + "-"):
                return rest[len(sid) + 1 :]
    return decision_id


def _asked_quantity(text: str) -> float | None:
    for s in sentences(text):
        q = quantities(s)
        if q:
            return q[0]
    return None


def _profiles(root: Path | None) -> dict[str, dict]:
    from ask_soony import api

    out: dict[str, dict] = {}
    if root is None or not (root / "suppliers").is_dir():
        return out
    for p in sorted((root / "suppliers").glob("*/profile.csv")):
        for r in api.read_csv(p):
            if r.get("supplier_id"):
                out[r["supplier_id"]] = r
    return out


def build(
    cards: list[dict],
    net: dict,
    clock: Any,
    cur: datetime,
    actions: list[dict] | None = None,
    statuses: dict | None = None,
    known_refs: dict[str, set[str]] | None = None,
) -> list[dict]:
    """
    Commitments from the cases' supplier emails and replies, one per case and supplier.
    `known_refs`: part -> shipment references Launch already lists for it (a commitment that
    names one of them annotates it instead of counting again).
    """
    from ask_soony import api, tracking

    actions = actions if actions is not None else api.read_list("actions.json")
    statuses = statuses if statuses is not None else api.read_json("decision_status.json", {})
    root = net.get("root")
    nodes = net.get("nodes") or {}
    profiles = _profiles(root)
    supplier_ids = set(profiles) | {k for k, n in nodes.items() if n.get("kind") == "supplier"}
    by_mail: dict[str, str] = {}
    for sid, r in profiles.items():
        for k, v in r.items():
            if v and k and "mail" in k.lower() and "@" in v:
                by_mail[v.strip().lower()] = sid
    dc = next((n["id"] for n in nodes.values() if n.get("kind") == "dc"), None)
    windows = list(getattr(clock, "windows", None) or [])
    by_card = {c["id"]: c for c in cards}
    desc_to_code: dict[str, str] = {}
    derived_refs: dict[str, set[str]] = {}
    for c in cards:
        if c.get("part") and re.fullmatch(r"[A-Z]{2,}[-\w]*\d", str(c["part"])):
            desc_to_code.setdefault(str(c.get("part_name") or ""), str(c["part"]))
    try:
        disp = json.loads(api.cards_file().read_text(encoding="utf-8"))
        for p in disp.get("parts") or []:
            if p.get("description") and p.get("part"):
                desc_to_code[str(p["description"])] = str(p["part"])
            if known_refs is None and p.get("part"):
                refs = {
                    str(x.get("reference"))
                    for k in ("arriving", "not_counted", "supply")
                    for x in p.get(k) or []
                    if isinstance(x, dict) and x.get("reference")
                }
                derived_refs.setdefault(str(p["part"]), set()).update(refs)
    except (OSError, ValueError, AttributeError):
        pass

    known_refs = known_refs if known_refs is not None else derived_refs
    all_parts = set(desc_to_code.values()) | set(derived_refs)
    if root is not None:
        all_parts |= {
            str(r.get("component_id"))
            for r in api.read_csv(root / "soony" / "master" / "components.csv")
            if r.get("component_id")
        }
    # outgoing supply asks per (case, supplier), with every message id of their threads
    groups: dict[tuple[str, str], dict] = {}
    for a in actions:
        if a.get("kind") not in SENT_KINDS or a.get("channel") != "email":
            continue
        to = parseaddr(str(a.get("to") or ""))[1].lower()
        sid = by_mail.get(to)
        if not sid:
            continue
        case = origin_of(str(a.get("decision_id") or ""), supplier_ids)
        card = by_card.get(case)
        if not card:
            continue
        what = f"{a.get('action') or ''} {a.get('subject') or ''}"
        if a.get("kind") == "approve":
            opt = next(
                (
                    o
                    for o in card.get("options") or []
                    if str(o.get("index")) == str(a.get("option"))
                ),
                None,
            )
            what = f"{(opt or {}).get('action') or ''} {a.get('action') or ''}"
        g = groups.setdefault((case, sid), {"asks": [], "ids": set(), "subjects": set(), "to": to})
        is_ask = bool(_ASK.search(what)) and not _NOT_ASK.search(what)
        in_thread = _norm_subject(a.get("subject")) in g["subjects"]
        if not (is_ask or in_thread):
            continue
        g["asks"].append({**a, "_what": what, "_order": bool(_ORDER.search(what))})
        if a.get("message_id"):
            g["ids"].add(a["message_id"])
        g["subjects"].add(_norm_subject(a.get("subject")))
        g["to"] = to
    receipts = tracking.read_receipts(root) if root else []
    out: list[dict] = []
    for (case, sid), g in groups.items():
        if not g["asks"]:
            continue
        card = by_card[case]
        part = str(card.get("part") or "")
        part = desc_to_code.get(part, part)
        asks = sorted(g["asks"], key=lambda a: api.parse_ts(a.get("at")) or cur)
        first = asks[0]
        asked = next(
            (
                q
                for a in asks
                if (q := _asked_quantity(a["_what"]) or _asked_quantity(str(a.get("draft") or "")))
                is not None
            ),
            None,
        )
        replies = sorted(
            (
                r
                for r in actions
                if r.get("kind") == "reply"
                and origin_of(str(r.get("decision_id") or ""), supplier_ids) == case
                and (
                    r.get("in_reply_to") in g["ids"]
                    or _norm_subject(r.get("subject")) in g["subjects"]
                )
                and (by_mail.get(parseaddr(str(r.get("from") or ""))[1].lower()) in (sid, None))
            ),
            key=lambda r: api.parse_ts(r.get("at")) or cur,
        )
        entry = statuses.get(case) or {}
        status, quantity, confirmed_at, ready_at, arrival = "offered", asked, None, None, None
        evidence, ready_basis, arrival_quote, mode_hint = [], None, None, None
        ready_quote, arrival_known = None, False
        for r in replies:
            at = (api.parse_ts(r.get("at")) or cur).astimezone(cur.tzinfo)
            facts = None
            if entry.get("reply_at") and api.parse_ts(entry.get("reply_at")) == api.parse_ts(
                r.get("at")
            ):
                facts = {
                    "quantity": entry.get("reply_quantity"),
                    "new_date": entry.get("reply_date"),
                    "confirms": entry.get("reply_confirms"),
                }
            got = read_reply(
                str(r.get("text") or ""), at, asked, windows, facts, part, all_parts - {part}
            )
            if got["quantity"] is not None:
                quantity = got["quantity"]
            if got["mode"]:
                mode_hint = got["mode"]
            now_status = (
                "shipped"
                if got["shipped"] and (got["confirms"] or not got["conditional"])
                else "loading"
                if got["loading"]
                else "confirmed"
                if got["confirms"]
                else "declined"
                if got["refuses"] and not got["quantity"]
                else "offered"
            )
            if STATUS_RANK[now_status] > STATUS_RANK[status] or (
                status == "offered" and now_status == "declined"
            ):
                status = now_status
            if status in COUNTS and confirmed_at is None and now_status in COUNTS:
                confirmed_at = at
            if got["ready_hours"] is not None:
                ready_basis = (got["ready_hours"], at)
                ready_quote = got["ready_quote"]
            if got["arrival"]:
                arrival, arrival_quote, arrival_known = (
                    got["arrival"],
                    got["arrival_quote"],
                    got["arrival_known_time"],
                )
            evidence.append(
                {
                    "message_id": r.get("message_id"),
                    "at": at.isoformat(),
                    "from": r.get("from"),
                    "quote": got["arrival_quote"]
                    or got["quote"]
                    or got["ready_quote"]
                    or (sentences(str(r.get("text") or ""))[1:2] or [None])[0],
                }
            )
        if ready_basis:
            hours, said_at = ready_basis
            anchor = max(said_at, confirmed_at) if confirmed_at else said_at
            ready_at = anchor + timedelta(hours=hours)
        eta, estimated, basis = None, False, []
        if arrival:
            eta = arrival
            on = on_carriage(net, dc, arrival_quote)
            if on:
                eta = arrival + (
                    timedelta(hours=on["hours"])
                    if arrival_known
                    else timedelta(days=math.ceil(on["hours"] / 24))
                )
                estimated = True
                basis = [
                    f"stated arrival {_hm(arrival, cur) if arrival_known else _day(arrival, cur)}",
                    on["basis"],
                ]
            elif not arrival_known:
                basis = ["date only, counted at the end of that day"]
        else:
            tr = transit(net, sid, dc, mode_hint)
            start = (
                ready_at
                or confirmed_at
                or (api.parse_ts(first.get("at")) if status == "offered" and replies else None)
            )
            if tr and start:
                eta = start + timedelta(hours=tr["hours"])
                estimated = True
                basis = [
                    "confirmation" if confirmed_at else "our order",
                    f"{ready_basis[0]:g} h loading" if ready_at else "loading time unknown",
                    tr["basis"],
                ]
                mode_hint = mode_hint or tr["mode"]
        if mode_hint is None:
            tr = transit(net, sid, dc)
            mode_hint = tr["mode"] if tr else None
        texts = " ".join(str(r.get("text") or "") for r in replies)
        # The shipment it is: one a reply names (reference or order number), else a row for the
        # same supplier and part that leaves on or after the confirmation and that Launch does
        # not list yet (a new truck on the list).
        linked = None
        rows = [
            s
            for s in net.get("shipments_raw") or []
            if str(s.get("supplier_id")) == sid and part in str(s.get("material_ids") or "")
        ]
        for s in rows:
            ref = str(s.get("reference") or "")
            pos = [p for p in str(s.get("po_numbers") or "").split("|") if p]
            if (len(ref) >= 6 and ref in texts) or any(p in texts for p in pos):
                linked = {
                    "shipment_id": s.get("shipment_id"),
                    "reference": ref or None,
                    "named": True,
                    "known": ref in (known_refs.get(part) or set()),
                    "eta": s.get("eta"),
                    "status": s.get("status"),
                }
                break
        if linked is None and confirmed_at and status in COUNTS:
            for s in rows:
                ref = str(s.get("reference") or "")
                etd = api.parse_ts(s.get("etd"))
                if (
                    not etd
                    or etd.date() < confirmed_at.date()
                    or ref in (known_refs.get(part) or set())
                ):
                    continue
                linked = {
                    "shipment_id": s.get("shipment_id"),
                    "reference": ref or None,
                    "named": False,
                    "known": False,
                    "eta": s.get("eta"),
                    "status": s.get("status"),
                }
                status = max(status, "shipped", key=STATUS_RANK.get)
                if s.get("eta") and not arrival:
                    e = api.parse_ts(s["eta"])
                    if e:
                        dated = len(str(s["eta"])) <= 10
                        eta = _at_end_of_day(e, windows) if dated else e
                        arrival_known, estimated = not dated, False
                        basis = [f"shipment {ref or s.get('shipment_id')} ETA"]
                break
        # receipts booked from the day it was confirmed (or asked, if not confirmed yet)
        since = (confirmed_at or api.parse_ts(first.get("at")) or cur).date().isoformat()
        got_in = [
            r
            for r in receipts
            if str(r.get("supplier_id")) == sid
            and str(r.get("material_id")) == part
            and str(r.get("gr_date") or "") >= since
        ]
        received = sum(api.num(r.get("qty_received")) or 0 for r in got_in)
        delivered_by = None
        if got_in and quantity and received >= quantity:
            status, delivered_by = "delivered", [str(r.get("gr_number")) for r in got_in]
        supplier = profiles.get(sid) or {}
        name = supplier.get("name") or (nodes.get(sid) or {}).get("name") or sid
        out.append(
            {
                "id": f"{case}~{sid}",
                "card_id": case,
                "supplier_id": sid,
                "supplier_name": name,
                "contact": supplier.get("contact_name") or None,
                "to": g["to"],
                "language": (supplier.get("preferred_language") or "en").lower(),
                "part": part,
                "quantity": quantity,
                "asked": asked,
                "ordered": any(a["_order"] for a in asks),
                "asked_at": (api.parse_ts(first.get("at")) or cur)
                .astimezone(cur.tzinfo)
                .isoformat(),
                "status": status,
                "confirmed_at": confirmed_at.isoformat() if confirmed_at else None,
                "ready_at": ready_at.isoformat() if ready_at else None,
                "ready_hours": ready_basis[0] if ready_basis else None,
                "ready_quote": ready_quote,
                "eta": eta.isoformat() if eta else None,
                "eta_time_known": bool(eta and (arrival_known if arrival or linked else estimated)),
                "estimated": estimated,
                "basis": basis,
                "mode": mode_hint,
                "vehicle": _VEHICLE.get(mode_hint or "", "transport"),
                "shipment": linked,
                "delivered_by": delivered_by,
                "received": received or None,
                "evidence": evidence,
                "thread": {
                    "subject": (replies[-1].get("subject") if replies else None)
                    or next((a.get("subject") for a in reversed(asks) if a.get("subject")), None),
                    "last_message_id": (replies[-1].get("message_id") if replies else None)
                    or next(
                        (a.get("message_id") for a in reversed(asks) if a.get("message_id")), None
                    ),
                    "references": [
                        m
                        for m in [
                            *(a.get("message_id") for a in asks),
                            *(r.get("message_id") for r in replies),
                        ]
                        if m
                    ],
                },
            }
        )
    # one commitment per shipment it names: the most advanced one
    seen: dict[str, dict] = {}
    keep = []
    for c in sorted(out, key=lambda c: -STATUS_RANK[c["status"]]):
        key = (
            (c["shipment"] or {}).get("shipment_id") if (c["shipment"] or {}).get("named") else None
        )
        if key and key in seen:
            continue
        if key:
            seen[key] = c
        keep.append(c)
    return sorted(keep, key=lambda c: c["asked_at"])


def counts_separately(c: dict) -> bool:
    """Launch counts it as its own inbound row: confirmed, not delivered, not a shipment the list
    already has."""
    sh = c.get("shipment") or {}
    return c["status"] in COUNTS and not sh.get("named") and not sh.get("known")


# ---------------------------------------------------------------- what it saves (packing clock)


def simulate(
    clock: Any, product: str, cur: datetime, short: float, arrivals: list[tuple[datetime, float]]
) -> dict | None:
    """
    The line for one edition with `short` copies' worth of one part missing and `arrivals`
    (when, copies) of it on the way. Parts on hand are packed at the plan rate inside shifts; an
    arrival before the line runs dry simply extends it; an arrival after it ran dry restarts it,
    so it saves at most rate x shift hours left after it arrives; an arrival at or after the
    trucks saves nothing. Returns {short, runs_dry, saved: [copies per arrival], cap}; None when
    the clock lacks a rate, remaining copies or a launch time.
    """
    ed = clock.editions.get(product)
    launch = clock.launch
    if not ed or not ed.rate_per_hour or ed.remaining is None or launch is None:
        return None
    rate, total = ed.rate_per_hour, ed.remaining
    base = clock._base(cur, ed)
    cap = min(total, rate * clock.shift_hours_between(base, launch))

    def run(arr: list[tuple[datetime, float]]) -> tuple[float, datetime | None]:
        t, buffer, packed, first_dry = base, max(total - short, 0.0), 0.0, None
        for at, copies in sorted(arr, key=lambda x: x[0]):
            if at >= launch:
                continue
            at = max(at, t)
            can = rate * clock.shift_hours_between(t, at)
            todo = total - packed
            if buffer < todo and buffer <= can:  # stood still before this arrival
                first_dry = first_dry or clock.after_packing_hours(t, buffer / rate)
                packed += buffer
                buffer = 0.0
            else:
                step = min(can, buffer, todo)
                packed += step
                buffer -= step
            t = at
            buffer += copies
        can = rate * clock.shift_hours_between(t, launch)
        todo = total - packed
        if buffer < todo and buffer < can:
            first_dry = first_dry or clock.after_packing_hours(t, buffer / rate)
        packed += min(buffer, can, todo)
        return max(cap - packed, 0.0), first_dry

    before, _ = run([])
    saved, prev = [], before
    for i in range(len(arrivals)):
        s, _ = run(arrivals[: i + 1])
        saved.append(max(prev - s, 0.0))
        prev = s
    left, dry = run(arrivals)
    return {
        "short_before": round(before),
        "short": round(left),
        "runs_dry": dry if dry and dry < launch else None,
        "saved": [round(x) for x in saved],
        "rate": rate,
    }


def line_for(
    c: dict,
    cur: datetime,
    launch: datetime | None,
    saved: float | None,
    dry: datetime | None,
    cutoff: str = "the trucks",
) -> str:
    """'8,000 ordered from Karton Nowak, confirmed 07:13, loading within 4 h; arrives ~20:34
    (estimated: confirmation + 4 h loading + 9.3 h road ...), after the trucks leave'."""
    from ask_soony import api

    head = (
        f"{_n(c.get('quantity'))} {'ordered ' if c.get('ordered') else ''}from {c['supplier_name']}"
    )
    st = c["status"]
    conf = api.parse_ts(c.get("confirmed_at"))
    if st == "delivered":
        return (
            f"{head}, delivered (goods receipt {', '.join(c.get('delivered_by') or [])}): in stock"
        )
    if st == "declined":
        return f"{head}: declined"
    if st == "offered" and not c.get("evidence"):
        return f"{head}: asked {_hm(api.parse_ts(c.get('asked_at')), cur)}, no reply yet"
    bits = [head]
    if st == "offered":
        bits.append("not confirmed yet")
    elif conf:
        bits.append(f"confirmed {_hm(conf, cur)}")
    if st == "loading":
        bits.append("loading")
    if st == "shipped":
        bits.append("shipped")
    if c.get("ready_hours") and st in ("confirmed", "loading"):
        bits.append(f"ready within {c['ready_hours']:g} h")
    text = ", ".join(bits)
    eta = api.parse_ts(c.get("eta"))
    if not eta:
        return f"{text}; {c.get('vehicle') or 'transport'} details pending, arrival unknown"
    when = _hm(eta, cur) if c.get("eta_time_known") else _day(eta, cur)
    est = (
        f" ({'estimated: ' if c.get('estimated') else ''}{' + '.join(c.get('basis') or [])})"
        if c.get("basis")
        else ""
    )
    text += f"; arrives ~{when}{est}" if c.get("estimated") else f"; arrives {when}{est}"
    if launch and eta >= launch:
        return f"{text}, after {cutoff} leave"
    if st == "offered":
        return f"{text}, not counted until confirmed"
    if dry and eta > dry and saved is not None:
        return f"{text}; the line stands from {_hm(dry, cur)}, so it saves {_n(saved)} copies"
    return text


def status_line(cs: list[dict], cur: datetime) -> str | None:
    """'Ordered 06:50 · Karton confirmed 8,000 at 07:13 · truck details pending'."""
    from ask_soony import api

    if not cs:
        return None
    first = min(cs, key=lambda c: c["asked_at"])
    out = [
        f"{'Ordered' if any(c['ordered'] for c in cs) else 'Asked'} "
        f"{_hm(api.parse_ts(first['asked_at']), cur)}"
    ]
    for c in cs:
        who = str(c["supplier_name"]).split()[0]
        conf = api.parse_ts(c.get("confirmed_at"))
        eta = api.parse_ts(c.get("eta"))
        st = c["status"]
        if st == "delivered":
            out.append(f"{who} delivered {_n(c.get('received') or c.get('quantity'))}")
            continue
        if st == "declined":
            out.append(f"{who} declined")
            continue
        if st == "offered" and not c.get("evidence"):
            out.append(f"{who}: no reply yet")
            continue
        out.append(
            f"{who} {'offered' if st == 'offered' else 'confirmed'} {_n(c.get('quantity'))}"
            + (f" at {_hm(conf, cur)}" if conf else "")
            + (", loading" if st == "loading" else ", shipped" if st == "shipped" else "")
        )
        if not eta or (c.get("estimated") and not (c.get("shipment") or {}).get("reference")):
            if st in ("confirmed", "loading", "offered"):
                out.append(f"{c.get('vehicle') or 'transport'} details pending")
            if eta:
                out.append(f"arrives ~{_hm(eta, cur)} (estimated)")
        else:
            out.append(f"arrives {_hm(eta, cur) if c.get('eta_time_known') else _day(eta, cur)}")
    return " · ".join(out)


def apply(
    part: dict, cs: list[dict], clock: Any, cur: datetime, cutoff: str = "the trucks"
) -> dict:
    """
    Launch's part row with its commitments: own rows for those counted separately, a note on the
    shipment row a commitment names, and short / runs_dry recomputed from what arrives in time.
    Adds `arithmetic` with the numbers behind it.
    """
    from ask_soony import api

    launch = clock.launch
    mine = [c for c in cs if c["part"] == part["part"]]
    if not mine:
        return part
    per = max([api.num(v) or 0 for v in (part.get("per_copy") or {}).values()] or [0]) or 1
    own = sorted(
        (c for c in mine if counts_separately(c) and c.get("quantity") and c.get("eta")),
        key=lambda c: c["eta"],
    )
    arrivals = [(api.parse_ts(c["eta"]), c["quantity"] / per) for c in own]
    short0, dry0 = part.get("short"), api.parse_ts(part.get("runs_dry"))
    res = None
    if short0 and len(part.get("editions") or []) == 1:
        res = simulate(clock, part["editions"][0], cur, short0, arrivals)
    saved_of: dict[str, float] = {}
    if res:
        for c, saved in zip(own, res["saved"], strict=True):
            saved_of[c["id"]] = saved
        part["short"] = res["short"]
        part["runs_dry"] = res["runs_dry"].isoformat() if res["runs_dry"] else None
    elif short0 is not None:  # shared part: what arrives before the trucks reduces the gap
        good = sum(q for at, q in arrivals if at and launch and at < launch)
        for c in own:
            at = api.parse_ts(c.get("eta"))
            saved_of[c["id"]] = c["quantity"] / per if at and launch and at < launch else 0
        part["short"] = max(short0 - good, 0)
    rows = []
    for c in mine:
        pub = public(
            c, line_for(c, cur, launch, saved_of.get(c["id"]), dry0, cutoff), saved_of.get(c["id"])
        )
        sh = c.get("shipment") or {}
        if sh.get("named") or sh.get("known"):
            same = [
                r for r in part.get("inbound") or [] if r.get("reference") == sh.get("reference")
            ]
            hit = next(
                (r for r in same if r.get("quantity") == c.get("quantity")),
                same[0] if same else None,
            )
            if hit:
                hit["commitment"] = pub
                continue
        if c["status"] == "delivered":
            continue
        eta = api.parse_ts(c.get("eta"))
        rows.append(
            {
                "reference": (sh or {}).get("reference"),
                "quantity": c.get("quantity"),
                "eta": c.get("eta"),
                "counted": bool(saved_of.get(c["id"])),
                "reason": "commitment" if counts_separately(c) else f"commitment, {c['status']}",
                "after_launch": bool(eta and launch and eta >= launch),
                "supplier_id": c["supplier_id"],
                "shipment": None,
                "commitment": pub,
            }
        )
    part["inbound"] = [*rows, *(part.get("inbound") or [])]
    part["commitments"] = [c["id"] for c in mine]
    part["arithmetic"] = {
        "short_before": short0,
        "runs_dry_before": dry0.isoformat() if dry0 else None,
        "short": part.get("short"),
        "runs_dry": part.get("runs_dry"),
        "rate_per_hour": (res or {}).get("rate"),
        "arrivals": [
            {
                "commitment": c["id"],
                "eta": c.get("eta"),
                "copies": (c.get("quantity") or 0) / per,
                "saves": saved_of.get(c["id"]),
            }
            for c in own
        ],
    }
    return part


def public(c: dict, line: str, saved: float | None) -> dict:
    keep = (
        "id",
        "card_id",
        "supplier_id",
        "supplier_name",
        "part",
        "quantity",
        "status",
        "confirmed_at",
        "ready_at",
        "eta",
        "estimated",
        "basis",
        "evidence",
        "shipment",
    )
    return {**{k: c.get(k) for k in keep}, "line": line, "saves": saved}


# ---------------------------------------------------------------- the follow-up decision

# Paragraphs joined with blank lines; {placeholders} are filled from the data in late_draft.
_LATE_MAIL = {
    "pl": [
        "Dzień dobry,",
        "dziękujemy za potwierdzenie {qty} szt. {part}. Naszej linii pakującej zabraknie ich dziś "
        "o {dry}; transport po załadunku dotrze ok. {eta}, a nasze ciężarówki do sklepów "
        "odjeżdżają o {launch}. Każda godzina wcześniej to ok. {rate} egzemplarzy.",
        "Czy możliwe jest jedno z poniższych:\n"
        "1. bezpośredni bus z częścią towaru, gdy tylko będzie gotowa,\n"
        "2. podział ładunku: to, co gotowe, busem, reszta ciężarówką,\n"
        "3. wcześniejsza ciężarówka.",
        "Proszę o najwcześniejszy możliwy przyjazd do {site} i dodatkowy koszt. "
        "Koszt potwierdzimy przed rezerwacją.",
    ],
    "de": [
        "Guten Tag,",
        "danke für die Bestätigung der {qty} {part}. Unserer Packlinie gehen sie heute um {dry} "
        "aus; nach dem Verladen kommt die Lieferung gegen {eta} an, unsere LKW in den Handel "
        "fahren um {launch}. Jede Stunde früher sind etwa {rate} Exemplare.",
        "Ist eines davon möglich:\n"
        "1. ein direkter Transporter mit einem Teil der Ware, sobald er bereit ist,\n"
        "2. eine geteilte Ladung: was fertig ist per Transporter, der Rest per LKW,\n"
        "3. ein früherer LKW.",
        "Bitte nennen Sie die früheste Ankunft in {site} und die Mehrkosten. "
        "Wir bestätigen die Kosten vor der Buchung.",
    ],
    "ko": [
        "안녕하세요,",
        "{part} {qty}개 확인 감사합니다. 저희 포장 라인은 오늘 {dry}에 재고가 소진되며, 현재 "
        "일정으로는 {eta}경 도착하여 {launch} 출고 트럭에 늦습니다. 한 시간 앞당길 때마다 약 "
        "{rate}부를 살릴 수 있습니다.",
        "다음 중 가능한 방법이 있을까요:\n1. 준비된 일부 물량의 직송,\n2. 분할 출하,\n"
        "3. 더 이른 출발편.",
        "{site} 최초 도착 시간과 추가 비용을 알려 주세요. 예약 전에 비용을 확인드리겠습니다.",
    ],
    "zh": [
        "您好，",
        "感谢确认{qty}件{part}。我们的包装线今天{dry}将用完；按目前安排约{eta}到达，"
        "而我们发往零售的卡车{launch}出发。每提前一小时可多包装约{rate}份。",
        "能否安排以下之一：\n1. 已备好的部分直接专车发运，\n2. 分批发货，\n3. 更早的车次。",
        "请告知最早到达{site}的时间及额外费用。我们会在预订前确认费用。",
    ],
}
_LATE_EN = [
    "Dear {contact},",
    "thank you for confirming the {qty} {part}. Our packing line runs out of them at {dry} "
    "today; after loading they arrive around {eta}, and our trucks to retail leave at {launch}. "
    "Every hour earlier saves about {rate} copies.",
    "Could you help with one of these:\n"
    "1. a direct van for part of the load as soon as it is ready,\n"
    "2. a split load: what is ready now by van, the rest on the truck,\n"
    "3. an earlier truck.",
    "Please reply with the earliest arrival at {site} and the extra cost. "
    "We will confirm the cost before you book.",
]


def late_draft(
    c: dict, part_name: str, dry: datetime, cur: datetime, launch: datetime, rate: float, site: str
) -> str:
    """The ask in the supplier's language (profile), then in English, like the thread."""
    eta = datetime.fromisoformat(c["eta"])
    vals = {
        "qty": _n(c.get("quantity")),
        "part": part_name,
        "dry": _hm(dry, cur),
        "eta": _hm(eta, cur),
        "launch": _hm(launch, cur),
        "rate": _n(rate),
        "site": site,
        "contact": c.get("contact") or "Sir or Madam",
    }
    en = "\n\n".join(_LATE_EN).format(**vals)
    local = "\n\n".join(_LATE_MAIL.get(c.get("language") or "") or []) or None
    sign = "\n\nBest regards,\nSoony Procurement"
    return f"{local.format(**vals)}\n\n---\n\n{en}{sign}" if local else f"{en}{sign}"


def late_cards(
    cs: list[dict],
    launch_parts: list[dict],
    clock: Any,
    cur: datetime,
    net: dict,
    statuses: dict,
    viewer: str,
    cutoff: str = "the trucks",
) -> list[dict]:
    """
    A decision for each counted commitment that arrives after its part runs dry and leaves copies
    at risk: ask the supplier for a faster way, ask the site about a pick-up, or accept the loss.
    """
    from ask_soony import api, floor

    out = []
    launch = clock.launch
    by_part = {p["part"]: p for p in launch_parts}
    nodes = net.get("nodes") or {}
    dc = next((n for n in nodes.values() if n.get("kind") == "dc"), {})
    site = dc.get("name") or "the site"
    labels = api.edition_labels(net.get("root"))
    approver = api.exception_approver()
    for c in cs:
        p = by_part.get(c["part"])
        if not p or not counts_separately(c) or not c.get("eta") or not launch:
            continue
        ar = p.get("arithmetic") or {}
        dry = api.parse_ts(ar.get("runs_dry_before"))
        eta = api.parse_ts(c["eta"])
        left = p.get("short") or 0
        if not dry or not eta or eta <= dry or left <= 0 or len(p.get("editions") or []) != 1:
            continue
        ed = p["editions"][0]
        rate = (
            ar.get("rate_per_hour")
            or (clock.editions.get(ed).rate_per_hour if clock.editions.get(ed) else 0)
            or 0
        )
        cid = f"late-{c['supplier_id']}-{c['card_id']}"
        status, ui = api._ui_status(cid, statuses)
        name = p.get("short_name") or p.get("name") or c["part"]
        many = floor.plural(name)
        label = labels.get(ed, ed)
        head = (
            f"The {_n(c['quantity'])} {many} arrive around {_hm(eta, cur)}; the line runs dry at "
            f"{_hm(dry, cur)}: about {_n(left)} {label} copies still at risk"
        )
        ready = api.parse_ts(c.get("ready_at"))
        tr = transit(net, c["supplier_id"], dc.get("id"), "road")
        pick = None
        if tr:
            out_at = cur + timedelta(hours=tr["hours"])
            back = max(out_at, ready or out_at) + timedelta(hours=tr["hours"])
            res = simulate(clock, ed, cur, ar.get("short_before") or left, [(back, c["quantity"])])
            pick = (back, (ar.get("short_before") or left) - res["short"] if res else 0)
        need_by = dry
        options = [
            {
                "index": 0,
                "action": f"Ask {c['supplier_name']} for a direct van, a split load "
                "or an earlier truck",
                "cost": "unknown: they quote first",
                "cost_value": None,
                "effect": f"every hour earlier saves about {_n(rate)} copies; arriving by "
                f"{_hm(need_by, cur)} saves all {_n(left)}",
                "recommended": True,
                "draft": late_draft(c, f"{name} ({c['part']})", dry, cur, launch, rate, site),
                "to": c["to"],
                "channel": "email",
                "subject": _re(c["thread"].get("subject")),
                "in_reply_to": c["thread"].get("last_message_id"),
                "references": c["thread"].get("references"),
                "routine_type": None,
            },
            {
                "index": 1,
                "action": f"Ask {dc.get('name') or 'the site'} whether we can pick them up",
                "cost": "No cost to ask",
                "cost_value": None,
                "effect": (
                    f"a vehicle leaving now would be back around {_hm(pick[0], cur)}: "
                    + (
                        f"saves about {_n(pick[1])} copies"
                        if pick[1]
                        else f"after {cutoff}, saves nothing"
                    )
                )
                if pick
                else "the route is unknown",
                "recommended": False,
                "draft": (
                    f"Can we collect {_n(c['quantity'])} {many} from {c['supplier_name']} "
                    "ourselves? "
                    f"They are ready about {_hm(ready, cur) if ready else 'unknown'}"
                    + (f"; by road it is about {tr['hours']:.0f} h each way." if tr else ".")
                ),
                "to": dc.get("name") or "the site",
                "channel": "notify",
                "routine_type": "internal_note",
            },
            {
                "index": 2,
                "action": f"Accept the loss of about {_n(left)} {label} copies",
                "cost": "No cost",
                "cost_value": None,
                "effect": "off the list; Launch keeps counting the copies as lost",
                "recommended": False,
                "draft": None,
                "to": None,
                "channel": "wait",
                "routine_type": None,
            },
        ]
        signoff = (
            approver
            if approver and viewer and api.first_name(approver) != api.first_name(viewer)
            else None
        )
        card = api._base_card(
            cid,
            kind=KIND,
            part=c["part"],
            part_name=p.get("short_name"),
            headline=head,
            summary=line_for(c, cur, launch, (ar.get("short_before") or 0) - left, dry, cutoff),
            number=left,
            number_label="copies at risk",
            edition=ed,
            runs_dry=dry.isoformat(),
            arrives_at=eta.isoformat(),
            deadline=(ready if ready and cur < ready < dry else dry).isoformat(),
            level="red",
            status=status,
            ui=ui,
            approver=approver or viewer or None,
            signoff=signoff,
            routed_to_other=bool(signoff and status != "open"),
            reason="Extra transport costs money (not quoted yet): "
            f"{approver or 'the approver'} signs per the approval policy",
            options=options,
            evidence=[
                *(
                    {
                        "source": f"Reply from {e.get('from') or c['supplier_name']}",
                        "value": e.get("at"),
                        "quote": e.get("quote"),
                    }
                    for e in c.get("evidence") or []
                    if e.get("quote")
                ),
                {
                    "source": "Arrival",
                    "value": f"{_hm(eta, cur)}"
                    + (f" (estimated: {' + '.join(c['basis'])})" if c.get("estimated") else ""),
                },
                {
                    "source": "Packing clock",
                    "value": f"{name} runs out at {_hm(dry, cur)}, {_n(rate)} copies per hour",
                },
            ],
            trail=[
                {
                    "agent": "Commitments",
                    "at": cur.isoformat(),
                    "text": "Read the supplier's confirmation, estimated the arrival and held "
                    "it against the packing clock.",
                }
            ],
            supplier_id=c["supplier_id"],
            supplier_name=c["supplier_name"],
            created_at=c.get("confirmed_at") or cur.isoformat(),
            origin_card=c["card_id"],
            commitment=c["id"],
        )
        out.append(card)
        p.setdefault("cards", [])
        if cid not in p["cards"] and status in ("open", "escalated"):
            p["cards"].insert(0, cid)
    return out


def strip_adjust(strip: list[dict], launch_parts: list[dict], clock: Any, cur: datetime) -> None:
    """An edition's copies at risk and stop time include what its parts still miss after their
    commitments (a decided case leaves the list, its missing copies do not)."""
    from ask_soony import api

    for s in strip:
        for p in launch_parts:
            if not p.get("arithmetic") or p.get("editions") != [s["edition"]]:
                continue
            short = p.get("short") or 0
            if short > (s.get("copies_at_risk") or 0):
                s["copies_at_risk"] = short
            dry = api.parse_ts(p.get("runs_dry"))
            stops = api.parse_ts(s.get("stops_at"))
            if dry and short and (not stops or dry < stops):
                s["stops_at"] = dry.isoformat()
                s["finishes_at"] = None
                if s.get("breaks_first") is None or (p.get("cards") or [None])[0]:
                    s["breaks_first"] = {
                        "text": p.get("name") or p["part"],
                        "deadline": None,
                        "stops_at": dry.isoformat(),
                        "card": (p.get("cards") or [None])[0],
                    }
            if s.get("copies_at_risk") and s["level"] in ("blue", "grey") and short:
                s["level"], s["status"] = "red", "Stops the line"
