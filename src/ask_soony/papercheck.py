"""
Paper check: read every supplier-side document in the portal and hold it against the ERP.

Suppliers and the DC write on paper (order confirmations, shipping advices, packing lists,
delivery notes, inspection reports, notices); SAP only knows what someone typed in. This agent
turns each document into claims, then runs small generic rules that look for a contradiction
between a claim and an ERP row. Every contradiction becomes an inbox card (findings.json,
decisions.json, impacts.json in the state folder) with both sides quoted, so nobody has to pick
one side silently.

1. Claims. Document text comes from the anchor index (PDF text layer, OCR for scans, table rows).
   Deterministic extraction first: references to known PO, goods receipt and delivery note
   numbers, table rows (item, part, description, quantity, price, currency, date), part lines
   with ordered / shipped / balance columns found by their header words, artwork revisions and
   status sentences (on hold, short shipment, lot does not meet AQL, ...). Then, when an OpenAI
   key is set, one LLM call per distinct free text (cached by content hash, so the same
   boilerplate on fifty confirmations costs one call) and one vision call per receiving scan whose
   goods receipt is still in stock (handwritten corrections). LLM statements are only kept when
   their quote is found in the document text.
2. Rules (see RULES): late or short delivery, old revision, receipt booked wrong, failed lot
   counted as stock, held at supplier.
3. Impact: remaining need per launch edition (day-one demand minus packed, times BOM usage)
   against stock plus open PO lines due before launch, with and without the contradicted
   quantity. A shortfall makes the card red; otherwise it is amber.

Nothing about Soony is written here: file names, columns, ids and values are discovered at run
time. `python -m ask_soony.papercheck` writes the cards; `run(write=False)` only returns them.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from ask_soony import anchors, api, columns

VERSION = "papercheck-1"
ORIGIN = "papercheck"
AGENTS = ("Watcher", "Paper check", "Impact")
DEFAULT_MODEL = "gpt-4.1-mini"
# USD per million tokens (input, output) for the default model; used for the cost log only.
PRICE = {"gpt-4.1-mini": (0.40, 1.60), "gpt-4.1-nano": (0.10, 0.40), "gpt-4.1": (2.0, 8.0)}

DOC_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".eml", ".txt", ".md"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
SYSTEM_FOLDERS = ("soony/erp/", "soony/master/", "soony/logistics/")

_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_DMY = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(20\d{2})\b")
# 48,000 / 56.000 / 8 000 (a space groups thousands only after one or two digits: '500 300 200'
# is three numbers) / 5600
_QTY = re.compile(
    r"(?<![\w.,/-])(\d{1,3}(?:[,.']\d{3})+|\d{1,2}(?:[ \u00a0\u202f]\d{3})+|\d{3,})"
    r"(?![\w/-]|[.,]\d)"
)
_UNIT_AFTER = re.compile(r"^\s*(gb|tb|mb|cm|mm|kg|g\b|p\b|%|h\b|d\b|x\d)", re.I)
_REV = re.compile(r"\brev(?:ision)?\.?\s*([A-Z0-9]{1,2})\b|_rev([A-Z0-9]{1,2})\b", re.I)
_ROW = re.compile(
    r"\b(\d{5,6})\s+([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+)\s+(.+?)\s+"
    r"(\d{1,3}(?:[,.' ]\d{3})+|\d+)\s+(\d+(?:[.,]\d+)?)\s+([A-Z]{3})\s+(20\d{2}-\d{2}-\d{2})"
)

# Words that name a column in a supplier table, in the languages suppliers write in.
COLUMNS = {
    "ordered": ("ordered", "order qty", "발주", "zamówion", "订购", "bestellt"),
    "shipped": ("shipped", "출하수량", "wysłan", "发货", "geliefert", "dispatched"),
    "balance": ("balance", "잔량", "remaining", "reszta", "余量", "rest "),
}
# Status sentences. Each kind maps to the rule that uses it.
STATEMENTS = {
    "on_hold": r"\bon hold\b|zatrzyman|보류|暂扣|暂停发货|\bwithheld\b|we release it",
    "refused_terms": r"do not accept|not accept(?:ed)?\b|nie akceptuj|不接受|수락할 수 없",
    "short_shipment": r"short shipment|short delivery|partial shipment|offloaded|could not be "
    r"loaded|못했습니다|niedobór",
    "failed_inspection": r"does not meet|not meet aql|\breject(?:ed)?\b|nicht in ordnung|"
    r"\bfailed\b",
    "unrestricted_stock": r"unrestricted",
    "suspended": r"\bsuspended\b|wstrzyman|暂停",
    "delay": r"\bdelay(?:ed)?\b|postpone|move out by|opóźni",
}
LANG_HINTS = [("ko", r"[가-힣]"), ("zh", r"[一-鿿]"), ("pl", r"[ąćęłńśźż]")]

DOC_TYPES = [
    ("inspection_report", r"inspection|aql|prüfbericht"),
    ("shipping_advice", r"shipping advice|advice of dispatch|short shipment|출하 통지|发货通知"),
    ("packing_list", r"packing list|装箱单|lista pakowa"),
    (
        "order_confirmation",
        r"order confirmation|order acknowledge?ment|potwierdzenie zamówienia|"
        r"订单确认|주문 확인|auftragsbestätigung",
    ),
    ("delivery_note", r"delivery note|dokument wz|lieferschein|送货单|납품서|\bwz\b"),
    ("offer", r"\boffer\b|oferta|quotation|angebot|报价"),
    ("notice", r"notice|zawiadomienie|通知|통지|mitteilung"),
]


def log(msg: str) -> None:
    print(f"papercheck: {msg}", file=sys.stderr)


# ---------------------------------------------------------------- small helpers


def qty(text: Any) -> float | None:
    """'48,000', '8 000', '56.000' and '5600' as numbers; None for anything else."""
    s = re.sub(r"[\s  ']", "", anchors.norm(str(text or "")))
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def iso_dates(text: str) -> list[str]:
    out = [f"{y}-{m}-{d}" for y, m, d in _ISO.findall(text)]
    out += [f"{y}-{int(m):02d}-{int(d):02d}" for d, m, y in _DMY.findall(text)]
    return out


def fmt(v: float | None) -> str:
    return "?" if v is None else f"{v:,.0f}"


def fmt_day(value: str | None) -> str:
    try:
        return datetime.fromisoformat(str(value)[:10]).strftime("%a %-d %b")
    except ValueError:
        return str(value)


def sentences(text: str) -> list[str]:
    """Sentences, without splitting after abbreviations such as 'rev.', 'No.' or 'p.'."""
    out: list[str] = []
    for p in re.split(r"(?<=[.!?。])\s+|\s+/\s+(?=[A-Z])", text):
        p = p.strip()
        if not p:
            continue
        if out and re.search(r"(?:^|\s)\w{1,3}\.$", out[-1]):
            out[-1] = f"{out[-1]} {p}"
        else:
            out.append(p)
    return out


def language(text: str) -> str:
    for code, pattern in LANG_HINTS:
        if re.search(pattern, text, re.I):
            return code
    return "en"


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def found_in(quote: str, text: str) -> bool:
    """A quote counts as verbatim when its words appear in the text (spacing and case aside)."""
    q, t = squash(quote).lower(), squash(text).lower()
    if not q:
        return False
    if q in t:
        return True
    words = [w for w in re.findall(r"\w+", q) if len(w) > 2]
    return bool(words) and sum(w in t for w in words) / len(words) >= 0.8


def col(row: dict, *names: str) -> str:
    """The first non-empty value among the given column names (headers differ between files)."""
    for n in names:
        v = row.get(n)
        if v not in (None, ""):
            return str(v).strip()
    return ""


# ---------------------------------------------------------------- the ERP side


@dataclass
class Erp:
    root: Path
    launch_at: datetime
    pos: list[dict] = field(default_factory=list)
    grs: list[dict] = field(default_factory=list)
    stock: list[dict] = field(default_factory=list)
    bom: list[dict] = field(default_factory=list)
    products: list[dict] = field(default_factory=list)
    packout: list[dict] = field(default_factory=list)
    ecns: list[dict] = field(default_factory=list)
    components: dict[str, dict] = field(default_factory=dict)
    shipments: list[dict] = field(default_factory=list)
    suppliers: dict[str, dict] = field(default_factory=dict)  # id -> {dir, profile, parts}
    # PO line values a newer export changed: {po, line, material, field, old, new, files}
    revisions: list[dict] = field(default_factory=list)
    schedules: list[dict] = field(default_factory=list)  # supplier CSVs of confirmed dates
    snapshot: str = ""
    site: str = "our distribution center"  # where parts are delivered, from sites.csv

    @property
    def po_numbers(self) -> set[str]:
        return {r["po_number"] for r in self.pos if r.get("po_number")}

    @property
    def gr_numbers(self) -> set[str]:
        return {r["gr_number"] for r in self.grs if r.get("gr_number")}

    @property
    def delivery_notes(self) -> set[str]:
        return {r["delivery_note"] for r in self.grs if r.get("delivery_note")}

    def po_lines(self, po: str, material: str | None = None) -> list[dict]:
        return [
            r
            for r in self.pos
            if r.get("po_number") == po and (material is None or r.get("material_id") == material)
        ]

    def supplier_of_doc(self, rel: str) -> str | None:
        for sid, s in self.suppliers.items():
            if rel.startswith(s["dir"] + "/"):
                return sid
        return None

    # ---- need and cover

    def launch_editions(self) -> dict[str, dict]:
        return {p["product_id"]: p for p in self.products if p.get("lifecycle") == "launch"}

    def usage(self, material: str) -> dict[str, float]:
        eds = self.launch_editions()
        return {
            b["parent_id"]: qty(b.get("qty_per")) or 1
            for b in self.bom
            if b.get("child_id") == material and b.get("parent_id") in eds
        }

    def remaining(self, product: str) -> float:
        p = self.launch_editions().get(product) or {}
        packed = sum(
            qty(r.get("units_packed")) or 0
            for r in self.packout
            if r.get("product_id") == product
            and (not self.snapshot or str(r.get("date") or "") <= self.snapshot)
        )
        return max(0.0, (qty(p.get("day_one_qty")) or 0) - packed)

    def need(self, material: str) -> float:
        return sum(self.remaining(p) * per for p, per in self.usage(material).items())

    def stock_of(self, material: str, exclude: set[str] = frozenset()) -> float:
        return sum(
            qty(r.get("quantity")) or 0
            for r in self.stock
            if r.get("material_id") == material and r.get("batch") not in exclude
        )

    def due_before_launch(self, material: str) -> float:
        cut = self.launch_at.date().isoformat()
        return sum(
            qty(r.get("quantity")) or 0
            for r in self.pos
            if r.get("material_id") == material
            and is_open(r)
            and str(r.get("promised_date") or r.get("requested_date") or "9999")[:10] <= cut
        )

    def cover(self, material: str, minus: float = 0, exclude: set[str] = frozenset()) -> dict:
        need = self.need(material)
        stock = self.stock_of(material, exclude)
        due = self.due_before_launch(material)
        short = max(0.0, need - (stock + due - minus))
        usage = self.usage(material)
        edition = max(usage, key=self.remaining) if usage else None
        per = usage.get(edition, 1) if edition else 1
        return {
            "need": need,
            "stock": stock,
            "due": due,
            "short": short,
            "copies": int(-(-short // per)) if short else 0,
            "edition": edition,
            "editions": sorted(usage),
        }


def is_open(po_row: dict) -> bool:
    status = str(po_row.get("status") or "").lower()
    if status in ("delivered", "cancelled", "canceled", "closed", "completed"):
        return False
    q = qty(po_row.get("quantity"))
    if q is not None and q <= 0:  # a reversal or deletion line has nothing to deliver
        return False
    # Exports with status codes (SAP) say it with a delivery date instead.
    return not (po_row.get("delivered_date") and not status.startswith(("open", "partial")))


# What the readers did with each file on the last load: {path: {"read": bool, "wanted": [...]}}.
# The data check (datacheck.py) uses it to say which files are silently left out.
READS: dict[str, dict] = {}


def _csv_rows(
    root: Path, folder: str, pattern: str, need: tuple[str, ...], kind: str | None = None
) -> list[dict]:
    """
    Rows of every CSV in folder matching pattern whose header has all `need` columns, with
    canonical column names added (columns.py). With `kind`, a CSV under another name is read
    too when its mapped columns make it that kind of file (a German PO export, say).
    """
    out: list[dict] = []
    base = root / folder
    files = sorted(base.glob(pattern)) if base.is_dir() and pattern else []
    if kind and base.is_dir():
        mode = columns.model_mode()
        files += [
            f
            for f in sorted(base.rglob("*.csv"))
            if f not in files and columns.read(f, mode)[1].kind == kind
        ]
    for f in files:
        rows, mapping = columns.read(f, columns.model_mode())
        seen = READS.setdefault(str(f.relative_to(root)), {"read": False, "wanted": []})
        if rows and pattern != "*.csv" and not all(c in rows[0] for c in need):
            seen["wanted"].append(
                {"pattern": pattern, "missing": [c for c in need if c not in rows[0]]}
            )
        if rows and all(c in rows[0] for c in need):
            seen["read"] = True
            if mapping.needed:
                seen["mapped"] = {
                    "kind": mapping.kind,
                    "renamed": mapping.renamed,
                    "model": list(mapping.model),
                }
            out += [
                {**r, "_file": str(f.relative_to(root)), "_row": i + 1} for i, r in enumerate(rows)
            ]
    return out


def launch_time(root: Path) -> datetime:
    """When the trucks leave: ASK_LAUNCH_AT, else the end of the last packing window in the
    launch plan, else the earliest launch date of a launch product at the end of that day."""
    if os.environ.get("ASK_LAUNCH_AT"):
        dt = api.parse_ts(os.environ["ASK_LAUNCH_AT"])
        if dt:
            return dt
    try:
        from ask_soony import launch

        at = launch.load(root, api.tz()).launch
        if at:
            return at
    except Exception:  # a plan that cannot be read must not stop the paper check
        pass
    dates = [
        r["launch_date"]
        for r in _csv_rows(root, "soony/master", "products*.csv", ("product_id",))
        if r.get("lifecycle") == "launch" and r.get("launch_date")
    ]
    day = date.fromisoformat(min(dates)) if dates else api.now().date()
    return datetime(day.year, day.month, day.day, 23, 59, tzinfo=api.tz())


def _newness(root: Path, rel: str) -> tuple:
    """When a file arrived in the mirror, then the date in its name: later is newer."""
    try:
        mtime = (root / rel).stat().st_mtime
    except OSError:
        mtime = 0.0
    named = max(iso_dates(Path(rel).stem.replace("_", " ")) or [""])
    return (mtime, named, rel)


REVISED_FIELDS = ("material_id", "quantity", "unit_price", "promised_date", "status")


def merge_pos(root: Path, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    One row per PO line across all PO exports. When two files carry the same line, the newer
    file's values win (blank values do not erase older ones), and every value it changed is
    kept as a revision on the row (`_revised`) and in the returned list, so a moved date is
    evidence, not a silent overwrite.
    """
    for r in rows:
        if not r.get("promised_date") and r.get("confirmed_date"):
            r["promised_date"] = r["confirmed_date"]
    ordered = sorted(rows, key=lambda r: _newness(root, r["_file"]))
    out: list[dict] = []
    where: dict[tuple, int] = {}
    revisions: list[dict] = []
    for r in ordered:
        k = (r.get("po_number"), str(r.get("po_line") or ""))
        i = where.get(k)
        if i is None or out[i]["_file"] == r["_file"]:
            where[k] = len(out)
            out.append(r)
            continue
        old = out[i]
        changes = []
        for f in REVISED_FIELDS:
            a, b = str(old.get(f) or "").strip(), str(r.get(f) or "").strip()
            same = a == b or (qty(a) is not None and qty(a) == qty(b))
            if a and b and not same:
                changes.append(
                    {
                        "po": k[0],
                        "line": k[1],
                        "material": r.get("material_id") or old.get("material_id"),
                        "field": f,
                        "old": a,
                        "new": b,
                        "old_file": old["_file"],
                        "new_file": r["_file"],
                        "note": str(r.get("note") or "").strip(),
                    }
                )
        merged = {**old, **{c: v for c, v in r.items() if v not in (None, "")}}
        merged["_revised"] = (old.get("_revised") or []) + changes
        merged["_previous_file"] = old["_file"]
        out[i] = merged
        revisions += changes
    return out, revisions


def load_schedules(root: Path) -> list[dict]:
    """Supplier CSVs that restate confirmed dates per PO line (recognised by their columns)."""
    return _csv_rows(
        root, "suppliers", "", ("po_number", "material_id", "confirmed_date"), "confirmed_schedule"
    )


def load_erp(root: Path) -> Erp:
    erp = Erp(root=root, launch_at=launch_time(root))
    pos = _csv_rows(
        root, "soony/erp", "purchase_order*.csv", ("po_number", "material_id"), "purchase_orders"
    )
    erp.pos, erp.revisions = merge_pos(root, pos)
    erp.schedules = load_schedules(root)
    erp.grs = _csv_rows(
        root, "soony/erp", "goods_receipt*.csv", ("gr_number", "material_id"), "goods_receipts"
    )
    erp.stock = _csv_rows(root, "soony/erp", "stock*.csv", ("material_id", "quantity"), "stock")
    erp.packout = _csv_rows(root, "soony/erp", "packout*.csv", ("product_id", "units_packed"))
    erp.shipments = _csv_rows(root, "soony/erp", "*shipment*.csv", ("shipment_id",), "shipments")
    erp.bom = _csv_rows(root, "soony/master", "bom*.csv", ("parent_id", "child_id", "qty_per"))
    erp.ecns = _csv_rows(root, "soony/master", "*.csv", ("child_id", "rev_old", "rev_new"))
    erp.products = _csv_rows(root, "soony/master", "products*.csv", ("product_id",))
    dcs = [
        r
        for r in _csv_rows(root, "soony/master", "sites*.csv", ("site_id", "type"))
        if (r.get("type") or "").lower() == "dc"
    ]
    if dcs:
        city = (dcs[0].get("city") or "").strip()
        erp.site = f"DC {city}" if city else (dcs[0].get("name") or erp.site)
    for r in _csv_rows(root, "soony/master", "components*.csv", ("component_id", "description")):
        erp.components[r["component_id"]] = r
    for r in _csv_rows(root, "soony/master", "components*.csv", ("component_id", "field", "new")):
        if r["component_id"] in erp.components and r.get("field"):
            erp.components[r["component_id"]][r["field"]] = r["new"]
    erp.snapshot = max((str(r.get("snapshot_date") or "") for r in erp.stock), default="")
    sup_root = root / "suppliers"
    for d in sorted(sup_root.iterdir()) if sup_root.is_dir() else []:
        if not d.is_dir():
            continue
        profile = next(iter(api.read_csv(d / "profile.csv")), {})
        sid = profile.get("supplier_id") or d.name.split("_")[0]
        parts = {
            r["component_id"]: r
            for f in sorted(d.glob("components*.csv"))
            for r in api.read_csv(f)
            if r.get("component_id") and r.get("description")
        }
        erp.suppliers[sid] = {"dir": str(d.relative_to(root)), "profile": profile, "parts": parts}
    return erp


# ---------------------------------------------------------------- claims


@dataclass
class Doc:
    rel: str
    pages: list[tuple[int, str]]  # (page, text)
    source: str  # text | ocr | table
    supplier: str | None = None
    type: str = "document"
    language: str = "en"
    pos: list[str] = field(default_factory=list)
    grs: list[str] = field(default_factory=list)
    dns: list[str] = field(default_factory=list)
    lines: list[dict] = field(default_factory=list)
    statements: list[dict] = field(default_factory=list)
    llm: str = ""  # "", "text" or "vision"

    @property
    def text(self) -> str:
        return " ".join(t for _, t in self.pages)

    def page_of(self, snippet: str) -> int:
        key = squash(snippet)[:40].lower()
        for p, t in self.pages:
            if key and key in squash(t).lower():
                return p
        return self.pages[0][0] if self.pages else 1


def documents() -> list[Doc]:
    """Supplier-side documents from the anchor index (everything that is not an ERP export)."""
    with anchors._lock:
        items = list(anchors._index.items())
    docs = []
    for rel, units in sorted(items):
        if rel.startswith(SYSTEM_FOLDERS) or Path(rel).suffix.lower() not in DOC_SUFFIXES:
            continue
        pages = []
        for u in units:
            text = u.get("text") or " ".join(w[0] for w in u.get("words") or [])
            if text.strip():
                pages.append((int(u.get("page") or u.get("row") or 1), squash(text)))
        if pages:
            src = "ocr" if any(u.get("source") == "ocr" for u in units) else "text"
            docs.append(Doc(rel, pages, src))
    return docs


class PartMap:
    """Supplier part numbers to our material ids, learned from the documents themselves."""

    def __init__(self, erp: Erp):
        self.erp = erp
        self.learned: dict[str, str] = {}

    def material(self, part: str, desc: str = "", po: str | None = None, q: float | None = None):
        if part in self.erp.components:
            return part
        if part in self.learned:
            return self.learned[part]
        m = None
        if po and q is not None:  # the PO line with the same quantity names the material
            hits = {r["material_id"] for r in self.erp.po_lines(po) if qty(r.get("quantity")) == q}
            m = hits.pop() if len(hits) == 1 else None
        if m is None and desc:
            d = squash(desc).lower()
            hits = {
                cid
                for cid, c in self.erp.components.items()
                if squash(c.get("description", "")).lower() in d
                or d in squash(c.get("description", "")).lower()
            }
            m = hits.pop() if len(hits) == 1 else None
        if m:
            self.learned[part] = m
        return m


def classify(doc: Doc, erp: Erp) -> str:
    low = doc.text.lower()
    for kind, pattern in DOC_TYPES:
        if kind == "delivery_note" and doc.dns:
            return kind
        if re.search(pattern, low):
            return kind
    return "document"


def _descriptions(erp: Erp, material: str | None) -> list[str]:
    out = []
    if material and material in erp.components:
        out.append(erp.components[material].get("description", ""))
    for s in erp.suppliers.values():
        if material in s["parts"]:
            out.append(s["parts"][material].get("description", ""))
    return sorted({d for d in out if d}, key=len, reverse=True)


def _numbers(segment: str) -> list[float]:
    """Quantities in a text segment, skipping dates, prices and sizes like '128 GB'."""
    seg = _ISO.sub(" ", _DMY.sub(" ", segment))
    out = []
    for m in _QTY.finditer(seg):
        if _UNIT_AFTER.match(seg[m.end() :]):
            continue
        v = qty(m.group(1))
        if v is not None and v >= 1:
            out.append(v)
    return out


def _part_ids(text: str, erp: Erp, parts: PartMap) -> list[tuple[int, str]]:
    known = set(erp.components) | set(parts.learned)
    for s in erp.suppliers.values():
        known |= set(s["parts"])
    return sorted(
        (m.start(), m.group(0))
        for m in re.finditer(r"\b[A-Z]{2,}(?:-[A-Z0-9]+){1,3}\b", text)
        if m.group(0) in known
    )


def extract(doc: Doc, erp: Erp, parts: PartMap) -> None:
    """Deterministic claims: references, table rows, part lines, revisions and statements."""
    text = doc.text
    toks = anchors.tokens(text)
    name_toks = anchors.tokens(Path(doc.rel).stem)
    doc.pos = sorted(erp.po_numbers & toks) or sorted(erp.po_numbers & name_toks)
    doc.grs = sorted(erp.gr_numbers & toks)
    doc.dns = sorted({d for d in erp.delivery_notes if d.upper() in toks or d.upper() in name_toks})
    doc.supplier = erp.supplier_of_doc(doc.rel)
    doc.type = classify(doc, erp)
    doc.language = language(text)
    po = doc.pos[0] if len(doc.pos) == 1 else None

    # 1. table rows: item, part, description, quantity, price, currency, date
    rows = list(_ROW.finditer(text))
    for m in rows:
        part, desc, q = m.group(2), m.group(3), qty(m.group(4))
        mat = parts.material(part, desc, po, q)
        doc.lines.append(
            {
                "po": po,
                "item": m.group(1),
                "part": part,
                "material": mat,
                "confirmed": q,
                "confirmed_date": m.group(7),
                "price": qty(m.group(5)),
                "currency": m.group(6),
                "quote": squash(m.group(0)),
                "page": doc.page_of(m.group(0)),
            }
        )
    rest = text
    for m in reversed(rows):
        rest = rest[: m.start()] + " " + rest[m.end() :]

    # 2. part lines outside tables: columns named in the header (ordered / shipped / balance)
    low = rest.lower()
    order = sorted(
        (min(p for p in pos if p >= 0), name)
        for name, words in COLUMNS.items()
        if (pos := [low.find(w) for w in words]) and max(pos) >= 0
    )
    ids = _part_ids(rest, erp, parts)
    for i, (start, part) in enumerate(ids):
        if any(line["part"] == part for line in doc.lines):
            continue
        end = ids[i + 1][0] if i + 1 < len(ids) else min(len(rest), start + 400)
        seg = rest[start + len(part) : end]
        mat = parts.material(part, seg, po)
        for d in _descriptions(erp, mat):
            seg = seg.replace(d, " ")
        nums = _numbers(seg)
        line: dict[str, Any] = {"po": po, "part": part, "material": mat, "page": 1}
        labelled = re.search(
            r"(?:quantity|qty|ilość|数量|수량)\s*[:.]?\s*([\d][\d ,.']*\d)", seg, re.I
        )
        if len(order) >= 2 and len(nums) >= len(order):
            for (_, name), v in zip(order, nums):
                line[name] = v
        elif labelled:
            line["shipped" if doc.type in ("packing_list", "shipping_advice") else "qty"] = qty(
                labelled.group(1)
            )
        elif nums and doc.source == "text":
            line["qty"] = nums[0]
        raw = rest[start:end]
        used = [k for k in ("ordered", "shipped", "balance", "qty") if k in line]
        last = [m for m in _QTY.finditer(raw) if qty(m.group(1)) in {line[k] for k in used}]
        line["quote"] = squash(
            raw[: last[len(used) - 1].end()] if len(last) >= len(used) > 0 else raw
        )[:240]
        if "balance" in line:
            said = [
                s
                for s in sentences(rest)
                if re.search(r"balance|remaining|잔량|reszt", s, re.I) and iso_dates(s)
            ]
            if said:
                line["balance_arrival"] = max(d for s in said for d in iso_dates(s))
                line["quote"] += " · " + said[0][:200]
        line["page"] = doc.page_of(rest[start : start + 60])
        if len(line) > 5:
            doc.lines.append(line)

    # 3. revisions, attached to the nearest part named before them
    all_ids = _part_ids(text, erp, parts)
    for m in _REV.finditer(text):
        rev = (m.group(1) or m.group(2)).upper()
        before = [p for s, p in all_ids if s < m.start()]
        part = before[-1] if before else None
        mat = parts.material(part) if part else None
        if mat is None:
            mats = {line.get("material") for line in doc.lines if line.get("material")}
            mat = mats.pop() if len(mats) == 1 else None
        near = [s for s, _ in all_ids if m.start() - 80 <= s < m.start()]
        start = near[-1] if near else max(0, m.start() - 60)
        stop = re.compile(r"[.。](?:\s|$)").search(text, m.end())
        end = min(stop.end() if stop else len(text), m.end() + 120)
        sent = squash(text[start:end])
        doc.statements.append(
            {
                "kind": "revision",
                "revision": rev,
                "part": part,
                "material": mat,
                "quote": sent[:300],
                "page": doc.page_of(sent),
            }
        )

    # 4. status sentences
    for s in sentences(text):
        for kind, pattern in STATEMENTS.items():
            if re.search(pattern, s, re.I):
                doc.statements.append({"kind": kind, "quote": s[:300], "page": doc.page_of(s)})


# ---------------------------------------------------------------- LLM (optional, cached)


@dataclass
class Llm:
    key: str
    model: str
    cache: Path
    max_calls: int
    calls: int = 0
    cached: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def cost(self) -> float:
        p_in, p_out = PRICE.get(self.model, PRICE[DEFAULT_MODEL])
        return (self.tokens_in * p_in + self.tokens_out * p_out) / 1e6

    def ask(self, key: str, messages: list[dict]) -> dict | None:
        path = self.cache / f"{key}.json"
        if path.exists():
            try:
                with self.lock:
                    self.cached += 1
                return json.loads(path.read_text())
            except (OSError, ValueError):
                pass
        if not self.key:
            return None
        with self.lock:
            if self.calls >= self.max_calls:
                return None
            self.calls += 1
        import httpx

        try:
            r = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.key}"},
                json={
                    "model": self.model,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                    "temperature": 0,
                },
                timeout=90,
            )
            r.raise_for_status()
            body = r.json()
            out = json.loads(body["choices"][0]["message"]["content"])
        except Exception as e:  # no network, bad key, bad JSON: keep the deterministic claims
            log(f"LLM call failed: {e!r}"[:300])
            return None
        usage = body.get("usage") or {}
        with self.lock:
            self.tokens_in += int(usage.get("prompt_tokens") or 0)
            self.tokens_out += int(usage.get("completion_tokens") or 0)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, ensure_ascii=False))
        return out


TEXT_PROMPT = """You read one document a supplier sent to a buyer, or a receiving document.
Return JSON: {"language": ISO code of the main non-English language or "en",
"statements": [{"kind": one of "on_hold", "refused_terms", "short_shipment", "failed_inspection",
"unrestricted_stock", "suspended", "delay", "revision", "other", "quote": the sentence copied
verbatim from the text, "translation": English translation if the quote is not English else null,
"revision": artwork or drawing revision letter if kind is revision else null}],
"lines": [{"part": part number as written, "ordered": number or null, "shipped": number or null,
"balance": number or null, "confirmed": number or null, "arrival_date": "YYYY-MM-DD" or null}]}
Only statements that change whether goods arrive, how many, when, or whether they may be used.
Standard terms that change nothing are not statements. Numbers without thousands separators."""

VISION_PROMPT = """This is a scanned delivery note received at a warehouse. Read it carefully,
including handwriting, strike-throughs and stamps. Return JSON: {"delivery_note": number,
"po_numbers": [...], "language": ISO code, "lines": [{"part": part number, "printed_qty": the
printed quantity, "final_qty": the quantity after any handwritten correction, "corrected": true if
a printed quantity is struck through or corrected by hand}], "notes": short English description of
any handwritten correction or remark, or null}. Write quantities as strings exactly as written,
with their separators ("19.000", "8 000")."""


def _image_b64(path: Path) -> str | None:
    try:
        from PIL import Image

        if path.suffix.lower() == ".pdf":
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(str(path))
            try:
                im = pdf[0].render(scale=2).to_pil()
            finally:
                pdf.close()
        else:
            im = Image.open(path)
        im = im.convert("RGB")
        im.thumbnail((1600, 1600))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def _free_text(doc: Doc) -> str:
    """The document text without its table rows: what an LLM can add something to."""
    return squash(_ROW.sub(" ", doc.text))


def llm_plan(doc: Doc, erp: Erp) -> tuple[str, list[dict]] | None:
    """The cache key and messages for this document's LLM call, or None when it needs none."""
    if doc.source == "ocr" and doc.dns:
        grs = [g for g in erp.grs if g.get("delivery_note") in doc.dns]
        batches = {r.get("batch") for r in erp.stock}
        if not any(g.get("gr_number") in batches for g in grs):
            return None  # nothing of this delivery is still on the shelf
        img = _image_b64(erp.root / doc.rel)
        if not img:
            return None
        digest = hashlib.sha256((erp.root / doc.rel).read_bytes()).hexdigest()
        return f"vision-{VERSION}-{digest}", [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}},
                ],
            }
        ]
    if doc.source != "text" or not (doc.pos or doc.grs or doc.dns):
        return None
    free = _free_text(doc)
    if len(free) < 40:
        return None
    # Without its references the free text is often shared by many documents: one call for all.
    generic = free
    for ref in doc.pos + doc.grs + doc.dns + [Path(doc.rel).stem.split("_")[0]]:
        generic = generic.replace(ref, "#")
    generic = re.sub(r"\b(OA|DN|SN|WZ)\d+\b", "#", generic)
    generic = re.sub(r"\b20\d{2}-\d{2}-\d{2}\b", "#", generic)
    rows = [line["quote"] for line in doc.lines if line.get("item")]
    if rows:
        free = generic  # tables are parsed already; the LLM reads only the free text
    digest = hashlib.sha256(free.encode()).hexdigest()
    return f"text-{VERSION}-{digest}", [
        {"role": "system", "content": TEXT_PROMPT},
        {"role": "user", "content": free[:6000]},
    ]


def apply_llm(doc: Doc, out: dict, erp: Erp, parts: PartMap, vision: bool) -> None:
    if not isinstance(out, dict):
        return
    doc.llm = "vision" if vision else "text"
    if out.get("language") and doc.language == "en":
        doc.language = str(out["language"])[:5]
    po = doc.pos[0] if len(doc.pos) == 1 else None
    if vision:
        for ln in out.get("lines") or []:
            part = str(ln.get("part") or "")
            mat = parts.material(part) if part else None
            final, printed = qty(ln.get("final_qty")), qty(ln.get("printed_qty"))
            if mat is None or final is None:
                continue
            if (
                isinstance(ln.get("final_qty"), (int, float))
                and final < 1000
                and not ln.get("corrected")
            ):
                continue  # '19.000' read as the number 19: a separator, not a quantity
            note = out.get("notes")
            doc.lines.append(
                {
                    "po": po,
                    "part": part,
                    "material": mat,
                    "received": final,
                    "printed": printed,
                    "corrected": bool(ln.get("corrected")) and printed != final,
                    "quote": squash(
                        f"{' · '.join(doc.dns)} · {' · '.join(doc.pos)} · {part} · "
                        + (
                            f"{fmt(printed)} (struck through), {fmt(final)} (handwritten)"
                            if ln.get("corrected") and printed != final
                            else fmt(final)
                        )
                    ),
                    "translation": note,
                    "page": 1,
                    "by": "vision",
                }
            )
        return
    known = {(s["kind"], squash(s["quote"]).lower()) for s in doc.statements}
    for s in out.get("statements") or []:
        kind, quote = str(s.get("kind") or "other"), squash(s.get("quote") or "")
        if kind not in STATEMENTS and kind != "revision" or not found_in(quote, doc.text):
            continue  # not verbatim: never trust an LLM quote we cannot find in the document
        match = next((d for d in doc.statements if found_in(d["quote"], quote)), None)
        if match:
            match.setdefault("translation", s.get("translation"))
            continue
        if (kind, quote.lower()) in known:
            continue
        item = {
            "kind": kind,
            "quote": quote,
            "page": doc.page_of(quote),
            "by": "llm",
            "translation": s.get("translation"),
        }
        if kind == "revision" and s.get("revision"):
            item["revision"] = str(s["revision"]).upper()
            mats = {ln.get("material") for ln in doc.lines if ln.get("material")}
            item["material"] = mats.pop() if len(mats) == 1 else None
        doc.statements.append(item)
    for ln in out.get("lines") or []:  # fill gaps only: the parsed table wins
        part = str(ln.get("part") or "")
        mat = parts.material(part, "", po, qty(ln.get("ordered")))
        if not mat or any(x.get("material") == mat for x in doc.lines):
            continue
        line = {
            "po": po,
            "part": part,
            "material": mat,
            "by": "llm",
            "page": 1,
            "quote": next((s for s in sentences(doc.text) if part in s), part)[:240],
        }
        for k in ("ordered", "shipped", "balance", "confirmed"):
            if qty(ln.get(k)) is not None and found_in(fmt(qty(ln.get(k))), anchors.norm(doc.text)):
                line[k] = qty(ln.get(k))
        if ln.get("arrival_date") and "balance" in line:
            line["balance_arrival"] = str(ln["arrival_date"])[:10]
        if len(line) > 6:
            doc.lines.append(line)


def claims(erp: Erp, llm: Llm | None = None) -> list[Doc]:
    anchors_ready()
    docs = documents()
    parts = PartMap(erp)
    for _ in range(2):  # the second pass uses supplier part numbers learned in the first
        for d in docs:
            d.lines, d.statements = [], []
            extract(d, erp, parts)
    docs = [d for d in docs if d.pos or d.grs or d.dns]  # no PO or part reference: skip
    if llm:
        by_key: dict[str, tuple[list[dict], list[Doc]]] = {}
        for d in docs:
            plan = llm_plan(d, erp)
            if plan:
                by_key.setdefault(plan[0], (plan[1], []))[1].append(d)

        def one(key: str):
            return key, llm.ask(key, by_key[key][0])

        with ThreadPoolExecutor(max_workers=6) as pool:  # one call per distinct text
            for key, out in pool.map(one, list(by_key)):
                for d in by_key[key][1] if out else []:
                    apply_llm(d, out, erp, parts, key.startswith("vision"))
    return docs + [d for d in schedule_docs(erp) if d.pos]  # tables need no model reading


def schedule_docs(erp: Erp) -> list[Doc]:
    """
    A supplier's spreadsheet of confirmed dates (a revised schedule) as a document: one line per
    row, so the rules hold each confirmed date against the PO line like any confirmation.
    """
    by_file: dict[str, list[dict]] = {}
    for r in erp.schedules:
        by_file.setdefault(r["_file"], []).append(r)
    docs = []
    for rel, rows in by_file.items():
        raw = api.read_csv(erp.root / rel)  # the row as the supplier wrote it, for the quote
        pages, lines = [], []
        for r in rows:
            po, mat = r.get("po_number"), r.get("material_id")
            orig = raw[r["_row"] - 1] if r["_row"] <= len(raw) else {}
            quote = " · ".join(str(v).strip() for k, v in orig.items() if k and v)
            pages.append((r["_row"], quote))
            if po not in erp.po_numbers:
                continue
            lines.append(
                {
                    "po": po,
                    "item": r.get("po_line"),
                    "part": mat,
                    "material": mat,
                    "confirmed": qty(r.get("quantity")),
                    "confirmed_date": r.get("confirmed_date") or None,
                    "previous_date": r.get("previous_date") or None,
                    "quote": quote,
                    "page": r["_row"],
                }
            )
        d = Doc(rel, pages, "table", type="revised_schedule")
        d.supplier = erp.supplier_of_doc(rel)
        d.language = language(" ".join(t for _, t in pages))
        d.pos = sorted({ln["po"] for ln in lines})
        d.lines = lines
        docs.append(d)
    return docs


def anchors_ready(timeout: float = 600) -> None:
    if anchors._state.get("building"):
        end = time.monotonic() + timeout
        while anchors._state.get("building") and time.monotonic() < end:
            time.sleep(0.5)
    else:
        anchors.build_index()


# ---------------------------------------------------------------- findings


@dataclass
class Hit:
    """One contradiction: what a rule found, before it is written as a card."""

    rule: str
    po: str
    material: str
    supplier: str | None
    title: str
    summary: str
    system: list[dict]
    paper: list[dict]
    docs: list[Doc]
    cover: dict
    units: float
    unit_label: str
    options: list[dict]
    reason: str
    headline: str
    arrives_at: str | None = None
    matched: str = ""


def _po_side(rows: list[dict], value: str) -> dict:
    r = rows[0]
    side = {
        "source": "System says · SAP purchase order",
        "value": value,
        "doc": r["_file"],
        "quote": " ; ".join(
            f"PO {x['po_number']} line {x.get('po_line')} · {x['material_id']} · "
            f"{x.get('quantity')} pcs · promised {x.get('promised_date')} · {x.get('status')}"
            for x in rows
        ),
        "row": r.get("_row"),
    }
    revised = [c for x in rows for c in x.get("_revised") or []]
    if revised:  # a newer export changed the line: show what it said before
        side["revised"] = revised
        side["quote"] += " ; revised: " + " ; ".join(
            f"{c['field']} {c['old']} → {c['new']} ({Path(c['new_file']).name})" for c in revised
        )
    return side


def _doc_side(doc: Doc, label: str, value: str, quote: str, page: int, translation=None) -> dict:
    lang = doc.language if doc.source == "ocr" else language(quote)
    if translation and found_in(translation, quote):
        translation = None  # the quote is already English
    side = {
        "source": f"Paper says · {label} ({lang.upper()})",
        "value": value,
        "doc": doc.rel,
        "page": page,
        "language": lang,
        "quote": quote,
    }
    if translation and lang != "en":
        side["translation"] = translation
    return side


def _label(doc: Doc) -> str:
    stem = Path(doc.rel).stem
    return f"{doc.type.replace('_', ' ')} {stem}"


def _contact(erp: Erp, sid: str | None) -> dict:
    return (erp.suppliers.get(sid or "") or {}).get("profile") or {}


def _name(erp: Erp, sid: str | None) -> str:
    return _contact(erp, sid).get("name") or sid or "the supplier"


def _desc(erp: Erp, material: str) -> str:
    return (erp.components.get(material) or {}).get("description") or material


def _email(erp: Erp, sid: str | None, action: str, effect: str, body: str, rec=True) -> dict:
    who = _contact(erp, sid)
    greeting = f"Dear {who.get('contact_name')}" if who.get("contact_name") else "Dear team"
    return {
        "action": action,
        "cost": "No cost",
        "effect": effect,
        "channel": "email",
        "to": who.get("contact_email"),
        "recommended": rec,
        "draft": f"{greeting},\n\n{body}\n\nBest regards,\nSoony Procurement",
    }


def _notify(to: str, action: str, effect: str, draft: str, rec=False) -> dict:
    return {
        "action": action,
        "cost": "No cost",
        "effect": effect,
        "channel": "notify",
        "to": to,
        "recommended": rec,
        "draft": draft,
    }


def _buyer(rows: list[dict]) -> str:
    return next((r.get("buyer") for r in rows if r.get("buyer")), "") or "Buyer"


def _impact_text(erp: Erp, material: str, cover: dict) -> str:
    if cover["short"] > 0:
        return (
            f"{fmt(cover['stock'])} in stock + {fmt(cover['due'])} due before launch, minus this, "
            f"leaves {fmt(cover['need'] - cover['short'])} for {fmt(cover['need'])} still to "
            f"pack: {fmt(cover['copies'])} copies short."
        )
    return (
        f"{fmt(cover['need'])} {_desc(erp, material)} still to pack; stock and parts due before "
        "launch cover it even without these."
    )


def _alternatives(erp: Erp, material: str, sid: str | None, need: float) -> list[dict]:
    """Free options: ask every other supplier that lists the part."""
    out: list[dict] = []
    for other, s in erp.suppliers.items():
        if other == sid or material not in s["parts"]:
            continue
        out.append(
            _email(
                erp,
                other,
                f"Ask {_name(erp, other)} for {fmt(need)} {_desc(erp, material)} before launch",
                "covers the gap if they have stock",
                f"we need up to {fmt(need)} {_desc(erp, material)} ({material}) at {erp.site} "
                f"before {erp.launch_at.strftime('%a %-d %b %H:%M')}. Can you deliver from "
                "stock? Please reply with quantity, earliest arrival and price.",
                rec=not out,
            )
        )
    return out


def _offers(docs: list[Doc], erp: Erp) -> dict[str, list[dict]]:
    """Parts a supplier offers from stock in an offer document: material -> offers."""
    out: dict[str, list[dict]] = {}
    for d in docs:
        if d.type != "offer":
            continue
        for start, part in _part_ids(d.text, erp, PartMap(erp)):
            if part not in erp.components:
                continue
            seg = d.text[start : start + 300]
            for desc in _descriptions(erp, part):
                seg = seg.replace(desc, " ")
            nums = _numbers(seg)
            if nums:
                out.setdefault(part, []).append(
                    {"doc": d, "qty": nums[0], "quote": squash(d.text[start : start + 200])}
                )
    return out


def _offer_docs(erp: Erp) -> list[Doc]:
    """Offers carry no PO reference, so they are read separately from the claims."""
    out = []
    for d in documents():
        d.type = classify(d, erp)
        d.supplier = erp.supplier_of_doc(d.rel)
        d.language = language(d.text)
        if d.type == "offer":
            out.append(d)
    return out


# ---- rule (a)


def rule_late_or_short(docs: list[Doc], erp: Erp) -> list[Hit]:
    """
    (a) Arrives after launch / short shipment. A document ships or confirms less than the open
    PO line, or states that a balance arrives after the PO's promised date or after launch,
    while the ERP still counts the full line as due.
    """
    hits = []
    for d in docs:
        for ln in d.lines:
            po, mat = ln.get("po"), ln.get("material")
            if not po or not mat:
                continue
            rows = [r for r in erp.po_lines(po, mat) if is_open(r)]
            if not rows:
                continue
            ordered = sum(qty(r.get("quantity")) or 0 for r in rows)
            promised = min(str(r.get("promised_date") or "")[:10] for r in rows)
            launch_day = erp.launch_at.date().isoformat()
            late, when, what = 0.0, None, ""
            if ln.get("balance") and ln.get("shipped") is not None:
                when = ln.get("balance_arrival")
                if not when or when > promised or when > launch_day:
                    late = ln["balance"]
                    what = (
                        f"{fmt(ln['shipped'])} shipped · {fmt(late)} arrive "
                        f"{fmt_day(when) if when else 'later'}"
                    )
            elif ln.get("confirmed_date") and promised <= launch_day < ln["confirmed_date"]:
                late, when = ln.get("confirmed") or ordered, ln["confirmed_date"]
                what = f"{fmt(late)} confirmed for {fmt_day(when)}"
            elif (
                ln.get("shipped") is not None
                and ln["shipped"] < ordered
                and d.type in ("shipping_advice",)
            ):
                late = ordered - ln["shipped"]
                what = f"{fmt(ln['shipped'])} shipped of {fmt(ordered)}"
            elif (moved := _moved_to(d)) and promised <= launch_day < moved:
                # A delay notice that names a new date but no confirmed quantity per line.
                late, when = ln.get("qty") or ordered, moved
                what = f"{fmt(late)} moved to {fmt_day(when)}"
            if late <= 0:
                continue
            if when and when <= launch_day and when <= promised:
                continue
            cover = erp.cover(mat, minus=late)
            sid = rows[0].get("supplier_id") or d.supplier
            desc = _desc(erp, mat)
            after = bool(when and when > launch_day) or not when
            hits.append(
                Hit(
                    rule="late",
                    po=po,
                    material=mat,
                    supplier=sid,
                    title=f"SAP counts {fmt(ordered)} {desc} from PO {po}; "
                    f"{fmt(late)} arrive {'after launch' if after else 'late'}",
                    summary=(
                        f"{_name(erp, sid)}'s {d.type.replace('_', ' ')} says {fmt(late)} of PO "
                        f"{po} arrive{f' at {erp.site} on ' + fmt_day(when) if when else ' later'}"
                        f"{', after launch' if after else ''}. SAP still shows "
                        f"{fmt(ordered)} due {fmt_day(promised)}."
                    ),
                    system=[_po_side(rows, f"{fmt(ordered)} due {fmt_day(promised)}")],
                    paper=[_late_side(d, what, ln)],
                    docs=[d],
                    cover=cover,
                    units=late,
                    unit_label="units arrive late",
                    headline=f"{fmt(late)} {desc} arrive after launch, but SAP still counts them"
                    if after
                    else f"{fmt(late)} {desc} of PO {po} arrive late",
                    reason="Correct the plan now and cover the gap from another source, or the "
                    "line runs dry before launch."
                    if cover["short"]
                    else "Stock and other deliveries still cover launch; "
                    "correct the plan so the numbers stay true.",
                    options=[
                        *(
                            _alternatives(erp, mat, sid, cover["short"] or late)
                            if cover["short"]
                            else []
                        ),
                        _email(
                            erp,
                            sid,
                            f"Ask {_name(erp, sid)} for an earlier delivery of the {fmt(late)}",
                            "may pull the balance before launch",
                            f"your {d.type.replace('_', ' ')} for PO {po} says {fmt(late)} {desc} "
                            f"({mat}) arrive {fmt_day(when) if when else 'later'}. That is too "
                            "late for our launch. Is any earlier shipment possible, even a part "
                            "of it? Please reply today with quantity and arrival.",
                            rec=not cover["short"],
                        ),
                        _notify(
                            _buyer(rows),
                            "Ask the buyer to correct the PO dates in SAP",
                            "SAP shows the real arrival",
                            f"PO {po} {mat}: {fmt(late)} arrive "
                            f"{fmt_day(when) if when else 'later'} per {Path(d.rel).name}; "
                            f"SAP still says {fmt_day(promised)}.",
                        ),
                    ],
                    arrives_at=when,
                    matched=f"PO {po} {mat}: {fmt(ordered)} open, promised {promised}",
                )
            )
    return hits


def _moved_to(d: Doc) -> str | None:
    """The new arrival date of a delay notice: the latest date its delay statements name, else
    the latest date in the document (the new date often sits in the next sentence)."""
    delays = [st for st in d.statements if st.get("kind") == "delay"]
    if not delays:
        return None
    said = " ".join(f"{st.get('quote') or ''} {st.get('translation') or ''}" for st in delays)
    dates = iso_dates(said) or iso_dates(d.text)
    return max(dates) if dates else None


def _late_side(d: Doc, what: str, ln: dict) -> dict:
    """The document side of a late delivery, with the supplier's own reason (and its
    translation) when the document gives one in another language."""
    why = next(
        (
            s
            for s in d.statements
            if s["kind"] in ("short_shipment", "delay")
            and s.get("translation")
            and language(s["quote"]) != "en"
        ),
        None,
    )
    quote = ln["quote"] + (f" · {why['quote']}" if why and why["quote"] not in ln["quote"] else "")
    side = _doc_side(d, _label(d), what, quote, ln.get("page", 1))
    if why:
        side["language"] = language(why["quote"])
        side["source"] = f"Paper says · {_label(d)} ({side['language'].upper()}/EN)"
        side["translation"] = f"{why['quote']} = {why['translation']}"
    return side


# ---- rule (b)


def _blocked_revs(erp: Erp) -> dict[str, dict]:
    """Material -> the latest engineering change whose old revision must not be used."""
    out: dict[str, dict] = {}
    for e in sorted(erp.ecns, key=lambda e: str(e.get("effective_from") or "")):
        use_up = str(e.get("use_up_old_rev") or "").strip().lower()
        if use_up in ("n", "no", "false", "0"):
            out[e["child_id"]] = e
        elif e["child_id"] in out and use_up:
            out.pop(e["child_id"])
    return out


def rule_old_revision(docs: list[Doc], erp: Erp) -> list[Hit]:
    """
    (b) Old revision confirmed. A document confirms or ships a part to a revision that the latest
    engineering change says must not be packed (use-up of the old revision not allowed).
    Open PO lines are amber unless due before launch; delivered lines still in stock are taken
    out of the cover.
    """
    blocked = _blocked_revs(erp)
    by_key: dict[tuple[str, str], list[tuple[Doc, dict]]] = {}
    for d in docs:
        for s in d.statements:
            mat = s.get("material")
            e = blocked.get(mat or "")
            if s["kind"] != "revision" or not e or s.get("revision") != str(e.get("rev_old")):
                continue
            for po in d.pos:
                if erp.po_lines(po, mat):
                    by_key.setdefault((po, mat), []).append((d, s))
    hits = []
    for (po, mat), found in sorted(by_key.items()):
        e = blocked[mat]
        rows = erp.po_lines(po, mat)
        open_rows = [r for r in rows if is_open(r)]
        grs = {
            g["gr_number"]
            for g in erp.grs
            if g.get("po_number") == po and g.get("material_id") == mat
        }
        in_stock = [
            r
            for r in erp.stock
            if r.get("material_id") == mat
            and r.get("batch") in grs
            and (qty(r.get("quantity")) or 0) > 0
        ]
        stock_qty = sum(qty(r.get("quantity")) or 0 for r in in_stock)
        open_qty = sum(qty(r.get("quantity")) or 0 for r in open_rows)
        if not open_qty and not stock_qty:
            continue
        launch_day = erp.launch_at.date().isoformat()
        due_soon = sum(
            qty(r.get("quantity")) or 0
            for r in open_rows
            if str(r.get("promised_date"))[:10] <= launch_day
        )
        cover = erp.cover(mat, minus=due_soon, exclude={r.get("batch") for r in in_stock})
        sid = rows[0].get("supplier_id")
        old, new = e.get("rev_old"), e.get("rev_new")
        desc = _desc(erp, mat)
        system = []
        if open_rows:
            dates = " / ".join(fmt_day(r.get("promised_date")) for r in open_rows)
            system.append(
                _po_side(open_rows, f"{fmt(open_qty)} due {dates}, {open_rows[0].get('status')}")
            )
        if in_stock:
            system.append(
                {
                    "source": "System says · SAP stock",
                    "value": f"{fmt(stock_qty)} {desc} from PO {po} in stock, usable",
                    "doc": in_stock[0]["_file"],
                    "quote": " ; ".join(
                        f"{r['material_id']} · batch {r.get('batch')} · "
                        f"{r.get('quantity')} {r.get('uom') or ''}".strip()
                        for r in in_stock
                    ),
                    "row": in_stock[0].get("_row"),
                }
            )
        system.append(
            {
                "source": f"Engineering change {e.get('change_id') or ''}".strip(),
                "value": f"rev {old} to rev {new}, rev {old} must not be packed",
                "doc": e["_file"],
                "quote": f"{e.get('change_id')} · {mat} · rev {old} to {new} · {e.get('reason')}",
                "row": e.get("_row"),
            }
        )
        paper = []
        seen = set()
        for d, s in found:
            if d.rel in seen:
                continue
            seen.add(d.rel)
            lines = [ln for ln in d.lines if ln.get("material") == mat]
            q = " / ".join(
                fmt(ln.get("confirmed") or ln.get("shipped") or ln.get("qty")) for ln in lines
            )
            paper.append(
                _doc_side(
                    d,
                    _label(d),
                    f"rev. {old}" + (f" · {q} pcs" if q else ""),
                    s["quote"],
                    s.get("page", 1),
                    s.get("translation"),
                )
            )
        units = stock_qty + open_qty
        what = (
            (f"{fmt(stock_qty)} in stock" if stock_qty else "")
            + (" and " if stock_qty and open_qty else "")
            + (f"{fmt(open_qty)} on order" if open_qty else "")
        )
        new_file = _new_artwork(docs, mat, str(new))
        hits.append(
            Hit(
                rule="rev",
                po=po,
                material=mat,
                supplier=sid,
                title=f"{_name(erp, sid)} confirmed PO {po} with the old {desc} (rev {old})",
                summary=(
                    f"{e.get('change_id')} moved {mat} from rev {old} to rev {new} "
                    f"({e.get('reason')}). {_name(erp, sid)}'s paperwork for PO {po} still says "
                    f"rev {old}: {what}."
                ),
                system=system,
                paper=paper,
                docs=[d for d, _ in found],
                cover=cover,
                units=units,
                unit_label="units to the old revision",
                headline=f"{fmt(units)} {desc} on PO {po} are rev {old}, which must not be packed",
                reason=(
                    f"Parts to rev {old} cannot be packed. "
                    + ("Block the batch in stock so nobody packs it. " if stock_qty else "")
                    + (
                        "Open lines are still free to fix before printing starts."
                        if open_qty
                        else ""
                    )
                ).strip(),
                options=[
                    *(
                        [
                            _email(
                                erp,
                                sid,
                                f"Ask {_name(erp, sid)} to make PO {po} to rev {new}",
                                f"{fmt(open_qty)} usable",
                                f"your paperwork for PO {po} confirms "
                                f"{desc} ({mat}) to rev. {old}. Our part changed to rev. {new} "
                                f"({e.get('change_id')}); parts to rev. {old} cannot be packed.\n\n"
                                f"Please produce PO {po} to rev. {new}"
                                + (f" ({new_file})" if new_file else "")
                                + " and send a corrected order confirmation. If you do not "
                                "have the file, reply and we will send it today.",
                            )
                        ]
                        if open_qty
                        else []
                    ),
                    *(
                        [
                            _notify(
                                "DC receiving",
                                f"Block batch {', '.join(r.get('batch') or '' for r in in_stock)}",
                                "nobody packs the old revision",
                                f"Please block {fmt(stock_qty)} {desc} ({mat}) from PO {po}: "
                                f"rev {old}, which {e.get('change_id')} says must not be packed.",
                                rec=not open_qty,
                            )
                        ]
                        if in_stock
                        else []
                    ),
                    _notify(
                        _buyer(rows),
                        "Tell the buyer",
                        f"the buyer follows up with {_name(erp, sid)}",
                        f"{_name(erp, sid)} confirmed PO {po} "
                        f"({fmt(units)} {desc}) with rev {old}; {e.get('change_id')} "
                        f"requires rev {new}.",
                    ),
                ],
                matched=f"PO {po} {mat}: "
                + (
                    "; ".join(
                        f"line {r.get('po_line')} {r.get('quantity')} {r.get('status')}"
                        for r in rows
                    )
                ),
            )
        )
    return hits


def _new_artwork(docs: list[Doc], material: str, rev: str) -> str | None:
    """A file name for the new revision that another document already names, if any."""
    for d in docs:
        for s in d.statements:
            if (
                s["kind"] == "revision"
                and s.get("material") == material
                and s.get("revision") == rev
            ):
                m = re.search(r"[\w-]*rev" + re.escape(rev) + r"[\w-]*\.\w{3}", s["quote"], re.I)
                if m:
                    return m.group(0)
    return None


# ---- rule (c)


def rule_receipt_booked_wrong(docs: list[Doc], erp: Erp) -> list[Hit]:
    """
    (c) Stock booked wrong. The quantity on a delivery note (after handwritten corrections, read
    by the vision model) differs from the goods receipt booked against it. Without a reading of
    the note, a receipt that is exactly 10x or 1/10 of the ordered quantity is flagged as a
    probable typo, with the note as the document to check.
    """
    batches = {r.get("batch") for r in erp.stock}
    hits = []
    covered: set[str] = set()
    for d in docs:
        for ln in d.lines:
            if ln.get("received") is None or not d.dns:
                continue
            grs = [
                g
                for g in erp.grs
                if g.get("delivery_note") in d.dns and g.get("material_id") == ln.get("material")
            ]
            for g in grs:
                booked = qty(g.get("qty_received"))
                ratio = booked / ln["received"] if booked and ln["received"] else 1
                if not ln.get("corrected") and ratio in (1000, 0.001):
                    covered.add(g["gr_number"])  # a thousands separator misread, not a mismatch
                    continue
                if booked is None or booked == ln["received"]:
                    covered.add(g["gr_number"])
                    continue
                hits.append(_receipt_hit(erp, d, g, booked, ln["received"], ln))
                covered.add(g["gr_number"])
    for g in erp.grs:
        if g["gr_number"] in covered or g["gr_number"] not in batches:
            continue
        ordered, booked = qty(g.get("qty_ordered")), qty(g.get("qty_received"))
        if not ordered or not booked or booked / ordered not in (10, 100, 0.1, 0.01):
            continue
        d = next((x for x in docs if g.get("delivery_note") in x.dns), None)
        if d:
            hits.append(_receipt_hit(erp, d, g, booked, ordered, None))
    return hits


def _receipt_hit(erp: Erp, d: Doc, g: dict, booked: float, paper: float, ln: dict | None) -> Hit:
    mat, po, gr = g["material_id"], g.get("po_number") or "", g["gr_number"]
    ordered = qty(g.get("qty_ordered"))
    in_stock = [r for r in erp.stock if r.get("batch") == gr and r.get("material_id") == mat]
    stock_qty = sum(qty(r.get("quantity")) or 0 for r in in_stock)
    excess = booked - paper
    system_total = erp.stock_of(mat)
    real = system_total - excess
    cover = erp.cover(mat, minus=excess)
    desc = _desc(erp, mat)
    dn = g.get("delivery_note")
    if ln:
        value = f"{fmt(paper)} delivered, not {fmt(booked)}"
        quote = ln["quote"]
        translation = ln.get("translation")
    else:
        value = f"check: GR is {fmt(booked)} against {fmt(paper)} ordered"
        quote = squash(f"{dn} · {po} · {mat}")
        translation = None
    sid = g.get("supplier_id")
    rows = erp.po_lines(po, mat) if po else []
    price = qty((rows[0] if rows else {}).get("unit_price"))
    currency = (rows[0] if rows else {}).get("currency") or ""
    options = []
    if cover["short"]:
        for o in erp_offers(erp).get(mat, []):
            osid = o["doc"].supplier
            cost = f"about {currency} {fmt(o['qty'] * price)}" if price else "per the offer"
            options.append(
                _email(
                    erp,
                    osid,
                    f"Order {_name(erp, osid)}'s {fmt(o['qty'])} {desc} from stock",
                    f"covers {fmt(min(o['qty'], cover['short']))} of the {fmt(cover['short'])} gap",
                    f"thank you for your offer ({Path(o['doc'].rel).name}). We would like to order "
                    f"the {fmt(o['qty'])} {desc} ({mat}) from your stock, delivered to {erp.site}."
                    "\n\nPlease load them on the next truck and send us the truck details and the "
                    "delivery note number.",
                )
            )
            options[-1]["cost"] = cost
        options += _alternatives(erp, mat, sid, cover["short"]) if not options else []
    options.append(
        _notify(
            g.get("inspector") or "DC receiving",
            f"Ask receiving to correct goods receipt {gr} to {fmt(paper)}",
            "SAP shows the real stock again",
            f"Please check goods receipt {gr} (PO {po} line {g.get('po_line')}): booked "
            f"{fmt(booked)}, delivery note {dn} says {fmt(paper)}. Correct it if the shelf agrees.",
            rec=not options,
        )
    )
    return Hit(
        rule="gr",
        po=po,
        material=mat,
        supplier=sid,
        title=f"SAP counts {fmt(system_total)} {desc}; the shelf holds {fmt(real)}",
        summary=(
            f"Goods receipt {gr} booked {fmt(booked)} for PO {po} line {g.get('po_line')} "
            f"(ordered {fmt(qty(g.get('qty_ordered')))}, remark '{g.get('remark') or ''}'). "
            f"Delivery note {dn} says {fmt(paper)}"
            + (" (corrected by hand)." if ln and ln.get("corrected") else ".")
        ),
        system=[
            {
                "source": "System says · SAP goods receipt",
                "value": f"{fmt(booked)} received on GR {gr} (ordered {fmt(ordered)})",
                "doc": g["_file"],
                "row": g.get("_row"),
                "quote": f"GR {gr} · PO {po} line {g.get('po_line')} · {mat} · ordered "
                f"{g.get('qty_ordered')} · received {g.get('qty_received')} · {dn} · "
                f"{g.get('remark') or ''}".strip(" ·"),
            },
            *(
                [
                    {
                        "source": "System says · SAP stock",
                        "value": f"{fmt(system_total)} {desc} in stock on {erp.snapshot}",
                        "doc": in_stock[0]["_file"],
                        "row": in_stock[0].get("_row"),
                        "quote": f"{mat} · batch {gr} · {fmt(stock_qty).replace(',', '')} "
                        f"{in_stock[0].get('uom') or ''}".strip(),
                    }
                ]
                if in_stock
                else []
            ),
        ],
        paper=[_doc_side(d, f"delivery note {dn}", value, quote, 1, translation)],
        docs=[d],
        cover=cover,
        units=abs(excess),
        unit_label="units booked but not there" if excess > 0 else "units not booked",
        headline=(
            f"We have {fmt(real)} {desc}, not {fmt(system_total)}."
            + (f" {fmt(cover['copies'])} copies short." if cover["short"] else "")
        ),
        reason=(
            "SAP shows stock that is not on the shelf, so the gap stays hidden until the "
            "cell runs dry."
            if excess > 0
            else "SAP shows less than was delivered."
        ),
        options=options,
        matched=f"GR {gr} for {dn}: {fmt(booked)} booked",
    )


_OFFERS: dict[str, Any] = {}


def erp_offers(erp: Erp) -> dict[str, list[dict]]:
    key = str(erp.root)
    if key not in _OFFERS:
        _OFFERS[key] = _offers(_offer_docs(erp), erp)
    return _OFFERS[key]


# ---- rule (d)


def rule_failed_lot(docs: list[Doc], erp: Erp) -> list[Hit]:
    """
    (d) Failed lot counted as stock. An inspection document says the lot of a goods receipt
    failed (does not meet AQL, rejected) while the stock snapshot still holds that batch as
    usable stock (no blocked or quality-inspection stock type).
    """
    hits = []
    for d in docs:
        failed = sorted(
            (s for s in d.statements if s["kind"] == "failed_inspection"),
            key=lambda s: (
                not re.search(r"does not meet|not meet aql|rejected", s["quote"], re.I),
                len(s["quote"]) > 250,
            ),
        )
        if not failed or not d.grs:
            continue
        for gr in d.grs:
            g = next((x for x in erp.grs if x["gr_number"] == gr), None)
            rows = [r for r in erp.stock if r.get("batch") == gr and (qty(r.get("quantity")) or 0)]
            rows = [
                r
                for r in rows
                if not re.search(
                    r"block|quality|qi|restricted", col(r, "stock_type", "status", "type"), re.I
                )
            ]
            if not g or not rows:
                continue
            mat = g["material_id"]
            lot = sum(qty(r.get("quantity")) or 0 for r in rows)
            cover = erp.cover(mat, exclude={gr})
            desc = _desc(erp, mat)
            sid = g.get("supplier_id")
            po = g.get("po_number") or ""
            strong = [s for s in failed if len(s["quote"]) <= 250] or failed[:1]
            quote = " ".join(
                dict.fromkeys(
                    s["quote"]
                    for s in strong
                    + [
                        s
                        for s in d.statements
                        if s["kind"] == "unrestricted_stock" and len(s["quote"]) <= 250
                    ]
                )
            )[:400]
            hits.append(
                Hit(
                    rule="qa",
                    po=po,
                    material=mat,
                    supplier=sid,
                    title=f"{fmt(lot)} {desc} in stock failed incoming inspection",
                    summary=(
                        f"Goods receipt {gr} (PO {po}) put {fmt(lot)} {desc} in stock as "
                        f"usable, but the inspection report rejects the lot. Without them "
                        f"{fmt(cover['stock'])} are usable for {fmt(cover['need'])} still to pack."
                    ),
                    system=[
                        {
                            "source": "System says · SAP stock",
                            "value": f"{fmt(lot)} on GR {gr}, {fmt(erp.stock_of(mat))} in stock",
                            "doc": rows[0]["_file"],
                            "row": rows[0].get("_row"),
                            "quote": f"{mat} · batch {gr} · {rows[0].get('quantity')} "
                            f"{rows[0].get('uom') or ''}".strip(),
                        }
                    ],
                    paper=[
                        _doc_side(
                            d,
                            _label(d),
                            f"GR {gr} · lot rejected",
                            f"Goods receipt {gr} · {mat} · {quote}",
                            failed[0].get("page", 1),
                            failed[0].get("translation"),
                        )
                    ],
                    docs=[d],
                    cover=cover,
                    units=lot,
                    unit_label="units failed inspection",
                    headline=f"{fmt(lot)} {desc} on the shelf failed inspection."
                    + (
                        f" Without them we are {fmt(cover['copies'])} copies short."
                        if cover["short"]
                        else ""
                    ),
                    reason="The lot may only be used under a concession; until then it must not be "
                    "counted. A 100% sort could free the good parts today.",
                    options=[
                        _notify(
                            f"Supplier Quality, {_buyer(erp.po_lines(po))}",
                            "Ask Supplier Quality for a concession or a 100% sort today",
                            "the good parts of the lot can be packed",
                            f"Goods receipt {gr} (PO {po}, {fmt(lot)} {desc} {mat}) failed "
                            "incoming inspection but is booked as usable stock."
                            + (
                                f" Without it we are {fmt(cover['copies'])} copies short."
                                if cover["short"]
                                else ""
                            )
                            + " Please decide today: a concession or a 100% sort.",
                            rec=True,
                        ),
                        _email(
                            erp,
                            sid,
                            f"Tell {_name(erp, sid)} the lot failed and ask for replacements",
                            "replacement parts, if they can ship in time",
                            f"goods receipt {gr} for PO {po} ({fmt(lot)} {desc}, {mat}) failed "
                            "our incoming inspection. Can you send replacement parts, and how "
                            "fast? Please reply today.",
                            rec=False,
                        ),
                    ],
                    matched=f"stock batch {gr}: {fmt(lot)} {mat}",
                )
            )
    return hits


# ---- rule (e)


def rule_held_at_supplier(docs: list[Doc], erp: Erp) -> list[Hit]:
    """
    (e) Held at supplier. A document says the goods are on hold, refused or suspended (for
    example an incoterm the supplier does not accept), while the ERP shows the PO open and,
    where a shipment row exists, booked or moving.
    """
    hits = []
    for d in docs:
        held = [s for s in d.statements if s["kind"] in ("on_hold", "refused_terms", "suspended")]
        if not held:
            continue
        for po in d.pos:
            rows = [r for r in erp.po_lines(po) if is_open(r)]
            if not rows:
                continue
            launch_parts = [r for r in rows if erp.usage(r["material_id"])] or rows
            main = launch_parts[0]
            mat = main["material_id"]
            launch_day = erp.launch_at.date().isoformat()
            due_soon = sum(
                qty(r.get("quantity")) or 0
                for r in rows
                if r["material_id"] == mat and str(r.get("promised_date"))[:10] <= launch_day
            )
            cover = erp.cover(mat, minus=due_soon)
            ships = [s for s in erp.shipments if po in str(s.get("po_numbers") or "").split("|")]
            sid = main.get("supplier_id") or d.supplier
            total = sum(qty(r.get("quantity")) or 0 for r in rows)
            terms = sorted({r.get("incoterm") for r in rows if r.get("incoterm")})
            system = [
                _po_side(rows, f"{fmt(total)} open" + (f" · {', '.join(terms)}" if terms else ""))
            ]
            for s in ships:
                system.append(
                    {
                        "source": "System says · open shipments",
                        "value": f"{s.get('shipment_id')} {s.get('status')}, ETA {s.get('eta')}",
                        "doc": s["_file"],
                        "row": s.get("_row"),
                        "quote": f"{s.get('shipment_id')} · {s.get('reference')} · PO "
                        f"{s.get('po_numbers')} · {s.get('material_ids')} · ETA "
                        f"{s.get('eta')} · {s.get('status')}",
                    }
                )
            quote = f"PO {po} · " + " ".join(dict.fromkeys(s["quote"] for s in held))[:400]
            hits.append(
                Hit(
                    rule="hold",
                    po=po,
                    material=mat,
                    supplier=sid,
                    title=f"{_name(erp, sid)} holds PO {po}; SAP shows it "
                    + ("on its way" if ships else "open and on time"),
                    summary=(
                        f"{_name(erp, sid)}'s {d.type.replace('_', ' ')} says the goods for PO "
                        f'{po} are held: "{held[0]["quote"][:160]}". SAP shows {fmt(total)} '
                        "open"
                        + (
                            f" and shipment {ships[0].get('shipment_id')} {ships[0].get('status')}"
                            if ships
                            else ""
                        )
                        + "."
                    ),
                    system=system,
                    paper=[
                        _doc_side(
                            d,
                            _label(d),
                            "on hold at the supplier",
                            quote,
                            held[0].get("page", 1),
                            held[0].get("translation"),
                        )
                    ],
                    docs=[d],
                    cover=cover,
                    units=total,
                    unit_label="units held",
                    headline=f"{fmt(total)} parts on PO {po} are held at {_name(erp, sid)}",
                    reason="Nothing ships until someone answers the supplier. It costs nothing to "
                    "ask today.",
                    options=[
                        _email(
                            erp,
                            sid,
                            f"Ask {_name(erp, sid)} to release PO {po}",
                            "the goods ship on the next flight or truck",
                            f"your paperwork for PO {po} says the goods are on hold: "
                            f'"{held[0]["quote"][:200]}"\n\nPlease tell us exactly what you '
                            "need from us to release the shipment today. We will confirm in "
                            "writing as soon as procurement has agreed.",
                            rec=True,
                        ),
                        _notify(
                            _buyer(rows),
                            "Ask the buyer to settle the terms",
                            "the buyer decides who pays the freight",
                            f"{_name(erp, sid)} holds PO {po} ({fmt(total)} parts): "
                            f"{held[0]['quote'][:200]}"
                            + (f" SAP says {', '.join(terms)}." if terms else ""),
                        ),
                    ],
                    matched=f"PO {po}: {fmt(total)} open"
                    + (
                        f", shipment {ships[0].get('shipment_id')} {ships[0].get('status')}"
                        if ships
                        else ""
                    ),
                )
            )
    return hits


RULES: list[Callable[[list[Doc], Erp], list[Hit]]] = [
    rule_late_or_short,
    rule_old_revision,
    rule_receipt_booked_wrong,
    rule_failed_lot,
    rule_held_at_supplier,
]


# ---------------------------------------------------------------- cards


def finding_id(h: Hit) -> str:
    """Stable ids: paper-<po>-<part> for late/short (compatible with earlier cards), else
    paper-<po>-<part>-<rule>."""
    base = f"paper-{h.po or 'nopo'}-{h.material}"
    return base if h.rule == "late" else f"{base}-{h.rule}"


def _mtime(root: Path, rel: str) -> datetime:
    try:
        return datetime.fromtimestamp((root / rel).stat().st_mtime, api.tz())
    except OSError:
        return api.now()


def _deadline(erp: Erp, cur: datetime, red: bool) -> str:
    soon = (cur + timedelta(hours=1 if red else 3)).replace(minute=0, second=0, microsecond=0)
    soon += timedelta(hours=1)
    return min(soon, erp.launch_at).isoformat() if erp.launch_at > cur else soon.isoformat()


def card(h: Hit, erp: Erp, cur: datetime) -> tuple[dict, dict, dict]:
    fid = finding_id(h)
    red = h.cover["short"] > 0
    level = "red" if red else "amber"
    steps = []
    for d in h.docs:
        at = min(_mtime(erp.root, d.rel), cur)
        steps.append(
            {
                "agent": "Watcher",
                "at": at.isoformat(timespec="seconds"),
                "text": f"Found {Path(d.rel).name} in {Path(d.rel).parent}.",
            }
        )
    t = max([datetime.fromisoformat(s["at"]) for s in steps] + [cur - timedelta(minutes=3)])
    read = ", ".join(sorted({d.llm or "parsed" for d in h.docs}))
    steps.append(
        {
            "agent": "Paper check",
            "at": (t + timedelta(minutes=1)).isoformat(timespec="seconds"),
            "text": f"Read the document ({read}); matched {h.matched}.",
        }
    )
    steps.append(
        {
            "agent": "Impact",
            "at": (t + timedelta(minutes=2)).isoformat(timespec="seconds"),
            "text": _impact_text(erp, h.material, h.cover),
        }
    )
    finding = {
        "id": fid,
        "origin": ORIGIN,
        "rule": h.rule,
        "kind": {
            "late": "mismatch",
            "rev": "revision",
            "gr": "mismatch",
            "qa": "quality",
            "hold": "mismatch",
        }[h.rule],
        "part": h.material,
        "supplier": h.supplier,
        "title": h.title,
        "summary": h.summary,
        "status": "open",
        "level": level,
        "created_at": cur.isoformat(timespec="seconds"),
        "sides": h.system + h.paper,
        "trail": steps,
    }
    if h.arrives_at:
        finding["arrives_at"] = h.arrives_at
    decision = {
        "finding_id": fid,
        "level": level,
        "headline": h.headline,
        "reason": h.reason,
        "deadline": _deadline(erp, cur, red),
        "options": h.options,
    }
    impact: dict[str, Any] = {
        "finding_id": fid,
        "deadline": decision["deadline"],
        "edition": h.cover["edition"],
    }
    if red:
        impact["copies_at_risk"] = h.cover["copies"]
    else:
        impact["units_short"] = h.units
    impact["unit_label"] = h.unit_label
    return finding, decision, impact


def build(erp: Erp, llm: Llm | None, cur: datetime) -> tuple[list[tuple[dict, dict, dict]], list]:
    docs = claims(erp, llm)
    cards: dict[str, tuple[dict, dict, dict]] = {}
    for rule in RULES:
        try:
            hits = rule(docs, erp)
        except Exception as e:  # one broken rule must not hide the others
            log(f"{rule.__name__} failed: {e!r}")
            continue
        for h in hits:
            c = card(h, erp, cur)
            fid = c[0]["id"]
            if fid in cards:  # same contradiction in two documents: one card, both sides
                old = cards[fid][0]
                docs_seen = {s.get("doc") for s in old["sides"]}
                old["sides"] += [s for s in c[0]["sides"] if s.get("doc") not in docs_seen]
                continue
            cards[fid] = c
    return list(cards.values()), docs


# ---------------------------------------------------------------- state


def _merge_finding(old: dict | None, new: dict) -> dict:
    if not old:
        return new
    out = dict(new)
    out["created_at"] = old.get("created_at") or new["created_at"]
    if old.get("status") and old.get("status") != "open":
        out["status"] = old["status"]
    mine = {s.get("source") for s in new["sides"]}
    out["sides"] = new["sides"] + [
        s
        for s in old.get("sides") or []
        if s.get("source") not in mine and s.get("doc") not in {x.get("doc") for x in new["sides"]}
    ]
    foreign = [t for t in old.get("trail") or [] if t.get("agent") not in AGENTS]
    out["trail"] = sorted(new["trail"] + foreign, key=lambda t: str(t.get("at") or ""))
    return out


def write_state(
    cards: list[tuple[dict, dict, dict]], cur: datetime, blind: list[str] | None = None
) -> dict:
    """Merge by id: other entries stay, decision status (decision_status.json) is never touched,
    and papercheck cards whose contradiction is gone are marked resolved, unless an input file
    could not be read (`blind`: a renamed column, say): then a vanished contradiction is no
    proof, and the cards stay open until the data check is clean again."""
    findings = api.read_list("findings.json")
    by_id = {f.get("id"): f for f in findings}
    new_ids = {f["id"] for f, _, _ in cards}
    out, done = [], set()
    for f in findings:
        fid = f.get("id")
        new = next((c[0] for c in cards if c[0]["id"] == fid), None)
        if new:
            out.append(_merge_finding(f, new))
            done.add(fid)
        elif f.get("origin") == ORIGIN and f.get("status") == "open" and not blind:
            f = {
                **f,
                "status": "resolved",
                "trail": (f.get("trail") or [])
                + [
                    {
                        "agent": "Paper check",
                        "at": cur.isoformat(timespec="seconds"),
                        "text": "The documents no longer contradict the ERP.",
                    }
                ],
            }
            out.append(f)
        else:
            out.append(f)
    out += [_merge_finding(by_id.get(f["id"]), f) for f, _, _ in cards if f["id"] not in done]
    api.write_json("findings.json", out)

    def merge(name: str, items: list[dict]) -> None:
        raw = api.read_json(name, [])
        if isinstance(raw, dict):  # another agent keeps this file keyed by event: leave it
            log(f"{name} is not a list; not touched")
            return
        old = {e.get("finding_id"): e for e in raw if isinstance(e, dict)}
        mine = {i["finding_id"] for i in items}
        merged = [e for e in raw if isinstance(e, dict) and e.get("finding_id") not in mine]
        merged += [{**old.get(i["finding_id"], {}), **i} for i in items]
        api.write_json(name, merged)

    merge("decisions.json", [d for _, d, _ in cards])
    merge("impacts.json", [i for _, _, i in cards])
    return {"written": len(new_ids)}


def make_llm(enabled: bool | None = None) -> Llm | None:
    from ask_soony.config import load_dotenv

    load_dotenv()
    if enabled is None:
        enabled = os.environ.get("ASK_PAPERCHECK_LLM", "on").lower() not in ("0", "off", "no")
    if not enabled:
        return None
    return Llm(
        key=os.environ.get("OPENAI_API_KEY", "").strip(),
        model=os.environ.get("ASK_PAPERCHECK_MODEL", DEFAULT_MODEL),
        cache=Path(os.environ.get("ASK_DATA_DIR", "data/store")) / "papercheck",
        max_calls=int(os.environ.get("ASK_PAPERCHECK_MAX_CALLS", "60")),
    )


def run(write: bool = True, llm: bool | None = None, verbose: bool = True) -> list[tuple]:
    """Read the documents, run the rules and (with write) merge the cards into the state."""
    root = api.portal_root()
    if root is None:
        log("no portal data found (set ASK_PORTAL_DIR)")
        return []
    cur = api.now()
    READS.clear()
    erp = load_erp(root)
    # Inputs read before and unreadable now (a renamed column): files never read don't count.
    ever = api.read_json("papercheck_reads.json", {}) or {}
    blind = sorted(r for r, v in READS.items() if not v["read"] and v["wanted"] and ever.get(r))
    if write:
        api.write_json(
            "papercheck_reads.json", {**ever, **{r: True for r, v in READS.items() if v["read"]}}
        )
    _OFFERS.pop(str(root), None)
    if verbose and erp.revisions:
        moved = [c for c in erp.revisions if c["field"] == "promised_date"]
        late = [c for c in moved if c["old"][:10] <= erp.launch_at.date().isoformat() < c["new"]]
        log(
            f"newer PO export changed {len(erp.revisions)} values on "
            f"{len({(c['po'], c['line']) for c in erp.revisions})} PO lines "
            f"({len(moved)} promised dates, {len(late)} of them moved past launch); "
            "the newer values are used, the old ones kept as evidence"
        )
    model = make_llm(llm)
    cards, docs = build(erp, model, cur)
    if write:
        write_state(cards, cur, blind)
    if blind:
        log(f"not read (column changed): {', '.join(blind)}; open cards are kept open")
    if verbose:
        log(f"{len(docs)} documents with references, {len(cards)} contradictions")
        if model:
            log(
                f"LLM: {model.calls} calls, {model.cached} cached, {model.tokens_in} in / "
                f"{model.tokens_out} out tokens, about USD {model.cost:.4f} ({model.model})"
                + ("" if model.key else "; no OPENAI_API_KEY, cached answers only")
            )
    return cards


# ---------------------------------------------------------------- background


def _signature(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            h.update(f"{p}:{st.st_size}:{st.st_mtime_ns}".encode())
    return h.hexdigest()


def loop(interval: int | None = None) -> None:
    """Run at start and again whenever the portal mirror changes (checked every interval)."""
    every = interval or int(os.environ.get("ASK_PAPERCHECK_INTERVAL", "60"))
    last = None
    while True:
        root = api.portal_root()
        sig = _signature(root) if root else None
        if sig and sig != last:
            try:
                anchors.build_index()
                run(write=True)
                from ask_soony import datacheck

                datacheck.check(root)
                last = sig
            except Exception as e:  # keep the loop alive
                log(f"run failed: {e!r}")
        time.sleep(every)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Hold supplier documents against the ERP.")
    ap.add_argument("--dry-run", action="store_true", help="print, do not write the state")
    ap.add_argument("--no-llm", action="store_true", help="deterministic extraction only")
    args = ap.parse_args(argv)
    cards = run(write=not args.dry_run, llm=False if args.no_llm else None)
    for f, d, i in cards:
        n = i.get("copies_at_risk", i.get("units_short"))
        print(f"{f['level']:5} {f['id']}: {d['headline']} [{n}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
