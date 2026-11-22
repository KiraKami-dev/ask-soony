"""Platform API: a small JSON view over data/state/ and the portal master data, plus the built UI.

Reads the shared state shapes written by the other modules (findings, impacts, decisions, claims,
events) and the portal files (suppliers, ports, sites, routes, shipments). Everything shown comes
from those files at request time; nothing about Soony lives in this module. Every field is
optional so a half-finished module never breaks the screen.

The UI writes back only to state files that the other modules read:
  actions.json         one Action per click (approve / escalate / dismiss), status "approved" etc.
  decision_status.json UI status per decision id, for rendering
  trust.json           {action_type: "allow_always" | "ask"}
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import threading
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ask_soony import commitments, floor, launch_view, signoff, supplier_master, tracking
from ask_soony import launch as launch_clock
from ask_soony.launch import PackClock

LEVEL_RANK = {"red": 3, "amber": 2, "grey": 1, "blue": 0}
DONE_STATUSES = {"approved", "dismissed", "escalated", "handled", "sent"}
_lock = threading.Lock()


# ---------------------------------------------------------------- config and file helpers


def state_dir() -> Path:
    return Path(os.environ.get("ASK_STATE_DIR", "data/state"))


def portal_root() -> Path | None:
    """The folder that holds soony/master (the portal mirror), wherever it was put."""
    candidates: list[Path] = []
    if os.environ.get("ASK_PORTAL_DIR"):
        candidates.append(Path(os.environ["ASK_PORTAL_DIR"]))
    data = Path("data")
    candidates += [data / "portal", data / "soony-data", data]
    for base in candidates:
        for root in [base, *sorted(base.glob("*"))] if base.is_dir() else []:
            if (root / "soony" / "master").is_dir():
                return root
    return None


def tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("ASK_TIMEZONE", "Europe/Berlin"))


def now() -> datetime:
    override = os.environ.get("ASK_NOW")
    return datetime.fromisoformat(override) if override else datetime.now(tz())


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=tz())


def read_json(name: str, default: Any) -> Any:
    path = state_dir() / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(name: str, data: Any) -> None:
    path = state_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_list(name: str) -> list[dict]:
    data = read_json(name, [])
    if isinstance(data, dict):
        for key in ("items", name.removesuffix(".json")):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def read_csv(path: Path) -> list[dict]:
    """
    Rows of a CSV as dicts, whatever the export looks like: UTF-8 (with or without BOM), else
    Windows-1252 (SAP and Excel exports from German systems), and ';' or ',' as separator.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    first = text.split("\n", 1)[0]
    sep = ";" if first.count(";") > first.count(",") else ","
    return list(csv.DictReader(io.StringIO(text, newline=""), delimiter=sep))


def num(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").replace(" ", ""))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- map: nodes, legs, shipments

# World geography, not Soony data: the usual deep-sea lane from East Asia to North Europe, east to
# west. Used only when a route's `via` port is missing from ports.csv or the route has no
# waypoints of its own. Only lane points west of the origin are used.
SEA_LANE = [
    (112.0, 14.0),  # South China Sea
    (104.5, 1.4),  # Strait of Malacca / Singapore
    (80.5, 5.5),  # south of Sri Lanka
    (52.0, 12.8),  # Gulf of Aden
    (43.4, 12.6),  # Bab-el-Mandeb
    (32.6, 29.9),  # Suez
    (32.5, 33.5),  # eastern Mediterranean
    (11.0, 37.5),  # central Mediterranean
    (-5.6, 36.0),  # Gibraltar
    (-9.8, 43.5),  # Finisterre
    (-3.5, 49.8),  # Channel approach
    (3.0, 52.2),  # southern North Sea
]


def sea_path(a: tuple[float, float], b: tuple[float, float], via: tuple[float, float] | None):
    """Waypoints (lon, lat) between two ports. Direct if both ends are on the same side."""
    pts = [a]
    if via:
        pts.append(via)
    if a[0] > 40 and b[0] < 30:  # Asia to Europe
        pts += [p for p in SEA_LANE if p[0] < pts[-1][0] - 3]
    pts.append(b)
    return [[round(x, 3), round(y, 3)] for x, y in pts]


def load_network() -> dict:
    root = portal_root()
    if root is None:
        return {"nodes": [], "legs": [], "shipments_raw": [], "root": None}
    soony = root / "soony"
    nodes: dict[str, dict] = {}
    for p in read_csv(soony / "master" / "ports.csv"):
        lat, lon = num(p.get("lat")), num(p.get("lon"))
        if lat is not None and lon is not None:
            nodes[p["locode"]] = {
                "id": p["locode"],
                "kind": "port",
                "name": p.get("name"),
                "country": p.get("country"),
                "lat": lat,
                "lon": lon,
                "mode": p.get("mode"),
            }
    for s in read_csv(soony / "master" / "sites.csv"):
        lat, lon = num(s.get("lat")), num(s.get("lon"))
        if lat is not None and lon is not None:
            nodes[s["site_id"]] = {
                "id": s["site_id"],
                "kind": "dc" if s.get("type") == "dc" else "site",
                "name": s.get("name"),
                "country": s.get("country"),
                "lat": lat,
                "lon": lon,
            }
    sup_dir = root / "suppliers"
    for prof in sorted(sup_dir.glob("*/profile.csv")) if sup_dir.is_dir() else []:
        for r in read_csv(prof):
            lat, lon = num(r.get("lat")), num(r.get("lon"))
            if lat is None or lon is None or not r.get("supplier_id"):
                continue
            nodes[r["supplier_id"]] = {
                "id": r["supplier_id"],
                "kind": "supplier",
                "name": r.get("name"),
                "country": r.get("country"),
                "lat": lat,
                "lon": lon,
                "city": r.get("city"),
            }

    def xy(node_id: str) -> tuple[float, float] | None:
        n = nodes.get(node_id)
        return (n["lon"], n["lat"]) if n else None

    legs = []
    for r in read_csv(soony / "master" / "routes.csv"):
        a, b = xy(r.get("origin", "")), xy(r.get("destination", ""))
        if not a or not b:
            continue
        mode = (r.get("mode") or "").lower()
        via_id = r.get("via") or ""
        path = None
        if mode == "sea":
            path = sea_path(a, b, xy(via_id) if via_id else None)
        legs.append(
            {
                "id": r["route_id"],
                "origin": r["origin"],
                "destination": r["destination"],
                "mode": mode,
                "via": via_id or None,
                "via_known": (not via_id) or via_id in nodes,
                "carrier": r.get("carrier_id"),
                "transit_days": num(r.get("transit_days")),
                "path": path,
            }
        )
    # supplier -> origin port, from the shipments each supplier has on which route
    ships = read_csv(soony / "erp" / "open_shipments.csv")
    return {"nodes": nodes, "legs": legs, "shipments_raw": ships, "root": root}


def shipment_view(
    raw: dict, cur: datetime, ref: datetime | None, silent_h: float, derived: dict | None = None
) -> dict | None:
    """One shipment for the map. Silence is measured against the clock, never against the
    newest row of the list (a frozen list would look fresh); `ref` is kept for old callers and
    ignored. With `derived` (tracking.py) status and silence come from every source: `silent` is
    then "silent and needed for the launch", `status` the reconciled status and `list_status`
    what the hand-kept list says."""
    etd, eta = parse_ts(raw.get("etd")), parse_ts(raw.get("eta"))
    upd = parse_ts(raw.get("updated_at"))
    if etd and eta and eta > etd:
        t = (cur - etd) / (eta - etd)
    else:
        t = 1.0 if (eta and cur >= eta) else 0.0
    hours = (cur - upd).total_seconds() / 3600 if upd else None
    silent = bool(hours is not None and hours >= silent_h)
    status = raw.get("status")
    if derived:
        hours, silent = derived.get("silent_hours"), bool(derived.get("alert"))
        if derived.get("status") not in (None, "unknown"):
            status = derived["status"]
        if status in ("received", "arrived_not_booked"):
            t = 1.0
    return {
        "id": raw.get("shipment_id"),
        "route_id": raw.get("route_id"),
        "mode": raw.get("mode"),
        "supplier_id": raw.get("supplier_id"),
        "status": status,
        "list_status": raw.get("status"),
        "etd": raw.get("etd"),
        "eta": raw.get("eta"),
        "updated_at": raw.get("updated_at"),
        "progress": max(0.0, min(1.0, t)),
        "departed": t > 0,
        "silent": silent,
        "silent_hours": round(hours) if (silent and hours is not None) else None,
        "note": raw.get("note") or None,
        **({"derived": tracking.public(derived)} if derived else {}),
    }


# ---------------------------------------------------------------- cards


def first_name(person: str | None) -> str:
    return (person or "").split()[0] if person else ""


def viewer_title() -> str | None:
    """The buyer's job title from the setup (ASK_VIEWER_TITLE in .env); the data does not say."""
    from ask_soony.config import load_dotenv

    load_dotenv()
    return os.environ.get("ASK_VIEWER_TITLE") or None


def viewer_name(root: Path | None) -> str:
    """The person using the inbox: the buyer named on most purchase orders, unless overridden."""
    if os.environ.get("ASK_VIEWER"):
        return os.environ["ASK_VIEWER"]
    if root is None:
        return ""
    buyers = Counter()
    for f in (root / "soony" / "erp").glob("purchase_orders_2025H2*.csv"):
        buyers.update(r.get("buyer") for r in read_csv(f) if r.get("buyer"))
    return buyers.most_common(1)[0][0] if buyers else ""


_COST_TEXT = re.compile(r"(?:EUR|USD|€|\$)\s*([\d][\d,.']*)|([\d][\d,.']*)\s*(?:EUR|USD|€)", re.I)


def option_cost(option: dict) -> float:
    """What an option costs: the number if given, else read from its cost text
    ('about EUR 16,800'). Anything that mentions money but cannot be read counts as costing."""
    for key in ("cost_value", "cost_eur"):
        v = num(option.get(key))
        if v:
            return v
    text = str(option.get("cost") or "")
    m = _COST_TEXT.search(text)
    if m:
        raw = (m.group(1) or m.group(2)).replace(",", "").replace("'", "")
        try:
            return float(raw.rstrip("."))
        except ValueError:
            return 1.0
    return 0.0


def routine_type(decision: dict, option: dict) -> str | None:
    """Action type that may be allowed to run automatically: routine, free, no commitment."""
    kind = str(option.get("type") or decision.get("action_type") or "")
    flag = option.get("routine", decision.get("routine"))
    if option_cost(option) > 0:  # the money guard: costs never run as a routine
        return None
    if flag is True or (flag is None and "chase" in kind.lower()):
        return kind or "chaser"
    return None


_COVERS = re.compile(r"covers\s+([\d][\d,.]*)", re.I)


def _covers(option: dict) -> float:
    """How many of the gap an option closes, from its effect ('covers 5,900 of the 5,900 gap')."""
    m = _COVERS.search(str(option.get("effect") or ""))
    return float(m.group(1).replace(",", "")) if m else 0.0


def build_cards(net: dict, ships: dict[str, dict]) -> list[dict]:
    findings = {str(f.get("id")): f for f in read_list("findings.json")}
    impacts = {str(i.get("finding_id")): i for i in read_list("impacts.json")}
    decisions = {str(d.get("finding_id")): d for d in read_list("decisions.json")}
    claims = {str(c.get("id")): c for c in read_list("claims.json")}
    statuses = read_json("decision_status.json", {})
    actions = read_list("actions.json")
    trust = read_json("trust.json", {})
    nodes = net.get("nodes") or {}
    viewer = viewer_name(net.get("root"))
    ship_by_supplier: dict[str, list[dict]] = {}
    for s in ships.values():
        ship_by_supplier.setdefault(s["supplier_id"], []).append(s)

    cards = []
    for fid, f in findings.items():
        d = decisions.get(fid, {})
        imp = impacts.get(fid, {})
        sev = d.get("severity")
        base_status = f.get("status") or "open"
        ui = statuses.get(fid, {})
        if base_status in ("resolved", "dismissed") or sev == "log" and not ui:
            status = "dismissed" if base_status != "open" else "logged"
        else:
            status = ui.get("status", "open")
        cl = [claims[c] for c in (f.get("claims") or []) if c in claims]
        supplier = f.get("supplier") or next(
            (c.get("supplier") for c in cl if c.get("supplier")), None
        )
        shipment = f.get("shipment") or next(
            (c.get("shipment") for c in cl if c.get("shipment")), None
        )
        if shipment and shipment in ships:
            supplier = supplier or ships[shipment]["supplier_id"]
            routes = [ships[shipment]["route_id"]]
        else:
            routes = sorted({s["route_id"] for s in ship_by_supplier.get(supplier or "", [])})
        short = num(imp.get("copies_at_risk"))
        hours, slack = num(imp.get("cell_hours_lost")), num(imp.get("slack_hours"))
        level = d.get("level") or f.get("level")
        if level not in LEVEL_RANK:
            if d.get("stops_line") or (hours is not None and slack is not None and hours > slack):
                level = "red"
            elif f.get("kind") == "stale":
                level = "grey"
            elif short or d.get("options"):
                level = "amber"
            else:
                level = "blue"
        options = []
        for i, o in enumerate(d.get("options") or []):
            options.append(
                {
                    "index": i,
                    "action": o.get("action"),
                    "cost": o.get("cost"),
                    "effect": o.get("effect"),
                    "recommended": bool(o.get("recommended", i == 0)),
                    "draft": o.get("draft"),
                    "to": o.get("to"),
                    "channel": o.get("channel"),
                    "routine_type": routine_type(d, o),
                }
            )
        approver = d.get("approver")
        if not approver and any(option_cost(o) for o in d.get("options") or []):
            approver = exception_approver()  # money: whoever the approval policy names
        # Someone else signs (approval policy), but it is only theirs once it was passed on:
        # until then it stays with the viewer, whose next step is to ask them.
        signoff = (
            approver if approver and viewer and first_name(approver) != first_name(viewer) else None
        )
        called = next(
            (
                a
                for a in reversed(actions)
                if str(a.get("decision_id")) == fid and a.get("channel") == "call" and a.get("at")
            ),
            None,
        )
        evidence = [
            {
                "source": s.get("source"),
                "value": s.get("value"),
                "doc": s.get("doc"),
                "page": s.get("page"),
                "quote": s.get("quote"),
                "translation": s.get("translation"),
            }
            for s in (f.get("sides") or [])
        ]
        for c in cl:
            evidence.append(
                {
                    "source": c.get("source_doc"),
                    "value": c.get("value"),
                    "doc": c.get("source_doc"),
                    "page": c.get("page"),
                    "quote": c.get("quote"),
                    "language": c.get("language"),
                    "translation": c.get("quote_en") or c.get("translation"),
                    "unit": c.get("unit"),
                }
            )
        follow_up = None
        if status in ("approved", "sent") and short:
            # Half done: the chosen option does not close the gap but another one would (e.g. the
            # booking was corrected, the missing books were never ordered). Keep it open.
            done = next(
                (
                    a.get("option")
                    for a in reversed(actions)
                    if a.get("decision_id") == fid and a.get("kind") == "approve"
                ),
                None,
            )
            idx = int(done) if str(done).isdigit() else None
            cover = [_covers(o) for o in options]
            if idx is not None and idx < len(options) and not cover[idx] and any(cover):
                follow_up = {
                    "done": options[idx].get("action"),
                    "done_index": idx,
                    "at": ui.get("at"),
                    "still_short": short,
                }
                status = "open"
                for i, o in enumerate(options):
                    o["done"] = i == idx
                    o["recommended"] = i == cover.index(max(cover))
        cards.append(
            {
                "id": fid,
                "kind": f.get("kind"),
                "part": f.get("part"),
                "follow_up": follow_up,
                "headline": d.get("headline") or f.get("title") or f.get("summary") or fid,
                "summary": f.get("summary"),
                "number": short if short is not None else num(imp.get("units_short")),
                "number_label": "copies at risk" if short is not None else "units short",
                "edition": imp.get("edition"),
                "cell_hours_lost": hours,
                "slack_hours": slack,
                "deadline": d.get("deadline") or imp.get("deadline"),
                "runs_dry": imp.get("runs_dry") or d.get("runs_dry"),
                "arrives_at": stated_arrival(f, imp),
                "severity": sev,
                "level": level,
                "status": status,
                "approver": approver,
                "routed_to_other": bool(signoff and status != "open"),
                "signoff": signoff,
                "reason": d.get("reason"),
                "called_at": called.get("at") if called else None,
                "options": options,
                "evidence": evidence,
                "trail": d.get("trail") or f.get("trail") or [],
                "supplier_id": supplier,
                "supplier_name": (nodes.get(supplier) or {}).get("name"),
                "shipment": shipment,
                "routes": routes,
                "ui": ui,
                "created_at": f.get("created_at"),
                "escalation_brief": escalation_brief(f, d, imp, options, viewer),
                "trusted_types": [k for k, v in trust.items() if v == "allow_always"],
            }
        )
    known = {c["id"] for c in cards}
    for extra in verifier_cards(net, statuses, actions, trust, viewer) + event_cards(
        net, statuses, trust
    ):
        if extra["id"] not in known:
            cards.append(extra)
    names = part_names(net.get("root"))
    for c in cards:
        c["part_name"] = names.get(str(c.get("part") or ""))
    cards.sort(key=rank_key)
    return cards


def part_names(root: Path | None) -> dict[str, str]:
    """Short readable names for part codes, from components.csv: 'Art Book Grand Heist 6 (96 p,
    hardcover)' -> 'Art Book'. The launch title (the part of a launch product's name before ' – ')
    and anything in brackets are dropped."""
    if root is None:
        return {}
    master = root / "soony" / "master"
    titles = {
        r.get("name", "").split("–")[0].strip()
        for r in read_csv(master / "products.csv")
        if r.get("lifecycle") == "launch" and "–" in r.get("name", "")
    }
    out = {}
    for r in read_csv(master / "components.csv"):
        name = str(r.get("description") or "")
        for t in filter(None, titles):
            name = name.replace(f"– {t}", "").replace(t, "")
        name = re.sub(r"\s*\([^)]*\)", "", name)
        name = re.sub(r"\s+", " ", name).strip(" –-")
        if r.get("component_id") and name:
            out[r["component_id"]] = name
            out[str(r.get("description"))] = name  # cards that already carry the description
    return out


# ---------------------------------------------------------------- adapters for teammates' outputs


def cards_file() -> Path:
    """The verifier's decisions: dispatch.json from the pipeline, else the older decision_cards.json."""
    if os.environ.get("ASK_CARDS_FILE"):
        return Path(os.environ["ASK_CARDS_FILE"])
    dispatch = state_dir() / "dispatch.json"
    store = Path(os.environ.get("ASK_DATA_DIR", "data/store"))
    return dispatch if dispatch.exists() else store / "decision_cards.json"


def readings_dir() -> Path:
    return Path(os.environ.get("ASK_DATA_DIR", "data/store")) / "image_recognizer"


_approver_cache: dict[str, Any] = {}


def exception_approver() -> str | None:
    """Who approves exceptions per the approval policy, read by the verifier's people module from
    the org charts and the policy. Cached per version of the verifier output."""
    f, root = cards_file(), portal_root()
    key = f"{f}:{f.stat().st_mtime if f.exists() else 0}:{root}"
    if key not in _approver_cache:
        name = None
        try:
            from ask_soony.dispatch.decisions.people import People
            from ask_soony.verifier.linker import build

            records = build(root, readings_dir()).records
            # today's date: a role holder who starts later (org chart v2) is not active yet
            people = People(records[records.kind == "reading"].to_dict("records"), now())
            holder = people.exception_approver()
            name = people.label(holder, "") or None
        except Exception:  # verifier not installed or no readings: no named approver
            name = None
        _approver_cache.clear()
        _approver_cache[key] = name
    return _approver_cache[key]


_docs_cache: dict[str, Any] = {}


def documents_by_part() -> dict[str, list[dict]]:
    """Source documents the image recognizer read, keyed by the part ids on their lines."""
    from ask_soony.verifier.baseline import reading_metas  # live store, then frozen baseline

    metas = reading_metas(readings_dir())
    stamp = (len(metas), max((p.stat().st_mtime for p in metas), default=0))
    if _docs_cache.get("stamp") != stamp:
        out: dict[str, list[dict]] = {}
        for meta_path in metas:
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                v = json.loads((meta_path.parent / "verdict.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            verdict = v.get("verdict") or {}
            flags = verdict.get("red_flags") if isinstance(verdict, dict) else None
            doc = {
                "source": " ".join(
                    str(x)
                    for x in (
                        v.get("document_type", "").replace("_", " ").capitalize(),
                        v.get("document_number"),
                    )
                    if x
                ),
                "doc": meta.get("path"),
                "language": ", ".join(v.get("languages") or []),
                "quote": (flags or [None])[0]
                or next(
                    (
                        c.get("description")
                        for c in v.get("corrections") or []
                        if isinstance(c, dict) and c.get("description")
                    ),
                    None,
                ),
                "translation": (verdict.get("summary_en") if isinstance(verdict, dict) else None)
                or next(
                    (st.get("text_en") for st in v.get("statements") or [] if st.get("text_en")),
                    None,
                ),
            }
            for line in v.get("lines") or []:
                if line.get("material_id"):
                    out.setdefault(line["material_id"], []).append(doc)
        _docs_cache.update(stamp=stamp, docs=out)
    return _docs_cache["docs"]


def fmt_dt(dt: datetime) -> str:
    return dt.astimezone(tz()).strftime("%a %H:%M")


def person_of(role: str | None) -> str | None:
    """'Dr. Maren Vogt, Head of Procurement' -> 'Dr. Maren Vogt'; roles without a name stay."""
    return role.split(",")[0].split(" (")[0].strip() if role else None


def parse_when(value: Any) -> datetime | None:
    """ISO, or the verifier's 'Sun 22 Nov 12:30' (DC clock, year of the current clock)."""
    dt = parse_ts(value)
    if dt or not value:
        return dt
    for fmt in ("%a %d %b %H:%M", "%d %b %H:%M"):
        try:
            d = datetime.strptime(f"{now().year} {str(value).strip()}", f"%Y {fmt}")
            return d.replace(tzinfo=tz())
        except ValueError:
            continue
    return None


_ARRIVAL = re.compile(
    r"\barriv\w*\b[^.;]{0,40}?\b((?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+)?\d{1,2}\s+[A-Z][a-z]{2}|\d{4}-\d{2}-\d{2})"
)


def stated_arrival(f: dict, imp: dict) -> str | None:
    """When the late goods of a finding arrive: an explicit `arrives_at` from the finding or its
    impact, else the first date the summary or title gives after 'arrive' ('20,000 arrive in
    Hamburg on Tue 24 Nov'). Date only: the start of that day, local time."""
    explicit = f.get("arrives_at") or imp.get("arrives_at")
    if explicit:
        return _iso(explicit)
    for text in (f.get("summary"), f.get("title")):
        m = _ARRIVAL.search(str(text or ""))
        if not m:
            continue
        raw = m.group(1)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return datetime.fromisoformat(raw).replace(tzinfo=tz()).isoformat()
        day_month = " ".join(raw.split()[-2:])
        try:
            return (
                datetime.strptime(f"{now().year} {day_month}", "%Y %d %b")
                .replace(tzinfo=tz())
                .isoformat()
            )
        except ValueError:
            continue
    return None


def _iso(value: Any) -> str | None:
    dt = parse_when(value)
    return dt.isoformat() if dt else None


def _ui_status(cid: str, statuses: dict, default: str = "open") -> tuple[str, dict]:
    ui = statuses.get(cid, {})
    return ui.get("status", default), ui


def _base_card(cid: str, **kw) -> dict:
    card = {
        "id": cid,
        "kind": None,
        "part": None,
        "headline": cid,
        "summary": None,
        "number": None,
        "number_label": "copies at risk",
        "edition": None,
        "cell_hours_lost": None,
        "slack_hours": None,
        "deadline": None,
        "runs_dry": None,
        "arrives_at": None,
        "severity": None,
        "level": "blue",
        "status": "open",
        "approver": None,
        "routed_to_other": False,
        "reason": None,
        "called_at": None,
        "options": [],
        "evidence": [],
        "trail": [],
        "supplier_id": None,
        "supplier_name": None,
        "shipment": None,
        "routes": [],
        "ui": {},
        "created_at": None,
        "escalation_brief": "",
        "trusted_types": [],
    }
    card.update(kw)
    return card


_MONEY = re.compile(r"(\d[\d,.]*)\s*(EUR|USD|PLN|€|\$)")


def verifier_cards(
    net: dict, statuses: dict, actions: list[dict], trust: dict, viewer: str
) -> list[dict]:
    """The verifier's decisions (dispatch.json `decisions`, or decision_cards.json `cards`) as UI cards.

    Each ready message becomes an option (send it); the verifier's own option lines are facts about
    supply and timing and go into the recommended option's effect. Routing (docs/plan.md §5): a card
    whose recommended action costs money, or whose recommendation is a decision message, goes to
    the person who must decide; routine supplier questions and internal notes stay with the buyer.
    """
    f = cards_file()
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("decisions") or data.get("cards") or []
    nodes = net.get("nodes") or {}
    made = datetime.fromtimestamp(f.stat().st_mtime, tz()).isoformat()
    approver_of_exceptions = exception_approver() if items else None
    descriptions = {
        str(p.get("part")): p.get("description")
        for p in data.get("parts") or []
        if p.get("description")
    }
    out = []
    for vc in items:
        cid = str(vc.get("id") or "")
        if not cid:
            continue
        msgs = vc.get("messages") or []
        facts = [str(x) for x in vc.get("options") or []]
        at_risk = num(vc.get("at_risk")) or 0
        if_acted = num(vc.get("at_risk_if_acted"))
        runs_dry = _iso(vc.get("runs_dry"))
        level = (
            "red"
            if at_risk and runs_dry
            else "amber"
            if at_risk or vc.get("blocked_only")
            else "blue"
        )
        supplier = next(
            (
                sid
                for sid, n in nodes.items()
                if n.get("kind") == "supplier"
                and (
                    any(n.get("name") and n["name"] in str(m.get("to_role")) for m in msgs)
                    or any(sid in str(e) for e in vc.get("evidence") or [])
                )
            ),
            None,
        )

        options = []
        for i, m in enumerate(msgs):
            who = person_of(m.get("to_role")) or "recipient"
            external = bool(m.get("to_email"))
            subject = str(m.get("subject") or "")
            names = [
                str(n.get("name"))
                for n in nodes.values()
                if n.get("name") and n["name"] in str(m.get("to_role"))
            ]
            cost_line = next(
                (
                    x
                    for x in facts
                    if _MONEY.search(x) and (not names or any(nm in x for nm in names))
                ),
                None,
            )
            cost_m = _MONEY.search(cost_line) if cost_line and external else None
            cost_value = num(cost_m.group(1)) if cost_m else None
            commits = external and (cost_value or subject.lower().startswith("order"))
            options.append(
                {
                    "index": i,
                    "action": f"{'Email' if external else 'Message'} {who}: {subject}",
                    "cost": (f"about {cost_m.group(1)} {cost_m.group(2)}" if cost_m else "No cost"),
                    "cost_value": cost_value,
                    "effect": None,
                    "recommended": False,
                    "draft": m.get("body"),
                    "to": m.get("to_email") or m.get("to_role"),
                    "to_role": m.get("to_role"),
                    "channel": "email" if external else "notify",
                    "routine_type": None
                    if commits
                    else ("supplier_question" if external else "internal_note"),
                }
            )
        rec = next((o for o in options if o["channel"] == "email"), options[0] if options else None)
        if rec:
            rec["recommended"] = True
            saved = at_risk - if_acted if if_acted is not None else None
            rec["effect"] = (
                "; ".join(
                    [*([f"saves {saved:,.0f} copies if it lands in time"] if saved else []), *facts]
                )
                or None
            )
            options.remove(rec)
            options.insert(0, rec)
            for i, o in enumerate(options):
                o["index"] = i
        decision_msg = next(
            (o for o in options if o["channel"] == "notify" and vc.get("decision")), None
        )
        if rec and (
            rec.get("cost_value") or rec["routine_type"] is None and rec["channel"] == "email"
        ):
            approver = approver_of_exceptions or person_of((decision_msg or {}).get("to_role"))
            reason = f"Commits money ({rec['cost']}): above the buyer's routine"
        elif rec and rec is decision_msg:
            approver = person_of(rec.get("to_role"))
            reason = vc.get("decision")
        else:
            approver = viewer or None
            reason = (
                f"Decision for {person_of(decision_msg['to_role'])}: {vc.get('decision')}"
                if decision_msg
                else "Routine, no cost"
            )
        if (
            level == "red"
            and approver == viewer
            and approver_of_exceptions
            and rec
            and rec.get("cost_value")
        ):
            approver = approver_of_exceptions
        edition = str(vc.get("edition") or "").split(" + ")[0] or None
        status, ui = _ui_status(cid, statuses)
        called = next(
            (
                a
                for a in reversed(actions)
                if str(a.get("decision_id")) == cid and a.get("channel") == "call" and a.get("at")
            ),
            None,
        )
        approver = person_of(approver)
        deadline = parse_when(vc.get("act_by"))
        if deadline and deadline < now():
            reason = f"{reason} · The verifier's decide-by ({fmt_dt(deadline)}, as of the stock data) has passed"
        card = _base_card(
            cid,
            kind="mismatch",
            part=descriptions.get(str(vc.get("part"))) or vc.get("part"),
            headline=vc.get("title") or cid,
            summary=vc.get("decision"),
            number=at_risk,
            edition=edition,
            deadline=_iso(vc.get("act_by")),
            runs_dry=runs_dry,
            level=level,
            status=status,
            ui=ui,
            approver=approver,
            routed_to_other=bool(
                approver
                and viewer
                and first_name(person_of(approver)) != first_name(viewer)
                and status != "open"
            ),
            signoff=approver
            if approver and viewer and first_name(person_of(approver)) != first_name(viewer)
            else None,
            reason=reason,
            called_at=called.get("at") if called else None,
            options=options,
            created_at=made,
            evidence=[
                {"source": "System says", "value": vc.get("system_says")},
                {"source": "Paper says", "value": vc.get("paper_says")},
                *({"source": "Evidence", "quote": e} for e in vc.get("evidence") or []),
                *documents_by_part().get(str(vc.get("part")), []),
            ],
            trail=[
                {
                    "agent": "Verification",
                    "at": made,
                    "text": "Linked orders, receipts, scans and stock; priced the gap.",
                },
                {"agent": "Routing", "at": made, "text": f"{reason} → {approver or 'buyer'}."},
            ],
            supplier_id=supplier,
            supplier_name=(nodes.get(supplier) or {}).get("name"),
            trusted_types=[k for k, v in trust.items() if v == "allow_always"],
        )
        card["escalation_brief"] = escalation_brief(
            {"summary": card["headline"]}, {"deadline": card["deadline"]}, {}, options, viewer
        )
        out.append(card)
    return out


def event_impacts() -> dict:
    """The event impact agent's results keyed by event: event_impacts.json, plus an older
    impacts.json when it is keyed by event (the paper check keeps a list there)."""
    out: dict = {}
    for name in ("impacts.json", "event_impacts.json"):
        data = read_json(name, None)
        if isinstance(data, dict):
            out.update(data)
    return out


def event_cards(net: dict, statuses: dict, trust: dict) -> list[dict]:
    """News and notice impacts from the event impact agent (event_impacts.json, by event)."""
    data = event_impacts()
    nodes = net.get("nodes") or {}
    out = []
    for eid, r in data.items():
        if not isinstance(r, dict) or "overall_launch_risk" not in r:
            continue
        cid = "EV-" + hashlib.sha1(str(eid).encode()).hexdigest()[:8]
        risk = r.get("overall_launch_risk")
        affected = r.get("affected") or []
        hit = next(
            (a for a in affected if a.get("launch_risk") == "yes"), affected[0] if affected else {}
        )
        status, ui = _ui_status(cid, statuses, "open" if risk == "yes" else "logged")
        supplier = hit.get("supplier_id") if hit.get("supplier_id") in nodes else None
        out.append(
            _base_card(
                cid,
                kind="risk_news",
                part=hit.get("component_id"),
                headline=r.get("event_summary") or str(eid),
                summary=r.get("recommendation"),
                level="amber" if risk == "yes" else "grey",
                status=status,
                ui=ui,
                reason=(
                    r.get("recommendation")
                    if risk == "yes"
                    else "; ".join(a.get("mechanism", "") for a in affected[:2])
                    or "No part of the launch is touched."
                ),
                created_at=r.get("analysed_at"),
                supplier_id=supplier,
                supplier_name=(nodes.get(supplier) or {}).get("name"),
                evidence=[
                    {
                        "source": a.get("supplier_id"),
                        "value": f"{a.get('effect')}, launch risk {a.get('launch_risk')}",
                        "quote": a.get("evidence"),
                    }
                    for a in affected
                ],
                trail=[
                    {
                        "agent": "Event impact",
                        "at": r.get("analysed_at"),
                        "text": f"Read {r.get('source')}.",
                    }
                ],
                trusted_types=[k for k, v in trust.items() if v == "allow_always"],
            )
        )
    return out


def escalation_brief(f: dict, d: dict, imp: dict, options: list[dict], viewer: str) -> str:
    lead = options[0] if options else {}
    parts = [
        f"{first_name(viewer) or 'The buyer'} needs your decision: {d.get('headline') or f.get('summary') or ''}."
    ]
    if d.get("deadline") or imp.get("deadline"):
        parts.append(f"Decide by {d.get('deadline') or imp.get('deadline')}.")
    if lead:
        parts.append(
            f"Recommended: {lead.get('action')} ({lead.get('cost') or 'no cost stated'}). {lead.get('effect') or ''}"
        )
    return " ".join(p.strip() for p in parts if p)


def _promote_by_launch(cards: list[dict], launch: dict, clock) -> bool:
    """
    Rank by what the launch loses: a case on a part that runs dry before the trucks becomes red
    and is due when the part runs dry, however its detector rated it. Site questions about the
    same part stay as they are (they support the case, they are not the case).
    """
    launch_at = parse_ts((launch or {}).get("launch_at")) or parse_ts(
        getattr(clock, "launch_at", None) and clock.launch_at.isoformat()
    )
    changed = False
    by_id = {c["id"]: c for c in cards}
    for part in (launch or {}).get("parts") or []:
        dry = parse_ts(part.get("runs_dry"))
        if not dry or (launch_at and dry >= launch_at) or not part.get("short"):
            continue
        for cid in part.get("cards") or []:
            c = by_id.get(cid)
            if not c or c.get("site_question") or c["status"] not in ("open", "escalated"):
                continue
            c["level"] = "red"
            c["runs_dry"] = c.get("runs_dry") or part["runs_dry"]
            dl = parse_ts(c.get("deadline"))
            if not dl or dl > dry:
                c["deadline"] = part["runs_dry"]
            changed = True
    return changed


def rank_key(c: dict):
    dl = parse_ts(c.get("deadline"))
    return (
        -LEVEL_RANK.get(c["level"], 0),
        dl.timestamp() if dl else math.inf,
        -(c.get("number") or 0),
    )


# ---------------------------------------------------------------- state view


def edition_labels(root: Path | None) -> dict[str, str]:
    """Launch editions named in products.csv, keyed by product id."""
    out: dict[str, str] = {}
    if root:
        for r in read_csv(root / "soony" / "master" / "products.csv"):
            if r.get("lifecycle") == "launch":
                name = r.get("name", "")
                label = name.split("–")[-1].split("(")[0].strip() if "–" in name else name
                out[r["product_id"]] = label.replace(" Edition", "")
    return out


def edition_copies_at_risk(
    mine: list[dict], verifier_figure: float | None, remaining: float | None
) -> float:
    """Copies at risk for one edition, always a number the user can find on screen.

    Rule: the largest 'copies at risk' figure among the edition's open cards, or the verifier's
    edition total when that is higher (it covers its own cards without double counting). Max, not
    sum: every box needs all its parts, so shortfalls of different parts hit the same unpacked
    copies and the scarcest part bounds the loss. Cards counted in parts ('units short') are left
    out. Never more than the copies still to pack.
    """
    figures = [c.get("number") or 0 for c in mine if c.get("number_label") == "copies at risk"]
    best = max([*figures, verifier_figure or 0])
    return min(best, remaining) if remaining is not None else best


def build_strip(
    cards: list[dict], root: Path | None, clock: PackClock, cur: datetime
) -> list[dict]:
    """One cell per launch edition. Slack and the time a line stops come from the packing clock
    (shift windows, rate, copies left), so a line never 'stops' at night. Each cell also carries
    when the line finishes or stops, for the launch timeline (build_clock)."""
    launch = {
        str(e.get("edition")): e for e in (read_json("launch.json", {}).get("editions") or [])
    }
    labels = edition_labels(root)
    try:  # the verifier prices copies at risk per edition without double counting shared parts
        vdata = json.loads(cards_file().read_text(encoding="utf-8"))
        verifier_at_risk = {
            str(e["product"]): num(e.get("at_risk")) or 0 for e in vdata.get("editions") or []
        }
    except (OSError, json.JSONDecodeError, KeyError, AttributeError):
        verifier_at_risk = {}
    keys: list[str] = list(launch)
    for k in [*labels, *(c["edition"] for c in cards if c.get("edition"))]:
        if k not in keys:
            keys.append(k)

    def label_of(k: str) -> str:
        if k in launch and launch[k].get("label"):
            return launch[k]["label"]
        for pid, lab in labels.items():
            if k == pid or k.lower() in lab.lower() or lab.lower().startswith(k.lower()):
                return lab
        return k.replace("_", " ").title()

    strip = []
    for k in keys:
        mine = [
            c
            for c in cards
            if c.get("edition") == k
            and c["status"] in ("open", "escalated")
            and not c.get("site_question")  # questions to the site: the count, not a shortfall
        ]
        base = launch.get(k, {})
        worst = max((LEVEL_RANK[c["level"]] for c in mine), default=0)
        slack = base.get("slack_hours")
        if slack is None:
            slack = clock.slack_hours(cur, k)
        if slack is None:
            vals = [c["slack_hours"] for c in mine if c.get("slack_hours") is not None]
            slack = min(vals) if vals else None
        ed = clock.editions.get(k)
        at_risk = edition_copies_at_risk(
            mine, verifier_at_risk.get(k), ed.remaining if ed else None
        )
        if not mine and k in verifier_at_risk:
            at_risk = verifier_at_risk[k]
        # The line stops when a part runs dry (a card says when), or once it has packed every copy
        # it has parts for (clock), whichever comes first.
        stops_when = [
            t
            for t in (parse_when(c.get("runs_dry")) for c in mine if (c.get("number") or 0) > 0)
            if t
        ]
        by_clock = clock.line_stops(cur, k, at_risk)
        stops = min([*stops_when, *([by_clock] if by_clock else [])], default=None)
        # A stop in the past stays where it was: the line stood still from then on (moving it to
        # 'now' would make it creep on every poll).
        finishes = None if stops else clock.finishes(cur, k)
        # What breaks first is the card whose shortfall sets the line's limit (the largest copies
        # at risk, as edition_copies_at_risk takes the max), not simply the most urgent card;
        # otherwise a stop caused by one part is pinned on another part's decision.
        limiting = [
            c
            for c in mine
            if c.get("number_label") == "copies at risk" and (c.get("number") or 0) > 0
        ]
        first = (
            max(limiting, key=lambda c: (c.get("number") or 0, LEVEL_RANK.get(c["level"], 0)))
            if limiting
            else (sorted(mine, key=rank_key)[0] if mine else None)
        )
        breaks = None
        if first:
            breaks = {
                "text": first.get("part") or first.get("headline"),
                "deadline": first.get("deadline"),
                "stops_at": stops.isoformat() if stops else None,
                "card": first["id"],
            }
        left = clock.remaining_at(cur, k)
        if stops and stops <= cur and left is not None:  # a stopped line packs nothing more
            left = max(
                left, min(at_risk, ed.remaining if ed and ed.remaining is not None else at_risk)
            )
        rate = ed.rate_per_hour if ed else None
        strip.append(
            {
                "edition": k,
                "label": label_of(k),
                "level": {3: "red", 2: "amber", 1: "grey", 0: "blue"}[worst],
                "status": {3: "Stops the line", 2: "At risk", 1: "Unclear", 0: "On track"}[worst],
                "copies_at_risk": at_risk,
                "slack_hours": slack,
                "breaks_first": breaks,
                "finishes_at": finishes.isoformat() if finishes else None,
                "stops_at": stops.isoformat() if stops else None,
                "remaining": round(left) if left is not None else None,
                "rate_per_hour": rate,
                "packed_as_of": ed.packed_as_of.isoformat() if ed and ed.packed_as_of else None,
                # Copies the shifts cannot pack even with every part present (plan rate, hours left).
                "short_of_time": round(max(left - rate * clock.hours_left(cur), 0))
                if left is not None and rate
                else None,
            }
        )
    return strip


def build_clock(strip: list[dict], clock: PackClock, cur: datetime) -> dict:
    """The packing clock for the launch timeline: launch time, the shifts from today's start until
    launch, and per edition when its line finishes or stops."""
    windows = clock.windows
    if not windows:  # no plan and no launch date: the brief's shifts in the coming launch week
        sunday = (cur + timedelta(days=(6 - cur.weekday()) % 7)).date()
        windows = launch_clock.default_windows(sunday, tz())
    launch_at = windows[-1][1]
    day_start = cur.astimezone(tz()).replace(hour=0, minute=0, second=0, microsecond=0)
    keep = [(s, e) for s, e in windows if e > day_start and s < launch_at] or windows[-1:]
    fields = (
        "edition",
        "label",
        "level",
        "finishes_at",
        "stops_at",
        "copies_at_risk",
        "slack_hours",
        "remaining",
        "rate_per_hour",
        "packed_as_of",
        "short_of_time",
    )
    return {
        "launch": launch_at.isoformat(),
        # What leaves at the deadline, per customer (trucks, a ship date, start of production)
        "cutoff": {
            "name": os.environ.get("ASK_CUTOFF_NAME", "the trucks"),
            "short": os.environ.get("ASK_CUTOFF_SHORT", "Trucks"),
        },
        "windows": [[s.isoformat(), e.isoformat()] for s, e in keep],
        "editions": [
            {f: x.get(f) for f in fields}
            | {"breaks_first_card": (x.get("breaks_first") or {}).get("card")}
            for x in strip
        ],
    }


def _watching() -> bool:
    """Same test as the background loop: switched on and a portal token to poll with."""
    from ask_soony.config import Settings

    on = os.environ.get("ASK_BACKGROUND", "").lower() in ("on", "1", "true")
    return on and bool(Settings.from_env().portal_token)


def build_activity(cards: list[dict], events: list[dict], root: Path | None) -> list[dict]:
    items = []
    # Who decided: the card's recorded action (decision_status action_id), else its latest action.
    actions = read_list("actions.json")
    by_id = {a.get("id"): a for a in actions if a.get("id")}
    latest = {str(a.get("decision_id")): a for a in actions}

    def act_of(c: dict) -> dict:
        return by_id.get((c.get("ui") or {}).get("action_id")) or latest.get(c["id"]) or {}

    def via(c: dict) -> str | None:
        return act_of(c).get("via")

    for e in events:
        items.append(
            {"at": e.get("at"), "text": f"Read {e.get('path') or e.get('file')}", "kind": "read"}
        )
    for c in cards:
        if c.get("site_question"):  # questions to the site write their own lines
            items += c.get("activity") or []
            continue
        if c["status"] in ("dismissed", "logged"):
            items.append(
                {
                    "at": c.get("created_at"),
                    "kind": "dismissed",
                    "card": c["id"],
                    "text": f"{'No launch risk' if c['status'] == 'logged' else 'Dismissed'}: {c['headline']}",
                    "detail": c.get("reason") or c.get("summary"),
                    "via": via(c),
                    "by": act_of(c).get("by"),
                    "action_id": act_of(c).get("id"),
                }
            )
        elif c["status"] != "open":
            ui = c.get("ui") or {}
            items.append(
                {
                    "at": ui.get("at") or c.get("created_at"),
                    "kind": c["status"],
                    "card": c["id"],
                    "text": f"{c['status'].title()}: {c['headline']}",
                    "detail": ui.get("note"),
                    "via": via(c),
                    "by": act_of(c).get("by"),
                    "action_id": act_of(c).get("id"),
                }
            )
    items.sort(
        key=lambda x: parse_ts(x.get("at")) or datetime.min.replace(tzinfo=tz()), reverse=True
    )
    return items[:200]


def event_node(path: str, net: dict) -> str | None:
    nodes = net.get("nodes") or {}
    for seg in str(path).split("/"):
        sid = seg.split("_")[0]
        if sid in nodes:
            return sid
    return next((n["id"] for n in nodes.values() if n["kind"] == "dc"), None)


def newest_mtime(root: Path | None) -> datetime | None:
    if not root:
        return None
    best = 0.0
    for p in root.rglob("*"):
        if p.is_file():
            best = max(best, p.stat().st_mtime)
    return datetime.fromtimestamp(best, tz()) if best else None


def build_state() -> dict:
    cur = now()
    net = load_network()
    root = net["root"]
    silent_h = float(os.environ.get("ASK_SILENT_HOURS", "18"))
    clock = launch_clock.load(root, tz())
    # One reconciled truth per shipment (receipts, site answers, documents, replies, the list),
    # silence against the clock. Shipments the list still shows as moving stay, with `derived`.
    tracked = tracking.for_state(net, cur, clock)
    ships = {}
    for r in net["shipments_raw"]:
        v = shipment_view(r, cur, None, silent_h, tracked["shipments"].get(r.get("shipment_id")))
        if v and v["id"] and v["list_status"] not in ("delivered", "received", "cancelled"):
            ships[v["id"]] = v
    cards = build_cards(net, ships)
    # The packing clock, with the site's reported counts in place of the projection, and the
    # questions to the site (stale counts, due shipments, uncounted book stock) as cards.
    site = floor.prepare(root, clock, cur, net)
    cards += floor.cards(
        site,
        read_json("decision_status.json", {}),
        read_list("actions.json"),
        read_json("trust.json", {}),
        viewer_name(root),
        cur,
    )
    cards += tracking.state_cards(tracked, net, cur, clock, cards)
    try:  # newer supplier profiles and bank detail notices against the master data
        cards += supplier_master.cards(
            net, cur, read_json("decision_status.json", {}), viewer_name(root)
        )
    except Exception as e:  # one unreadable profile must not take Today down
        print(f"supplier master check failed: {e!r}", file=sys.stderr)
    names = part_names(root)
    for c in cards:
        if c.get("site_question") and not c.get("part_name"):
            c["part_name"] = names.get(str(c.get("part") or ""))
    cards.sort(key=rank_key)
    open_cards = [c for c in cards if c["status"] == "open"]

    route_level: dict[str, str] = {}
    badges: Counter = Counter()
    ship_level: dict[str, str] = {}
    for c in cards:
        if c["status"] not in ("open", "escalated"):
            continue
        for r in c["routes"]:
            if LEVEL_RANK[c["level"]] >= LEVEL_RANK[route_level.get(r, "blue")]:
                route_level[r] = c["level"]
        if c["shipment"] in ships:
            ship_level[c["shipment"]] = c["level"]
        badges[
            c["supplier_id"]
            or next((n["id"] for n in net["nodes"].values() if n["kind"] == "dc"), "")
        ] += 1
    for s in ships.values():
        s["level"] = ship_level.get(s["id"]) or ("grey" if s["silent"] else "blue")

    events = sorted(read_list("events.json"), key=lambda e: str(e.get("at")), reverse=True)
    for e in events:
        e["node"] = event_node(e.get("path") or e.get("file") or "", net)
        e["id"] = (
            e.get("id")
            or hashlib.sha1(f"{e.get('at')}{e.get('path') or e.get('file')}".encode()).hexdigest()[
                :10
            ]
        )
    status = read_json("status.json", {})
    last_drop = parse_ts((events[0].get("at") if events else None)) or newest_mtime(root)
    last_check = parse_ts(status.get("last_check_at")) or last_drop
    since = cur.replace(hour=6, minute=0, second=0, microsecond=0)
    if cur < since:
        since -= timedelta(days=1)
    read_n = (
        status.get("files_read_since_06")
        if isinstance(status.get("files_read_since_06"), int)
        else sum(1 for e in events if (parse_ts(e.get("at")) or since) >= since)
    )

    inbox = [c for c in open_cards if not c["routed_to_other"]]
    strip = build_strip(cards, root, clock, cur)
    # Confirmed orders become inbound: Launch counts what they can still save, a decided case shows
    # where its order stands, and an order that lands too late opens its own decision.
    try:
        commits = commitments.build(cards, net, clock, cur)
    except Exception as e:  # a reply we cannot read must not take Today down
        print(f"commitments failed: {e!r}", file=sys.stderr)
        commits = []
    launch = launch_view.build(
        cards,
        net["shipments_raw"],
        clock,
        cur,
        strip,
        site,
        tracked=tracked["shipments"],
        commits=commits,
    )
    for c in cards:
        mine = [x for x in commits if x["card_id"] == c["id"]]
        if mine:
            c["commitment_line"] = commitments.status_line(mine, cur)
            c["commitments"] = [x["id"] for x in mine]
    late = commitments.late_cards(
        commits,
        launch["parts"],
        clock,
        cur,
        net,
        read_json("decision_status.json", {}),
        viewer_name(root),
        os.environ.get("ASK_CUTOFF_NAME", "the trucks"),
    )
    if late:
        cards += late
        cards.sort(key=rank_key)
        strip = build_strip(cards, root, clock, cur)
    commitments.strip_adjust(strip, launch["parts"], clock, cur)
    open_cards = [c for c in cards if c["status"] == "open"]
    inbox = [c for c in open_cards if not c["routed_to_other"]]
    if _promote_by_launch(cards, launch, clock):
        cards.sort(key=rank_key)
        open_cards = [c for c in cards if c["status"] == "open"]
        inbox = [c for c in open_cards if not c["routed_to_other"]]
    signoff.attach(cards)  # who a passed-on case or internal message reached, and their answer
    from ask_soony import card_alerts

    card_alerts.attach(cards)  # decided on a phone call: a line on the case's trail
    launch["money"] = launch_view.money(
        cards, read_list("actions.json"), cur, viewer_name(root), exception_approver()
    )
    return {
        "now": cur.isoformat(),
        "viewer": viewer_name(root),
        # The buyer's job title is not in the data; set once per customer (ASK_VIEWER_TITLE).
        "viewer_title": viewer_title(),
        "portal_found": root is not None,
        "map": {
            "nodes": list((net["nodes"] or {}).values()),
            "legs": net["legs"],
            "version": hashlib.sha1(
                json.dumps([net["nodes"], net["legs"]], sort_keys=True, default=str).encode()
            ).hexdigest()[:8],
        },
        "route_level": route_level,
        "badges": dict(badges),
        "shipments": list(ships.values()),
        "tracker": tracked["tracker"],  # list_age_hours, frozen, contradicted, ...
        "strip": strip,
        "clock": build_clock(strip, clock, cur),
        "launch": launch,
        "cards": cards,
        "inbox_ids": [c["id"] for c in inbox],
        "calm": not inbox,
        "events": events[:20],
        "activity": build_activity(cards, events, root),
        "last_drop_at": last_drop.isoformat() if last_drop else None,
        "last_check_at": last_check.isoformat() if last_check else None,
        # Whether this instance polls the portal at all (the demo does not): no heartbeat if not.
        "watching": _watching(),
        "read_since_6": read_n,
        "trust": read_json("trust.json", {}),
    }


# ---------------------------------------------------------------- app


app = FastAPI(title="ask-soony")


@app.get("/api/state")
def get_inbox_state() -> dict:
    return build_state()


def _card(card_id: str) -> dict:
    for c in build_state()["cards"]:
        if c["id"] == card_id:
            return c
    raise HTTPException(404, "unknown card")


def _record(card_id: str, status: str, action: dict) -> dict:
    stamp = now().isoformat()
    with _lock:
        actions = read_list("actions.json")
        action = {"id": uuid.uuid4().hex[:10], "decision_id": card_id, "at": stamp, **action}
        actions.append(action)
        write_json("actions.json", actions)
        sts = read_json("decision_status.json", {})
        sts[card_id] = {
            "status": status,
            "at": stamp,
            "note": action.get("note"),
            "action_id": action["id"],
        }
        write_json("decision_status.json", sts)
    return action


@app.get("/api/cards/{card_id}/recent_sends")
def card_recent_sends(card_id: str) -> dict:
    """What already went to this card's supplier about it lately, from the app or by hand."""
    from ask_soony.outbox import recent_sends

    return {"sends": recent_sends(_card(card_id))}


@app.get("/api/cards/{card_id}/contacts")
def card_contacts(card_id: str) -> dict:
    """Who this card's email may go to (known contacts only) and the default subject."""
    from ask_soony.cardtools import contacts

    return {"contacts": contacts(_card(card_id))}


@app.post("/api/cards/{card_id}/approve")
def approve(card_id: str, body: dict = Body(default_factory=dict)) -> dict:
    card = _card(card_id)
    idx = int(body.get("option", 0))
    if idx >= len(card["options"]):
        raise HTTPException(400, "unknown option")
    opt = card["options"][idx]
    if opt.get("channel") == "wait":  # "wait for the books": nothing to send, off the list
        return {
            "action": dismiss(
                card_id, {"note": opt.get("action"), "via": body.get("via") or "inbox"}
            ),
            "offer_trust": None,
        }
    trust = read_json("trust.json", {})
    # Same send path for the Approve button and a spoken yes (alert agent, cardtools.deliver):
    # an email option to a known supplier address goes out now; anything else is only recorded.
    from ask_soony.cardtools import deliver

    if opt.get("channel") == "email" and not body.get("force"):
        from ask_soony.outbox import recent_sends

        # The mailbox is shared: someone may have sent this by hand already. Ask before sending
        # it twice (the review sheet shows it and offers "Send anyway").
        earlier = recent_sends(card)
        if earlier:
            raise HTTPException(409, {"duplicate": earlier[-1], "count": len(earlier)})
    cc = body.get("cc") or []
    sent = deliver(
        card,
        opt,
        body.get("text"),
        subject=body.get("subject"),
        to=body.get("to"),
        cc=cc if isinstance(cc, list) else [cc],
    )
    status = "sent" if sent else "approved"
    # An internal message (channel "notify") reaches its person through a sign-off link.
    reached = None
    if not sent and opt.get("channel") == "notify":
        from ask_soony import signoff

        asker = body.get("by") or viewer_name(portal_root())
        reached = signoff.notify(card, opt, body.get("text"), asker)
    action = _record(
        card_id,
        status,
        {
            "kind": "approve",
            "option": idx,
            "action": opt["action"],
            "channel": opt.get("channel"),
            "to": (sent or {}).get("to") or opt.get("to"),
            **({"cc": sent["cc"]} if sent and sent.get("cc") else {}),
            "draft": body.get("text") or opt.get("draft"),
            "edited": bool(body.get("text") and body.get("text") != opt.get("draft")),
            "trust": "allow_always"
            if trust.get(opt.get("routine_type") or "") == "allow_always"
            else "allow_once",
            "status": status,
            "action_type": opt.get("routine_type"),
            "via": body.get("via") or "inbox",
            **({"by": body["by"]} if body.get("by") else {}),
            **(
                {"channels": reached["channels"], "signoff_request": reached["request"]}
                if reached
                else {}
            ),
            **({"message_id": sent["message_id"], "subject": sent["subject"]} if sent else {}),
            **(
                {"link": sent["link"], "channels": sent["channels"]}
                if sent and sent.get("link")
                else {}
            ),
        },
    )
    offer = (
        opt.get("routine_type")
        if opt.get("routine_type") and opt["routine_type"] not in trust
        else None
    )
    return {"action": action, "offer_trust": offer}


@app.post("/api/cards/{card_id}/escalate")
def escalate(card_id: str, body: dict = Body(default_factory=dict)) -> dict:
    from ask_soony import signoff

    card = _card(card_id)
    note = (body.get("note") or "").strip()
    viewer = viewer_name(portal_root())
    brief = card["escalation_brief"] + (f" Note from {first_name(viewer)}: {note}" if note else "")
    # The approver gets a link to approve or decline (push, Teams); channels [] = reached nobody.
    reached = signoff.escalation(card, body.get("option"), note, viewer)
    action = _record(
        card_id,
        "escalated",
        {
            "kind": "escalate",
            "to": card.get("approver"),
            "brief": brief,
            "note": note,
            **({"option": body["option"]} if isinstance(body.get("option"), int) else {}),
            "status": "requested",
            "via": body.get("via") or "inbox",
            **(
                {"channels": reached["channels"], "signoff_request": reached["request"]}
                if reached
                else {}
            ),
        },
    )
    if reached:
        signoff.start_simulation(reached["request"])
    return action


@app.post("/api/cards/{card_id}/dismiss")
def dismiss(card_id: str, body: dict = Body(default_factory=dict)) -> dict:
    _card(card_id)
    return _record(
        card_id,
        "dismissed",
        {
            "kind": "dismiss",
            "note": body.get("note"),
            "status": "dismissed",
            "via": body.get("via") or "inbox",
        },
    )


@app.post("/api/trust")
def set_trust(body: dict = Body(...)) -> dict:
    kind, mode = body.get("action_type"), body.get("mode")
    if not kind or mode not in ("allow_always", "ask"):
        raise HTTPException(400, "action_type and mode (allow_always|ask) required")
    with _lock:
        trust = read_json("trust.json", {})
        trust[kind] = mode
        write_json("trust.json", trust)
    return trust


@app.get("/api/file")
def get_file(path: str):
    """Source documents behind the evidence, served from the portal mirror only."""
    root = portal_root()
    if root is None:
        raise HTTPException(404)
    target = (root / path).resolve()
    if not target.is_file() or root.resolve() not in target.parents:
        raise HTTPException(404)
    return FileResponse(target)


# ---------------------------------------------------------------- Jonas: alert agent in one server
# Voice, push, card tools, supplier replies and call webhooks are routers from the alert agent. They
# read verifier-shaped cards through `api.get_state()` and the helpers below, which live in
# globe_api (his original dashboard API); importing it also starts his opt-in background loops
# (ASK_BACKGROUND=on, ASK_REPLY_POLL=on). The inbox's own state stays at /api/state.

from ask_soony import (  # noqa: E402
    anchors,
    calls,
    cardtools,
    datacheck,
    globe_api,
    push,
    replies,
    routines,
    teams,
    voice,
)
from ask_soony.basic_auth import BasicAuth  # noqa: E402
from ask_soony.globe_api import (  # noqa: E402,F401  used by the routers as api.<name>
    ROOT,
    _action_status,
    _add_event,
    _now,
    _read,
    _state_dir,
    _write,
)

get_state = globe_api.get_state
for _router in (
    voice.router,
    push.router,
    cardtools.router,
    replies.router,
    calls.router,
    anchors.router,
    teams.router,
    routines.router,
    datacheck.router,
    floor.router,
    signoff.router,
):
    app.include_router(_router)
_GLOBE_ROUTES = {"/api/feed", "/api/network", "/api/mode", "/api/cards/{card_id}/action"}
app.router.routes.extend(
    r for r in globe_api.app.router.routes if getattr(r, "path", None) in _GLOBE_ROUTES
)
app.add_middleware(BasicAuth)


def mount_frontend() -> None:
    dist = Path(os.environ.get("ASK_WEB_DIST", "web/dist"))
    if not dist.is_dir():
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404)
        f = (dist / full_path).resolve()
        if full_path and f.is_file() and dist.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(dist / "index.html")


mount_frontend()
