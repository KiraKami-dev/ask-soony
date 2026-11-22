"""
Readings (M3): what the image recognizer read from each document, as linker records.

One 'reading' record per document (keys from the references inside it) and one 'doc_line' record
per line on it (part and values). Fixes for known reader errors are applied here, on load:
- counts are re-parsed from the raw text ('19.000' is 19000, see normalize.count_number)
- corrections are derived from the values (crossed out or handwritten), not taken from the
  reader's own list, which mostly holds signatures and stamps
- supplier part numbers are mapped to the buyer's (map_supplier_part_numbers)

The raw text of every value is kept next to the number, so nothing the reader saw is lost.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ask_soony.verifier.linker.normalize import clean, count_number
from ask_soony.verifier.linker.records import make_record

REFERENCE_KEYS = {
    "po_number": "po_numbers",
    "delivery_note": "delivery_note",
    "air_waybill": "reference",
    "container": "reference",
    "batch": "batch",
    "goods_receipt": "batch",  # a goods receipt number is the batch number
}


@dataclass(frozen=True)
class ReadingSource:
    """Where a reading came from, shaped like a discovered Source for make_record."""

    path: str
    sha256: str


def _references(verdict: dict) -> dict:
    keys: dict = {"po_numbers": []}
    for ref in verdict.get("references") or []:
        column = REFERENCE_KEYS.get(ref.get("type"))
        value = clean(ref.get("value"))
        if column is None or value is None:
            continue
        if column == "po_numbers":
            if value not in keys["po_numbers"]:
                keys["po_numbers"].append(value)
        else:
            keys.setdefault(column, value)
    return keys


def _value(raw: dict) -> dict:
    field = raw.get("field")
    return {
        "field": field if field != "other" else f"other:{raw.get('field_label') or '?'}",
        "raw": raw.get("value"),
        "number": count_number(field, raw.get("value"), raw.get("number")),
        "unit": raw.get("unit"),
        "written": raw.get("written"),
        "crossed_out": bool(raw.get("crossed_out")),
        "legible": raw.get("legible"),
    }


def final_values(values: list[dict]) -> dict:
    """Per field, the value that stands: not crossed out; handwritten beats printed."""
    standing: dict = {}
    for value in values:
        if value["crossed_out"]:
            continue
        current = standing.get(value["field"])
        if current is None or (
            value["written"] == "handwritten" and current["written"] != "handwritten"
        ):
            standing[value["field"]] = value
    return standing


def _current_readings(readings_dir: Path, current_sha: dict[str, str]) -> list[Path]:
    """
    One reading per document. A document read twice (its file changed in between) keeps only
    the reading of its current version; with no current reading, the old ones stay, marked stale.
    """
    by_doc: dict[str, list[tuple[Path, str | None]]] = {}
    for meta_path in sorted(readings_dir.glob("*/meta.json")):
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            continue
        if meta.get("status") == "ok" and meta.get("path"):
            by_doc.setdefault(meta["path"], []).append((meta_path, meta.get("sha256")))
    chosen = []
    for doc_path, readings in by_doc.items():
        current = [m for m, sha in readings if sha == current_sha.get(doc_path)]
        chosen += current or [m for m, _ in readings]
    return chosen


def load_readings(readings_dir: Path, current_sha: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """
    Records from every successful reading. Returns (records, source rows for the summary).

    current_sha maps each portal path to its current checksum, so a reading of an older version
    of a file is still loaded but marked (attrs.read_sha256 differs), never silently trusted.
    """
    records, rows = [], []
    for meta_path in _current_readings(readings_dir, current_sha):
        verdict_path = meta_path.parent / "verdict.json"
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            continue
        if meta.get("status") != "ok" or not verdict_path.exists():
            continue
        verdict = json.loads(verdict_path.read_text())
        doc_path = meta["path"]
        source = ReadingSource(
            f"{readings_dir.name}/{meta_path.parent.name}/verdict.json",
            hashlib.sha256(verdict_path.read_bytes()).hexdigest(),
        )
        keys = _references(verdict)
        lines = [
            [_value(v) for v in line.get("values") or []] for line in verdict.get("lines") or []
        ]
        all_values = [v for line in lines for v in line]
        received = next((v["raw"] for v in all_values if v["field"] == "date_received"), None)
        verdict_block = verdict.get("verdict") or {}
        attrs = {
            "doc_type": verdict.get("document_type"),
            "document_number": verdict.get("document_number"),
            "document_date": verdict.get("document_date"),
            "issuer": verdict.get("issuer"),
            "date_received": received,
            "status": verdict_block.get("status"),
            "confidence": verdict_block.get("confidence"),
            "red_flags": verdict_block.get("red_flags") or [],
            "decisions": verdict.get("decisions") or [],
            "statements": [
                {"topic": s.get("topic"), "text_en": s.get("text_en")}
                for s in verdict.get("statements") or []
            ],
            # Derived, not the reader's list: crossed-out and handwritten values
            "corrections": [
                f"{v['field']}: {v['raw']} ({'crossed out' if v['crossed_out'] else v['written']})"
                for v in all_values
                if v["crossed_out"] or v["written"] == "handwritten"
            ],
            "read_sha256": meta.get("sha256"),
            "current_sha256": current_sha.get(doc_path),
            "model": meta.get("model"),
        }
        reading = make_record(
            "reading",
            doc_path,
            source,
            attrs,
            doc_path=doc_path,
            po_numbers=keys["po_numbers"],
            delivery_note=keys.get("delivery_note"),
            reference=keys.get("reference"),
            batch=keys.get("batch"),
        )
        records.append(reading)
        for index, (line, values) in enumerate(zip(verdict.get("lines") or [], lines), start=1):
            records.append(
                make_record(
                    "doc_line",
                    f"{doc_path}#{index}",
                    source,
                    {
                        "item": line.get("item"),
                        "description": line.get("description"),
                        "supplier_part_no": clean(line.get("supplier_part_no")),
                        "material_raw": clean(line.get("material_id")),
                        "values": values,
                        "final": {f: v["number"] for f, v in final_values(values).items()},
                        "crossed": {v["field"]: v["number"] for v in values if v["crossed_out"]},
                    },
                    doc_path=doc_path,
                    po_numbers=keys["po_numbers"],
                    material_id=clean(line.get("material_id")),
                )
            )
        rows.append(
            {
                "path": source.path,
                "kind": "reading",
                "sha256": source.sha256,
                "adapter": True,
                "records": 1 + len(lines),
                "error": None,
            }
        )
    return records, rows


def map_supplier_part_numbers(records: list[dict], known_parts: set[str]) -> None:
    """
    Replace supplier part numbers on document lines with the buyer's part numbers, in place.

    The mapping is learned from the readings themselves: any line that names both a known part
    and a supplier part number (a contract annex, a packing list) teaches one pair. Nothing is
    typed in by hand.
    """
    lines = [r for r in records if r["kind"] == "doc_line"]
    mapping = {
        line["attrs"]["supplier_part_no"]: line["material_id"]
        for line in lines
        if line["material_id"] in known_parts and line["attrs"]["supplier_part_no"]
    }
    for line in lines:
        material = line["material_id"]
        if material and material not in known_parts and material in mapping:
            line["material_id"] = mapping[material]
            line["attrs"]["mapped_from"] = material
        elif not material and line["attrs"]["supplier_part_no"] in mapping:
            line["material_id"] = mapping[line["attrs"]["supplier_part_no"]]
            line["attrs"]["mapped_from"] = line["attrs"]["supplier_part_no"]
