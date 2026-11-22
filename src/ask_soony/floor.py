"""
Ask the floor: where Soony's systems lag behind the warehouse, an agent asks the people at the
site and uses their answer as the truth until the systems catch up.

A site question is {kind, subject, last_known (value, as_of, source), asked_at, token, answer}.
Three kinds, all found in the portal data at request time (nothing about Soony is written here):

- "packed": the packing count of a launch edition is older than ASK_PACKOUT_STALE_HOURS shift
  hours (only time inside packing windows counts), so Launch only projects at plan rate. Answer:
  copies packed now. A reported count newer than the log replaces the projection.
- "received": a shipment to the site is due (ETA passed, or at the port / out for delivery) and
  no goods receipt is booked for its order and part. Answer: arrived yes/no and how many. A "yes,
  N" counts as stock (reported, not booked) until a goods receipt appears.
- "count": a launch part whose book stock is what covers the plan (short, or less than
  ASK_COUNT_MARGIN headroom) was last counted ASK_COUNT_STALE_DAYS ago or before heavy use
  (ASK_COUNT_USAGE_SHARE of the book stock used since). Answer: the number on the shelf, which
  replaces the book stock for that part.

Each question becomes an inbox card (kind = the question kind). Approving it sends the question
to the site through the Teams channel and a push, with a link to /count/<token>: a phone page
that needs no dashboard password, only a valid token (random, stored here, expires at launch).
The card closes itself ("answered") when the answer arrives or the books catch up.

State files (data/state, gitignored): site_questions.json (questions seen), count_tokens.json
(links), floor_counts.json (answers), floor_photos/ (photos of count sheets and delivery notes).
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
import re
import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response

from ask_soony import launch as launch_clock
from ask_soony.launch import PackClock

log = logging.getLogger("ask_soony.floor")
router = APIRouter()
_lock = threading.Lock()

KINDS = ("packed", "received", "count")
QUESTIONS = "site_questions.json"
TOKENS = "count_tokens.json"
ANSWERS = "floor_counts.json"
CLOSED = "answered"

# What a routine condition and step say for each kind, in the buyer's words.
KIND_WHEN = {
    "packed": "a packing count is stale",
    "received": "a shipment is due at the site but not booked in",
    "count": "the launch depends on book stock nobody counted recently",
}


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def stale_hours_limit() -> float:
    return _f("ASK_PACKOUT_STALE_HOURS", 2)


def _api():
    from ask_soony import api

    return api


def _n(n: float | None) -> str:
    return f"{n:,.0f}" if n is not None else "unknown"


def _hm(dt: datetime, cur: datetime) -> str:
    """'06:12' today, 'Sat 20:50' on another day."""
    tz = _api().tz()
    d = dt.astimezone(tz)
    return d.strftime("%H:%M") if d.date() == cur.astimezone(tz).date() else d.strftime("%a %H:%M")


def plural(name: str | None) -> str:
    """'Game Card 128 GB' -> 'game cards', 'Statue "The Getaway"' -> 'statues', "Collector's
    Box" -> "collector's boxes"; codes keep their case."""
    if not name:
        return "parts"
    base = name.split('"')[0].strip() or name
    words = base.split()
    while len(words) > 1 and re.fullmatch(r"\d[\w.]*|[A-Z]{2,}|[–-]", words[-1]):
        words.pop()
    words = [w.lower() if re.fullmatch(r"[A-Z][a-z']+", w) else w for w in words]
    last = words[-1]
    if re.fullmatch(r"[a-z]+", last) and not last.endswith("s"):
        words[-1] = last + ("es" if re.search(r"(x|z|ch|sh)$", last) else "s")
    return " ".join(words)


STATUS_WORDS = {
    "arrived_port": "at the port",
    "at_port": "at the port",
    "in_delivery": "out for delivery",
    "in_transit": "on its way",
    "delayed": "late",
    "booked": "booked",
    "arrived": "arrived",
}


def _day(d: date) -> str:
    return d.strftime("%a %-d %b")


# ---------------------------------------------------------------- site and portal data


@dataclass
class Site:
    id: str | None
    name: str
    city: str | None

    @property
    def short(self) -> str:
        return self.city or self.name


def site(root: Path | None) -> Site:
    """The distribution center from sites.csv (type dc): its city is what people call it."""
    api = _api()
    rows = api.read_csv(root / "soony" / "master" / "sites.csv") if root else []
    dc = next((r for r in rows if (r.get("type") or "").lower() == "dc"), None)
    if not dc:
        return Site(None, "the distribution center", None)
    return Site(dc.get("site_id"), dc.get("name") or dc.get("site_id") or "the DC", dc.get("city"))


def _csvs(root: Path, folder: str, pattern: str) -> list[tuple[str, dict]]:
    api = _api()
    base = root / "soony" / folder
    out = []
    for f in sorted(base.glob(pattern)) if base.is_dir() else []:
        out += [(str(f.relative_to(root)), r) for r in api.read_csv(f)]
    return out


def launch_bom(root: Path, editions: set[str]) -> dict[str, dict[str, float]]:
    """{material: {edition: qty per copy}} for the launch editions."""
    out: dict[str, dict[str, float]] = {}
    for _, r in _csvs(root, "master", "bom*.csv"):
        parent, child = r.get("parent_id"), r.get("child_id")
        qty = launch_clock._num(r.get("qty_per"))
        if parent in editions and child and qty and "qty_per" in r:
            out.setdefault(child, {})[parent] = qty
    return out


def _date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


def book_stock(root: Path) -> dict[str, dict]:
    """Book stock per material from the newest stock source: a stock workbook (a sheet whose
    header names material, qty and last count) or the stock snapshot CSV. {material: {book,
    last_count, used, as_of, source}}."""
    found: dict[str, dict] = {}

    def keep(mat: str, row: dict) -> None:
        old = found.get(mat)
        if old is None or (row["as_of"] or date.min) >= (old["as_of"] or date.min):
            found[mat] = row

    erp = root / "soony" / "erp"
    snaps: dict[str, dict] = {}
    for rel, r in _csvs(root, "erp", "stock*.csv"):
        mat, d = r.get("material_id"), _date(r.get("snapshot_date"))
        if not mat or "quantity" not in r:
            continue
        cur = snaps.setdefault(
            mat, {"book": 0.0, "last_count": None, "used": None, "as_of": d, "source": rel}
        )
        if d and cur["as_of"] and d > cur["as_of"]:
            cur.update(book=0.0, as_of=d, last_count=None)
        if d == cur["as_of"]:
            cur["book"] += launch_clock._num(r.get("quantity")) or 0
            lc = _date(r.get("last_count"))
            if lc and (cur["last_count"] is None or lc > cur["last_count"]):
                cur["last_count"] = lc
    for mat, row in snaps.items():
        keep(mat, row)
    for book in sorted(erp.glob("*.xlsx")) if erp.is_dir() else []:
        for sheet, rows in launch_clock._rows(book).items():
            found_t = launch_clock._table(rows, "material", "qty", "last count")
            if not found_t:
                continue
            header, body = found_t
            mc, qc = launch_clock._col(header, "material"), launch_clock._col(header, "qty")
            lc, uc = launch_clock._col(header, "last count"), launch_clock._col(header, "used")
            title = " ".join(str(c or "") for r in rows[:3] for c in r)
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", title)
            as_of = _date(dates[0]) if dates else None
            for r in body:
                mat = str(r[mc] or "").strip() if mc is not None and mc < len(r) else ""
                qty = launch_clock._num(r[qc]) if qc is not None and qc < len(r) else None
                if not mat or qty is None:
                    continue
                keep(
                    mat,
                    {
                        "book": qty,
                        "last_count": _date(r[lc]) if lc is not None and lc < len(r) else None,
                        "used": launch_clock._num(r[uc])
                        if uc is not None and uc < len(r)
                        else None,
                        "as_of": as_of,
                        "source": f"{book.relative_to(root)} · {sheet}",
                    },
                )
    return found


def receipts(root: Path) -> set[tuple[str, str]]:
    """(po_number, material) pairs with a goods receipt booked."""
    out = set()
    for _, r in _csvs(root, "erp", "goods_receipt*.csv"):
        if (launch_clock._num(r.get("qty_received")) or 0) > 0 and r.get("po_number"):
            out.add((str(r["po_number"]).strip(), str(r.get("material_id") or "").strip()))
    return out


def po_lines(root: Path) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for _, r in _csvs(root, "erp", "purchase_order*.csv"):
        key = (str(r.get("po_number") or "").strip(), str(r.get("material_id") or "").strip())
        out[key] = out.get(key, 0) + (launch_clock._num(r.get("quantity")) or 0)
    return out


def _line_word(root: Path, pid: str) -> str:
    """'the cell' or 'the lines', from the pack-out log's line column."""
    line = ""
    for _, r in _csvs(root, "erp", "packout_log*.csv"):
        if r.get("product_id") == pid:
            line = next((str(v) for k, v in r.items() if k and "line" in k.lower() and v), line)
    if "cell" in line.lower():
        return "the cell"
    return "the lines" if line else "packing"


# ---------------------------------------------------------------- state files


def answers() -> list[dict]:
    return _api().read_list(ANSWERS)


def _questions() -> dict:
    data = _api().read_json(QUESTIONS, {})
    return data if isinstance(data, dict) else {}


def _tokens() -> dict:
    data = _api().read_json(TOKENS, {})
    return data if isinstance(data, dict) else {}


def photos_dir() -> Path:
    return _api().state_dir() / "floor_photos"


def token_entry(token: str) -> dict | None:
    """The question behind a valid, unexpired link; None otherwise."""
    if not token or not re.fullmatch(r"[\w-]{16,64}", token):
        return None
    entry = _tokens().get(token)
    api = _api()
    if not entry:
        return None
    exp = api.parse_ts(entry.get("expires_at"))
    if exp is None or api.now() >= exp:
        return None
    return entry


# ---------------------------------------------------------------- the picture


@dataclass
class Floor:
    """Everything the floor knows for one request: per edition (packed), per part (received,
    counted) and the questions with their state."""

    site: Site
    editions: dict[str, dict]
    parts: dict[str, dict]
    questions: list[dict]


def prepare(root: Path | None, clock: PackClock, cur: datetime, net: dict) -> Floor:
    """Apply the site's answers to the packing clock (reported counts replace the projection) and
    work out which questions are open. Mutates `clock`."""
    api = _api()
    s = site(root)
    labels = api.edition_labels(root)
    all_answers = answers()
    eds = _packed(root, clock, cur, labels, all_answers)
    live: list[dict] = []
    parts: dict[str, dict] = {}
    if root is not None and clock.editions:
        try:
            live += _packed_questions(eds, cur, clock)
            live += _received_questions(root, clock, cur, net, s, labels, all_answers, parts, eds)
            live += _count_questions(root, clock, cur, labels, all_answers, parts, eds)
        except Exception as exc:  # one broken source must not hide the inbox
            log.warning("site questions failed: %s", exc)
    for q in live:
        q.setdefault("label", labels.get(q.get("edition") or "", q.get("subject")))
    return Floor(s, eds, parts, _sync(live, eds, all_answers, root, cur))


def _packed(root, clock: PackClock, cur: datetime, labels: dict, all_answers: list[dict]):
    out: dict[str, dict] = {}
    for pid, ed in clock.editions.items():
        log_n, log_at, log_src = ed.packed, ed.packed_as_of, ed.packed_source
        mine = [a for a in all_answers if a.get("kind") == "packed" and a.get("subject") == pid]
        rep = None
        for a in sorted(mine, key=lambda a: str(a.get("at"))):
            at = _api().parse_ts(a.get("at"))
            if at and (log_at is None or at > log_at) and a.get("count") is not None:
                rep = {**a, "_at": at}
        if rep:
            ed.packed, ed.packed_as_of = float(rep["count"]), rep["_at"]
            ed.packed_source = "reported"
        rate = ed.rate_per_hour
        left = clock.remaining_at(cur, pid)
        projected = (ed.day_one - left) if ed.day_one is not None and left is not None else None
        base = ed.packed_as_of
        shift_h = round(clock.shift_hours_between(base, cur), 1) if base and base < cur else 0.0
        age_h = round((cur - base).total_seconds() / 3600, 1) if base and base < cur else 0.0
        launch_at = clock.launch
        stale = bool(
            base
            and rate
            and left
            and launch_at
            and cur < launch_at
            and shift_h >= stale_hours_limit()
        )
        below = None
        if rep and log_n is not None and rate:
            plan_at = (
                log_n + rate * clock.shift_hours_between(log_at, rep["_at"]) if log_at else log_n
            )
            if ed.day_one is not None:
                plan_at = min(plan_at, ed.day_one)
            gap = plan_at - float(rep["count"])
            below = {
                "copies": round(gap),
                "since": log_at.isoformat() if log_at else None,
                "stood_still": gap >= rate * _f("ASK_COUNT_STILL_HOURS", 1),
            }
        out[pid] = {
            "edition": pid,
            "label": labels.get(pid, pid),
            "rate": rate,
            "day_one": ed.day_one,
            "log": {
                "number": log_n,
                "as_of": log_at.isoformat() if log_at else None,
                "source": log_src,
            },
            "counted": {
                "number": ed.packed,
                "as_of": base.isoformat() if base else None,
                "source": ed.packed_source,
                "by": (rep or {}).get("by"),
                "reported": bool(rep),
            },
            "projected": round(projected) if projected is not None else None,
            "stale_hours": shift_h,
            "age_hours": age_h,
            "stale": stale,
            "reported": _public_answer(rep) if rep else None,
            "below_plan": below,
            "line": _line_word(root, pid) if root else "packing",
        }
    return out


def _public_answer(a: dict) -> dict:
    out = {k: v for k, v in a.items() if not k.startswith("_") and k != "token"}
    if a.get("photo"):
        out["photo_url"] = f"/api/floor/photos/{a['photo']}"
    return out


def _stamp(dt: datetime | date | None) -> str:
    if dt is None:
        return "none"
    return dt.strftime("%m%d%H%M") if isinstance(dt, datetime) else dt.strftime("%m%d")


def _packed_questions(eds: dict[str, dict], cur: datetime, clock: PackClock) -> list[dict]:
    out = []
    for pid, e in eds.items():
        if not e["stale"]:
            continue
        base = _api().parse_ts(e["counted"]["as_of"])
        out.append(
            {
                "id": f"ASK-packed-{pid}-{_stamp(base)}",
                "kind": "packed",
                "subject": pid,
                "edition": pid,
                "last_known": {
                    "value": e["counted"]["number"],
                    "as_of": e["counted"]["as_of"],
                    "source": e["counted"]["source"],
                },
            }
        )
    return out


def _eds(per: dict, labels: dict) -> str:
    return " and ".join(labels.get(e, e) for e in per)


def _needs(editions_text: str) -> str:
    return "need" if " and " in editions_text else "needs"


def _need_now(bom: dict[str, float], clock: PackClock, at: datetime) -> float:
    return sum(q * (clock.remaining_at(at, ed) or 0) for ed, q in bom.items())


def consumed_since(st: dict, per: dict[str, float], eds: dict[str, dict]) -> float:
    """Parts used since the stock figure: book stock is backflushed (packed copies times parts
    per box), so every copy packed after the stock update also leaves the books. Packed now is
    the site's count when it reported one, else the projection. Counted from the edition's
    packing count of the stock's day; 0 when the two cannot be lined up."""
    out = 0.0
    as_of = st.get("as_of")
    for ed, q in per.items():
        e = eds.get(ed) or {}
        log_at = _api().parse_ts((e.get("log") or {}).get("as_of"))
        base, now = (e.get("log") or {}).get("number"), e.get("projected")
        if as_of is None or log_at is None or base is None or now is None:
            continue
        if log_at.date() >= as_of:  # the stock and the packing count are from the same day
            out += q * max(now - base, 0)
    return out


def _received_questions(
    root, clock, cur, net, s: Site, labels, all_answers, parts, eds=None
) -> list[dict]:
    api = _api()
    bom = launch_bom(root, set(clock.editions))
    booked = receipts(root)
    lines = po_lines(root)
    stock = book_stock(root)
    names = api.part_names(root)
    nodes = net.get("nodes") or {}
    out = []
    for r in net.get("shipments_raw") or []:
        sid = str(r.get("shipment_id") or r.get("id") or "")
        status = str(r.get("status") or "").lower()
        dest = r.get("dest_site") or r.get("destination_site")
        if (
            not sid
            or status in ("delivered", "received", "cancelled")
            or (dest and s.id and dest != s.id)
        ):
            continue
        eta = _date(r.get("eta"))
        due = (eta is not None and eta < cur.date()) or status in (
            "in_delivery",
            "arrived_port",
            "arrived",
            "at_port",
        )
        if not due:
            continue
        pos = [p.strip() for p in str(r.get("po_numbers") or "").split("|") if p.strip()]
        mats = [m.strip() for m in str(r.get("material_ids") or "").split("|") if m.strip()]
        for mat in mats:
            if mat not in bom:
                continue
            mine = [p for p in pos if (p, mat) in lines] or pos
            if any((p, mat) in booked for p in mine):
                continue
            qty = sum(lines.get((p, mat), 0) for p in mine) or None
            qid = f"ASK-received-{sid}-{mat}"
            ans = next((a for a in reversed(all_answers) if a.get("question") == qid), None)
            if ans and ans.get("arrived") and ans.get("count"):
                p = parts.setdefault(mat, {})
                p.setdefault("received", []).append(
                    {
                        "reference": r.get("reference") or sid,
                        "shipment": sid,
                        "quantity": float(ans["count"]),
                        "at": ans.get("at"),
                        "by": ans.get("by"),
                    }
                )
            st = stock.get(mat) or {}
            need = _need_now(bom[mat], clock, cur)
            left = (st.get("book") or 0) - (consumed_since(st, bom[mat], eds or {}) if st else 0)
            short = max(need - max(left, 0), 0)
            sup = nodes.get(r.get("supplier_id")) or {}
            noun = plural(names.get(mat) or mat)
            eds = _eds(bom[mat], labels)
            out.append(
                {
                    "id": qid,
                    "kind": "received",
                    "subject": f"{sid}:{mat}",
                    "shipment": sid,
                    "part": mat,
                    "part_name": noun,
                    "edition": next(iter(bom[mat])),
                    "supplier_id": r.get("supplier_id"),
                    "supplier_name": sup.get("name"),
                    "quantity": qty,
                    "status": status,
                    "eta": eta.isoformat() if eta else None,
                    "short": short,
                    "editions_text": eds,
                    "note": r.get("note"),
                    "last_known": {
                        "value": STATUS_WORDS.get(status, status.replace("_", " ")),
                        "as_of": r.get("updated_at"),
                        "source": "soony/erp/open_shipments.csv",
                    },
                }
            )
    return out


def _count_questions(root, clock, cur, labels, all_answers, parts, eds=None) -> list[dict]:
    api = _api()
    bom = launch_bom(root, set(clock.editions))
    stock = book_stock(root)
    names = api.part_names(root)
    margin = _f("ASK_COUNT_MARGIN", 0.25)
    days = _f("ASK_COUNT_STALE_DAYS", 2)
    share = _f("ASK_COUNT_USAGE_SHARE", 0.5)
    out = []
    for mat, per in bom.items():
        st = stock.get(mat)
        if not st or st.get("book") is None:
            continue
        book, last = float(st["book"]), st.get("last_count")
        if book <= 0:
            continue  # nothing on the books to check
        # A count answered after the books' last count replaces the book stock.
        ans = None
        for a in sorted(
            (a for a in all_answers if a.get("kind") == "count" and a.get("subject") == mat),
            key=lambda a: str(a.get("at")),
        ):
            at = api.parse_ts(a.get("at"))
            if at and (last is None or at.date() >= last):
                ans = a
        if ans:
            parts.setdefault(mat, {})["counted"] = {
                "count": float(ans["count"]),
                "at": ans.get("at"),
                "by": ans.get("by"),
                "book": book,
                "photo_url": f"/api/floor/photos/{ans['photo']}" if ans.get("photo") else None,
            }
        need = _need_now(per, clock, cur)
        since = consumed_since(st, per, eds or {})
        left = max(book - since, 0)  # what the books say now, after packing since the update
        if need <= 0 or left > need * (1 + margin):
            continue
        age = (cur.date() - last).days if last else None
        used = (st.get("used") or 0) + since
        heavy = bool(
            used and last and st.get("as_of") and last >= st["as_of"] and used >= share * book
        )
        if not (age is None or age >= days or heavy):
            continue
        noun = plural(names.get(mat) or mat)
        out.append(
            {
                "id": f"ASK-count-{mat}-{_stamp(last)}",
                "kind": "count",
                "subject": mat,
                "part": mat,
                "part_name": noun,
                "edition": next(iter(per)),
                "book": book,
                "book_now": round(left),
                "used_since": round(since),
                "need": round(need),
                "last_count": last.isoformat() if last else None,
                "used": used if heavy else None,
                "editions_text": _eds(per, labels),
                "last_known": {
                    "value": book,
                    "as_of": st["as_of"].isoformat() if st.get("as_of") else None,
                    "source": st.get("source"),
                },
            }
        )
    return out


def _sync(live: list[dict], eds: dict, all_answers: list[dict], root, cur: datetime) -> list[dict]:
    """Remember every question seen, so a closed one stays in the history; mark each open or
    answered. Writes only when a new question appears."""
    api = _api()
    live_ids = {q["id"] for q in live}
    with _lock:
        known = _questions()
        new = [q for q in live if q["id"] not in known]
        for q in new:
            known[q["id"]] = {**q, "opened_at": cur.isoformat()}
        if new:
            api.write_json(QUESTIONS, known)
    by_q: dict[str, dict] = {}
    notes: dict[str, list[dict]] = {}
    for a in all_answers:
        if not a.get("question"):
            continue
        if a.get("kind") == "received" and a.get("arrived") is False:
            notes.setdefault(a["question"], []).append(a)  # not there yet: stays open
        else:
            by_q.setdefault(a["question"], a)  # the first answer closes it
    out = []
    for qid, saved in known.items():
        q = {**saved, **next((x for x in live if x["id"] == qid), {})}
        ans = by_q.get(qid)
        if q.get("kind") == "packed" and not ans:
            # A newer count from anyone (the site or the log) answers the question.
            e = eds.get(q.get("subject") or "")
            if e and q["last_known"].get("as_of") != e["counted"]["as_of"]:
                ans = e["reported"] or {
                    "by_books": True,
                    "count": e["counted"]["number"],
                    "at": e["counted"]["as_of"],
                }
        if not ans and qid not in live_ids:
            ans = {"gone": True, "at": None}  # the reason to ask went away
        q["notes"] = [_public_answer(a) for a in notes.get(qid, [])]
        q["state"] = CLOSED if ans else "open"
        q["answer"] = _public_answer(ans) if ans else None
        out.append(q)
    return out


# ---------------------------------------------------------------- cards


def _sentence(q: dict, f: Floor, cur: datetime) -> tuple[str, str, str]:
    """Headline, the one-sentence contradiction (why it matters in copies or time), reason."""
    api = _api()
    city = f.site.short
    if q["kind"] == "packed":
        e = f.editions.get(q["subject"]) or {}
        lk = q["last_known"]
        at = api.parse_ts(lk.get("as_of"))
        age = int(((cur - at).total_seconds() / 3600) if at else 0)
        who = (
            f"{city} last reported"
            if lk.get("source") == "reported"
            else "The pack-out log last counted"
        )
        rate = e.get("rate") or 0
        clock_hours = e.get("stale_hours") or 0
        missing = round(rate * clock_hours)
        proj = e.get("projected")
        line = e.get("line") or "packing"
        ans = q.get("answer") or {}
        ans_at = api.parse_ts(ans.get("at"))
        if q.get("state") == CLOSED and ans_at and ans.get("count") is not None and at:
            # Closed: say what the answer was, as of the answer, not a projection that moved on.
            age = int((ans_at - at).total_seconds() / 3600)
            src = "the pack-out log" if ans.get("by_books") else city
            below = (e.get("below_plan") or {}) if not ans.get("by_books") else {}
            tail = (
                f", {_n(below['copies'])} fewer than planned: {line} likely stood still."
                if below.get("stood_still")
                else "."
            )
            head = f"{e.get('label', q['subject'])} count was {age} h old"
            body = (
                f"{who} {_n(lk.get('value'))} at {_hm(at, cur)}; {src} reported "
                f"{_n(ans['count'])} at {_hm(ans_at, cur)}{tail}"
            )
            return head, body, "Answered: the reported count replaces the projection."
        head = f"{e.get('label', q['subject'])} count is {age} h old"
        body = (
            f"{who} {_n(lk.get('value'))} at {_hm(at, cur) if at else 'an unknown time'}; we "
            f"assume {_n(proj)} by now, and if {line} stopped it is already {_n(missing)} short."
        )
        reason = (
            f"{clock_hours:g} packing hours without a count; Launch projects at plan rate "
            f"({_n(rate)} an hour) until someone counts."
        )
        return head, body, reason
    if q["kind"] == "received":
        qty = q.get("quantity")
        what = f"{_n(qty)} {q['part_name']}" if qty else q["part_name"]
        sup = q.get("supplier_name") or "the supplier"
        head = f"Did the {what} from {sup} reach {city}?"
        since = api.parse_ts(q["last_known"].get("as_of"))
        status = q["last_known"]["value"]
        short = q.get("short")
        why = (
            f"{q['editions_text']} {_needs(q['editions_text'])} {_n(short)} more "
            f"{q['part_name']} than {city}'s books show"
            if short
            else f"The launch relies on these {q['part_name']}"
        )
        note = f" ({q['note']})" if q.get("note") else ""
        body = (
            f"{why}; shipment {q['shipment']} is {status} since "
            f"{_hm(since, cur) if since else 'an unknown time'}{note}, but no goods receipt is "
            "booked."
        )
        reason = "Due at the site with no goods receipt: only the dock knows whether it is there."
        return head, body, reason
    book, need = q.get("book"), q.get("need")
    last = _date(q.get("last_count"))
    when = _day(last) if last else "never"
    head = f"Are there really {_n(book)} {q['part_name']} in {city}?"
    used = f" before {_n(q['used'])} were used" if q.get("used") else ""
    left = q.get("book_now", book)
    says = f"say {_n(book)}"
    if q.get("used_since"):
        says += f", about {_n(left)} after packing since"
    if left is not None and need is not None and left < need:
        body = (
            f"{q['editions_text']} {_needs(q['editions_text'])} {_n(need)} {q['part_name']} "
            "until the trucks; the books "
            f"{says}, last counted {when}{used}, so the gap is at least {_n(need - left)}."
        )
    else:
        body = (
            f"{q['editions_text']} {_needs(q['editions_text'])} {_n(need)} {q['part_name']} "
            "until the trucks; the books "
            f"{says}, only {_n((left or 0) - (need or 0))} spare, last counted {when}{used}."
        )
    reason = "Book stock, not counted: the shelf may hold less than the system says."
    return head, body, reason


OPTION_ASK = {
    "packed": "Ask {city} for the current count",
    "received": "Ask {city} whether it arrived",
    "count": "Ask {city} to count the shelf",
}
OPTION_WAIT = {
    "packed": ("Wait for the next pack-out log", "Launch keeps projecting at plan rate."),
    "received": ("Wait for the goods receipt", "The shipment stays uncounted until it is booked."),
    "count": ("Keep the book stock", "Launch keeps trusting the book stock."),
}


def _effect(kind: str, city: str) -> str:
    use = {
        "packed": "their number replaces the projection",
        "received": "a yes counts as stock until the goods receipt is booked",
        "count": "their number replaces the book stock",
    }[kind]
    return f"Sends {city} a one-minute form on Teams and as a phone alert; {use}"


def cards(
    f: Floor, statuses: dict, actions: list[dict], trust: dict, viewer: str, cur: datetime
) -> list[dict]:
    api = _api()
    city = f.site.short
    trusted = [k for k, v in trust.items() if v == "allow_always"]
    by_card: dict[str, list[dict]] = {}
    for a in actions:
        by_card.setdefault(str(a.get("decision_id")), []).append(a)
    out = []
    for q in f.questions:
        qid, kind = q["id"], q["kind"]
        head, body, reason = _sentence(q, f, cur)
        lk = q["last_known"]
        status, ui = api._ui_status(qid, statuses)
        if q["state"] == CLOSED:
            status = CLOSED
        e = f.editions.get(q.get("edition") or "") or {}
        number = None
        if kind == "packed":
            number = round((e.get("rate") or 0) * (e.get("stale_hours") or 0))
        elif kind == "received":
            number = q.get("short")
        elif q.get("book") is not None and q.get("need") is not None:
            number = max(q["need"] - q.get("book_now", q["book"]), 0) or None
        level = "amber"
        if kind != "packed" and not number:
            level = "grey"
        options = [
            {
                "index": 0,
                "action": OPTION_ASK[kind].format(city=city),
                "cost": "No cost",
                "cost_value": None,
                "effect": _effect(kind, city),
                "recommended": True,
                "draft": None,
                "to": city,
                "channel": "floor",
                "routine_type": f"site_{kind}",
            },
            {
                "index": 1,
                "action": OPTION_WAIT[kind][0],
                "cost": "No cost",
                "cost_value": None,
                "effect": OPTION_WAIT[kind][1],
                "recommended": False,
                "draft": None,
                "to": None,
                "channel": "wait",
                "routine_type": None,
            },
        ]
        doc = str(lk.get("source") or "").split(" · ")[0] or None
        evidence = [
            {
                "source": {
                    "packed": "Last count",
                    "received": "Shipment feed",
                    "count": "Book stock",
                }[kind],
                "value": (
                    _n(lk["value"])
                    if isinstance(lk.get("value"), (int, float))
                    else str(lk.get("value"))
                )
                + (
                    f" at {_hm(api.parse_ts(lk['as_of']), cur)}"
                    if api.parse_ts(lk.get("as_of"))
                    else ""
                ),
                "doc": doc if doc and doc != "reported" else None,
            }
        ]
        ans = q.get("answer")
        if ans and not ans.get("by_books") and not ans.get("gone"):
            evidence.append(_answer_evidence(ans, city, cur))
        opened = q.get("opened_at")
        trail = [{"agent": "Floor check", "at": opened, "text": reason}]
        card = api._base_card(
            qid,
            kind=kind,
            part=q.get("part"),
            headline=head,
            summary=body,
            number=number,
            number_label={
                "packed": "copies not counted",
                "received": "units short",
                "count": "units short",
            }[kind],
            edition=q.get("edition"),
            level=level,
            status=status,
            ui=ui,
            approver=viewer or None,
            reason=reason,
            options=options,
            evidence=evidence,
            trail=trail,
            supplier_id=q.get("supplier_id"),
            supplier_name=q.get("supplier_name"),
            shipment=q.get("shipment"),
            created_at=opened,
            trusted_types=trusted,
        )
        card["site_question"] = {
            "kind": kind,
            "subject": q["subject"],
            "last_known": lk,
            "asked_at": next(
                (a.get("at") for a in by_card.get(qid, []) if a.get("channel") == "floor"), None
            ),
            "answer": ans,
            "site": city,
        }
        card["group"] = f"site|{kind}"
        card["escalation_brief"] = f"{head}. {body}"
        card["activity"] = _activity(card, q, by_card.get(qid, []), city, f, cur)
        out.append(card)
    return out


def _answer_evidence(ans: dict, city: str, cur: datetime) -> dict:
    api = _api()
    at = api.parse_ts(ans.get("at"))
    who = f" by {ans['by']}" if ans.get("by") else ""
    if ans.get("kind") == "received" and not ans.get("arrived"):
        value = f"not arrived{who}"
    else:
        value = f"{_n(ans.get('count'))} at {_hm(at, cur) if at else '?'}{who}"
    ev = {
        "source": f"Reported by {city} on the count page",
        "value": value,
        "quote": "photo of the sheet"
        if ans.get("photo")
        else f"reported by {ans.get('by') or city} on the count page",
    }
    if ans.get("photo"):
        ev["photo"] = f"/api/floor/photos/{ans['photo']}?crop=1"
    return ev


def _what(q: dict, f: Floor) -> str:
    if q["kind"] == "packed":
        return f"for the {(f.editions.get(q['subject']) or {}).get('label', q['subject'])} count"
    if q["kind"] == "received":
        return f"whether {q.get('shipment')} arrived"
    return f"to count the {q.get('part_name') or q['subject']}"


def _activity(card: dict, q: dict, acts: list[dict], city: str, f: Floor, cur: datetime):
    api = _api()
    items = []
    for a in acts:
        if a.get("kind") == "approve" and a.get("channel") == "floor":
            routine = str(a.get("via") or "").lower().startswith("routine")
            items.append(
                {
                    "at": a.get("at"),
                    "kind": "sent",
                    "card": card["id"],
                    "text": f"{'Your routine asked' if routine else 'Asked'} {city} {_what(q, f)}",
                    "detail": None,
                    "via": a.get("via"),
                    "action_id": a.get("id"),
                }
            )
        elif a.get("kind") == "dismiss":
            items.append(
                {
                    "at": a.get("at"),
                    "kind": "dismissed",
                    "card": card["id"],
                    "text": f"{OPTION_WAIT[q['kind']][0]}: {card['headline']}",
                    "detail": a.get("note"),
                    "via": a.get("via"),
                    "action_id": a.get("id"),
                }
            )
    for n in q.get("notes") or []:
        at = api.parse_ts(n.get("at"))
        items.append(
            {
                "at": n.get("at"),
                "kind": "note",
                "card": card["id"],
                "text": f"{city} reported {q.get('shipment')} has not arrived"
                + (f" ({_hm(at, cur)})" if at else ""),
                "detail": f"by {n['by']}" if n.get("by") else None,
                "via": None,
                "action_id": None,
            }
        )
    ans = q.get("answer")
    if ans:
        at = api.parse_ts(ans.get("at"))
        if ans.get("gone"):
            text = f"No longer needed: {card['headline'].rstrip('?')}"
        elif ans.get("by_books"):
            text = f"The books caught up: {card['headline'].rstrip('?')}"
        elif q["kind"] == "received" and not ans.get("arrived"):
            text = f"{city} reported {q.get('shipment')} has not arrived"
        else:
            text = f"{city} reported {_n(ans.get('count'))}" + (f" at {_hm(at, cur)}" if at else "")
        items.append(
            {
                "at": ans.get("at") or cur.isoformat(),
                "kind": "answered",
                "card": card["id"],
                "text": text,
                "detail": f"by {ans['by']}" if ans.get("by") else None,
                "via": None,
                "action_id": None,
            }
        )
    return items


# ---------------------------------------------------------------- asking


def link_for(token: str) -> str:
    from ask_soony import teams

    return f"{teams._app_url()}/count/{token}"


def ask(card: dict) -> dict:
    """Send the question on a card to the site: a fresh link on Teams and as a push. Returns what
    went out; raises 503 when no channel reaches the site."""
    from ask_soony import push, teams

    api = _api()
    sq = card.get("site_question") or {}
    root = api.portal_root()
    clock = launch_clock.load(root, api.tz())
    expires = clock.launch or (api.now() + timedelta(hours=24))
    token = secrets.token_urlsafe(18)
    with _lock:
        tokens = _tokens()
        cur = api.now()
        tokens = {
            k: v for k, v in tokens.items() if (api.parse_ts(v.get("expires_at")) or cur) > cur
        }
        tokens[token] = {
            "question": card["id"],
            "kind": sq.get("kind"),
            "subject": sq.get("subject"),
            "created_at": cur.isoformat(),
            "expires_at": expires.isoformat(),
        }
        api.write_json(TOKENS, tokens)
    url = link_for(token)
    city = sq.get("site") or "the site"
    title = card["headline"]
    channels = []
    try:
        if teams.post_payload(teams.question_payload(card, url)):
            channels.append("teams")
    except Exception as exc:  # Teams is one of two channels
        log.warning("teams question failed: %s", exc)
    try:
        if push.subscriptions() and push.send(f"Question for {city}", title, None, url=url).get(
            "sent"
        ):
            channels.append("push")
    except Exception as exc:
        log.warning("push question failed: %s", exc)
    if not channels:
        raise HTTPException(503, f"No Teams channel or phone is set up to reach {city}")
    return {
        "message_id": None,
        "to": city,
        "subject": title,
        "sent_body": f"{title} {url}",
        "link": url,
        "token": token,
        "channels": channels,
    }


# ---------------------------------------------------------------- the count page


def _question_for(token: str) -> tuple[dict, dict, dict]:
    entry = token_entry(token)
    if entry is None:
        raise HTTPException(404, "This link is not valid any more.")
    state = _api().build_state()
    card = next((c for c in state["cards"] if c["id"] == entry.get("question")), None)
    if card is None:
        raise HTTPException(404, "This question is closed.")
    return entry, card, state


def form(card: dict, state: dict, cur: datetime) -> dict:
    """What the count page shows and asks, per kind."""
    api = _api()
    sq = card["site_question"]
    kind, lk = sq["kind"], sq["last_known"]
    at = api.parse_ts(lk.get("as_of"))
    launch = state.get("launch") or {}
    floor = launch.get("floor") or {}
    base = {
        "kind": kind,
        "site": sq.get("site"),
        "question": card["id"],
        "answered": card["status"] == CLOSED,
        "answer": sq.get("answer"),
    }
    if kind == "packed":
        e = (floor.get("editions") or {}).get(sq["subject"]) or {}
        return base | {
            "title": f"How many {e.get('label', '')} copies are packed?",
            "edition": e.get("label"),
            "last": {
                "label": "Last counted",
                "value": lk.get("value"),
                "at": lk.get("as_of"),
                "when": _hm(at, cur) if at else None,
            },
            "field": "Copies packed now",
            "min": lk.get("value") or 0,
            "max": e.get("day_one"),
            "photo": "Photo of the count sheet",
        }
    q = next((x for x in (floor.get("questions") or []) if x["id"] == card["id"]), {})
    if kind == "received":
        qty = q.get("quantity")
        return base | {
            "title": f"Did shipment {q.get('shipment')} arrive?",
            "part": q.get("part_name"),
            "supplier": q.get("supplier_name"),
            "last": {
                "label": "Expected",
                "value": qty,
                "unit": q.get("part_name"),
                "at": q.get("eta"),
                "when": q.get("eta"),
            },
            "field": f"{q.get('part_name') or 'Units'} received",
            "min": 1,
            "max": qty * 1.5 if qty else None,
            "photo": "Photo of the delivery note",
        }
    return base | {
        "title": f"How many {q.get('part_name') or 'units'} are on the shelf?",
        "part": q.get("part_name"),
        "last": {
            "label": "Book stock",
            "value": q.get("book"),
            "at": q.get("last_count"),
            "when": f"last counted {_day(_date(q.get('last_count')))}"
            if q.get("last_count")
            else None,
        },
        "field": f"{q.get('part_name') or 'Units'} counted now",
        "min": 0,
        "max": (q.get("book") or 0) * 3 or None,
        "photo": "Photo of the count sheet",
    }


_NUM = re.compile(r"^\d{1,3}(?:[.,'’ ]\d{3})+$|^\d+$")
_NOTE = re.compile(r"\b[A-Z]{1,4}[-/ ]?\d{4,}(?:[-/]\d+)*\b")


def _parse_num(text: str) -> float | None:
    t = text.strip().strip(".,;:()")
    if not _NUM.match(t):
        return None
    return float(re.sub(r"[.,'’ ]", "", t))


def suggest_numbers(words: list[list], lo: float, hi: float | None) -> dict:
    """The largest plausible number among OCR words (and digit groups split by a space), with
    its box; never submitted on its own."""
    cands: list[tuple[float, list[float]]] = []
    for i, w in enumerate(words):
        n = _parse_num(str(w[0]))
        if n is not None:
            cands.append((n, list(w[1:5])))
        if i + 1 < len(words):
            nxt = words[i + 1]
            if (
                re.fullmatch(r"\d{1,3}", str(w[0]))
                and re.fullmatch(r"\d{3}", str(nxt[0]))
                and abs(float(w[2]) - float(nxt[2])) < 0.02
            ):
                box = [min(w[1], nxt[1]), min(w[2], nxt[2]), max(w[3], nxt[3]), max(w[4], nxt[4])]
                cands.append((float(f"{w[0]}{nxt[0]}"), box))
    plausible = [c for c in cands if c[0] >= lo and (hi is None or c[0] <= hi)]
    best = max(plausible, key=lambda c: c[0]) if plausible else None
    text = " ".join(str(w[0]) for w in words)
    note = _NOTE.search(text)
    return {
        "suggestion": best[0] if best else None,
        "box": best[1] if best else None,
        "numbers": sorted({c[0] for c in cands}, reverse=True)[:8],
        "note_number": note.group(0) if note else None,
    }


def save_photo(token: str, data_url: str) -> tuple[str, Any]:
    from PIL import Image, ImageOps

    raw = data_url.split(",", 1)[-1]
    try:
        blob = base64.b64decode(raw, validate=False)
        img = Image.open(io.BytesIO(blob))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        raise HTTPException(400, "That photo could not be read. Try again.") from None
    img.thumbnail((2000, 2000))
    name = f"{hashlib.sha1(token.encode()).hexdigest()[:8]}-{uuid.uuid4().hex[:10]}.jpg"
    photos_dir().mkdir(parents=True, exist_ok=True)
    img.save(photos_dir() / name, format="JPEG", quality=85)
    return name, img


@router.get("/api/count/{token}")
def get_question(token: str):
    _, card, state = _question_for(token)
    return form(card, state, _api().now())


@router.post("/api/count/{token}/photo")
def post_photo(token: str, body: dict = Body(...)):
    """Store a photo of the sheet and suggest the number on it (local OCR)."""
    from ask_soony import anchors

    _, card, state = _question_for(token)
    if not isinstance(body.get("image"), str) or len(body["image"]) > 25_000_000:
        raise HTTPException(400, "Send one photo.")
    name, img = save_photo(token, body["image"])
    f = form(card, state, _api().now())
    words = anchors.ocr_words(img)
    out = suggest_numbers(words, float(f.get("min") or 0), f.get("max"))
    out["photo"] = name
    if out["box"]:
        boxes = _api().read_json("floor_photo_boxes.json", {})
        boxes[name] = out["box"]
        _api().write_json("floor_photo_boxes.json", boxes)
    return out


@router.post("/api/count/{token}")
def post_answer(token: str, body: dict = Body(...)):
    api = _api()
    entry, card, state = _question_for(token)
    cur = api.now()
    f = form(card, state, cur)
    kind = f["kind"]
    arrived = True
    if kind == "received":
        arrived = body.get("arrived") is not False and str(body.get("arrived")).lower() != "no"
    count = None
    if arrived:
        try:
            count = float(str(body.get("count")).replace(",", "").replace(".", "").replace(" ", ""))
        except ValueError:
            raise HTTPException(422, "Please type the number.") from None
        lo, hi = float(f.get("min") or 0), f.get("max")
        if count < lo:
            raise HTTPException(
                422,
                f"That is less than the {_n(lo)} already counted"
                + (f" at {f['last']['when']}" if kind == "packed" and f["last"].get("when") else "")
                + ". Please count again.",
            )
        if hi is not None and count > hi:
            raise HTTPException(422, f"That is more than {_n(hi)}. Please check the number.")
    photo = str(body.get("photo") or "") or None
    if photo and not (
        re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{10}\.jpg", photo) and (photos_dir() / photo).is_file()
    ):
        photo = None
    by = str(body.get("by") or "").strip()[:60] or None
    claim = {
        "id": uuid.uuid4().hex[:10],
        "question": card["id"],
        "kind": kind,
        "subject": entry.get("subject"),
        **({"edition": entry.get("subject")} if kind == "packed" else {}),
        "count": count,
        **({"arrived": arrived} if kind == "received" else {}),
        **({"note_number": str(body["note_number"])[:40]} if body.get("note_number") else {}),
        "at": cur.isoformat(),
        "by": by,
        "photo": photo,
        "box": api.read_json("floor_photo_boxes.json", {}).get(photo) if photo else None,
        "token": token,
    }
    with _lock:
        items = answers()
        items.append(claim)
        api.write_json(ANSWERS, items)
    if kind == "received" and not arrived:
        text = f"Thanks. {f.get('site')} has not received it yet; we keep it open."
    elif kind == "packed":
        text = f"Thanks. Launch now uses {_n(count)} packed at {_hm(cur, cur)}."
    elif kind == "received":
        text = (
            f"Thanks. Launch counts {_n(count)} {f.get('part')} as received, until the goods "
            "receipt is booked."
        )
    else:
        text = f"Thanks. Launch now uses {_n(count)} {f.get('part')} on the shelf."
    return {"ok": True, "text": text, "answer": _public_answer(claim)}


@router.get("/api/floor/photos/{name}")
def get_photo(name: str, crop: int = 0):
    """A photo from the count page (dashboard only), or the crop around the suggested number."""
    from PIL import Image

    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{10}\.jpg", name):
        raise HTTPException(404)
    path = photos_dir() / name
    if not path.is_file():
        raise HTTPException(404)
    box = _api().read_json("floor_photo_boxes.json", {}).get(name) if crop else None
    if not box:
        return Response(path.read_bytes(), media_type="image/jpeg")
    with Image.open(path) as im:
        w, h = im.size
        pad_x, pad_y = 0.12, 0.08
        x0, y0 = max(box[0] - pad_x, 0) * w, max(box[1] - pad_y, 0) * h
        x1, y1 = min(box[2] + pad_x, 1) * w, min(box[3] + pad_y, 1) * h
        buf = io.BytesIO()
        im.crop((int(x0), int(y0), int(x1), int(y1))).save(buf, format="JPEG", quality=88)
    return Response(buf.getvalue(), media_type="image/jpeg")


def public_path(path: str) -> bool:
    """True for the count page and its API when the token in the path is valid: these need no
    dashboard password."""
    m = re.fullmatch(r"/count/([\w-]+)/?|/api/count/([\w-]+)(?:/photo)?", path)
    if not m:
        return False
    return token_entry(m.group(1) or m.group(2)) is not None


def view(f: Floor) -> dict:
    """The floor's part of /api/state launch: per edition and per part, plus open questions."""
    return {
        "site": f.site.short,
        "stale_after_hours": stale_hours_limit(),
        "editions": f.editions,
        "parts": f.parts,
        "questions": [
            {k: v for k, v in q.items() if k != "answer"} | {"answered": q["state"] == CLOSED}
            for q in f.questions
        ],
    }
