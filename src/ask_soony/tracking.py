"""
Shipment truth: one reconciled status per shipment, from every source, instead of trusting the
hand-kept shipment list.

Step 4 "Track" at the customer is a list someone updates "when someone remembers". Other sources
know more: Hamburg books goods receipts, the site answers "did it arrive?", suppliers send
shipping advices, packing lists and delay notices, and they reply to our emails. Each source
becomes a claim about a shipment {source, label, status, qty, at, anchor, text}; the derived
status follows a fixed precedence:

    goods receipt  >  site answer  >  supplier document / reply  >  the list

A document or reply only beats the list when it is not older than the list's row and not behind
it (a "shipped" never undoes "at the port"), or when it reports a delay (risk signals stay).
Contradictions are listed, never silently resolved ("System export: in transit (20 Nov); Hamburg
booked GR 5000413067 on 21 Nov").

Silence is measured against the clock: last_heard is the newest timestamp across all sources for
the shipment, silent_hours = now - last_heard. A frozen list (newest updated_at older than
ASK_TRACKER_STALE_HOURS, default 12) that contradicts other sources is one finding, not one alarm
per row. Per-shipment silence only alerts for shipments that are not received, carry a launch
part that matters (short, or due before the trucks leave) and have no fresher source.

Pure functions take plain rows; `collect` reads the portal and state files. Nothing about the
customer is written here: files, columns, ids and values are discovered at run time.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

STATUSES = (
    "booked",
    "departed",
    "in_transit",
    "at_port",
    "delayed",
    "arrived_not_booked",
    "received",
    "unknown",
)
RANK = {
    "booked": 0,
    "departed": 1,
    "in_transit": 2,
    "delayed": 2,
    "at_port": 3,
    "arrived_not_booked": 4,
    "received": 5,
    "unknown": -1,
}
LABEL = {
    "booked": "booked",
    "departed": "departed",
    "in_transit": "in transit",
    "at_port": "at the port",
    "delayed": "delayed",
    "arrived_not_booked": "arrived, not booked",
    "received": "received",
    "unknown": "unknown",
}
LISTS = ("tracker", "handkept", "list")  # sources somebody types in by hand
CARD_KIND = "tracking"

# Words in any of the suppliers' languages; first match wins (order matters: a delay notice that
# also says "shipped" is a delay).
_WORDS: list[tuple[str, str]] = [
    ("cancelled", r"cancel+ed|storn"),
    ("received", r"\b(delivered|received|goods receipt)\b"),
    ("delayed", r"delay|postpon|verspät|opóźn|지연|延误|延迟|推迟|on hold|rolled over"),
    ("arrived_not_booked", r"\barrived\b(?! ?port)|\bunloaded\b|at the dock|도착|已到达|dotar"),
    ("at_port", r"arrived[ _]?port|at[ _]port|discharged|terminal|in port|入港|港口"),
    (
        "in_transit",
        r"in[ _]?transit|in[ _]delivery|out for delivery|on (the )?way|unterwegs|w drodze|"
        r"운송|运输中",
    ),
    (
        "departed",
        r"depart|shipped|dispatch|sailed|loaded|on board|shipping advice|packing list|"
        r"bill of lading|air ?waybill|출하|선적|发货|已发|wysłan|wysyłk",
    ),
    ("booked", r"booked|planned|confirmed|pending|scheduled"),
]
_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def stale_hours() -> float:
    return _env_f("ASK_TRACKER_STALE_HOURS", 12)


def silent_hours_limit() -> float:
    return _env_f("ASK_SILENT_HOURS", 18)


def grace_hours() -> float:
    """A short part's shipment still matters this many hours after the trucks leave (an air or
    road leg that could be pulled forward)."""
    return _env_f("ASK_TRACK_GRACE_HOURS", 24)


# ---------------------------------------------------------------- small helpers


def norm_status(text: Any, allowed: tuple[str, ...] | None = None) -> str | None:
    """'arrived_port' -> 'at_port', 'in_delivery' -> 'in_transit', 'Booked' -> 'booked'. With
    `allowed`, only those statuses are looked for."""
    s = str(text or "").strip().lower()
    if not s:
        return None
    for status, pattern in _WORDS:
        if allowed is not None and status not in allowed:
            continue
        if re.search(pattern, s, re.I):
            return status
    return None


def _ts(value: Any, tz: ZoneInfo) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=tz)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=tz)
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", "").replace(" ", "")) if v not in (None, "") else None
    except ValueError:
        return None


def _split(v: Any) -> list[str]:
    return [x.strip() for x in re.split(r"[|,;]", str(v or "")) if x.strip()]


def day(dt: datetime | None) -> str:
    return dt.strftime("%-d %b") if dt else "no date"


def fmt_n(n: float | None) -> str:
    return f"{n:,.0f}" if n is not None else "?"


def _hours(a: datetime | None, b: datetime) -> float | None:
    return round((b - a).total_seconds() / 3600, 1) if a else None


def claim(
    source: str,
    label: str,
    status: str | None,
    at: datetime | None,
    text: str,
    file: str | None = None,
    row: int | None = None,
    page: int | None = None,
    **kw: Any,
) -> dict:
    return {
        "source": source,
        "label": label,
        "status": status,
        "at": at.isoformat() if at else None,
        "text": text,
        "anchor": {
            k: v for k, v in (("file", file), ("row", row), ("page", page)) if v is not None
        },
        **kw,
    }


def _at(c: dict, tz: ZoneInfo) -> datetime | None:
    return _ts(c.get("at"), tz)


def keys_of(row: dict) -> set[str]:
    """What other sources call a shipment: its id and its carrier reference (container, AWB,
    truck order). Short references are left out: they would match by accident."""
    out = {str(row.get("shipment_id") or "").strip().upper()}
    ref = str(row.get("reference") or "").strip().upper()
    if len(re.sub(r"\W", "", ref)) >= 6:
        out.add(ref)
    return {k for k in out if k}


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _mentions(text: str, keys: set[str]) -> bool:
    up = re.sub(r"\s+", " ", text.upper())
    return any(re.search(rf"(?<![\w-]){re.escape(k)}(?![\w-])", up) for k in keys)


# ---------------------------------------------------------------- claims per source


def tracker_claim(row: dict, file: str, index: int, tz: ZoneInfo) -> dict:
    status = norm_status(row.get("status"))
    upd = _ts(row.get("updated_at"), tz)
    note = str(row.get("note") or "").strip()
    return claim(
        "tracker",
        "System export",
        status,
        upd,
        f"System export: {LABEL.get(status or '', row.get('status') or 'no status')}"
        f" ({day(upd)}), ETA {row.get('eta') or '?'}" + (f" · {note}" if note else ""),
        file,
        index + 1,
        raw_status=row.get("status"),
        eta=row.get("eta"),
    )


def receipt_claims(rows: list[dict], receipts: list[dict], tz: ZoneInfo) -> dict[str, list[dict]]:
    """Goods receipts per shipment: same order and part, booked on or after the shipment left
    and not before the list last touched the row (an older receipt on the same order line is an
    earlier delivery). One receipt goes to one shipment: the one whose ETA is closest."""
    out: dict[str, list[dict]] = {}
    for g in receipts:
        po, mat = str(g.get("po_number") or "").strip(), str(g.get("material_id") or "").strip()
        got, gd = _num(g.get("qty_received")), _ts(g.get("gr_date"), tz)
        if not po or not got or gd is None:
            continue
        cands = []
        for r in rows:
            if po not in _split(r.get("po_numbers")) or mat not in _split(r.get("material_ids")):
                continue
            etd, upd, eta = (_ts(r.get(k), tz) for k in ("etd", "updated_at", "eta"))
            if etd and gd.date() < etd.date():
                continue
            if upd and gd.date() < upd.date():
                continue
            gap = abs((gd - eta).total_seconds()) if eta else float("inf")
            cands.append((gap, str(r.get("shipment_id"))))
        if not cands:
            continue
        sid = min(cands)[1]
        remark = str(g.get("remark") or "").strip()
        ordered = _num(g.get("qty_ordered"))
        out.setdefault(sid, []).append(
            claim(
                "receipt",
                "Goods receipt",
                "received",
                gd,
                f"Hamburg booked GR {g.get('gr_number')} on {day(gd)}: {fmt_n(got)} {mat}"
                + (f" of {fmt_n(ordered)} ordered" if ordered and ordered != got else "")
                + (f" ({remark})" if remark else ""),
                g.get("_file"),
                g.get("_row"),
                qty=got,
                ordered=ordered,
                material=mat,
                ref=g.get("gr_number"),
                delivery_note=g.get("delivery_note"),
            )
        )
    return out


def list_claims(rows: list[dict], tables: list[dict], tz: ZoneInfo) -> dict[str, list[dict]]:
    """Rows of other hand-kept sheets (a logistics tracker, the launch plan's inbound list) that
    name a shipment. `tables` items: {file, sheet, row, cells: {header: value}}."""
    out: dict[str, list[dict]] = {}
    keyed = [(r, keys_of(r)) for r in rows]
    for t in tables:
        cells = {str(k).strip().lower(): v for k, v in (t.get("cells") or {}).items() if k}
        values = {str(v).strip().upper() for v in cells.values() if v not in (None, "")}
        for r, keys in keyed:
            if not keys & values:
                continue

            def col(*words: str) -> Any:
                return next((v for k, v in cells.items() if any(w in k for w in words)), None)

            raw = col("status")
            status = norm_status(raw)
            qty = _num(col("quantity", "qty"))
            upd = _ts(col("updated", "as of", "last update"), tz)
            note = col("comment", "note")
            label = t.get("label") or "List"
            out.setdefault(str(r.get("shipment_id")), []).append(
                claim(
                    "list",
                    label,
                    status,
                    upd,
                    f"{label}: {LABEL.get(status or '', raw or 'listed')}"
                    + (f", {fmt_n(qty)}" if qty else "")
                    + (f" · {note}" if note else ""),
                    t.get("file"),
                    t.get("row"),
                    qty=qty,
                    eta=col("eta"),
                    sheet=t.get("sheet"),
                )
            )
    return out


INITIALS = re.compile(r"^\s*([A-Z]{2,3})\s*[:\-]\s*(.+)$")


def handkept_claims(
    rows: list[dict],
    tracker_rows: list[dict],
    tz: ZoneInfo,
    *,
    file: str | None = None,
    changed_at: datetime | None = None,
    seen: datetime | None = None,
    people: dict[str, str] | None = None,
) -> dict[str, list[dict]]:
    """The hand-kept tracker (the spreadsheet somebody updates "when someone remembers"): status,
    ETA, container/AWB and the comment per shipment. It has no date column, so it is dated by
    when the file last changed while we watched (`changed_at`); a file only ever seen in one
    version has no date and is no news about any shipment (`seen` is kept for the reader).
    Initials in a comment ("RP: call terminal") become a name only when `people` (initials ->
    name, unambiguous) knows them. `tracker_rows` items: {header: value, "_row": n}."""
    out: dict[str, list[dict]] = {}
    keyed = [(r, keys_of(r)) for r in rows]
    people = people or {}
    for t in tracker_rows:
        cells = {
            str(k).strip().lower(): v for k, v in t.items() if k and not str(k).startswith("_")
        }

        def col(*words: str, cells=cells) -> Any:
            return next((v for k, v in cells.items() if any(w in k for w in words)), None)

        sid = str(col("shipment") or "").strip().upper()
        ref = str(col("container", "awb", "reference") or "").strip().upper()
        r = next((r for r, keys in keyed if sid in keys or (ref and ref in keys)), None)
        if r is None:
            continue
        raw = col("status")
        status = norm_status(raw)
        comment = str(col("comment", "note") or "").strip() or None
        by = None
        m = INITIALS.match(comment or "")
        if m:
            by = people.get(m.group(1)) or m.group(1)
        eta = col("eta")
        out.setdefault(str(r.get("shipment_id")), []).append(
            claim(
                "handkept",
                "Hand-kept tracker",
                status,
                changed_at,
                f"Hand-kept tracker: {LABEL.get(status or '', raw or 'listed')}"
                + (f", ETA {str(eta)[:10]}" if eta else "")
                + (f" · comment “{comment}”" if comment else "")
                + (f" ({by})" if by and m and by != m.group(1) else ""),
                file,
                t.get("_row"),
                raw_status=raw,
                eta=str(eta)[:10] if eta else None,
                comment=comment,
                by=by,
                seen=seen.isoformat() if seen else None,
                reference=ref or None,
            )
        )
    return out


def site_claims(rows: list[dict], answers: list[dict], tz: ZoneInfo, city: str = "Hamburg"):
    """The site's answers to "did it arrive?" (floor_counts.json, kind "received"). A yes with a
    count is arrived, not booked; a no is news too (it counts for last heard)."""
    out: dict[str, list[dict]] = {}
    ids = {str(r.get("shipment_id")) for r in rows}
    for a in answers:
        if a.get("kind") != "received":
            continue
        q, subj = str(a.get("question") or ""), str(a.get("subject") or "")
        sid = subj.split(":")[0] if subj.split(":")[0] in ids else None
        sid = sid or next((s for s in ids if q.startswith(f"ASK-received-{s}-")), None)
        if not sid:
            continue
        at = _ts(a.get("at"), tz)
        yes = a.get("arrived") is not False
        count = _num(a.get("count"))
        by = f" ({a['by']})" if a.get("by") else ""
        out.setdefault(sid, []).append(
            claim(
                "site",
                city,
                "arrived_not_booked" if yes else None,
                at,
                f"{city}{by}: arrived" + (f", {fmt_n(count)} counted" if count else "")
                if yes
                else f"{city}{by}: not arrived yet ({day(at)})",
                None,
                qty=count if yes else None,
                arrived=yes,
            )
        )
    return out


def doc_claims(rows: list[dict], docs: list[dict], cur: datetime) -> dict[str, list[dict]]:
    """Supplier documents that name a shipment's id or carrier reference: a shipping advice or
    packing list says it left, a notice may say it is delayed. Dated by the newest ISO date in
    the document that is not in the future. `docs` items: {file, pages: [(page, text)]}."""
    out: dict[str, list[dict]] = {}
    keyed = [(r, keys_of(r)) for r in rows]
    for d in docs:
        pages = d.get("pages") or []
        text = " ".join(t for _, t in pages)
        name = Path(str(d.get("file") or "")).stem.replace("_", " ")
        for r, keys in keyed:
            if not (_mentions(text, keys) or _mentions(name, keys)):
                continue
            status = norm_status(f"{name} {text[:600]}", ("delayed", "at_port", "departed"))
            dates = [
                datetime(int(y), int(m), int(dd), tzinfo=cur.tzinfo)
                for y, m, dd in _ISO.findall(text)
                if 1 <= int(m) <= 12 and 1 <= int(dd) <= 31
            ]
            past = [x for x in dates if x.date() <= cur.date()]
            at = max(past) if past else None
            page = next((p for p, t in pages if _mentions(t, keys)), pages[0][0] if pages else 1)
            short = bool(re.search(r"short shipment|partial|balance|잔량|余量|reszt", text, re.I))
            kind = " ".join(w for w in name.split() if not re.search(r"\d", w)) or "Document"
            out.setdefault(str(r.get("shipment_id")), []).append(
                claim(
                    "document",
                    _cap(kind[:40]),
                    status,
                    at,
                    f"{_cap(kind)} ({day(at)}): {LABEL.get(status or '', 'mentions it')}"
                    + (" · short shipment" if short else ""),
                    d.get("file"),
                    None,
                    page,
                    short=short,
                )
            )
    return out


def reply_claims(
    rows: list[dict], actions: list[dict], card_shipments: dict[str, str], tz: ZoneInfo
) -> dict[str, list[dict]]:
    """Supplier replies (actions of kind "reply") tied to a shipment: through the message they
    answer (its shipment, its card's shipment, or shipment ids in our draft) or by naming it."""
    out: dict[str, list[dict]] = {}
    sent = {
        a.get("message_id"): a for a in actions if a.get("message_id") and a.get("kind") != "reply"
    }
    keyed = [(r, keys_of(r)) for r in rows]
    for a in actions:
        if a.get("kind") != "reply":
            continue
        orig = sent.get(a.get("in_reply_to")) or {}
        text = f"{a.get('subject') or ''} {a.get('summary') or ''} {a.get('text') or ''}"
        mine: set[str] = set()
        for src in (orig.get("shipment_id"), card_shipments.get(str(orig.get("decision_id")))):
            if src:
                mine.add(str(src))
        for r, keys in keyed:
            if _mentions(text, keys) or _mentions(str(orig.get("draft") or ""), keys):
                mine.add(str(r.get("shipment_id")))
        if not mine:
            continue
        at = _ts(a.get("at"), tz)
        # A supplier cannot book our receipt, and "confirmed" is no transport news.
        status = norm_status(
            f"{a.get('summary') or ''} {str(a.get('text') or '')[:800]}",
            ("delayed", "arrived_not_booked", "at_port", "in_transit", "departed"),
        )
        who = str(a.get("from") or "Supplier").split("<")[0].strip().strip('"') or "Supplier"
        for sid in mine:
            out.setdefault(sid, []).append(
                claim(
                    "reply",
                    "Supplier reply",
                    status,
                    at,
                    f"{who} replied ({day(at)}): {str(a.get('summary') or '')[:160]}",
                    None,
                    reply_date=a.get("reply_date"),
                )
            )
    return out


# ---------------------------------------------------------------- one shipment


def derive(
    row: dict,
    claims: list[dict],
    cur: datetime,
    silent_h: float | None = None,
    shipped_qty: float | None = None,
) -> dict:
    """The reconciled view of one shipment from its claims (tracker row included)."""
    tz = cur.tzinfo if isinstance(cur.tzinfo, ZoneInfo) else ZoneInfo("Europe/Berlin")
    silent_h = silent_hours_limit() if silent_h is None else silent_h
    sources = ("receipt", "site", "document", "reply", "handkept", "tracker", "list")
    by = {s: [c for c in claims if c["source"] == s] for s in sources}
    tracker = (by["tracker"] or [None])[0]
    mats = _split(row.get("material_ids"))
    received = None
    deciding: dict | None = None
    status = "unknown"

    def newest(cs: list[dict]) -> dict | None:
        dated = [c for c in cs if c.get("status")]
        if not dated:
            return None
        return max(dated, key=lambda c: _at(c, tz) or datetime.min.replace(tzinfo=tz))

    if by["receipt"]:
        got = sum(c.get("qty") or 0 for c in by["receipt"])
        ordered = sum(c.get("ordered") or 0 for c in by["receipt"])
        covered = {c.get("material") for c in by["receipt"]}
        missing = [m for m in mats if m not in covered]
        full = not missing and (shipped_qty is None or got >= shipped_qty)
        received = {
            "qty": got,
            "full": full,
            "shipped": shipped_qty,
            "missing_parts": missing,
            "order_balance": max(ordered - got, 0) or None,
            "receipts": [c.get("ref") for c in by["receipt"]],
        }
        deciding = newest(by["receipt"])
        status = "received"
    elif any(c.get("status") for c in by["site"]):
        deciding = newest(by["site"])
        status = "arrived_not_booked"
    else:
        # A document or reply beats the list when it is not older and not behind it (a fresh
        # "shipped" does not undo the list's "at the port"); a delay always counts.
        paper = newest(by["document"] + by["reply"])
        t_at = _at(tracker, tz) if tracker else None
        t_status = (tracker or {}).get("status")
        if paper and (
            not t_status
            or paper["status"] == "delayed"
            or (
                _at(paper, tz)
                and (t_at is None or _at(paper, tz) >= t_at)
                and RANK.get(paper["status"], -1) >= RANK.get(t_status, -1)
            )
        ):
            deciding = paper
        elif tracker and tracker.get("status"):
            deciding = tracker
            # The hand-kept tracker beats the system export only when it is newer and not
            # behind it (its "in transit" never undoes the export's "arrived at the port").
            hk = newest(by["handkept"])
            if (
                hk
                and _at(hk, tz)
                and (t_at is None or _at(hk, tz) > t_at)
                and RANK.get(hk["status"], -1) >= RANK.get(t_status, -1)
            ):
                deciding = hk
        else:
            deciding = newest(by["handkept"]) or newest(by["list"])
        if deciding:
            status = deciding["status"] if deciding["status"] in RANK else "unknown"
    if status == "cancelled":
        status = "unknown"

    contradictions: list[str] = []
    behind: list[str] = []  # sources that state less than the truth
    done: set[int] = set()
    dec_text = (deciding or {}).get("text", "")
    lists = by["tracker"] + by["handkept"] + by["list"]
    for c in lists:
        s = c.get("status")
        if c is deciding or not s or s not in RANK:
            continue
        if RANK[s] < RANK[status] or (status == "delayed" and s != "delayed"):
            contradictions.append(f"{c['text'].split(' · ')[0]}; {dec_text}")
            behind.append(c["source"])
            done.add(id(c))
    if any(deciding is c for c in lists):  # no better source: the lists against each other
        for c in lists:
            s = c.get("status")
            if c is deciding or id(c) in done or not s or s == status:
                continue
            contradictions.append(f"{dec_text.split(' · ')[0]}; {c['text']}")
            behind.append(c["source"])
    # The two trackers disagree on the date (the export's ETA against the hand-kept one).
    for c in by["handkept"]:
        e1, e2 = _ts(c.get("eta"), tz), _ts(row.get("eta"), tz)
        if e1 and e2 and e1.date() != e2.date():
            contradictions.append(
                f"System export: ETA {e2.date().isoformat()}; {c['label']}: ETA "
                f"{e1.date().isoformat()}"
            )
            behind.append("handkept")
    if received:
        for c in by["list"] + by["handkept"]:
            q = c.get("qty")
            if q and abs(q - received["qty"]) > 0.5 and not received.get("missing_parts"):
                contradictions.append(
                    f"{c['label']}: {fmt_n(q)} inbound; Hamburg booked {fmt_n(received['qty'])}"
                    f" ({', '.join('GR ' + str(r) for r in received['receipts'])})"
                )
                behind.append(c["source"])

    heard = [t for t in (_at(c, tz) for c in claims) if t]
    last = max(heard) if heard else None
    hrs = _hours(last, cur)
    eta, etd = _ts(row.get("eta"), tz), _ts(row.get("etd"), tz)
    return {
        "status": status,
        "label": LABEL[status],
        "as_of": (deciding or {}).get("at"),
        "source": (deciding or {}).get("source"),
        "source_label": (deciding or {}).get("label"),
        "deciding": dec_text or None,
        "contradictions": contradictions,
        "tracker_behind": "tracker" in behind,
        "handkept_behind": "handkept" in behind,
        "handkept": next(
            (
                {k: c.get(k) for k in ("status", "raw_status", "eta", "comment", "by", "seen")}
                for c in by["handkept"]
            ),
            None,
        ),
        "tracker_status": (tracker or {}).get("status"),
        "last_heard": last.isoformat() if last else None,
        "silent_hours": hrs,
        "silent": status != "received" and (hrs is None or hrs >= silent_h),
        "received": received,
        "eta_passed": bool(
            eta and eta.date() < cur.date() and status not in ("received", "arrived_not_booked")
        ),
        "departure_overdue": bool(status == "booked" and etd and etd.date() < cur.date()),
        "claims": claims,
    }


# ---------------------------------------------------------------- all shipments


def reconcile(
    rows: list[dict],
    extra: dict[str, list[dict]],
    cur: datetime,
    *,
    tracker_file: str = "",
    launch: datetime | None = None,
    launch_parts: set[str] | None = None,
    short_copies: dict[str, float] | None = None,
    shipped: dict[str, float] | None = None,
    stale_h: float | None = None,
    silent_h: float | None = None,
    handkept: dict | None = None,
) -> dict:
    """{shipments: {id: derived}, tracker: {...}}. `extra`: claims per shipment id from the other
    sources; `short_copies`: copies each launch part cannot cover (0 when it lasts); `handkept`:
    {file, ids} of the hand-kept tracker, to flag shipments that only one of the lists knows."""
    tz = cur.tzinfo if isinstance(cur.tzinfo, ZoneInfo) else ZoneInfo("Europe/Berlin")
    stale_h = stale_hours() if stale_h is None else stale_h
    short_copies = short_copies or {}
    shipped = shipped or {}
    out: dict[str, dict] = {}
    for i, r in enumerate(rows):
        sid = str(r.get("shipment_id") or "")
        if not sid:
            continue
        cl = [tracker_claim(r, tracker_file, i, tz), *extra.get(sid, [])]
        d = derive(r, cl, cur, silent_h, shipped.get(str(r.get("reference") or "")))
        mats = _split(r.get("material_ids"))
        parts = [m for m in mats if launch_parts is None or m in launch_parts]
        copies = max([short_copies.get(m) or 0 for m in parts] or [0])
        eta = _ts(r.get("eta"), tz)
        in_time = eta is None or launch is None or eta <= launch
        in_grace = eta is None or launch is None or eta <= launch + timedelta(hours=grace_hours())
        cancelled = norm_status(r.get("status")) == "cancelled"
        d["launch_parts"] = parts
        d["copies"] = copies or None
        d["matters"] = bool(
            parts
            and not cancelled
            and d["status"] != "received"
            and (in_time or (copies > 0 and in_grace))
        )
        d["alert"] = d["matters"] and d["silent"]
        d["on_handkept"] = None if handkept is None else sid in set(handkept.get("ids") or ())
        out[sid] = d
    upds = [t for t in (_ts(r.get("updated_at"), tz) for r in rows) if t]
    newest = max(upds) if upds else None
    age = _hours(newest, cur)
    contradicted = sorted(s for s, d in out.items() if d["tracker_behind"])
    received_in_list = sorted(
        s for s, d in out.items() if d["status"] == "received" and d["tracker_status"] != "received"
    )
    return {
        "shipments": out,
        "tracker": {
            "file": tracker_file,
            "rows": len(out),
            "newest_update": newest.isoformat() if newest else None,
            "list_age_hours": age,
            "stale_after_hours": stale_h,
            "frozen": bool(age is not None and age >= stale_h),
            "contradicted": contradicted,
            "received_in_list": received_in_list,
            "handkept": None
            if handkept is None
            else {
                "file": handkept.get("file"),
                "rows": len(handkept.get("ids") or ()),
                "seen": handkept.get("seen"),
                "changed_at": handkept.get("changed_at"),
                "only_in_export": sorted(s for s, d in out.items() if d["on_handkept"] is False),
                "only_in_handkept": sorted(set(handkept.get("ids") or ()) - set(out)),
                "contradicted": sorted(s for s, d in out.items() if d["handkept_behind"]),
            },
        },
    }


# ---------------------------------------------------------------- cards


def _first(name: str | None) -> str:
    return (name or "").split()[0] if name else ""


def _when(dt: datetime | None) -> str:
    return dt.strftime("%a %-d %b %H:%M") if dt else "?"


def _ship_line(r: dict, d: dict, names: dict[str, str]) -> str:
    parts = ", ".join(names.get(m, m) for m in d.get("launch_parts") or []) or r.get("material_ids")
    ref = r.get("reference")
    return (
        f"{r.get('shipment_id')}"
        + (f" · {ref}" if ref else "")
        + f" · {r.get('mode') or '?'} · {parts} · ETA {r.get('eta') or '?'}"
        + f" · our list: {LABEL.get(d.get('tracker_status') or '') or r.get('status')}"
    )


def _why(r: dict, d: dict, names: dict[str, str], launch: datetime | None) -> str:
    parts = ", ".join(names.get(m, m) for m in d.get("launch_parts") or []) or "launch parts"
    hrs = d.get("silent_hours")
    heard = (
        f"nobody has heard of it for {hrs:.0f} h" if hrs is not None else "nobody has heard of it"
    )
    bits = [f"{r.get('shipment_id')} ({parts}): {heard}"]
    if d.get("departure_overdue"):
        bits.append(f"it should have left on {r.get('etd')} and the list still says booked")
    elif d.get("eta_passed"):
        bits.append(f"its ETA {r.get('eta')} has passed and no goods receipt is booked")
    hk = d.get("handkept") or {}
    if hk.get("comment") or (hk.get("status") and hk.get("status") != d.get("status")):
        bits.append(
            f"the hand-kept tracker says {LABEL.get(hk.get('status') or '', hk.get('raw_status'))}"
            + (f", comment “{hk['comment']}”" if hk.get("comment") else "")
            + (f" ({hk['by']})" if hk.get("by") and hk["by"] not in str(hk.get("comment")) else "")
        )
    elif d.get("on_handkept") is False:
        bits.append("it is not on the hand-kept tracker")
    if d.get("copies"):
        bits.append(
            f"{fmt_n(d['copies'])} copies depend on {parts} arriving before {_when(launch)}"
            if launch
            else f"{fmt_n(d['copies'])} copies depend on {parts}"
        )
    elif launch:
        bits.append(f"it is due before the trucks leave ({_when(launch)})")
    return "; ".join(bits)


def _status_mail(
    viewer: str, contact: str | None, lines: list[str], launch: datetime | None
) -> str:
    when = f" before {_when(launch)}" if launch else ""
    return (
        f"Dear {contact or 'team'},\n\n"
        f"we need every part of these shipments in Hamburg{when}. Our tracking has not updated, so "
        "could you please confirm where each one is now and when it reaches Hamburg?\n\n"
        + "\n".join(f"- {x}" for x in lines)
        + "\n\nA short reply with the current status and arrival date is enough.\n\n"
        f"Thank you,\n{viewer or 'Procurement'}"
    )


def _done(cid: str, statuses: dict) -> tuple[str, dict]:
    ui = statuses.get(cid, {}) if isinstance(statuses, dict) else {}
    return ui.get("status", "open"), ui


def _asked_recently(sid: str, actions: list[dict], cur: datetime, hours: float, ids: set[str]):
    """A status request for this shipment went out within `hours` (we are waiting for it)."""
    tz = cur.tzinfo if isinstance(cur.tzinfo, ZoneInfo) else ZoneInfo("Europe/Berlin")
    for a in actions:
        if a.get("status") != "sent":
            continue
        hit = a.get("shipment_id") == sid or (
            str(a.get("decision_id")) in ids and sid in str(a.get("draft") or "")
        )
        at = _ts(a.get("at"), tz)
        if hit and at and (cur - at).total_seconds() / 3600 < hours:
            return a
    return None


def cards(
    result: dict,
    rows: list[dict],
    cur: datetime,
    *,
    nodes: dict | None = None,
    names: dict[str, str] | None = None,
    emails: dict[str, dict] | None = None,
    statuses: dict | None = None,
    actions: list[dict] | None = None,
    trust: dict | None = None,
    viewer: str = "",
    launch: datetime | None = None,
    site_questions: dict[str, str] | None = None,
    city: str = "Hamburg",
    base=None,
) -> list[dict]:
    """Inbox cards: one for a frozen list that contradicts other sources, and one per silent
    shipment that matters and is not covered by that card. `emails`: supplier id -> {email,
    contact}; `site_questions`: shipment id -> id of the site's "did it arrive?" card."""
    nodes, names, emails = nodes or {}, names or {}, emails or {}
    statuses, actions, trust = statuses or {}, actions or [], trust or {}
    site_questions = site_questions or {}
    make = base or (lambda cid, **kw: {"id": cid, **kw})
    tz = cur.tzinfo if isinstance(cur.tzinfo, ZoneInfo) else ZoneInfo("Europe/Berlin")
    trusted = [k for k, v in trust.items() if v == "allow_always"]
    silent_h = silent_hours_limit()
    by_id = {str(r.get("shipment_id")): r for r in rows}
    ships = result.get("shipments") or {}
    tr = result.get("tracker") or {}
    out: list[dict] = []
    trail = [
        {
            "agent": "Tracking",
            "at": cur.isoformat(),
            "text": "Held the shipment list against goods receipts, site answers, supplier "
            "documents and replies.",
        }
    ]

    def sup_name(sid_sup: str | None) -> str:
        return (nodes.get(sid_sup or "") or {}).get("name") or sid_sup or "the supplier"

    def evidence(d: dict) -> list[dict]:
        return [
            {
                "source": c["label"],
                "value": c["text"],
                "doc": (c.get("anchor") or {}).get("file"),
                "page": (c.get("anchor") or {}).get("page"),
                "quote": c["text"],
            }
            for c in d.get("claims") or []
        ]

    alerts = [s for s, d in ships.items() if d.get("alert") and s in by_id]
    covered: set[str] = set()

    # (a) the list stopped changing and contradicts what other sources know
    if tr.get("frozen") and tr.get("contradicted"):
        cid = "TRK-feed-" + re.sub(r"\D", "", str(tr.get("newest_update") or ""))[:12]
        status, ui = _done(cid, statuses)
        recv = tr.get("received_in_list") or []
        age = tr.get("list_age_hours") or 0
        newest = _ts(tr.get("newest_update"), tz)
        n_bad = len(tr["contradicted"])
        head = f"The shipment list has not changed for {age:.0f} h; " + (
            f"{len(recv)} shipment{'s' if len(recv) != 1 else ''} in it "
            f"{'are' if len(recv) != 1 else 'is'} already received"
            if recv
            else f"{n_bad} row{'s' if n_bad != 1 else ''} contradict other sources"
        )
        groups: dict[str, list[str]] = {}
        for s in alerts:
            groups.setdefault(str(by_id[s].get("supplier_id") or ""), []).append(s)
        why = [_why(by_id[s], ships[s], names, launch) for s in alerts]
        hkt = tr.get("handkept") or {}
        outdated = [
            f"{s}: " + "; ".join(ships[s]["contradictions"][:2])
            for s in sorted({*tr["contradicted"], *(hkt.get("contradicted") or [])})
        ]
        if hkt.get("only_in_export"):
            outdated.append("Not on the hand-kept tracker: " + ", ".join(hkt["only_in_export"]))
        if hkt.get("only_in_handkept"):
            outdated.append("Only on the hand-kept tracker: " + ", ".join(hkt["only_in_handkept"]))
        summary = f"Last row update {_when(newest)}. " + (
            f"Still open and needed for the launch: {'. '.join(why)}."
            if why
            else "No open shipment for the launch is waiting on it."
        )
        options = []
        messages = []
        for sup, sids in groups.items():
            info = emails.get(sup) or {}
            if not info.get("email"):
                continue
            lines = [_ship_line(by_id[s], ships[s], names) for s in sids]
            messages.append(
                {
                    "to": info["email"],
                    "subject": "Status of our open shipments",
                    "body": _status_mail(viewer, info.get("contact"), lines, launch),
                    "shipments": sids,
                    "supplier_id": sup,
                }
            )
        if messages:
            who = ", ".join(sup_name(m["supplier_id"]) for m in messages)
            n_ship = sum(len(m["shipments"]) for m in messages)
            each = ", one email each" if len(messages) > 1 else ""
            options.append(
                {
                    "action": f"Email {who}: status of {n_ship} open "
                    f"shipment{'s' if n_ship != 1 else ''}{each}",
                    "cost": "No cost",
                    "cost_value": None,
                    "effect": "fresh status from the people who ship it; replies land on this card",
                    "draft": "\n\n---\n\n".join(f"To {m['to']}:\n{m['body']}" for m in messages),
                    "to": ", ".join(m["to"] for m in messages),
                    "channel": "email",
                    "messages": messages,
                    "subject": "Status of our open shipments",
                    "routine_type": "ask_status",
                }
            )
        options.append(
            {
                "action": f"Tell {_first(viewer) or 'the buyer'} which rows of the list are "
                "outdated",
                "cost": "No cost",
                "cost_value": None,
                "effect": f"{n_bad} rows corrected at the source",
                "draft": "These rows of the shipment list are outdated:\n"
                + "\n".join(f"- {x}" for x in outdated),
                "to": viewer or None,
                "channel": "notify",
                "routine_type": "internal_note",
            }
        )
        for i, o in enumerate(options):
            o["index"], o["recommended"] = i, i == 0
        card = make(
            cid,
            kind=CARD_KIND,
            headline=head,
            summary=summary,
            number=max([ships[s].get("copies") or 0 for s in alerts] or [0]) or None,
            number_label="copies depend on it",
            level="amber" if alerts else "grey",
            status=status,
            ui=ui,
            approver=viewer or None,
            reason="Routine, no cost: a status question, nothing is committed",
            options=options,
            evidence=[
                {
                    "source": "System export",
                    "value": f"newest update {_when(newest)}, {tr.get('rows')} rows",
                    "doc": tr.get("file"),
                },
                *(
                    {"source": s, "value": c, "quote": c}
                    for s in sorted({*tr["contradicted"], *(hkt.get("contradicted") or [])})
                    for c in ships[s]["contradictions"]
                ),
                *(
                    [
                        {
                            "source": "Hand-kept tracker",
                            "value": f"{hkt.get('rows')} rows; missing "
                            + ", ".join(hkt["only_in_export"]),
                            "doc": hkt.get("file"),
                        }
                    ]
                    if hkt.get("only_in_export")
                    else []
                ),
            ],
            trail=trail,
            routes=sorted(
                {str(by_id[s].get("route_id")) for s in alerts if by_id[s].get("route_id")}
            ),
            created_at=cur.isoformat(),
            trusted_types=trusted,
            tracking={"kind": "feed", "shipments": alerts, "contradicted": tr["contradicted"]},
        )
        out.append(card)
        sent_at = _ts(ui.get("at"), tz) if status in ("sent", "approved") else None
        if status == "open" or (sent_at and (cur - sent_at).total_seconds() / 3600 < silent_h):
            covered |= set(alerts)

    # (b) one shipment, silent and needed
    feed_ids = {c["id"] for c in out}
    for s in alerts:
        cid = f"TRK-silent-{s}"
        status, ui = _done(cid, statuses)
        if s in covered and cid not in statuses:
            continue
        if cid not in statuses and _asked_recently(s, actions, cur, silent_h, feed_ids | {cid}):
            continue
        r, d = by_id[s], ships[s]
        sup = str(r.get("supplier_id") or "")
        info = emails.get(sup) or {}
        parts = ", ".join(names.get(m, m) for m in d.get("launch_parts") or [])
        options = []
        if info.get("email"):
            options.append(
                {
                    "action": f"Email {sup_name(sup)}: status of {r.get('reference') or s}",
                    "cost": "No cost",
                    "cost_value": None,
                    "effect": "a fresh status before it is too late to react",
                    "draft": _status_mail(
                        viewer, info.get("contact"), [_ship_line(r, d, names)], launch
                    ),
                    "to": info["email"],
                    "subject": f"Status of shipment {s}",
                    "channel": "email",
                    "routine_type": "ask_status",
                }
            )
        else:
            options.append(
                {
                    "action": f"Call {sup_name(sup)} about {s}: no email address on file",
                    "cost": "No cost",
                    "cost_value": None,
                    "effect": None,
                    "draft": None,
                    "to": viewer or None,
                    "channel": "notify",
                    "routine_type": "internal_note",
                }
            )
        if d.get("eta_passed") or d["status"] in ("at_port",):
            qid = site_questions.get(s)
            if qid:
                options.append(
                    {
                        "action": f"Ask {city} whether it arrived",
                        "cost": "No cost",
                        "cost_value": None,
                        "effect": "a yes counts as stock until the goods receipt is booked",
                        "draft": None,
                        "to": city,
                        "channel": "floor",
                        "question": qid,
                        "routine_type": "site_question",
                    }
                )
            # TODO(site): when the site has no "received" question for this shipment (the floor
            # thinks a receipt on the same order line covers it), offer one here once floor.py
            # can open a question for a given shipment on demand.
        for i, o in enumerate(options):
            o["index"], o["recommended"] = i, i == 0
        head = (
            f"No news of {r.get('reference') or s} for {d['silent_hours']:.0f} h"
            if d.get("silent_hours") is not None
            else f"No news of {r.get('reference') or s}"
        ) + (f": {parts}" if parts else "")
        out.append(
            make(
                cid,
                kind=CARD_KIND,
                part=(d.get("launch_parts") or [None])[0],
                headline=head,
                summary=_why(r, d, names, launch) + ".",
                number=d.get("copies"),
                number_label="copies depend on it",
                arrives_at=(_ts(r.get("eta"), tz) or cur).isoformat() if r.get("eta") else None,
                level="amber" if d.get("copies") else "grey",
                status=status,
                ui=ui,
                approver=viewer or None,
                reason="Routine, no cost: a status question, nothing is committed",
                options=options,
                evidence=evidence(d),
                trail=trail,
                supplier_id=sup or None,
                supplier_name=sup_name(sup) if sup else None,
                shipment=s,
                routes=[str(r["route_id"])] if r.get("route_id") else [],
                created_at=cur.isoformat(),
                trusted_types=trusted,
                tracking={"kind": "silent", "shipments": [s]},
            )
        )
    return out


# ---------------------------------------------------------------- reading the sources (I/O)

_table_cache: dict[str, tuple[float, list[dict]]] = {}
_doc_cache: dict[str, tuple[float, dict | None]] = {}
SYSTEM_TABLES = ("goods_receipt", "purchase_order", "packout", "stock")


def _label_of(rel: str) -> str:
    stem = Path(rel).stem
    words = [
        w
        for w in re.split(r"[_\s-]+", stem)
        if w and not re.fullmatch(r"v\d+|final\d*|copy|draft|\d{4}.*", w, re.I)
    ]
    return _cap(" ".join(words) or stem)


def read_tables(root: Path, skip: str | set[str]) -> list[dict]:
    """Rows of every sheet under soony/ (except master data, the list itself and the ERP's
    receipt, order, pack-out and stock exports) as {file, sheet, row, label, cells}."""
    out: list[dict] = []
    base = root / "soony"
    for p in sorted(base.rglob("*")) if base.is_dir() else []:
        rel = str(p.relative_to(root))
        if (
            not p.is_file()
            or p.suffix.lower() not in (".csv", ".xlsx", ".xlsm")
            or rel in ({skip} if isinstance(skip, str) else skip)
            or rel.startswith("soony/master/")
            or any(t in p.name.lower() for t in SYSTEM_TABLES)
        ):
            continue
        stamp = p.stat().st_mtime
        hit = _table_cache.get(rel)
        if hit and hit[0] == stamp:
            out += hit[1]
            continue
        rows: list[dict] = []
        try:
            sheets: list[tuple[str, list[list]]]
            if p.suffix.lower() == ".csv":
                with p.open(encoding="utf-8-sig", errors="replace", newline="") as fh:
                    sheets = [("", list(csv.reader(fh)))]
            else:
                import openpyxl

                wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
                sheets = [
                    (ws.title, [list(r) for r in ws.iter_rows(values_only=True)])
                    for ws in wb.worksheets
                ]
                wb.close()
        except Exception:  # one unreadable sheet must not hide the rest
            sheets = []
        for sheet, data in sheets:
            header: list[str] = []
            for i, cells in enumerate(data):
                vals = ["" if v is None else str(v).strip() for v in cells]
                filled = [v for v in vals if v]
                if len(filled) >= 3 and sum(bool(re.search(r"\d", v)) for v in filled) <= 1:
                    header = vals
                    continue
                if header and filled:
                    rows.append(
                        {
                            "file": rel,
                            "sheet": sheet,
                            "row": i,
                            "label": _label_of(rel),
                            "cells": {h: v for h, v in zip(header, vals, strict=False) if h},
                        }
                    )
        _table_cache[rel] = (stamp, rows)
        out += rows
    return out


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


_hk_cache: dict[str, tuple[float, dict | None]] = {}


def _newest_first(p: Path) -> tuple:
    """A new tracker version (`..._v38.xlsx` next to `..._v37_FINAL.xlsx`) wins: the highest
    version number in the name first, then the newest file."""
    m = re.findall(r"(?:^|[_\-\s.])v(\d+)", p.stem, re.I)
    return (-(int(m[-1]) if m else -1), -p.stat().st_mtime, str(p))


def read_handkept(root: Path) -> dict | None:
    """The hand-kept shipment tracker, found by its columns (the verifier's "shipment_tracker"
    layout), not by its name: {file, sheet, rows: [{header: value, _row, _id}]}."""
    from ask_soony.verifier.linker import discover

    required = discover.XLSX_LAYOUTS.get("shipment_tracker")
    base = root / "soony"
    if not required or not base.is_dir():
        return None
    for p in sorted(base.rglob("*.xls[xm]"), key=_newest_first):
        rel = str(p.relative_to(root))
        stamp = p.stat().st_mtime
        hit = _hk_cache.get(rel)
        if not hit or hit[0] != stamp:
            found = None
            try:
                table = discover.find_xlsx_table(p, required)
                if table:
                    import openpyxl

                    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
                    try:
                        values = list(wb[table[0]].iter_rows(values_only=True))
                    finally:
                        wb.close()
                    head = [str(c).strip() if c is not None else "" for c in values[table[1]]]
                    key = next((h for h in head if "shipment" in h.lower()), head[0])
                    rows = []
                    for i, v in enumerate(values[table[1] + 1 :], start=table[1] + 1):
                        if not any(x not in (None, "") for x in v):
                            continue
                        cells = {h: x for h, x in zip(head, v, strict=False) if h}
                        rows.append({**cells, "_row": i, "_id": str(cells.get(key) or "").strip()})
                    found = {"file": rel, "sheet": table[0], "rows": rows}
            except Exception:  # an unreadable workbook is no tracker
                found = None
            hit = _hk_cache[rel] = (stamp, found)
        if hit[1]:
            return hit[1]
    return None


def file_times(root: Path, rel: str) -> tuple[datetime | None, datetime | None]:
    """(changed_at, seen) of a portal file from the watcher's store: when its current version
    appeared, if the file changed while we watched (else None), and when we first saw it."""
    import sqlite3

    db = Path(os.environ.get("ASK_DATA_DIR", "data/store")) / "ask-soony.db"
    if not db.is_file():
        return None, None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT first_seen FROM versions WHERE path = ? ORDER BY id", (rel,)
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return None, None
    times = [t for t in (_ts(r[0], ZoneInfo("UTC")) for r in rows) if t]
    if not times:
        return None, None
    return (times[-1] if len(times) > 1 else None), times[0]


_people_cache: dict[str, dict[str, str]] = {}


def initials_of_people(root: Path) -> dict[str, str]:
    """Initials -> name of the people the org chart readings name, only where unambiguous."""
    from ask_soony import api

    f = api.cards_file()
    key = f"{root}:{f.stat().st_mtime if f.exists() else 0}"
    if key not in _people_cache:
        names: dict[str, set[str]] = {}
        try:
            from ask_soony.dispatch.decisions.people import People
            from ask_soony.verifier.linker import build

            records = build(root, api.readings_dir()).records
            people = People(records[records.kind == "reading"].to_dict("records"), None)
            for h in people.holders:
                if h.name:
                    words = [w for w in h.name.replace("Dr.", "").split() if w[:1].isupper()]
                    if len(words) >= 2:
                        names.setdefault(words[0][0] + words[-1][0], set()).add(h.name)
        except Exception:  # verifier or readings missing: initials stay initials
            names = {}
        _people_cache.clear()
        _people_cache[key] = {k: next(iter(v)) for k, v in names.items() if len(v) == 1}
    return _people_cache[key]


def read_receipts(root: Path) -> list[dict]:
    out = []
    for p in sorted((root / "soony" / "erp").glob("goods_receipt*.csv")):
        try:
            with p.open(encoding="utf-8-sig", newline="") as fh:
                for i, r in enumerate(csv.DictReader(fh)):
                    out.append({**r, "_file": str(p.relative_to(root)), "_row": i + 1})
        except OSError:
            continue
    return out


def read_docs(root: Path, cache_dir: Path) -> list[dict]:
    """Supplier documents as text, from the evidence index's cache (no OCR is run here: a
    document not indexed yet is simply not read until it is)."""
    out = []
    base = root / "suppliers"
    for p in sorted(base.rglob("*")) if base.is_dir() else []:
        if not p.is_file() or p.suffix.lower() in (".csv", ".json", ".xlsx"):
            continue
        rel = str(p.relative_to(root))
        st = p.stat()
        stamp = st.st_mtime + st.st_size
        hit = _doc_cache.get(rel)
        if not hit or hit[0] != stamp:
            doc = None
            try:
                digest = hashlib.sha256(p.read_bytes()).hexdigest()
                units = json.loads((cache_dir / f"{digest}.json").read_text(encoding="utf-8"))
                pages = []
                for u in units:
                    text = u.get("text") or " ".join(w[0] for w in u.get("words") or [])
                    if text.strip():
                        pages.append((int(u.get("page") or u.get("row") or 1), text))
                doc = {"file": rel, "pages": pages} if pages else None
            except (OSError, ValueError):
                doc = None
            hit = _doc_cache[rel] = (stamp, doc)
        if hit[1]:
            out.append(hit[1])
    return out


def supplier_contacts(root: Path) -> dict[str, dict]:
    """supplier id -> {email, contact} from the portal profiles."""
    out: dict[str, dict] = {}
    for p in (
        sorted((root / "suppliers").glob("*/profile.csv")) if (root / "suppliers").is_dir() else []
    ):
        try:
            with p.open(encoding="utf-8-sig", newline="") as fh:
                for r in csv.DictReader(fh):
                    sid = (r.get("supplier_id") or "").strip()
                    mail = next(
                        (
                            v.strip().lower()
                            for k, v in r.items()
                            if v and k and "mail" in k.lower() and "@" in v
                        ),
                        None,
                    )
                    if sid and mail:
                        out[sid] = {
                            "email": mail,
                            "contact": (r.get("contact_name") or "").strip() or None,
                        }
        except OSError:
            continue
    return out


def verifier_parts(dispatch: dict) -> tuple[set[str] | None, dict[str, float], dict[str, float]]:
    """From the verifier's output: the launch parts, copies each part cannot cover, and the
    quantity shipped per carrier reference where a supplier document states it."""
    parts = [p for p in dispatch.get("parts") or [] if isinstance(p, dict) and p.get("part")]
    if not parts:
        return None, {}, {}
    short: dict[str, float] = {}
    shipped: dict[str, float] = {}
    for p in parts:
        per = max([_num(v) or 0 for v in (p.get("per_copy") or {}).values()] or [0]) or 1
        short[str(p["part"])] = max(_num(p.get("gap")) or 0, 0) / per
        for a in p.get("arriving") or []:
            if a.get("reference") and "shipped" in str(a.get("basis") or "").lower():
                shipped[str(a["reference"])] = _num(a.get("quantity")) or 0
    return {str(p["part"]) for p in parts}, short, shipped


def collect(
    root: Path | None,
    rows: list[dict],
    cur: datetime,
    *,
    launch=None,
    dispatch=None,
    answers=None,
    actions=None,
    card_shipments=None,
    city: str = "Hamburg",
    tracker_file: str = "soony/erp/open_shipments.csv",
) -> dict:
    """Read every source for the given list rows and reconcile them."""
    tz = cur.tzinfo if isinstance(cur.tzinfo, ZoneInfo) else ZoneInfo("Europe/Berlin")
    extra: dict[str, list[dict]] = {}

    def add(found: dict[str, list[dict]]) -> None:
        for k, v in found.items():
            extra.setdefault(k, []).extend(v)

    hk_info = None
    if root is not None:
        cache = Path(os.environ.get("ASK_DATA_DIR", "data/store")) / "anchors"
        hk = read_handkept(root)
        if hk:
            changed, seen = file_times(root, hk["file"])
            hk_info = {**hk, "changed_at": _iso(changed), "seen": _iso(seen)}
            hk_info["ids"] = [str(t.get("_id")) for t in hk["rows"] if t.get("_id")]
        steps = [
            lambda: receipt_claims(rows, read_receipts(root), tz),
            lambda: list_claims(
                rows, read_tables(root, {tracker_file, *([hk["file"]] if hk else [])}), tz
            ),
            lambda: doc_claims(rows, read_docs(root, cache), cur),
            lambda: (
                handkept_claims(
                    rows,
                    hk["rows"],
                    tz,
                    file=hk["file"],
                    changed_at=changed,
                    seen=seen,
                    people=initials_of_people(root),
                )
                if hk
                else {}
            ),
        ]
        for step in steps:
            try:
                add(step())
            except Exception:  # one broken source must not hide the others
                continue
    add(site_claims(rows, answers or [], tz, city))
    add(reply_claims(rows, actions or [], card_shipments or {}, tz))
    launch_parts, short, shipped = verifier_parts(dispatch or {})
    return reconcile(
        rows,
        extra,
        cur,
        tracker_file=tracker_file,
        launch=launch,
        launch_parts=launch_parts,
        short_copies=short,
        shipped=shipped,
        handkept=hk_info,
    )


def public(d: dict) -> dict:
    """The per-shipment `derived` block for /api/state (claims trimmed to what the UI shows)."""
    keys = (
        "status",
        "label",
        "as_of",
        "source",
        "source_label",
        "contradictions",
        "last_heard",
        "silent_hours",
        "silent",
        "matters",
        "alert",
        "copies",
        "received",
        "eta_passed",
        "departure_overdue",
        "tracker_status",
    )
    out = {k: d.get(k) for k in keys}
    out["claims"] = [
        {k: c.get(k) for k in ("source", "label", "status", "at", "text", "anchor")}
        for c in d.get("claims") or []
    ]
    return out


# ---------------------------------------------------------------- glue for the API


def for_state(net: dict, cur: datetime, clock: Any = None) -> dict:
    """Reconcile the shipment list of the current portal mirror with every other source."""
    from ask_soony import api, launch_view

    actions = api.read_list("actions.json")
    statuses = api.read_json("decision_status.json", {})
    card_shipments = {
        str(a.get("decision_id")): str(a["shipment_id"]) for a in actions if a.get("shipment_id")
    }
    card_shipments.update(
        {k: k.removeprefix("TRK-silent-") for k in statuses if k.startswith("TRK-silent-")}
    )
    return collect(
        net.get("root"),
        net.get("shipments_raw") or [],
        cur,
        launch=getattr(clock, "launch", None),
        dispatch=launch_view._dispatch(),
        answers=api.read_list("floor_counts.json"),
        actions=actions,
        card_shipments=card_shipments,
        city=_city(net),
    )


def _city(net: dict) -> str:
    dc = next((n for n in (net.get("nodes") or {}).values() if n.get("kind") == "dc"), {})
    name = str(dc.get("city") or dc.get("name") or "")
    return name.split()[-1] if name else "the site"


def state_cards(result: dict, net: dict, cur: datetime, clock: Any, existing: list[dict]):
    """The tracking cards for /api/state, in the inbox's card shape."""
    from ask_soony import api

    root = net.get("root")
    site_q = {
        str(c.get("shipment")): c["id"]
        for c in existing
        if c.get("site_question") and c.get("kind") == "received" and c.get("status") == "open"
    }
    out = cards(
        result,
        net.get("shipments_raw") or [],
        cur,
        nodes=net.get("nodes") or {},
        names=api.part_names(root),
        emails=supplier_contacts(root) if root else {},
        statuses=api.read_json("decision_status.json", {}),
        actions=api.read_list("actions.json"),
        trust=api.read_json("trust.json", {}),
        viewer=api.viewer_name(root),
        launch=getattr(clock, "launch", None),
        site_questions=site_q,
        city=_city(net),
        base=api._base_card,
    )
    names = api.part_names(root)
    for c in out:
        c["part_name"] = names.get(str(c.get("part") or ""))
    return out


def _edited(text: str | None, messages: list[dict]) -> dict[str, str]:
    """Bodies per address from an edited combined draft ("To x:\\n<body>" blocks)."""
    out: dict[str, str] = {}
    for block in re.split(r"\n\s*---\s*\n", text or ""):
        m = re.match(r"\s*To\s+(\S+@\S+?):?\s*\n(.*)", block, re.S)
        if m:
            out[m.group(1).lower()] = m.group(2).strip()
    return out if set(out) >= {str(x["to"]).lower() for x in messages} else {}


def send_each(card: dict, option: dict, text: str | None, known: set[str], send) -> dict | None:
    """Send one email per recipient of a multi-message option (the feed card). The first message
    is recorded by the approve path as usual; every further one gets its own action, so replies
    to it find the card. Only known supplier addresses are written to."""
    from ask_soony import api

    msgs = [m for m in option.get("messages") or [] if str(m.get("to") or "").lower() in known]
    if not msgs:
        return None
    bodies = _edited(text, msgs)
    first = None
    for m in msgs:
        to = str(m["to"]).lower()
        body = bodies.get(to) or m["body"]
        subject = m.get("subject") or card.get("headline") or "Status of our open shipments"
        mid = send(to, subject, body)
        sent = {"message_id": mid, "to": to, "subject": subject, "sent_body": body}
        if first is None:
            first = sent
            continue
        with api._lock:
            acts = api.read_list("actions.json")
            acts.append(
                {
                    "id": os.urandom(5).hex(),
                    "decision_id": card["id"],
                    "at": api.now().isoformat(),
                    "kind": "approve",
                    "action": option.get("action"),
                    "channel": "email",
                    "to": to,
                    "subject": subject,
                    "draft": body,
                    "status": "sent",
                    "message_id": mid,
                    "shipment_ids": m.get("shipments"),
                    "action_type": option.get("routine_type"),
                }
            )
            api.write_json("actions.json", acts)
    return first


def ask_site(question_id: str) -> dict | None:
    """Send the site's "did it arrive?" question behind a tracking card's option (floor.ask)."""
    from ask_soony import api, floor

    q = next(
        (
            c
            for c in api.build_state()["cards"]
            if c["id"] == question_id and c.get("site_question")
        ),
        None,
    )
    return floor.ask(q) if q else None
