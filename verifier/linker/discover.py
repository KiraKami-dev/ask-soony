"""
Classify every portal file by what it contains, not by its exact name.

Spreadsheets are recognised by their columns, documents by their filename pattern. A file that
matches nothing is reported as 'unknown', so a new kind of file shows up instead of being skipped.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

# Spreadsheet layouts: kind → columns a file must have. The most specific match wins.
CSV_LAYOUTS: dict[str, frozenset[str]] = {
    "purchase_orders": frozenset(
        {"po_number", "po_line", "supplier_id", "material_id", "quantity"}
    ),
    "purchase_orders_sap": frozenset({"EBELN", "EBELP", "LIFNR", "MATNR", "MENGE"}),
    "goods_receipts": frozenset(
        {"gr_number", "po_number", "po_line", "delivery_note", "qty_received"}
    ),
    "stock": frozenset({"snapshot_date", "material_id", "batch", "quantity"}),
    "shipments": frozenset({"shipment_id", "po_numbers", "material_ids", "eta", "status"}),
    "supplier_profile": frozenset({"supplier_id", "name", "legal_name", "qualification_status"}),
    "parts_master": frozenset({"component_id", "description", "abc_class", "drawing_rev"}),
}
XLSX_LAYOUTS: dict[str, frozenset[str]] = {
    "shipment_tracker": frozenset({"Shipment", "POs", "Status"}),
}
XLSX_HEADER_SEARCH_ROWS = 10
# JSON layouts: kind → dotted key paths a file must have.
JSON_LAYOUTS: dict[str, frozenset[str]] = {
    "supplier_profile_json": frozenset({"supplier.supplierId", "supplier.name"}),
}

# Documents: kind → filename pattern. Named groups become the document's keys.
DOC_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("confirmation", re.compile(r"^OA(?P<conf>\w+?)_(?P<po_number>\d+)\.pdf$", re.I)),
    (
        "shipping_advice",
        re.compile(r"^Shipping_advice_(?P<awb>[\w-]+)_(?P<po_number>\d+)\.pdf$", re.I),
    ),
    ("packing_list", re.compile(r"^PL_(?P<ref>\w+)_(?P<po_number>\d+)\.pdf$", re.I)),
    (
        "delivery_note",
        re.compile(
            r"^(?P<date>\d{8})_(?P<delivery_note>[A-Z]{2,4}-\d{6}-\d{3})\.(?:jpe?g|png|pdf)$", re.I
        ),
    ),
    ("inspection_report", re.compile(r"^inspection_report_(?P<batch>\d+)\.pdf$", re.I)),
]


@dataclass(frozen=True)
class Source:
    path: str  # relative to the portal root, '/'-separated
    abspath: Path
    kind: str
    sha256: str
    meta: dict = field(default_factory=dict)


def sniff_separator(first_line: str) -> str:
    return ";" if first_line.count(";") > first_line.count(",") else ","


def csv_header(path: Path) -> set[str]:
    with path.open(encoding="utf-8-sig") as handle:
        first = handle.readline().strip()
    return {col.strip() for col in first.split(sniff_separator(first))}


def find_xlsx_table(path: Path, required: frozenset[str]) -> tuple[str, int] | None:
    """Return (sheet name, header row index) of the first sheet whose header holds `required`."""
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for sheet in workbook.worksheets:
            for index, row in enumerate(sheet.iter_rows(values_only=True)):
                if index >= XLSX_HEADER_SEARCH_ROWS:
                    break
                cells = {str(cell).strip() for cell in row if cell is not None}
                if required <= cells:
                    return sheet.title, index
    finally:
        workbook.close()
    return None


def flatten(value, prefix: str = "") -> dict:
    """{'a': {'b': 1}} → {'a.b': 1}. Lists are kept as values."""
    if not isinstance(value, dict):
        return {prefix: value}
    flat = {}
    for key, inner in value.items():
        flat.update(flatten(inner, f"{prefix}.{key}" if prefix else str(key)))
    return flat


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _best_layout(header: set[str], layouts: dict[str, frozenset[str]]) -> str | None:
    matches = [(len(cols), kind) for kind, cols in layouts.items() if cols <= header]
    return max(matches)[1] if matches else None


def classify(path: Path) -> tuple[str, dict]:
    name = path.name
    for kind, pattern in DOC_PATTERNS:
        match = pattern.match(name)
        if match:
            return kind, match.groupdict()
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            kind = _best_layout(csv_header(path), CSV_LAYOUTS)
            if kind:
                return kind, {}
        elif suffix in (".xlsx", ".xlsm"):
            for kind, required in XLSX_LAYOUTS.items():
                table = find_xlsx_table(path, required)
                if table:
                    return kind, {"sheet": table[0], "header_row": table[1]}
        elif suffix == ".json":
            kind = _best_layout(set(flatten(load_json(path))), JSON_LAYOUTS)
            if kind:
                return kind, {}
    except Exception as error:  # a broken file is reported, never fatal
        return "unknown", {"error": f"{type(error).__name__}: {error}"}
    return "unknown", {}


def discover(root: Path) -> list[Source]:
    sources = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        kind, meta = classify(path)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        sources.append(Source(path.relative_to(root).as_posix(), path, kind, sha, meta))
    return sources
