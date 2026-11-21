"""
Supply-network graph for the globe, built at runtime from the portal data folder.

Files are found by their columns, not their paths, so renamed or re-formatted drops keep working:
a table with site_id/lat/lon is a site list, one with locode/lat/lon a port list, and so on.
Missing files or columns are skipped. Nothing about the customer is hardcoded here.
"""

import csv
import json
from datetime import date
from pathlib import Path

ReadRows = list[dict[str, str]]


def _csv(path: Path) -> ReadRows:
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            return [dict(r) for r in csv.DictReader(f)]
    except (OSError, UnicodeDecodeError, csv.Error):
        return []


def _json_rows(path: Path) -> ReadRows:
    """A JSON file holding a list of records, or an object with one such list."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _tables(root: Path) -> list[tuple[Path, ReadRows]]:
    out = []
    for path in sorted(root.rglob("*")):
        if path.suffix == ".csv":
            rows = _csv(path)
        elif path.suffix == ".json":
            rows = _json_rows(path)
        else:
            continue
        if rows:
            out.append((path, rows))
    return out


def _has(rows: ReadRows, *cols: str) -> bool:
    return all(c in rows[0] for c in cols)


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _s(value) -> str:
    return "" if value is None else str(value).strip()


def _node(kind: str, nid: str, name: str, r: dict, **extra) -> dict | None:
    lat, lon = _num(r.get("lat")), _num(r.get("lon"))
    if not nid or lat is None or lon is None:
        return None
    return {"id": nid, "kind": kind, "name": name or nid, "lat": lat, "lon": lon, **extra}


def build_network(root: Path, today: date | None = None) -> dict | None:
    """Return the network dict, or None when the folder is missing or holds nothing usable."""
    if not root.is_dir():
        return None
    today = today or date.today()
    sites: dict[str, dict] = {}
    ports: dict[str, dict] = {}
    suppliers: dict[str, dict] = {}
    routes_raw: ReadRows = []
    ships_raw: ReadRows = []
    products: dict[str, dict] = {}
    bom: ReadRows = []
    components: dict[str, dict] = {}
    supplier_components: list[tuple[Path, ReadRows]] = []

    for path, rows in _tables(root):
        if _has(rows, "site_id", "lat", "lon"):
            for r in rows:
                n = _node("site", _s(r["site_id"]), _s(r.get("name")), r, type=_s(r.get("type")))
                if n:
                    sites[n["id"]] = n
        elif _has(rows, "locode", "lat", "lon"):
            for r in rows:
                n = _node("port", _s(r["locode"]), _s(r.get("name")), r, mode=_s(r.get("mode")))
                if n:
                    ports[n["id"]] = n
        elif _has(rows, "supplier_id", "lat", "lon", "name") and "shipment_id" not in rows[0]:
            for r in rows:
                n = _node(
                    "supplier", _s(r["supplier_id"]), _s(r["name"]), r, country=_s(r.get("country"))
                )
                if n:
                    suppliers[n["id"]] = n
        elif _has(rows, "route_id", "origin", "destination", "transit_days"):
            routes_raw += rows
        elif _has(rows, "shipment_id", "eta"):
            ships_raw += rows
        elif _has(rows, "product_id", "name"):
            for r in rows:
                products[_s(r["product_id"])] = r
        elif _has(rows, "parent_id", "child_id", "qty_per"):
            bom += rows
        elif _has(rows, "component_id", "share"):
            supplier_components.append((path, rows))
        elif _has(rows, "component_id", "description"):
            for r in rows:
                components[_s(r["component_id"])] = r

    nodes = {**ports, **sites}
    if not (sites or ports or suppliers):
        return None

    routes, by_origin = [], {}
    for r in routes_raw:
        origin, dest = _s(r["origin"]), _s(r["destination"])
        via = [v for v in _s(r.get("via")).replace("|", ",").split(",") if v.strip() in nodes]
        if origin not in nodes or dest not in nodes:
            continue
        route = {
            "id": _s(r["route_id"]),
            "origin": origin,
            "destination": dest,
            "mode": _s(r.get("mode")) or "sea",
            "transit_days": _num(r.get("transit_days")),
            "via": [v.strip() for v in via],
            "path": [origin, *[v.strip() for v in via], dest],
        }
        routes.append(route)
        by_origin.setdefault(origin, []).append(route)
    route_by_id = {r["id"]: r for r in routes}

    shipments = []
    for r in ships_raw:
        route = route_by_id.get(_s(r.get("route_id")))
        if route is None:
            origin, dest = _s(r.get("origin")), _s(r.get("destination"))
            if origin not in nodes or dest not in nodes:
                continue
            route = {
                "path": [origin, dest],
                "mode": _s(r.get("mode")),
                "transit_days": None,
                "id": "",
            }
        path = list(route["path"])
        target = _s(r.get("dest_site"))
        seen = {path[0]}
        while target in nodes and path[-1] != target and len(path) < 8:
            nxt = [x for x in by_origin.get(path[-1], []) if x["destination"] not in seen]
            nxt.sort(key=lambda x: x["destination"] != target)
            if not nxt:
                break
            path += nxt[0]["path"][1:]
            seen.add(path[-1])
        etd, eta = _s(r.get("etd") or r.get("departure")), _s(r.get("eta"))
        last = (_num(r.get("last_lat")), _num(r.get("last_lon")))
        shipments.append(
            {
                "id": _s(r["shipment_id"]),
                "supplier": _s(r.get("supplier_id") or r.get("supplier")),
                "mode": _s(r.get("mode")) or route["mode"],
                "route": route["id"],
                "path": path,
                "etd": etd or None,
                "eta": eta or None,
                "transit_days": route["transit_days"],
                "status": _s(r.get("status")),
                "updated_at": _s(r.get("updated_at")) or None,
                "last_position": list(last) if None not in last else None,
                "parts": [m for m in _s(r.get("material_ids")).split("|") if m],
                "po_numbers": [p for p in _s(r.get("po_numbers")).split("|") if p],
                "note": _s(r.get("note")),
            }
        )

    # Parts to suppliers: a components table sits next to its supplier's profile.
    part_suppliers: dict[str, list[dict]] = {}
    for path, rows in supplier_components:
        sid = next(
            (s for s in suppliers if s in path.parent.name or s in path.parent.as_posix()), None
        )
        sid = sid or _s(rows[0].get("supplier_id"))
        for r in rows:
            sup = _s(r.get("supplier_id")) or sid
            if sup in suppliers:
                part_suppliers.setdefault(_s(r["component_id"]), []).append(
                    {"supplier": sup, "share": _num(r.get("share"))}
                )
    for sh in shipments:  # fall back on shipments when no components table names the supplier
        for part in sh["parts"]:
            if sh["supplier"] in suppliers and not any(
                s["supplier"] == sh["supplier"] for s in part_suppliers.get(part, [])
            ):
                part_suppliers.setdefault(part, []).append(
                    {"supplier": sh["supplier"], "share": None}
                )

    used = {n for r in routes for n in r["path"]} | {n for s in shipments for n in s["path"]}
    feeders = {(s["supplier"], s["path"][0]) for s in shipments if s["supplier"] in suppliers}
    for sup, origin in sorted(feeders):
        routes.append(
            {
                "id": f"feeder:{sup}:{origin}",
                "origin": sup,
                "destination": origin,
                "mode": "feeder",
                "transit_days": None,
                "via": [],
                "path": [sup, origin],
            }
        )
    out_nodes = [
        *sites.values(),
        *suppliers.values(),
        *[p for pid, p in ports.items() if pid in used],
    ]

    iso = today.isoformat()
    by_parent: dict[str, ReadRows] = {}
    for r in bom:
        by_parent.setdefault(_s(r["parent_id"]), []).append(r)
    out_products = []
    for pid, rows in by_parent.items():
        live = [
            r for r in rows if _s(r.get("valid_from")) <= iso <= (_s(r.get("valid_to")) or "9999")
        ]
        parts = []
        for r in live or rows:
            comp = components.get(_s(r["child_id"]), {})
            parts.append(
                {
                    "part": _s(r["child_id"]),
                    "qty_per": _num(r["qty_per"]),
                    "description": _s(comp.get("description")),
                    "category": _s(comp.get("category")),
                }
            )
        prod = products.get(pid, {})
        out_products.append(
            {
                "id": pid,
                "name": _s(prod.get("name")) or pid,
                "day_one": _num(prod.get("day_one_qty")),
                "parts": parts,
            }
        )
    return {
        "nodes": out_nodes,
        "routes": routes,
        "shipments": shipments,
        "part_suppliers": part_suppliers,
        "products": out_products,
    }
