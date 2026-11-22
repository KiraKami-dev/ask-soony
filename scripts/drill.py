"""
Drills: break the world on a copy of the portal, run the pipeline, and measure what it catches.

    uv run python scripts/drill.py port_strike      # free: no documents to read
    uv run python scripts/drill.py stock_shock      # free
    uv run python scripts/drill.py document_flood   # ~20 gpt-5.4-mini reads, about $0.25
    uv run python scripts/drill.py all

Each drill copies data/portal into data/store/drills/<name>/ (the real portal and stores are never
touched), runs the pipeline once on the untouched copy (before) and once after the change (after),
and reports: how long each step took, what the verifier newly flags, and how the launch picture
moved (copies at risk, fixes, run-out times). The change is picked from the data, not typed in:
the drill finds the shipments, parts and scans to break at run time.

Report: printed, and saved to data/store/drills/<name>/report.json.
"""

import csv
import io
import json
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import openpyxl

from ask_soony.config import load_dotenv
from ask_soony.dispatch.decisions.cards import build_cards
from ask_soony.verifier.baseline import BASELINE_DIR, reading_metas
from ask_soony.verifier.image_recognizer.live import read_unread
from ask_soony.verifier.impact.shortfall import compute
from ask_soony.verifier.impact.timing import fmt
from ask_soony.verifier.linker import build

PORTAL = Path("data/portal")
DRILLS = Path("data/store/drills")


# --- pipeline and snapshot ------------------------------------------------------------------


def pipeline(root: Path, readings: Path, changed: list[str]) -> dict:
    """The same steps as dispatch.refresh, timed one by one."""
    t0 = time.perf_counter()
    reading = read_unread(changed, root, readings)
    t1 = time.perf_counter()
    graph = build(root, readings)
    result = compute(graph.records, graph.issues)
    cards = build_cards(result, graph.issues, graph.records)
    t2 = time.perf_counter()
    return {
        "seconds": {"read": round(t1 - t0, 1), "verify": round(t2 - t1, 1)},
        "reading": reading.as_dict(),
        "snapshot": snapshot(graph, result, cards),
        "_result": result,
    }


def snapshot(graph, result, cards) -> dict:
    issues = graph.issues
    live = issues[issues["live"] == True]  # noqa: E712 (pandas mask)
    return {
        "editions": {
            e.product: {
                "remaining": e.remaining,
                "at_risk": result.at_risk(e.product)[0],
                "at_risk_if_acted": result.at_risk(e.product, acted=True)[0],
            }
            for e in result.editions
        },
        "parts": {
            p.part: {
                "gap": p.gap,
                "gap_if_acted": p.gap_if_acted,
                "runs_dry": fmt(p.runs_dry) if p.runs_dry else None,
                "fixes": sorted(f"{s['kind']} {s['quantity']:,.0f}" for s in p.supply),
            }
            for p in result.parts
        },
        "cards": {c.id: {"at_risk": c.at_risk, "act_by": c.act_by} for c in cards},
        "live_issues": sorted({(r["rule"], r["record_id"]) for _, r in live.iterrows()}),
    }


def diff(before: dict, after: dict) -> dict:
    b, a = before["snapshot"], after["snapshot"]
    new_issues = [i for i in a["live_issues"] if i not in set(map(tuple, b["live_issues"]))]
    by_rule: dict[str, int] = {}
    for rule, _ in new_issues:
        by_rule[rule] = by_rule.get(rule, 0) + 1
    moved = {}
    for part, now in a["parts"].items():
        was = b["parts"].get(part, {})
        changes = {k: (was.get(k), v) for k, v in now.items() if was.get(k) != v}
        if changes:
            moved[part] = changes
    return {
        "new_live_issues": by_rule,
        "editions": {
            k: {f: (b["editions"].get(k, {}).get(f), v[f]) for f in v}
            for k, v in a["editions"].items()
        },
        "cards_added": sorted(set(a["cards"]) - set(b["cards"])),
        "cards_removed": sorted(set(b["cards"]) - set(a["cards"])),
        "cards_changed": {
            k: (b["cards"][k], v)
            for k, v in a["cards"].items()
            if k in b["cards"] and b["cards"][k] != v
        },
        "parts_moved": moved,
    }


# --- helpers ----------------------------------------------------------------------------------


def fresh_copy(name: str) -> Path:
    base = DRILLS / name
    if base.exists():
        shutil.rmtree(base)
    shutil.copytree(PORTAL, base / "portal")
    return base


def find(root: Path, header_words: set[str], suffix: str) -> list[Path]:
    """Files whose header row has all these column names (found by content, not by name)."""
    out = []
    for path in sorted(root.rglob(f"*{suffix}")):
        try:
            head = path.read_text(encoding="utf-8-sig").splitlines()[0]
        except (OSError, UnicodeDecodeError, IndexError):
            continue
        if header_words <= {c.strip() for c in head.split(",")}:
            out.append(path)
    return out


# --- scenario 1: port strike ------------------------------------------------------------------


def port_strike(root: Path, result) -> tuple[list[str], str]:
    """
    A strike closes the port: every shipment that has reached it, or is on the last leg, is held
    and its ETA moves three days. The system export says so; nobody calls.
    """
    changed, held = [], 0
    for path in find(root, {"shipment_id", "eta", "status", "updated_at"}, ".csv"):
        rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
        stamp = (result.now + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S+01:00")
        for row in rows:
            status = (row["status"] or "").lower()
            if "arrived" in status or "deliver" in status:
                row["status"] = "held_port_strike"
                eta = datetime.fromisoformat(row["eta"][:10]) + timedelta(days=3)
                row["eta"] = eta.strftime("%Y-%m-%d")
                row["updated_at"] = stamp
                row["note"] = "port strike: terminal closed, no pick-up until further notice"
                held += 1
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        path.write_text(out.getvalue(), encoding="utf-8")
        changed.append(path.relative_to(root).as_posix())
    return changed, f"{held} shipments at or near the port held, ETA +3 days"


# --- scenario 2: stock count shock ------------------------------------------------------------


def stock_shock(root: Path, result) -> tuple[list[str], str]:
    """
    The morning count: a new stock workbook. Every row moves (packing went on since the last one),
    and one Collector's part with the most slack is counted 35 % below what the lines will need.
    """
    source = next(
        p
        for p in sorted(root.rglob("*.xlsx"))
        if any(
            "book stock" in str(ws.cell(1, 1).value or "")
            for ws in openpyxl.load_workbook(p, read_only=True)
        )
    )
    later = result.now.replace(hour=10, minute=0) + timedelta(days=1)
    hours = _packing_hours(result, later)
    # Copies packed since the last count: at the edition's rate, but never more than its parts allow
    packed = {}
    for edition in result.editions:
        parts = [p for p in result.parts if edition.product in p.editions]
        allowed = min((p.usable / p.per_copy.get(edition.product, 1) for p in parts), default=0)
        packed[edition.product] = max(
            min(edition.remaining, hours * _edition_rate(result, edition.product), allowed), 0
        )
    used = {
        p.part: sum(packed[e] * p.per_copy.get(e, 1) for e in p.editions if e in packed)
        for p in result.parts
    }
    still_needed = {
        p.part: sum(
            (e.remaining - packed[e.product]) * p.per_copy.get(e.product, 1)
            for e in result.editions
            if e.product in p.editions
        )
        for p in result.parts
    }
    slack = {
        p.part: (p.usable - p.needed) / p.needed
        for p in result.parts
        if p.needed and p.gap == 0 and len(p.editions) == 1
    }
    victim = max(slack, key=slack.get) if slack else None

    wb = openpyxl.load_workbook(source)
    stamp = later.strftime("%Y-%m-%d %H:%M")
    for ws in wb:
        title = str(ws.cell(1, 1).value or "")
        ws.cell(1, 1).value = title.replace(
            title[title.find("20") : title.find("20") + 10], stamp, 1
        )
        header = [c.value for c in ws[3]]
        if "Material" not in header or "Qty" not in header:
            continue
        mat, qty = header.index("Material") + 1, header.index("Qty") + 1
        for row in range(4, ws.max_row + 1):
            part = ws.cell(row, mat).value
            if not part:
                continue
            value = float(str(ws.cell(row, qty).value).replace(".", "").replace(",", "") or 0)
            if ws.title.lower().startswith("finished"):
                ws.cell(row, qty).value = int(value + packed.get(part, 0))
            elif part == victim:
                ws.cell(row, qty).value = int(still_needed[part] * 0.65)
            else:
                ws.cell(row, qty).value = int(max(value - used.get(part, 0), 0))
    target = source.with_name(f"stock_update_{later:%Y-%m-%d}.xlsx")
    wb.save(target)
    return [target.relative_to(root).as_posix()], (
        f"new count at {stamp}: {hours:.1f} packing hours on every row "
        f"({', '.join(f'{k} +{v:,.0f}' for k, v in packed.items())} packed); "
        f"{victim} counted at 65 % of what is still needed"
    )


def _packing_hours(result, later: datetime) -> float:
    """Packing hours between the stock time and `later` (inside the last shift), from the result's
    own clock: hours left then, minus the hours from `later` to the end of packing."""
    return max(result.hours_left - (result.packing_ends - later).total_seconds() / 3600, 0)


def _edition_rate(result, product: str) -> float:
    """Copies per packing hour for an edition, from the rate of its binding part."""
    rates = [
        p.rate_per_hour / p.per_copy.get(product, 1)
        for p in result.parts
        if product in p.editions and p.rate_per_hour and p.per_copy.get(product)
    ]
    return min(rates) if rates else 0


# --- scenario 3: document flood ---------------------------------------------------------------


def document_flood(root: Path, result) -> tuple[list[str], str]:
    """
    Twenty delivery-note scans land at once: nineteen clean ones (copies of earlier notes under
    new numbers, so SAP has no receipt for them) and one with a handwritten correction.
    """
    metas = [json.loads(m.read_text()) for m in reading_metas(None, BASELINE_DIR)]
    scans, corrected = [], None
    for meta in metas:
        path = meta["path"]
        if "/receiving/" not in path or not path.lower().endswith((".jpg", ".png")):
            continue
        verdict = json.loads((BASELINE_DIR / _folder(meta) / "verdict.json").read_text())
        values = [v for line in verdict.get("lines") or [] for v in line.get("values") or []]
        marked = any(v.get("crossed_out") or v.get("written") == "handwritten" for v in values)
        if marked and corrected is None:
            corrected = path
        elif not marked:
            scans.append(path)
    picks = scans[-19:] + ([corrected] if corrected else [])
    existing = {p.name for p in (root / Path(picks[0]).parent).iterdir()}
    changed = []
    day = (result.now + timedelta(days=1)).strftime("%Y%m%d")
    for n, path in enumerate(picks, start=901):
        prefix = Path(path).name.split("_", 1)[1].split("-", 1)[0]  # WZ / SN / DN
        name = f"{day}_{prefix}-{day[2:]}-{n}{Path(path).suffix}"
        assert name not in existing
        target = root / Path(path).parent / name
        shutil.copyfile(root / path, target)
        changed.append(target.relative_to(root).as_posix())
    return (
        changed,
        f"{len(changed)} new delivery-note scans "
        f"({'1' if corrected else '0'} with a handwritten correction)",
    )


def _folder(meta: dict) -> str:
    return f"{meta['path'].replace('/', '__')}__{meta['sha256'][:12]}"


# --- run ----------------------------------------------------------------------------------------

SCENARIOS = {
    "port_strike": port_strike,
    "stock_shock": stock_shock,
    "document_flood": document_flood,
}


def run(name: str) -> dict:
    base = fresh_copy(name)
    root, readings = base / "portal", base / "readings"
    before = pipeline(root, readings, [])
    t = time.perf_counter()
    changed, what = SCENARIOS[name](root, before["_result"])
    change_s = round(time.perf_counter() - t, 1)
    after = pipeline(root, readings, changed)
    report = {
        "scenario": name,
        "what_changed": what,
        "changed_files": changed,
        "seconds": {
            "change": change_s,
            **after["seconds"],
            "total": round(after["seconds"]["read"] + after["seconds"]["verify"], 1),
        },
        "reading": {k: len(v) if isinstance(v, list) else v for k, v in after["reading"].items()},
        "diff": diff(before, after),
    }
    (base / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    show(report)
    return report


def show(r: dict) -> None:
    d = r["diff"]
    print(f"\n=== {r['scenario']}: {r['what_changed']}")
    s = r["seconds"]
    print(
        f"time: read {s['read']} s + verify {s['verify']} s = {s['total']} s   "
        f"(reading: {r['reading']})"
    )
    print("new live issues:", d["new_live_issues"] or "none")
    for ed, v in d["editions"].items():
        print(
            f"  {ed}: at risk {v['at_risk'][0]:,.0f} -> {v['at_risk'][1]:,.0f}   "
            f"if acted {v['at_risk_if_acted'][0]:,.0f} -> {v['at_risk_if_acted'][1]:,.0f}"
        )
    for part, ch in d["parts_moved"].items():
        print(f"  {part}: " + "; ".join(f"{k} {a} -> {b}" for k, (a, b) in ch.items()))
    if d["cards_added"] or d["cards_removed"]:
        print(f"  cards added {d['cards_added']} removed {d['cards_removed']}")
    for card, (a, b) in d["cards_changed"].items():
        print(f"  card {card}: {a} -> {b}")


if __name__ == "__main__":
    load_dotenv()
    names = list(SCENARIOS) if sys.argv[1:] in ([], ["all"]) else sys.argv[1:]
    for scenario in names:
        run(scenario)
