"""
Column mapping: read a procurement export by what its columns mean, not by their exact names.

Exports come from SAP (EBELN, MATNR), from German Excel (Bestellnummer, Menge) and from English
tools (po_number, qty). This module maps every header to one canonical field name with a synonym
table (domain knowledge, no data values), matched without regard to case, accents, spaces or
punctuation. Headers the table does not know can be put to one cached model call; its answer is
used only where it says it is sure, and is recorded as coming from the model.

`canonical_rows(path)` returns the rows of a CSV with the canonical keys added next to the
original ones (originals are never changed). Only the canonical copies are normalised: decimal
commas to dots (decided per file from the separator and the values), German dates to ISO, SAP item
numbers in steps of ten to plain line numbers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from ask_soony import api

PROMPT_VERSION = "columns-1"

# Canonical field → what it means (shown to the model) and its synonyms (EN, DE, SAP).
FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "po_number": (
        "purchase order number",
        (
            "po_number",
            "po",
            "po no",
            "po nr",
            "purchase order",
            "order no",
            "order number",
            "customer po",
            "your order",
            "Bestellnummer",
            "Bestellung",
            "Bestell-Nr",
            "EBELN",
        ),
    ),
    "po_line": (
        "line or item number within the purchase order",
        (
            "po_line",
            "po item",
            "item",
            "item no",
            "line no",
            "line item",
            "Pos",
            "Position",
            "Bestellposition",
            "EBELP",
        ),
    ),
    "supplier_id": (
        "supplier or vendor id",
        (
            "supplier_id",
            "supplier",
            "vendor",
            "vendor id",
            "vendor no",
            "Lieferant",
            "Lieferantennummer",
            "Kreditor",
            "LIFNR",
        ),
    ),
    "material_id": (
        "our material or part number",
        (
            "material_id",
            "material",
            "material no",
            "part no",
            "part number",
            "part_id",
            "Materialnummer",
            "Artikel",
            "Artikelnummer",
            "MATNR",
        ),
    ),
    "plant": (
        "plant, site or warehouse receiving the goods",
        ("plant", "site", "Werk", "Lagerort", "WERKS"),
    ),
    "quantity": (
        "ordered or stocked quantity",
        ("quantity", "qty", "order qty", "Menge", "Bestellmenge", "MENGE"),
    ),
    "unit_price": (
        "price per unit",
        ("unit_price", "price", "net price", "Preis", "Nettopreis", "Einzelpreis", "NETPR"),
    ),
    "currency": ("currency code", ("currency", "curr", "Waehrung", "Währung", "WAERS")),
    "order_date": (
        "date the order was placed",
        ("order_date", "po date", "Bestelldatum", "BEDAT"),
    ),
    "requested_date": (
        "delivery date the buyer asked for",
        ("requested_date", "requested delivery", "need date", "Wunschtermin", "EINDT"),
    ),
    "confirmed_date": (
        "delivery date the supplier confirmed or promised (the latest one)",
        (
            "confirmed_date",
            "promised_date",
            "confirmed delivery",
            "confirmed_delivery_dt",
            "confirmed_new",
            "new confirmed date",
            "bestaetigter Termin",
            "AB-Termin",
            "ZZCONF",
        ),
    ),
    "previous_date": (
        "previously confirmed delivery date, before a revision",
        ("previous_date", "confirmed_old", "old confirmed date", "alter Termin"),
    ),
    "delivered_date": (
        "date the goods were actually delivered",
        (
            "delivered_date",
            "actual_delivery_date",
            "delivery date actual",
            "Wareneingangsdatum",
            "WEDAT",
        ),
    ),
    "status": ("line status", ("status", "Status", "ZZSTAT")),
    "buyer": (
        "buyer or person who created the order",
        ("buyer", "purchaser", "Einkäufer", "Einkaeufer", "Sachbearbeiter", "ERNAM"),
    ),
    "note": ("free-text remark", ("note", "remark", "comment", "Bemerkung", "Kommentar")),
    "incoterm": ("incoterm", ("incoterm", "incoterms", "Lieferbedingung", "INCO1")),
    "gr_number": (
        "goods receipt number",
        ("gr_number", "goods receipt", "Wareneingang", "WE-Nummer", "MBLNR"),
    ),
    "gr_date": ("goods receipt date", ("gr_date", "receipt date", "Buchungsdatum", "BUDAT")),
    "qty_ordered": ("quantity ordered on a receipt", ("qty_ordered", "ordered qty")),
    "qty_received": (
        "quantity received on a receipt",
        ("qty_received", "received qty", "Liefermenge", "erhaltene Menge"),
    ),
    "delivery_note": (
        "supplier delivery note number",
        ("delivery_note", "delivery note no", "Lieferschein", "Lieferscheinnummer", "XBLNR"),
    ),
    "snapshot_date": ("date of a stock snapshot", ("snapshot_date", "Stichtag")),
    "batch": ("batch or lot", ("batch", "lot", "Charge", "CHARG")),
    "shipment_id": ("shipment id", ("shipment_id", "shipment", "Sendung", "Sendungsnummer")),
    "eta": ("estimated arrival", ("eta", "estimated arrival", "Ankunft")),
}

# Canonical columns a file needs to be read as each kind. The most specific match wins.
LAYOUTS: dict[str, frozenset[str]] = {
    "purchase_orders": frozenset(
        {"po_number", "po_line", "supplier_id", "material_id", "quantity"}
    ),
    "confirmed_schedule": frozenset(
        {"po_number", "po_line", "material_id", "confirmed_date", "previous_date"}
    ),
    "goods_receipts": frozenset({"gr_number", "po_number", "material_id", "qty_received"}),
    "stock": frozenset({"snapshot_date", "material_id", "quantity"}),
    "shipments": frozenset({"shipment_id", "eta"}),
}
IDENTIFIERS = {"po_number", "gr_number", "shipment_id"}
LABELS = {
    "purchase_orders": "purchase orders",
    "confirmed_schedule": "a schedule of confirmed dates",
    "goods_receipts": "goods receipts",
    "stock": "stock",
    "shipments": "shipments",
}

DATE_FIELDS = {
    "order_date", "requested_date", "confirmed_date", "previous_date", "delivered_date",
    "gr_date", "snapshot_date", "eta",
}  # fmt: skip
NUMBER_FIELDS = {"quantity", "unit_price", "qty_ordered", "qty_received"}
LINE_FIELDS = {"po_line"}


def key(name: str) -> str:
    """'Einkäufer' → 'einkaeufer', 'PO No.' → 'pono': compare headers by letters and digits."""
    s = str(name or "").strip().lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s)
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in s if not unicodedata.combining(c)))


SYNONYMS: dict[str, str] = {}
for _field, (_, _names) in FIELDS.items():
    for _n in (_field, *_names):
        SYNONYMS.setdefault(key(_n), _field)


@dataclass
class Mapping:
    columns: dict[str, str] = field(default_factory=dict)  # original header → canonical field
    model: list[str] = field(default_factory=list)  # originals mapped by the model
    unknown: list[str] = field(default_factory=list)

    @property
    def fields(self) -> set[str]:
        return set(self.columns.values())

    @property
    def kind(self) -> str | None:
        return kind_of(self.fields)

    @property
    def renamed(self) -> dict[str, str]:
        """Only the columns whose name differs from their canonical field."""
        return {o: c for o, c in self.columns.items() if o != c}

    @property
    def needed(self) -> bool:
        """Whether the file is only recognised because of the mapping (a layout column renamed)."""
        kind = self.kind
        return bool(kind and set(self.renamed.values()) & LAYOUTS[kind])


def kind_of(fields: set[str]) -> str | None:
    matches = [(len(cols), kind) for kind, cols in LAYOUTS.items() if cols <= fields]
    return max(matches)[1] if matches else None


def map_header(header: list[str], model: bool | str = False) -> Mapping:
    """
    Map headers to canonical fields. A header that already is a canonical name keeps it; the
    first header for a field wins. `model`: False (table only), "cache" (also cached model
    answers) or True (may make one model call for the headers the table does not know).
    """
    m = Mapping()
    taken: set[str] = set()
    names = [h for h in header if isinstance(h, str) and h.strip()]
    for h in sorted(names, key=lambda h: h not in FIELDS):  # exact canonical names first
        canon = SYNONYMS.get(key(h))
        if canon and canon not in taken:
            m.columns[h] = canon
            taken.add(canon)
    unknown = [h for h in names if h not in m.columns]
    # Ask only about files that look like a procurement export the table half understands:
    # an identifier column and at least two more, but no complete layout.
    looks = bool(taken & IDENTIFIERS) and len(taken) >= 3
    if model and unknown and kind_of(taken) is None and looks:
        for h, canon in _ask_model(names, unknown, call=model is True).items():
            if canon in FIELDS and canon not in taken and h in unknown:
                m.columns[h] = canon
                m.model.append(h)
                taken.add(canon)
    m.unknown = [h for h in names if h not in m.columns]
    return m


# ---------------------------------------------------------------- model fallback

PROMPT = """You map the column headers of a procurement export (purchase orders, goods receipts,
stock, shipments; any language, SAP technical names possible) to canonical fields.
Canonical fields:
{fields}
Return JSON: {{"columns": {{"<header exactly as given>": {{"field": canonical field or "unknown",
"confidence": "high" | "medium" | "low"}}}}}}. Map only the headers listed under "Unknown".
Say "high" only when the meaning is unambiguous."""


def _cache_dir() -> Path:
    return Path(os.environ.get("ASK_DATA_DIR", "data/store")) / "columns"


def _signature(header: list[str], unknown: list[str]) -> str:
    raw = json.dumps([PROMPT_VERSION, sorted(map(key, header)), sorted(unknown)])
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _ask_model(header: list[str], unknown: list[str], call: bool) -> dict[str, str]:
    """{header: canonical} for the headers the model maps with high confidence; cached."""
    sig = _signature(header, unknown)
    path = _cache_dir() / f"{sig}.json"
    out = None
    if path.exists():
        try:
            out = json.loads(path.read_text())
        except (OSError, ValueError):
            out = None
    if out is None and call:
        out = _call_model(header, unknown)
        if out is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(out, ensure_ascii=False))
    cols = (out or {}).get("columns") or {}
    return {
        h: v.get("field")
        for h, v in cols.items()
        if isinstance(v, dict) and v.get("confidence") == "high" and v.get("field") in FIELDS
    }


def _call_model(header: list[str], unknown: list[str]) -> dict | None:
    """One chat call through the paper check's client (key, model, cost log)."""
    from ask_soony import papercheck

    llm = papercheck.make_llm()
    if llm is None or not llm.key:
        return None
    llm.cache = _cache_dir() / "raw"
    fields = "\n".join(f"- {f}: {desc}" for f, (desc, _) in FIELDS.items())
    messages = [
        {"role": "system", "content": PROMPT.format(fields=fields)},
        {"role": "user", "content": json.dumps({"header": header, "unknown": unknown})},
    ]
    out = llm.ask(_signature(header, unknown), messages)
    if llm.calls:
        papercheck.log(f"column mapping: 1 model call, about USD {llm.cost:.5f} ({llm.model})")
    return out


# ---------------------------------------------------------------- values

_DMY_DOT = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_SLASH = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_DE_NUM = re.compile(r"^-?\d{1,3}(\.\d{3})+(,\d+)?$|^-?\d+,(\d{1,2}|\d{4,})$")
_EN_NUM = re.compile(r"^-?\d{1,3}(,\d{3})+\.\d+$|^-?\d+\.\d+$")


def decimal_comma(separator: str, values: list[str]) -> bool:
    """
    Whether this file writes decimals with a comma. The values decide when they can ('1.234,5',
    '88,38', '9,8000' are German; '1,234.5' and '9.8' are English); else the separator does: ';'
    files come from locales with a decimal comma, ',' files cannot use one unquoted.
    """
    vals = [v.strip() for v in values if v and v.strip()]
    if any(_DE_NUM.match(v) for v in vals):
        return True
    if any(_EN_NUM.match(v) for v in vals):
        return False
    return separator == ";"


def number(value: str, comma: bool) -> str:
    s = str(value or "").strip().replace(" ", "").replace(" ", "")
    if not s:
        return ""
    s = s.replace(".", "").replace(",", ".") if comma else s.replace(",", "")
    try:
        f = float(s)
    except ValueError:
        return str(value)
    return str(int(f)) if f.is_integer() else repr(f)


def slash_order(values: list[str]) -> str | None:
    """'dmy' or 'mdy' when some slash date in the column proves it ('30/10' or '10/30')."""
    firsts, seconds = [], []
    for v in values:
        m = _SLASH.match(str(v or "").strip())
        if m:
            firsts.append(int(m.group(1)))
            seconds.append(int(m.group(2)))
    if any(x > 12 for x in firsts):
        return "dmy"
    if any(x > 12 for x in seconds):
        return "mdy"
    return None


def iso_date(value: str, order: str | None) -> str:
    s = str(value or "").strip()
    if m := _DMY_DOT.match(s):  # dotted dates are day first everywhere they are used
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    if (m := _SLASH.match(s)) and order:
        a, b = int(m.group(1)), int(m.group(2))
        d, mo = (a, b) if order == "dmy" else (b, a)
        return f"{m.group(3)}-{mo:02d}-{d:02d}"
    return s


def line_step(values: list[str]) -> int:
    """10 when every line number is a multiple of ten (SAP items 10, 20, 30), else 1."""
    nums = [v.strip() for v in values if v and v.strip()]
    if nums and all(v.isdigit() for v in nums) and all(int(v) % 10 == 0 and int(v) for v in nums):
        return 10
    return 1


# ---------------------------------------------------------------- files

_CACHE: dict[tuple, tuple[list[dict], Mapping]] = {}


def separator(path: Path) -> str:
    try:
        raw = path.read_bytes().split(b"\n", 1)[0]
    except OSError:
        return ","
    return ";" if raw.count(b";") > raw.count(b",") else ","


def read(path: Path, model: bool | str = False) -> tuple[list[dict], Mapping]:
    """Rows with canonical keys added (originals kept), and the mapping used."""
    try:
        st = path.stat()
    except OSError:
        return [], Mapping()
    ck = (str(path), st.st_mtime_ns, st.st_size, model)
    if ck in _CACHE:
        return _CACHE[ck]
    rows = api.read_csv(path)
    header = [k for k in (rows[0].keys() if rows else []) if isinstance(k, str) and k]
    m = map_header(header, model)
    sep = separator(path)
    cols = {o: [str(r.get(o) or "") for r in rows] for o in m.renamed}
    comma = decimal_comma(
        sep, [v for o, c in m.renamed.items() if c in NUMBER_FIELDS for v in cols[o]]
    )
    order = {o: slash_order(cols[o]) for o, c in m.renamed.items() if c in DATE_FIELDS}
    step = {o: line_step(cols[o]) for o, c in m.renamed.items() if c in LINE_FIELDS}
    out = []
    for r in rows:
        row = dict(r)
        for o, c in m.renamed.items():
            if c in r:  # the file has a column with the canonical name itself: keep it
                continue
            v = str(r.get(o) or "").strip()
            if c in NUMBER_FIELDS:
                v = number(v, comma)
            elif c in DATE_FIELDS:
                v = iso_date(v, order.get(o))
            elif c in LINE_FIELDS and v.isdigit():
                v = str(int(v) // step.get(o, 1))
            row[c] = v
        out.append(row)
    _CACHE[ck] = (out, m)
    return out, m


def canonical_rows(path: Path, model: bool | str = False) -> list[dict]:
    return read(path, model)[0]


def model_mode() -> bool | str:
    """True when the column mapper may call the model (same switch as the paper check)."""
    off = os.environ.get("ASK_PAPERCHECK_LLM", "on").lower() in ("0", "off", "no")
    return "cache" if off else True


def describe(path: Path, model: bool | str = "cache") -> dict | None:
    """What a CSV is read as through the mapping: kind, renamed columns, model-mapped ones."""
    m = read(path, model)[1]
    kind = m.kind
    if not kind or not m.needed:
        return None
    return {"kind": kind, "label": LABELS[kind], "renamed": m.renamed, "model": list(m.model)}
