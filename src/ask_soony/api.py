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
import json
import math
import os
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
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


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
                "id": p["locode"], "kind": "port", "name": p.get("name"), "country": p.get("country"),
                "lat": lat, "lon": lon, "mode": p.get("mode"),
            }
    for s in read_csv(soony / "master" / "sites.csv"):
        lat, lon = num(s.get("lat")), num(s.get("lon"))
        if lat is not None and lon is not None:
            nodes[s["site_id"]] = {
                "id": s["site_id"], "kind": "dc" if s.get("type") == "dc" else "site",
                "name": s.get("name"), "country": s.get("country"), "lat": lat, "lon": lon,
            }
    sup_dir = root / "suppliers"
    for prof in sorted(sup_dir.glob("*/profile.csv")) if sup_dir.is_dir() else []:
        for r in read_csv(prof):
            lat, lon = num(r.get("lat")), num(r.get("lon"))
            if lat is None or lon is None or not r.get("supplier_id"):
                continue
            nodes[r["supplier_id"]] = {
                "id": r["supplier_id"], "kind": "supplier", "name": r.get("name"),
                "country": r.get("country"), "lat": lat, "lon": lon, "city": r.get("city"),
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
        legs.append({
            "id": r["route_id"], "origin": r["origin"], "destination": r["destination"],
            "mode": mode, "via": via_id or None, "via_known": (not via_id) or via_id in nodes,
            "carrier": r.get("carrier_id"), "transit_days": num(r.get("transit_days")),
            "path": path,
        })
    # supplier -> origin port, from the shipments each supplier has on which route
    ships = read_csv(soony / "erp" / "open_shipments.csv")
    return {"nodes": nodes, "legs": legs, "shipments_raw": ships, "root": root}


def shipment_view(raw: dict, cur: datetime, ref: datetime | None, silent_h: float) -> dict | None:
    etd, eta = parse_ts(raw.get("etd")), parse_ts(raw.get("eta"))
    upd = parse_ts(raw.get("updated_at"))
    if etd and eta and eta > etd:
        t = (cur - etd) / (eta - etd)
    else:
        t = 1.0 if (eta and cur >= eta) else 0.0
    silent = bool(upd and ref and (ref - upd) >= timedelta(hours=silent_h))
    return {
        "id": raw.get("shipment_id"), "route_id": raw.get("route_id"), "mode": raw.get("mode"),
        "supplier_id": raw.get("supplier_id"), "status": raw.get("status"),
        "etd": raw.get("etd"), "eta": raw.get("eta"), "updated_at": raw.get("updated_at"),
        "progress": max(0.0, min(1.0, t)), "departed": t > 0, "silent": silent,
        "silent_hours": round((ref - upd).total_seconds() / 3600) if (silent and upd and ref) else None,
        "note": raw.get("note") or None,
    }


# ---------------------------------------------------------------- cards


def first_name(person: str | None) -> str:
    return (person or "").split()[0] if person else ""


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


def routine_type(decision: dict, option: dict) -> str | None:
    """Action type that may be allowed to run automatically: routine, free, no commitment."""
    kind = str(option.get("type") or decision.get("action_type") or "")
    flag = option.get("routine", decision.get("routine"))
    cost = num(option.get("cost_value", option.get("cost_eur"))) or 0
    if cost > 0:
        return None
    if flag is True or (flag is None and "chase" in kind.lower()):
        return kind or "chaser"
    return None


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
        supplier = f.get("supplier") or next((c.get("supplier") for c in cl if c.get("supplier")), None)
        shipment = f.get("shipment") or next((c.get("shipment") for c in cl if c.get("shipment")), None)
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
            options.append({
                "index": i, "action": o.get("action"), "cost": o.get("cost"), "effect": o.get("effect"),
                "recommended": bool(o.get("recommended", i == 0)), "draft": o.get("draft"),
                "to": o.get("to"), "channel": o.get("channel"),
                "routine_type": routine_type(d, o),
            })
        approver = d.get("approver")
        other = bool(approver and viewer and first_name(approver) != first_name(viewer))
        called = next(
            (a for a in reversed(actions)
             if str(a.get("decision_id")) == fid and a.get("channel") == "call" and a.get("at")),
            None,
        )
        evidence = [
            {"source": s.get("source"), "value": s.get("value"), "doc": s.get("doc"),
             "page": s.get("page"), "quote": s.get("quote"), "translation": s.get("translation")}
            for s in (f.get("sides") or [])
        ]
        for c in cl:
            evidence.append({
                "source": c.get("source_doc"), "value": c.get("value"), "doc": c.get("source_doc"),
                "page": c.get("page"), "quote": c.get("quote"), "language": c.get("language"),
                "translation": c.get("quote_en") or c.get("translation"),
                "unit": c.get("unit"),
            })
        cards.append({
            "id": fid, "kind": f.get("kind"), "part": f.get("part"),
            "headline": d.get("headline") or f.get("title") or f.get("summary") or fid,
            "summary": f.get("summary"),
            "number": short if short is not None else num(imp.get("units_short")),
            "number_label": "copies at risk" if short is not None else "units short",
            "edition": imp.get("edition"), "cell_hours_lost": hours, "slack_hours": slack,
            "deadline": d.get("deadline") or imp.get("deadline"),
            "severity": sev, "level": level, "status": status,
            "approver": approver, "routed_to_other": other,
            "reason": d.get("reason"), "called_at": called.get("at") if called else None,
            "options": options, "evidence": evidence, "trail": d.get("trail") or f.get("trail") or [],
            "supplier_id": supplier, "supplier_name": (nodes.get(supplier) or {}).get("name"),
            "shipment": shipment, "routes": routes,
            "ui": ui, "created_at": f.get("created_at"),
            "escalation_brief": escalation_brief(f, d, imp, options, viewer),
            "trusted_types": [k for k, v in trust.items() if v == "allow_always"],
        })
    cards.sort(key=rank_key)
    return cards


def escalation_brief(f: dict, d: dict, imp: dict, options: list[dict], viewer: str) -> str:
    lead = options[0] if options else {}
    parts = [f"{first_name(viewer) or 'The buyer'} needs your decision: {d.get('headline') or f.get('summary') or ''}."]
    if d.get("deadline") or imp.get("deadline"):
        parts.append(f"Decide by {d.get('deadline') or imp.get('deadline')}.")
    if lead:
        parts.append(f"Recommended: {lead.get('action')} ({lead.get('cost') or 'no cost stated'}). {lead.get('effect') or ''}")
    return " ".join(p.strip() for p in parts if p)


def rank_key(c: dict):
    dl = parse_ts(c.get("deadline"))
    return (-LEVEL_RANK.get(c["level"], 0), dl.timestamp() if dl else math.inf, -(c.get("number") or 0))


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


def build_strip(cards: list[dict], root: Path | None) -> list[dict]:
    launch = {str(e.get("edition")): e for e in (read_json("launch.json", {}).get("editions") or [])}
    labels = edition_labels(root)
    keys: list[str] = list(launch) or []
    for c in cards:
        if c.get("edition") and c["edition"] not in keys:
            keys.append(c["edition"])
    for pid in labels:
        if pid not in keys:
            keys.append(pid)

    def label_of(k: str) -> str:
        if k in launch and launch[k].get("label"):
            return launch[k]["label"]
        for pid, lab in labels.items():
            if k == pid or k.lower() in lab.lower() or lab.lower().startswith(k.lower()):
                return lab
        return k.replace("_", " ").title()

    strip = []
    for k in keys:
        mine = [c for c in cards if c.get("edition") == k and c["status"] == "open"]
        base = launch.get(k, {})
        worst = max((LEVEL_RANK[c["level"]] for c in mine), default=0)
        slack_vals = [c["slack_hours"] for c in mine if c.get("slack_hours") is not None]
        slack = base.get("slack_hours") if base.get("slack_hours") is not None else (min(slack_vals) if slack_vals else None)
        first = sorted(mine, key=rank_key)[0] if mine else None
        strip.append({
            "edition": k, "label": label_of(k),
            "level": {3: "red", 2: "amber", 1: "grey", 0: "blue"}[worst],
            "status": {3: "Stops the line", 2: "At risk", 1: "Unclear", 0: "On track"}[worst],
            "copies_at_risk": sum(c.get("number") or 0 for c in mine),
            "slack_hours": slack,
            "breaks_first": ({"text": first.get("part") or first.get("headline"),
                              "deadline": first.get("deadline"), "card": first["id"]} if first else None),
        })
    return strip


def build_activity(cards: list[dict], events: list[dict], root: Path | None) -> list[dict]:
    items = []
    for e in events:
        items.append({"at": e.get("at"), "text": f"Read {e.get('path') or e.get('file')}", "kind": "read"})
    for c in cards:
        if c["status"] in ("dismissed", "logged"):
            items.append({"at": c.get("created_at"), "kind": "dismissed", "card": c["id"],
                          "text": f"{'No launch risk' if c['status'] == 'logged' else 'Dismissed'}: {c['headline']}",
                          "detail": c.get("reason") or c.get("summary")})
        elif c["status"] != "open":
            ui = c.get("ui") or {}
            items.append({"at": ui.get("at") or c.get("created_at"), "kind": c["status"], "card": c["id"],
                          "text": f"{c['status'].title()}: {c['headline']}", "detail": ui.get("note")})
    items.sort(key=lambda x: (parse_ts(x.get("at")) or datetime.min.replace(tzinfo=tz())), reverse=True)
    return items[:40]


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
    upds = [u for u in (parse_ts(r.get("updated_at")) for r in net["shipments_raw"]) if u]
    ref = max(upds) if upds else None
    ships = {}
    for r in net["shipments_raw"]:
        v = shipment_view(r, cur, ref, silent_h)
        if v and v["id"] and v["status"] not in ("delivered", "received", "cancelled"):
            ships[v["id"]] = v
    cards = build_cards(net, ships)
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
        badges[c["supplier_id"] or next((n["id"] for n in net["nodes"].values() if n["kind"] == "dc"), "")] += 1
    for s in ships.values():
        s["level"] = ship_level.get(s["id"]) or ("grey" if s["silent"] else "blue")

    events = sorted(read_list("events.json"), key=lambda e: str(e.get("at")), reverse=True)
    for e in events:
        e["node"] = event_node(e.get("path") or e.get("file") or "", net)
        e["id"] = e.get("id") or hashlib.sha1(f"{e.get('at')}{e.get('path') or e.get('file')}".encode()).hexdigest()[:10]
    status = read_json("status.json", {})
    last_drop = parse_ts((events[0].get("at") if events else None)) or newest_mtime(root)
    last_check = parse_ts(status.get("last_check_at")) or last_drop
    since = cur.replace(hour=6, minute=0, second=0, microsecond=0)
    if cur < since:
        since -= timedelta(days=1)
    read_n = status.get("files_read_since_06") if isinstance(status.get("files_read_since_06"), int) else sum(
        1 for e in events if (parse_ts(e.get("at")) or since) >= since
    )

    inbox = [c for c in open_cards if not c["routed_to_other"]]
    return {
        "now": cur.isoformat(), "viewer": viewer_name(root),
        "portal_found": root is not None,
        "map": {
            "nodes": list((net["nodes"] or {}).values()), "legs": net["legs"],
            "version": hashlib.sha1(json.dumps([net["nodes"], net["legs"]], sort_keys=True, default=str).encode()).hexdigest()[:8],
        },
        "route_level": route_level, "badges": dict(badges),
        "shipments": list(ships.values()),
        "strip": build_strip(cards, root),
        "cards": cards,
        "inbox_ids": [c["id"] for c in inbox],
        "calm": not inbox,
        "events": events[:20],
        "activity": build_activity(cards, events, root),
        "last_drop_at": last_drop.isoformat() if last_drop else None,
        "last_check_at": last_check.isoformat() if last_check else None,
        "read_since_6": read_n,
        "trust": read_json("trust.json", {}),
    }


# ---------------------------------------------------------------- app


app = FastAPI(title="ask-soony")


@app.get("/api/state")
def get_state() -> dict:
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
        sts[card_id] = {"status": status, "at": stamp, "note": action.get("note"), "action_id": action["id"]}
        write_json("decision_status.json", sts)
    return action


@app.post("/api/cards/{card_id}/approve")
def approve(card_id: str, body: dict = Body(default_factory=dict)) -> dict:
    card = _card(card_id)
    idx = int(body.get("option", 0))
    if idx >= len(card["options"]):
        raise HTTPException(400, "unknown option")
    opt = card["options"][idx]
    trust = read_json("trust.json", {})
    action = _record(card_id, "approved", {
        "kind": "approve", "option": idx, "action": opt["action"], "channel": opt.get("channel"),
        "to": opt.get("to"), "draft": body.get("text") or opt.get("draft"),
        "edited": bool(body.get("text") and body.get("text") != opt.get("draft")),
        "trust": "allow_always" if trust.get(opt.get("routine_type") or "") == "allow_always" else "allow_once",
        "status": "approved", "action_type": opt.get("routine_type"),
    })
    offer = opt.get("routine_type") if opt.get("routine_type") and opt["routine_type"] not in trust else None
    return {"action": action, "offer_trust": offer}


@app.post("/api/cards/{card_id}/escalate")
def escalate(card_id: str, body: dict = Body(default_factory=dict)) -> dict:
    card = _card(card_id)
    note = (body.get("note") or "").strip()
    brief = card["escalation_brief"] + (f" Note from {first_name(get_state()['viewer'])}: {note}" if note else "")
    return _record(card_id, "escalated", {
        "kind": "escalate", "to": card.get("approver"), "brief": brief, "note": note, "status": "requested",
    })


@app.post("/api/cards/{card_id}/dismiss")
def dismiss(card_id: str, body: dict = Body(default_factory=dict)) -> dict:
    _card(card_id)
    return _record(card_id, "dismissed", {"kind": "dismiss", "note": body.get("note"), "status": "dismissed"})


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
