"""The Launch page's numbers: per edition how far packing is, and per part of each bill of
materials whether it lasts until the trucks leave.

Everything comes from the verifier's output (dispatch.json `editions` and `parts`), the packing
clock and the open shipments; nothing about the launch is written here. A number the data does not
state stays None, and the page says "unknown".
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from ask_soony.launch import PackClock

OPEN = ("open", "escalated")


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _dispatch() -> dict:
    from ask_soony.api import cards_file

    try:
        data = json.loads(cards_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _cards_for(part: dict, cards: list[dict]) -> list[dict]:
    """Cards about this part: its code or its description on the card, or its code in the id."""
    code, desc = str(part.get("part") or ""), str(part.get("description") or "")
    out = []
    for c in cards:
        p = str(c.get("part") or "")
        if (code and (p == code or code in str(c.get("id") or ""))) or (desc and p == desc):
            out.append(c)
    return out


def _ships_by_ref(
    ships_raw: list[dict], cur: datetime, tracked: dict | None = None
) -> dict[str, dict]:
    """Shipments by carrier reference, with the reconciled status and clock-based silence of
    tracking.py when given (`tracked`: shipment id -> derived)."""
    from ask_soony.api import shipment_view

    silent_h = float(os.environ.get("ASK_SILENT_HOURS", "18"))
    out = {}
    for r in ships_raw:
        v = shipment_view(r, cur, None, silent_h, (tracked or {}).get(r.get("shipment_id")))
        if v and r.get("reference"):
            out[str(r["reference"])] = v
    return out


def _shipment_out(s: dict) -> dict:
    """The shipment on an inbound row. `status` keeps the list's words where the reconciled
    status means the same (the page words "arrived_port", "in_delivery"); otherwise it is the
    reconciled one ("received", "delayed", ...). `derived` carries the whole picture."""
    out = {
        k: s.get(k)
        for k in ("id", "status", "list_status", "mode", "eta", "silent", "silent_hours", "note")
    }
    raw, now = s.get("list_status"), s.get("status")
    if now == "at_port":
        out["status"] = "arrived_port"
    elif now == "in_transit" and raw == "in_delivery":
        out["status"] = raw
    if s.get("derived"):
        out["derived"] = s["derived"]
    return out


def _inbound(part: dict, ships: dict[str, dict]) -> list[dict]:
    """What is on its way for this part, as the verifier counted it, with the shipment's state."""
    rows: list[dict] = []
    seen: set[tuple] = set()

    def add(item: dict, counted: bool, reason: str | None, after_launch: bool) -> None:
        ref = str(item.get("reference") or "")
        key = (ref, _num(item.get("quantity")), counted)
        if key in seen:
            return
        seen.add(key)
        s = ships.get(ref)
        rows.append(
            {
                "reference": ref or None,
                "quantity": _num(item.get("quantity")),
                "eta": item.get("eta") or (s or {}).get("eta"),
                "counted": counted,
                "reason": reason,
                "after_launch": after_launch,
                "supplier_id": item.get("supplier_id"),
                "shipment": _shipment_out(s) if s else None,
            }
        )

    for a in part.get("arriving") or []:
        add(a, True, a.get("basis"), False)
    for a in part.get("not_counted") or []:
        add(a, False, a.get("reason"), False)
    listed = {r["reference"] for r in rows}
    for a in part.get("supply") or []:
        if str(a.get("reference") or "") in listed:
            continue
        add(a, False, a.get("note"), a.get("kind") == "after_launch")
    return rows


def _with_floor(p: dict, site: dict, clock: PackClock, cur: datetime) -> dict:
    """What the site reported for one part: a count replaces the book stock, a shipment reported
    as arrived counts as stock until its goods receipt is booked."""
    from ask_soony.api import parse_ts

    gap = _num(p.get("gap"))
    out: dict = {"counted": None, "reported_in": []}
    counted = site.get("counted")
    per = {str(k): _num(v) or 0 for k, v in (p.get("per_copy") or {}).items()}
    if counted:
        at = parse_ts(counted.get("at")) or cur
        # The need from the time of the count: copies still to pack then, times parts per copy.
        need = sum(q * (clock.remaining_at(at, ed) or 0) for ed, q in per.items())
        later = sum(
            _num(a.get("quantity")) or 0
            for a in p.get("arriving") or []
            if str(a.get("eta") or "") >= at.date().isoformat()
        )
        gap = max(need - counted["count"] - later, 0)
        out["counted"] = counted
    for r in site.get("received") or []:
        gap = max((gap or 0) - r["quantity"], 0)
        out["reported_in"].append(r)
    out["gap"] = gap
    return out


def build(
    cards: list[dict],
    ships_raw: list[dict],
    clock: PackClock,
    cur: datetime,
    strip: list[dict],
    site=None,
    tracked: dict | None = None,
    commits: list[dict] | None = None,
) -> dict:
    """{hours_left, editions: [...], parts: [...], floor} for the Launch page. `site` is the
    floor's picture (floor.prepare): reported counts per edition and per part. `tracked`: the
    reconciled shipments (tracking.py), so an inbound row shows what really happened to it.
    `commits`: confirmed orders (commitments.build); they count as inbound for the copies they can
    still save, and a decided case they belong to keeps its copies at risk until they arrive."""
    from ask_soony import commitments, floor
    from ask_soony.api import part_names, portal_root, rank_key

    data = _dispatch()
    hours_left = round(clock.hours_left(cur), 2) if clock.windows else None
    v_eds = {str(e.get("product")): e for e in data.get("editions") or [] if isinstance(e, dict)}
    editions = []
    for s in strip:
        k = s["edition"]
        plan = clock.editions.get(k)
        day_one = (
            plan.day_one
            if plan and plan.day_one is not None
            else _num((v_eds.get(k) or {}).get("day_one"))
        )
        editions.append(
            {"edition": k, "day_one": day_one, **((site.editions.get(k) or {}) if site else {})}
        )

    live = [c for c in cards if c.get("status") in OPEN]
    # A decided case whose order is still on its way: its shortfall stands until the parts arrive.
    pending = {c["card_id"] for c in commits or [] if c["status"] != "delivered"}
    waiting = [c for c in cards if c.get("status") not in OPEN and c["id"] in pending]
    cutoff = os.environ.get("ASK_CUTOFF_NAME", "the trucks")
    parts = []
    short_names = part_names(portal_root())
    ships = _ships_by_ref(ships_raw, cur, tracked)
    for p in data.get("parts") or []:
        if not isinstance(p, dict) or not p.get("part"):
            continue
        eds = [str(e) for e in p.get("editions") or []]
        mine = _cards_for(p, live)
        ordered = _cards_for(p, waiting)
        per_copy = max([_num(v) or 0 for v in (p.get("per_copy") or {}).values()] or [0]) or 1
        gap = _num(p.get("gap"))
        reported = (
            _with_floor(p, site.parts.get(str(p["part"])) or {}, clock, cur)
            if site and site.parts.get(str(p["part"]))
            else None
        )
        if reported:
            gap = reported["gap"]
        copies = [
            c.get("number") or 0
            for c in [*mine, *ordered]
            if c.get("number_label") == "copies at risk"
        ]
        if reported and reported["counted"]:
            copies = []  # a count on the shelf replaces what the cases inferred from book stock
        # Copies this part cannot cover: the verifier's gap, or a case's copies at risk if higher
        # (a case may know about stock the count still includes, such as a rejected lot).
        short = max([*copies, (gap or 0) / per_copy]) if (copies or gap is not None) else None
        runs_dry = None
        if short and len(eds) == 1:
            t = clock.line_stops(cur, eds[0], short)
            runs_dry = t.isoformat() if t else None
        row = (
            {
                "part": p["part"],
                "name": p.get("description") or p["part"],
                "short_name": short_names.get(str(p["part"])) or p.get("description") or p["part"],
                "editions": eds,
                "needed": _num(p.get("needed")),
                "usable": _num(p.get("usable")),
                "book_stock": _num(p.get("book_stock")),
                "book_as_of": p.get("book_as_of"),
                "gap": gap,
                "short": short,
                # When the line runs out of this part; None when it lasts, the rate is unknown, or
                # the part is shared by editions (then it depends on how the stock is split).
                "runs_dry": runs_dry,
                "shared": len(eds) > 1,
                "inbound": [
                    *(
                        {
                            "reference": r["reference"],
                            "quantity": r["quantity"],
                            "eta": None,
                            "counted": True,
                            "reason": "reported, not booked",
                            "after_launch": False,
                            "supplier_id": None,
                            "shipment": None,
                            "reported": r,
                        }
                        for r in (reported or {}).get("reported_in") or []
                    ),
                    *_inbound(p, ships),
                ],
                "counted": (reported or {}).get("counted"),
                "cards": [c["id"] for c in sorted(mine, key=rank_key)],
                "per_copy": p.get("per_copy") or {},
            }
        )
        parts.append(commitments.apply(row, commits or [], clock, cur, cutoff))
    return {
        "hours_left": hours_left,
        "editions": editions,
        "parts": parts,
        "floor": floor.view(site) if site else None,
    }
