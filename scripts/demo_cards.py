"""
Demo cards from the real portal data: writes findings, decisions and impacts into data/state.

Every quantity, date, price and address on a card is read from the portal files at run time
(SAP exports, stock snapshot, pack-out log, supplier profiles). Only the document references live
in this script: which goods receipt or PO a case is about, which file and page shows the other
side, and the verbatim text that proves it. A case whose files or rows are missing, or whose
document no longer says what the script expects, is skipped with a warning.

Entries are merged by id (other entries stay), so running it twice changes nothing but the
timestamps. Nothing is approved and no email is sent.

    uv run python scripts/demo_cards.py            # add or refresh the demo cards
    uv run python scripts/demo_cards.py --reset    # also clear decisions, actions, replies,
                                                   # Teams posts and drafts; switch routines off

--reset first backs up every file it changes as <name>.before-demo-<timestamp>.json.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ask_soony import api  # noqa: E402

# ---------------------------------------------------------------- document references (data)

ART_BOOKS = {
    "gr": "5000413065",
    "scan": "soony/receiving/DC-HAM/20261118_WZ-261116-473.jpg",
    "scan_quote": "Nr: WZ-261116-473 · Zamówienie: 4500318768 · CMP-GAM-008 · Ilość 80.000 "
    "(struck through), 8 000 (handwritten) · Waga brutto: 360 kg · Opak.: 40",
    "scan_translation": "Delivery note: the printed 80,000 is crossed out and corrected by hand "
    "to 8,000 (40 packages, 360 kg).",
    "offer": "suppliers/SUP-0028_Karton_Nowak/notices/Oferta_tryb_ekspresowy_2026-11-21.pdf",
    "offer_checks": ["8,000 pcs", "overrun from", "loaded"],
    "offer_quote": "CMP-GAM-008 Art Book Grand Heist 6 (96 p, hardcover) · 8 000 szt. / 8,000 pcs "
    "· overrun from production, in our finished-goods store · loaded within 4 h of your order",
    "offer_qty": 8000,
    "supplier_dir": "suppliers/SUP-0028_Karton_Nowak",
    "decide_by": time(10, 0),
    "lead_h": 1,
}

SLEEVES = {
    "ecn": "ECN-2026-118",
    "pos": ["4500318752", "4500318765", "4500318774", "4500318786"],
    "supplier_dir": "suppliers/SUP-0029_Guangzhou_PackRight",
    "supplier_part": "PR-SL-0604",
    "old_quote": "Artwork PR-SL-0604: rev. B (GH6_sleeve_revB.pdf)",
    # Karton Nowak already prints to the released file; its confirmations name it.
    "new_file_doc": "suppliers/SUP-0028_Karton_Nowak/order_confirmations/OA26293887_4500318775.pdf",
    "new_file": "GH6_sleeve_revC_USK2026.pdf",
    "decide_by": time(12, 0),
    "lead_h": 3,
}

STATUES = {
    "gr": "5000413063",
    "report": "soony/receiving/DC-HAM/inspection_report_5000413063.pdf",
    "report_checks": ["Lot does not meet AQL", "unrestricted", "concession"],
    "report_quote": "Goods receipt 5000413063 · 5,600 · 9 of 125 statues with paint defects. "
    "Lot does not meet AQL. Booked to stock as 'unrestricted' by receiving because the supplier "
    "is listed as active.",
    "decide_by": time(10, 0),
    "lead_h": 1,
}

RESET_FILES = {
    "decision_status.json": {},
    "actions.json": [],
    "replies_seen.json": [],
    "teams_posted.json": [],
    "routine_drafts.json": {},
}


# ---------------------------------------------------------------- portal helpers


def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


def n(v) -> int:
    return int(api.num(v) or 0)


def fmt(v: float) -> str:
    return f"{v:,.0f}"


def fmt_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%a %-d %b")
    except ValueError:
        return value


def csvs(root: Path, folder: str, pattern: str) -> list[dict]:
    rows: list[dict] = []
    for f in sorted((root / folder).glob(pattern)):
        rows += [{**r, "_file": str(f.relative_to(root))} for r in api.read_csv(f)]
    return rows


def pdf_text(path: Path) -> str:
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(path))
        text = " ".join(page.get_textpage().get_text_range() for page in doc)
    except Exception:  # unreadable or not a PDF
        return ""
    return re.sub(r"\s+", " ", text)


def says(root: Path, rel: str, checks: list[str]) -> bool:
    """The document exists and (for a PDF with a text layer) contains every check string."""
    path = root / rel
    if not path.is_file():
        warn(f"{rel} is missing")
        return False
    if path.suffix.lower() != ".pdf":
        return True
    text = pdf_text(path)
    missing = [c for c in checks if re.sub(r"\s+", " ", c) not in text]
    if missing:
        warn(f"{rel} no longer says {missing}")
    return not missing


def contact(root: Path, supplier_dir: str) -> dict:
    rows = api.read_csv(root / supplier_dir / "profile.csv")
    return rows[0] if rows else {}


def stock(root: Path) -> tuple[list[dict], str]:
    rows = csvs(root, "soony/erp", "stock_snapshot*.csv")
    return rows, max((r.get("snapshot_date") or "" for r in rows), default="")


def to_pack(root: Path, part: str, snapshot: str) -> tuple[str, int]:
    """Edition that needs this part and the copies still to pack as of the stock snapshot."""
    bom = [r for r in csvs(root, "soony/master", "bom.csv") if r.get("child_id") == part]
    launch = {
        r["product_id"]: r
        for r in csvs(root, "soony/master", "products.csv")
        if r.get("lifecycle") == "launch"
    }
    for b in bom:
        p = launch.get(b.get("parent_id") or "")
        if not p:
            continue
        packed = sum(
            n(r.get("units_packed"))
            for r in csvs(root, "soony/erp", "packout_log*.csv")
            if r.get("product_id") == p["product_id"] and (r.get("date") or "") <= snapshot
        )
        return p["product_id"], (n(p.get("day_one_qty")) - packed) * n(b.get("qty_per") or 1)
    return "", 0


def launch_day(root: Path) -> datetime:
    dates = [
        r["launch_date"]
        for r in csvs(root, "soony/master", "products.csv")
        if r.get("lifecycle") == "launch" and r.get("launch_date")
    ]
    day = datetime.fromisoformat(min(dates)) if dates else api.now()
    return day.replace(tzinfo=api.tz())


def deadline(root: Path, at: time, lead_h: float, cur: datetime) -> str:
    """The case's decide-by time on launch day, but never less than lead_h from now."""
    fixed = datetime.combine(launch_day(root).date(), at, tzinfo=api.tz())
    soon = cur + timedelta(hours=lead_h)
    soon = soon.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return max(fixed, soon).isoformat()


def trail(cur: datetime, steps: list[tuple[str, str]]) -> list[dict]:
    start = cur - timedelta(minutes=2 * len(steps))
    return [
        {
            "agent": a,
            "at": (start + timedelta(minutes=2 * i)).isoformat(timespec="seconds"),
            "text": t,
        }
        for i, (a, t) in enumerate(steps)
    ]


def po_line(po: dict) -> str:
    return (
        f"PO {po['po_number']} line {po['po_line']} · {po['material_id']} · {po['quantity']} pcs "
        f"· promised {po['promised_date']} · {po['status']}"
    )


# ---------------------------------------------------------------- cases


Card = tuple[dict, dict, dict]


def art_books(root: Path, cur: datetime) -> list[Card]:
    c = ART_BOOKS
    gr = next(
        (
            r
            for r in csvs(root, "soony/erp", "goods_receipts*.csv")
            if r.get("gr_number") == c["gr"]
        ),
        None,
    )
    if gr is None:
        warn(f"goods receipt {c['gr']} not found")
        return []
    if not (says(root, c["scan"], []) and says(root, c["offer"], c["offer_checks"])):
        return []
    part, po_no, ordered, booked = (
        gr["material_id"],
        gr["po_number"],
        n(gr["qty_ordered"]),
        n(gr["qty_received"]),
    )
    rows, snapshot = stock(root)
    system = sum(n(r.get("quantity")) for r in rows if r.get("material_id") == part)
    real = system - booked + ordered
    edition, need = to_pack(root, part, snapshot)
    short = max(0, need - real)
    po = next(
        (
            r
            for r in csvs(root, "soony/erp", "purchase_orders*.csv")
            if r.get("po_number") == po_no and r.get("po_line") == gr.get("po_line")
        ),
        {},
    )
    price, currency = api.num(po.get("unit_price")) or 0, po.get("currency") or "EUR"
    who = contact(root, c["supplier_dir"])
    offer_name = Path(c["offer"]).name
    fid = f"gr-{c['gr']}-{part}"
    finding = {
        "id": fid,
        "kind": "mismatch",
        "part": part,
        "supplier": gr.get("supplier_id"),
        "title": f"SAP counts {fmt(system)} art books; the shelf holds {fmt(real)}",
        "summary": (
            f"Goods receipt {c['gr']} booked {fmt(booked)} for PO {po_no} line {gr['po_line']} "
            f"(ordered {fmt(ordered)}, remark '{gr.get('remark')}'). The delivery note "
            f"{gr.get('delivery_note')} was corrected by hand to {fmt(ordered)}."
        ),
        "status": "open",
        "level": "red",
        "created_at": cur.isoformat(timespec="seconds"),
        "sides": [
            {
                "source": "System says · SAP goods receipt",
                "value": f"{fmt(booked)} received on GR {c['gr']} (ordered {fmt(ordered)})",
                "doc": gr["_file"],
                "quote": (
                    f"GR {c['gr']} · PO {po_no} line {gr['po_line']} · {part} · ordered {ordered}"
                    f" · received {booked} · {gr.get('remark')}"
                ),
            },
            {
                "source": "System says · SAP stock",
                "value": f"{fmt(system)} art books in stock on {snapshot}",
                "doc": next((r["_file"] for r in rows if r.get("material_id") == part), None),
                "quote": f"{part} · batch {c['gr']} · {booked} PC",
            },
            {
                "source": f"Paper says · delivery note {gr.get('delivery_note')} (PL)",
                "value": f"{fmt(ordered)} delivered, not {fmt(booked)}",
                "doc": c["scan"],
                "page": 1,
                "language": "pl",
                "quote": c["scan_quote"],
                "translation": c["scan_translation"],
            },
            {
                "source": "Supplier offer · Karton Nowak rush service (PL/EN)",
                "value": f"{fmt(c['offer_qty'])} art books in stock, loaded within 4 h",
                "doc": c["offer"],
                "page": 1,
                "language": "pl",
                "quote": c["offer_quote"],
            },
        ],
        "trail": trail(
            cur,
            [
                ("Watcher", f"Read the delivery note scan {Path(c['scan']).name} from DC Hamburg."),
                (
                    "Verification",
                    f"Matched GR {c['gr']} in SAP: {fmt(booked)} booked against "
                    f"{fmt(ordered)} ordered; the scan shows {fmt(ordered)}.",
                ),
                (
                    "Impact",
                    f"{fmt(real)} art books for {fmt(need)} Collector's copies still to pack: "
                    f"{fmt(short)} short.",
                ),
            ],
        ),
    }
    decision = {
        "finding_id": fid,
        "level": "red",
        "headline": f"We have {fmt(real)} art books, not {fmt(system)}. The Collector's cell runs "
        f"out about {fmt(short)} copies early.",
        "reason": "Karton Nowak has art books in stock and can load a truck within hours; the "
        "goods receipt should be corrected too, so SAP stops hiding the gap.",
        "deadline": deadline(root, c["decide_by"], c["lead_h"], cur),
        "options": [
            {
                "action": f"Order Karton Nowak's {fmt(c['offer_qty'])} art books from stock "
                "(truck tonight)",
                "cost": f"about {currency} {fmt(c['offer_qty'] * price)}",
                "effect": f"covers the {fmt(short)} gap; Poznań to Hamburg is about 9 h by truck",
                "channel": "email",
                "to": who.get("contact_email"),
                "recommended": True,
                "draft": (
                    f"Dear {who.get('contact_name') or 'Karton Nowak team'},\n\n"
                    f"thank you for your rush offer ({offer_name}). We would like to order the "
                    f"{fmt(c['offer_qty'])} Art Books Grand Heist 6 ({part}) from your "
                    "finished-goods store at the Annex A price, delivered to DC Hamburg.\n\n"
                    "Please load them on the next truck and send us the truck details and the "
                    "delivery note number.\n\n"
                    "Best regards,\nSoony Procurement"
                ),
            },
            {
                "action": f"Ask receiving to correct the goods receipt to {fmt(ordered)}",
                "cost": "No cost",
                "effect": "SAP shows the real stock again",
                "channel": "notify",
                "to": "DC receiving",
                "draft": (
                    f"Please correct goods receipt {c['gr']} (PO {po_no} line {gr['po_line']}) "
                    f"from {fmt(booked)} to {fmt(ordered)}: delivery note "
                    f"{gr.get('delivery_note')} was corrected by hand to {fmt(ordered)}."
                ),
            },
        ],
    }
    impact = {
        "finding_id": fid,
        "copies_at_risk": short,
        "edition": edition,
        "deadline": decision["deadline"],
    }
    return [(finding, decision, impact)]


def sleeves(root: Path, cur: datetime) -> list[Card]:
    c = SLEEVES
    ecn = next(
        (r for r in csvs(root, "soony/master", "bom_delta*.csv") if r.get("change_id") == c["ecn"]),
        None,
    )
    if ecn is None:
        warn(f"{c['ecn']} not found")
        return []
    if not says(root, c["new_file_doc"], [c["new_file"]]):
        return []
    part, old, new = ecn["child_id"], ecn.get("rev_old"), ecn.get("rev_new")
    who = contact(root, c["supplier_dir"])
    pos = csvs(root, "soony/erp", "purchase_orders*.csv")
    out: list[Card] = []
    for po_no in c["pos"]:
        lines = [
            r
            for r in pos
            if r.get("po_number") == po_no
            and r.get("material_id") == part
            and r.get("status") == "open"
        ]
        oa = sorted((root / c["supplier_dir"] / "order_confirmations").glob(f"OA*_{po_no}.pdf"))
        if not lines or not oa:
            warn(f"PO {po_no}: no open {part} line or no order confirmation")
            continue
        oa_rel = str(oa[0].relative_to(root))
        if not says(root, oa_rel, [c["old_quote"]]):
            continue
        qty = sum(n(r["quantity"]) for r in lines)
        due = " / ".join(fmt_date(r["promised_date"]) for r in lines)
        oa_no = oa[0].name.split("_")[0]
        fid = f"rev-{po_no}-{part}"
        finding = {
            "id": fid,
            "kind": "revision",
            "part": part,
            "supplier": lines[0].get("supplier_id"),
            "title": f"PackRight confirmed PO {po_no} with the old sleeve artwork (rev {old})",
            "summary": (
                f"{c['ecn']} moved {part} from rev {old} to rev {new} ({ecn.get('reason')}). "
                f"PackRight's confirmation {oa_no} still prints {fmt(qty)} sleeves to rev {old}."
            ),
            "status": "open",
            "level": "amber",
            "created_at": cur.isoformat(timespec="seconds"),
            "sides": [
                {
                    "source": "System says · SAP purchase order",
                    "value": f"{fmt(qty)} sleeves due {due}, open",
                    "doc": lines[0]["_file"],
                    "quote": " ; ".join(po_line(r) for r in lines),
                },
                {
                    "source": f"Paper says · PackRight order confirmation {oa_no} (EN)",
                    "value": f"artwork rev. {old}",
                    "doc": oa_rel,
                    "page": 1,
                    "language": "en",
                    "quote": f"PO {po_no} · {c['supplier_part']} · "
                    + " / ".join(fmt(n(r["quantity"])) for r in lines)
                    + f" · {c['old_quote']}",
                },
                {
                    "source": f"Engineering change {c['ecn']}",
                    "value": f"rev {old} to rev {new}, rev {old} must not be packed",
                    "doc": ecn["_file"],
                    "quote": f"{c['ecn']} · {part} · rev {old} to {new} · {ecn.get('reason')}",
                },
            ],
            "trail": trail(
                cur,
                [
                    ("Watcher", f"Read {Path(ecn['_file']).name}: {part} moves to rev {new}."),
                    (
                        "Verification",
                        f"Checked PackRight's confirmation {oa_no} for PO {po_no}: "
                        f"artwork rev. {old}.",
                    ),
                    (
                        "Verification",
                        f"Matched PO {po_no} in SAP: {fmt(qty)} sleeves due {due}, still open.",
                    ),
                ],
            ),
        }
        decision = {
            "finding_id": fid,
            "level": "amber",
            "headline": f"PackRight confirmed PO {po_no} with the old sleeve artwork (rev {old})",
            "reason": f"Sleeves printed to rev {old} cannot be packed. It is still free to fix "
            "before printing starts.",
            "deadline": deadline(root, c["decide_by"], c["lead_h"], cur),
            "options": [
                {
                    "action": f"Ask PackRight to print PO {po_no} to artwork rev {new}",
                    "cost": "No cost",
                    "effect": f"{fmt(qty)} sleeves usable after launch",
                    "channel": "email",
                    "to": who.get("contact_email"),
                    "recommended": True,
                    "draft": (
                        f"Dear {who.get('contact_name') or 'PackRight team'},\n\n"
                        f"your order confirmation {oa_no} for PO {po_no} confirms artwork "
                        f"{c['supplier_part']} rev. {old}. Our part {part} changed to rev. {new} "
                        f"({c['ecn']}); sleeves printed to rev. {old} cannot be packed.\n\n"
                        f"Please print PO {po_no} to artwork rev. {new} ({c['new_file']}) and "
                        "send us a corrected order confirmation. If you do not have the file, "
                        "reply and we will send it today.\n\n"
                        "Best regards,\nSoony Procurement"
                    ),
                },
                {
                    "action": "Tell the category lead",
                    "cost": "No cost",
                    "effect": "the category lead follows up with PackRight",
                    "channel": "notify",
                    "to": "Category lead",
                    "draft": f"PackRight confirmed PO {po_no} ({fmt(qty)} sleeves) with artwork "
                    f"rev {old}; {c['ecn']} requires rev {new}.",
                },
            ],
        }
        impact = {
            "finding_id": fid,
            "units_short": qty,
            "edition": ecn.get("parent_id"),
            "deadline": decision["deadline"],
        }
        out.append((finding, decision, impact))
    return out


def statues(root: Path, cur: datetime) -> list[Card]:
    c = STATUES
    gr = next(
        (
            r
            for r in csvs(root, "soony/erp", "goods_receipts*.csv")
            if r.get("gr_number") == c["gr"]
        ),
        None,
    )
    if gr is None:
        warn(f"goods receipt {c['gr']} not found")
        return []
    if not says(root, c["report"], c["report_checks"]):
        return []
    part, lot = gr["material_id"], n(gr["qty_received"])
    rows, snapshot = stock(root)
    total = sum(n(r.get("quantity")) for r in rows if r.get("material_id") == part)
    batch = sum(
        n(r.get("quantity"))
        for r in rows
        if r.get("material_id") == part and r.get("batch") == c["gr"]
    )
    usable = total - batch
    edition, need = to_pack(root, part, snapshot)
    short = max(0, need - usable)
    fid = f"qa-{c['gr']}-{part}"
    finding = {
        "id": fid,
        "kind": "quality",
        "part": part,
        "supplier": gr.get("supplier_id"),
        "title": f"{fmt(batch)} statues in stock failed incoming inspection",
        "summary": (
            f"Goods receipt {c['gr']} (PO {gr['po_number']}) put {fmt(lot)} statues in stock as "
            "unrestricted, but the inspection report rejects the lot. Without them "
            f"{fmt(usable)} statues are usable for {fmt(need)} Collector's copies."
        ),
        "status": "open",
        "level": "red",
        "created_at": cur.isoformat(timespec="seconds"),
        "sides": [
            {
                "source": "System says · SAP stock",
                "value": f"{fmt(batch)} statues on GR {c['gr']}, {fmt(total)} in stock",
                "doc": next((r["_file"] for r in rows if r.get("batch") == c["gr"]), None),
                "quote": f"{part} · batch {c['gr']} · {batch} PC",
            },
            {
                "source": "Paper says · incoming inspection report (EN)",
                "value": f"GR {c['gr']} · {fmt(lot)} statues rejected (AQL 1.0)",
                "doc": c["report"],
                "page": 1,
                "language": "en",
                "quote": c["report_quote"],
            },
        ],
        "trail": trail(
            cur,
            [
                ("Watcher", f"Read {Path(c['report']).name} from DC Hamburg."),
                (
                    "Verification",
                    f"Matched GR {c['gr']} in SAP: {fmt(lot)} statues booked "
                    "unrestricted; the report rejects the lot.",
                ),
                (
                    "Impact",
                    f"{fmt(usable)} usable statues for {fmt(need)} Collector's copies: "
                    f"{fmt(short)} short.",
                ),
            ],
        ),
    }
    decision = {
        "finding_id": fid,
        "level": "red",
        "headline": f"{fmt(batch)} statues on the shelf failed inspection. Without them we are "
        f"{fmt(short)} Collector's copies short.",
        "reason": "The report allows use only under a concession signed by Supplier Quality and "
        "the category lead; a 100% sort could free the good statues today.",
        "deadline": deadline(root, c["decide_by"], c["lead_h"], cur),
        "options": [
            {
                "action": "Ask Supplier Quality and the category lead for a concession or a "
                "100% sort today",
                "cost": "No cost",
                "effect": "the good statues of the lot can be packed",
                "channel": "notify",
                "to": "Supplier Quality, Category lead",
                "recommended": True,
                "draft": (
                    f"Goods receipt {c['gr']} (PO {gr['po_number']}, {fmt(lot)} statues {part}) "
                    "failed incoming inspection but is booked unrestricted. Without it we are "
                    f"{fmt(short)} Collector's copies short. Please decide today: a concession "
                    "or a 100% sort at DC Hamburg."
                ),
            },
        ],
    }
    impact = {
        "finding_id": fid,
        "copies_at_risk": short,
        "edition": edition,
        "deadline": decision["deadline"],
    }
    return [(finding, decision, impact)]


CASES = (art_books, sleeves, statues)


# ---------------------------------------------------------------- writing


def merge(name: str, key: str, entries: list[dict]) -> None:
    new = {e[key]: e for e in entries}
    kept = [e for e in api.read_list(name) if e.get(key) not in new]
    api.write_json(name, kept + list(new.values()))


def backup(name: str, stamp: str) -> None:
    path = api.state_dir() / name
    if path.exists():
        shutil.copy2(path, path.with_name(f"{path.stem}.before-demo-{stamp}.json"))


def reset(stamp: str) -> None:
    for name, empty in RESET_FILES.items():
        backup(name, stamp)
        api.write_json(name, empty)
    backup("routines.json", stamp)
    routines = api.read_list("routines.json")
    for r in routines:
        r["enabled"] = False
    api.write_json("routines.json", routines)


def build(root: Path, cur: datetime) -> list[Card]:
    cards: list[Card] = []
    for case in CASES:
        try:
            cards += case(root, cur)
        except (KeyError, ValueError) as exc:  # a file changed shape: skip this case only
            warn(f"{case.__name__} skipped: {exc!r}")
    return cards


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    ap.add_argument("--reset", action="store_true", help="clear decisions, actions and routines")
    args = ap.parse_args(argv)
    root = api.portal_root()
    if root is None:
        print("No portal data found under data/ (set ASK_PORTAL_DIR).", file=sys.stderr)
        return 1
    cur = api.now()
    if args.reset:
        reset(cur.strftime("%Y%m%d-%H%M%S"))
    cards = build(root, cur)
    merge("findings.json", "id", [f for f, _, _ in cards])
    merge("decisions.json", "finding_id", [d for _, d, _ in cards])
    merge("impacts.json", "finding_id", [i for _, _, i in cards])
    for f, d, _ in cards:
        print(f"{f['id']}: {d['headline']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
