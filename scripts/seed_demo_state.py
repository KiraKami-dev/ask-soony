"""Dev only: write PLACEHOLDER state into data/state/ so the UI can be clicked through.

Reads the real portal files at runtime and derives every number and name from them. The real
modules (verification, forecast, criticality agent) overwrite the same files with the same shapes;
the UI does not change. Output goes to data/state/, which is gitignored. Run:

    uv run python scripts/seed_demo_state.py            # open decisions
    uv run python scripts/seed_demo_state.py --calm     # nothing needs a human
    uv run python scripts/seed_demo_state.py --drop     # rehearse a live drop (needs the server)

--drop starts from the open state minus the late-shipment finding, then appends a portal-drop
event for the newest file of that shipment's supplier, waits, and adds the finding: the UI should
pulse the supplier, toast, recolour the route and slide the card in.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ask_soony import launch as packing  # noqa: E402
from ask_soony.api import (  # noqa: E402
    num,
    parse_ts,
    portal_root,
    read_csv,
    state_dir,
    tz,
    viewer_name,
)

# Placeholder: the real approver comes from the org chart and PP-07 (criticality agent).
PLACEHOLDER_APPROVER = os.environ.get("ASK_SEED_APPROVER", "Head of Procurement")


def money(v: float, cur: str) -> str:
    return f"{cur} {v:,.0f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calm", action="store_true")
    ap.add_argument("--drop", action="store_true", help="rehearse a live drop")
    ap.add_argument("--delay", type=float, default=4.0, help="seconds between drop and card")
    args = ap.parse_args()
    root = portal_root()
    if root is None:
        sys.exit("No portal data found under data/ (set ASK_PORTAL_DIR).")
    soony = root / "soony"
    now = parse_ts(os.environ["ASK_NOW"]) if os.environ.get("ASK_NOW") else datetime.now(tz())
    clock = packing.load(root, tz())

    comps = {r["component_id"]: r for r in read_csv(soony / "master" / "components.csv")}
    products = {r["product_id"]: r for r in read_csv(soony / "master" / "products.csv")}
    launch = [p for p in products.values() if p.get("lifecycle") == "launch"]
    bom = read_csv(soony / "master" / "bom.csv")
    suppliers = {}
    for f in root.glob("suppliers/*/profile.csv"):
        for r in read_csv(f):
            suppliers[r["supplier_id"]] = r
    pos = read_csv(soony / "erp" / "purchase_orders_2025H2-2026.csv")
    grs = read_csv(soony / "erp" / "goods_receipts.csv") + read_csv(
        soony / "erp" / "goods_receipts_update.csv"
    )
    ships = read_csv(soony / "erp" / "open_shipments.csv")

    def edition_of(material: str) -> tuple[str | None, float]:
        """Launch product that uses this material, preferring the one with most parts."""
        used = [b for b in bom if b["child_id"] == material and b["parent_id"] in {
            p["product_id"] for p in launch}]
        if not used:
            return None, 1.0
        parts = {p["product_id"]: sum(1 for b in bom if b["parent_id"] == p["product_id"]) for p in launch}
        best = max(used, key=lambda b: parts[b["parent_id"]])
        return best["parent_id"], num(best["qty_per"]) or 1.0

    def rate(product: str) -> float:
        ed = clock.editions.get(product)
        return (ed.rate_per_hour if ed and ed.rate_per_hour else 0) or 1.0

    findings, impacts, decisions, claims, events = [], [], [], [], []
    fixed = args.calm

    # 1. Largest over-delivery on a goods receipt: received far above ordered.
    over = [g for g in grs if (num(g["qty_ordered"]) or 0) > 0
            and (num(g["qty_received"]) or 0) / num(g["qty_ordered"]) >= 3]
    over.sort(key=lambda g: num(g["qty_received"]) / num(g["qty_ordered"]), reverse=True)
    if over and not fixed:
        g = over[0]
        mat, sup = g["material_id"], g["supplier_id"]
        ordered, got = num(g["qty_ordered"]), num(g["qty_received"])
        prod, per = edition_of(mat)
        phantom = got - ordered
        stock = read_csv(soony / "erp" / "stock_snapshot.csv")
        latest = max((r["snapshot_date"] for r in stock), default="")
        on_hand = sum(num(r["quantity"]) or 0 for r in stock
                      if r["material_id"] == mat and r["snapshot_date"] == latest)
        ed = clock.editions.get(prod)
        need = ed.remaining if ed and ed.remaining is not None else 0
        # A newer stock workbook (quantities as text, '77.800') beats the CSV snapshot.
        for book in sorted((soony / "erp").glob("stock_update*.xlsx")):
            for rows in packing._rows(book).values():
                for r in rows:
                    if mat in r:
                        q = [packing._num(c) for c in r[r.index(mat) + 1:] if packing._num(c) is not None]
                        on_hand = q[0] if q else on_hand
        copies = int(min(need, max(0, need * per - max(0, on_hand - phantom)) / per))
        r = rate(prod) if prod else 1
        hours = round(copies / r, 1)
        desc = comps.get(mat, {}).get("description", mat)
        po = next((p for p in pos if p["po_number"] == g["po_number"]), {})
        price, cur = num(po.get("unit_price")) or 0, po.get("currency", "EUR")
        alt = next((s for s in suppliers.values() if s["supplier_id"] != sup
                    and s["category"] == suppliers.get(sup, {}).get("category")), None)
        slack = clock.slack_hours(now, prod)
        stops = clock.line_stops(now, prod, copies)
        # Decide-by placeholder: a few hours before the line stops (real lead times: Lucas/Farhan).
        deadline = ((stops - timedelta(hours=3)) if stops else now + timedelta(hours=3)).isoformat()
        findings.append({
            "id": "F-1", "kind": "mismatch", "claims": ["C-1", "C-2"], "part": desc, "supplier": sup,
            "summary": f"Goods receipt {g['gr_number']} books {got:,.0f} {desc}; the PO is for {ordered:,.0f}",
            "created_at": (now - timedelta(minutes=7)).isoformat(), "status": "open",
            "sides": [{"source": f"SAP goods receipt {g['gr_number']}", "value": f"{got:,.0f} received"},
                      {"source": f"Purchase order {g['po_number']}", "value": f"{ordered:,.0f} ordered"}],
        })
        claims += [
            {"id": "C-1", "kind": "quantity", "part": mat, "po": g["po_number"], "supplier": sup,
             "value": got, "unit": "PC", "source_doc": f"soony/erp/goods_receipts.csv", "quote": g.get("remark") or None},
            {"id": "C-2", "kind": "quantity", "part": mat, "po": g["po_number"], "supplier": sup,
             "value": ordered, "unit": "PC", "source_doc": f"soony/erp/purchase_orders_2025H2-2026.csv",
             "quote": None, "language": "en"},
        ]
        impacts.append({"finding_id": "F-1", "part": mat, "units_short": phantom, "copies_at_risk": copies,
                        "edition": prod, "cell_hours_lost": hours, "deadline": deadline,
                        "slack_hours": slack, "runs_dry": stops.isoformat() if stops else None})
        decisions.append({
            "finding_id": "F-1", "severity": "call", "deadline": deadline,
            "headline": f"{copies:,} copies short: {desc} was booked {phantom:,.0f} too high",
            "reason": (f"Can stop the line ({hours} h lost, {slack} h slack)"
                       if slack is not None and hours > slack else "Above your limit"),
            "approver": PLACEHOLDER_APPROVER,
            "trail": [{"agent": "Verification", "at": (now - timedelta(minutes=7)).isoformat(),
                       "text": f"Matched goods receipt {g['gr_number']} against PO {g['po_number']}."},
                      {"agent": "Criticality", "at": (now - timedelta(minutes=6)).isoformat(),
                       "text": f"Short by {phantom:,.0f} units, {copies:,} copies, {hours} cell hours."}],
            "options": [
                {"action": f"Rush order {phantom:,.0f} to {(alt or {}).get('name', 'an alternative supplier')}",
                 "cost": money(phantom * price * 1.15, cur), "cost_value": phantom * price * 1.15,
                 "effect": "Arrives before the cell runs dry", "recommended": True,
                 "channel": "email", "to": (alt or {}).get("contact_email"),
                 "draft": f"Hello, we urgently need {phantom:,.0f} x {desc}. Can you deliver to Hamburg within 24 h?"},
                {"action": "Reclaim via Finance and pack what is on the shelf", "cost": "No extra cost",
                 "effect": f"{copies:,} copies not packed", "channel": "portal"},
            ],
        })

    # 2. A shipment the feed still shows as booked although its departure date has passed.
    late = [s for s in ships if s["status"] == "booked" and parse_ts(s["etd"]) and parse_ts(s["etd"]) < now]
    if late and not fixed:
        s = max(late, key=lambda x: parse_ts(x["eta"]))
        sup = suppliers.get(s["supplier_id"], {})
        mats = (s.get("material_ids") or "").split("|")
        desc = comps.get(mats[0], {}).get("description", mats[0])
        prod, per = edition_of(mats[0])
        findings.append({
            "id": "F-2", "kind": "forecast_late", "claims": [], "part": desc, "supplier": s["supplier_id"],
            "shipment": s["shipment_id"], "created_at": (now - timedelta(minutes=2)).isoformat(),
            "summary": f"{s['shipment_id']} left {s['etd']} but the feed still says booked",
            "status": "open",
            "sides": [{"source": "Shipment feed", "value": f"status: {s['status']}"},
                      {"source": "Planned departure", "value": f"{s['etd']} (ETD passed)"}],
        })
        impacts.append({"finding_id": "F-2", "part": mats[0], "copies_at_risk": 0, "edition": prod,
                        "deadline": (now + timedelta(hours=8)).isoformat()})
        decisions.append({
            "finding_id": "F-2", "severity": "email", "deadline": (now + timedelta(hours=8)).isoformat(),
            "headline": f"{s['shipment_id']} may not be moving: ask {sup.get('name', 'the supplier')} for a confirmed ETA",
            "reason": "Routine chaser, no cost", "approver": viewer_name(root), "routine": True,
            "options": [
                {"action": f"Send ETA chaser to {sup.get('name')}", "cost": "No cost", "type": "eta_chaser",
                 "effect": "Confirmed ETA before the next packing shift", "channel": "email",
                 "to": sup.get("contact_email"), "recommended": True,
                 "draft": f"Hello {sup.get('contact_name', '')}, please confirm the departure and ETA of {s['shipment_id']} (planned {s['etd']}). Thank you."},
                {"action": "Wait for the next carrier update", "cost": "No cost", "effect": "No new information"},
            ],
        })

    # 3. The longest-silent shipment still on the road, from the shipment feed alone.
    upd = [(parse_ts(s["updated_at"]), s) for s in ships if s["status"] in ("in_delivery", "in_transit")]
    if upd and not fixed:
        _, s = min(upd, key=lambda x: x[0])
        findings.append({
            "id": "F-3", "kind": "stale", "claims": [], "supplier": s["supplier_id"], "shipment": s["shipment_id"],
            "summary": f"{s['shipment_id']} has not updated since {s['updated_at']}", "status": "open",
            "created_at": (now - timedelta(minutes=20)).isoformat(),
            "sides": [{"source": "Shipment feed", "value": f"{s['status']} · updated {s['updated_at']}"},
                      {"source": "Planned arrival", "value": f"ETA {s['eta']}"}]})
        impacts.append({"finding_id": "F-3", "copies_at_risk": 0, "deadline": (now + timedelta(hours=12)).isoformat()})
        decisions.append({
            "finding_id": "F-3", "severity": "email", "deadline": (now + timedelta(hours=12)).isoformat(),
            "headline": f"Nobody knows where {s['shipment_id']} is",
            "reason": "No news is not good news", "approver": viewer_name(root), "routine": True,
            "options": [{"action": "Ask the carrier for a position", "cost": "No cost", "type": "eta_chaser",
                         "effect": "A real position and ETA", "channel": "email", "recommended": True}]})

    # 4. A news item that touches a supplier region: agents read it and found no launch risk.
    for f in sorted((soony / "news").glob("*.json")) if (soony / "news").is_dir() else []:
        items = json.loads(f.read_text(encoding="utf-8")).get("items", [])
        if items and not fixed:
            n = items[0]
            findings.append({
                "id": "F-4", "kind": "risk_news", "claims": [], "status": "dismissed",
                "summary": n["headline"], "created_at": (now - timedelta(hours=1)).isoformat()})
            decisions.append({"finding_id": "F-4", "severity": "log",
                              "reason": "Checked against open orders and stock: no delivery affected before the launch."})
            break

    # Recent portal files as "drops".
    files = sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[:6]:
        events.append({"at": datetime.fromtimestamp(p.stat().st_mtime, tz()).isoformat(),
                       "path": str(p.relative_to(root)), "kind": "file"})

    out = state_dir()
    out.mkdir(parents=True, exist_ok=True)

    def write(fs, ims, ds, evs) -> None:
        for name, data in [("findings", fs), ("impacts", ims), ("decisions", ds),
                           ("claims", claims), ("events", evs)]:
            tmp = out / f".{name}.json.tmp"
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(out / f"{name}.json")

    for stale in ("actions.json", "decision_status.json", "trust.json"):
        (out / stale).unlink(missing_ok=True)
    held = next((f for f in findings if f["kind"] == "forecast_late"), None)
    if not (args.drop and held):
        write(findings, impacts, decisions, events)
        print(f"seeded {len(findings)} findings, {len(events)} events into {out}")
        return
    keep = [f for f in findings if f is not held]
    write(keep, [i for i in impacts if i["finding_id"] != held["id"]],
          [d for d in decisions if d["finding_id"] != held["id"]], events)
    print(f"seeded {len(keep)} findings; dropping a file in {args.delay:.0f} s", flush=True)
    time.sleep(args.delay)
    sup_files = sorted((p for p in root.glob(f"suppliers/{held['supplier']}_*/**/*") if p.is_file()),
                       key=lambda p: p.stat().st_mtime)
    target = sup_files[-1] if sup_files else files[0]
    drop = {"at": datetime.now(tz()).isoformat(), "path": str(target.relative_to(root)), "kind": "file"}
    write(keep, [i for i in impacts if i["finding_id"] != held["id"]],
          [d for d in decisions if d["finding_id"] != held["id"]], [drop, *events])
    print(f"dropped {drop['path']}; agent card in {args.delay:.0f} s", flush=True)
    time.sleep(args.delay)
    held["created_at"] = datetime.now(tz()).isoformat()
    write(findings, impacts, decisions, [drop, *events])
    print(f"added {held['id']}: {held['summary']}")


if __name__ == "__main__":
    main()
