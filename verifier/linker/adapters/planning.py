"""
Planning and stock workbooks (launch plan, stock update), plus products and bills of materials.

Workbooks hold several tables; discover.find_workbook_tables locates each by its header row,
and every table becomes its own kind of record. Counts are parsed whatever their notation.
"""

import re

import openpyxl

from verifier.linker.adapters import rows
from verifier.linker.normalize import clean, parse_count, parse_date, to_utc_iso
from verifier.linker.records import make_record

_STAMP = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?")


def _as_of(title: str | None) -> str | None:
    """'status 2026-11-20 18:00' or '2026-11-21, late shift' in a table title."""
    match = _STAMP.search(title or "")
    if not match:
        return None
    return to_utc_iso(match.group(1) + (f"T{match.group(2)}" if match.group(2) else ""))


def _table_rows(path, sheet: str, header_row: int) -> list[dict]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        values = list(workbook[sheet].iter_rows(values_only=True))
    finally:
        workbook.close()
    header = [str(c).strip() if c is not None else "" for c in values[header_row]]
    out = []
    for row in values[header_row + 1 :]:
        if not any(c is not None for c in row):
            break  # a table ends at its first empty row
        out.append(dict(zip(header, row)))
    return out


def _stock_count(row, source, as_of, title, ctx):
    material = clean(row.get("Material"))
    if not material:
        return None
    attrs = {
        "quantity": parse_count(row.get("Qty")),
        "receipts_today": parse_count(row.get("Receipts today")),
        "used_today": parse_count(row.get("Used today")),
        "last_count": parse_date(row.get("Last count")),
        "site": clean(row.get("Site")),
        "title": title,
    }
    return make_record("stock_count", material, source, attrs, material_id=material, as_of=as_of)


def _packed_count(row, source, as_of, title, ctx):
    product = clean(row.get("Material"))
    if not product:
        return None
    attrs = {
        "quantity": parse_count(row.get("Qty")),
        "packed_today": parse_count(row.get("Packed today")),
        "title": title,
    }
    return make_record("packed_count", product, source, attrs, material_id=product, as_of=as_of)


def _plan_edition(row, source, as_of, title, ctx):
    product = clean(row.get("Product"))
    if not product:
        return None
    attrs = {
        "edition": clean(row.get("Edition")),
        "day_one": parse_count(row.get("Day-one demand")),
        "packed": parse_count(row.get("Packed")),
        "still_to_pack": parse_count(row.get("Still to pack")),
        "rate_per_hour": parse_count(row.get("Rate per hour")),
        "hours_available": parse_count(row.get("Hours available")),
        "title": title,
    }
    return make_record("plan_edition", product, source, attrs, material_id=product, as_of=as_of)


def _plan_part(row, source, as_of, title, ctx):
    part = clean(row.get("Part"))
    if not part:
        return None
    attrs = {
        "needed": parse_count(row.get("Needed for the remaining pack-out")),
        "erp_stock": parse_count(row.get("ERP stock")),
        "due_before_launch": parse_count(row.get("Due before launch")),
        "cover": parse_count(row.get("Cover")),
        "status": clean(row.get("Status")),
        "title": title,
    }
    return make_record("plan_part", part, source, attrs, material_id=part, as_of=as_of)


def _plan_inbound(row, source, as_of, title, ctx):
    reference = clean(row.get("Reference"))
    part = clean(row.get("Part"))
    if not reference or not part:
        return None
    supplier_raw = clean(row.get("Supplier"))
    attrs = {
        "quantity": parse_count(row.get("Quantity")),
        "mode": clean(row.get("Mode")),
        "etd": parse_date(row.get("ETD")),
        "eta": parse_date(row.get("ETA")),
        "status": clean(row.get("Status")),
        "comment": clean(row.get("Comment")),
        "supplier_name": supplier_raw,
    }
    return make_record(
        "plan_inbound",
        f"{reference}/{part}",
        source,
        attrs,
        supplier_id=ctx.suppliers.resolve(supplier_raw),
        material_id=part,
        reference=reference,
        as_of=as_of,
    )


def _plan_shift(row, source, as_of, title, ctx):
    day, shift = clean(row.get("Day")), clean(row.get("Shift"))
    if not day or not shift:  # the totals row has no day
        return None
    attrs = {
        "day": day,
        "shift": shift,
        "hours": parse_count(row.get("Hours")),
        "lines": clean(row.get("Lines")),
    }
    return make_record("plan_shift", f"{day} {shift}", source, attrs, as_of=as_of)


TABLE_READERS = {
    "stock_count": _stock_count,
    "packed_count": _packed_count,
    "plan_edition": _plan_edition,
    "plan_part": _plan_part,
    "plan_inbound": _plan_inbound,
    "plan_shift": _plan_shift,
}


def workbook(source, ctx) -> list[dict]:
    records = []
    for table in source.meta["tables"]:
        reader = TABLE_READERS[table["table"]]
        title = table.get("title")
        as_of = _as_of(title)
        for row in _table_rows(source.abspath, table["sheet"], table["header_row"]):
            record = reader(row, source, as_of, title, ctx)
            if record is not None:
                records.append(record)
    return records


def products(source, ctx) -> list[dict]:
    out = []
    for row in rows(source.abspath):
        product = clean(row.get("product_id"))
        if not product:
            continue
        attrs = {
            "name": clean(row.get("name")),
            "lifecycle": clean(row.get("lifecycle")),
            "launch_date": parse_date(row.get("launch_date")),
            "day_one_qty": parse_count(row.get("day_one_qty")),
            "weekly_plan_qty": parse_count(row.get("weekly_plan_qty")),
            "std_cost_eur": parse_count(row.get("std_cost_eur")),
        }
        out.append(make_record("product", product, source, attrs, material_id=product))
    return out


def bom(source, ctx) -> list[dict]:
    out = []
    for row in rows(source.abspath):
        parent, child = clean(row.get("parent_id")), clean(row.get("child_id"))
        if not parent or not child:
            continue
        attrs = {
            "parent": parent,
            "qty_per": parse_count(row.get("qty_per")),
            "valid_from": parse_date(row.get("valid_from")),
            "valid_to": parse_date(row.get("valid_to")),
        }
        out.append(make_record("bom_line", f"{parent}>{child}", source, attrs, material_id=child))
    return out


def bom_change(source, ctx) -> list[dict]:
    out = []
    for row in rows(source.abspath):
        change = clean(row.get("change_id"))
        if not change:
            continue
        attrs = {
            key: clean(row.get(key))
            for key in ("parent_id", "action", "rev_old", "rev_new", "use_up_old_rev", "reason")
        }
        attrs["effective_from"] = parse_date(row.get("effective_from"))
        out.append(
            make_record("bom_change", change, source, attrs, material_id=clean(row.get("child_id")))
        )
    return out


def supplier_parts(source, ctx) -> list[dict]:
    """A supplier's own list of the parts it supplies, with its notes."""
    supplier = next(
        (
            ctx.suppliers.resolve(part)
            for part in source.path.split("/")[:-1]
            if ctx.suppliers.resolve(part)
        ),
        None,
    )
    out = []
    for row in rows(source.abspath):
        part = clean(row.get("component_id"))
        if not part:
            continue
        attrs = {
            "share": parse_count(row.get("share")),
            "unit_price": parse_count(row.get("unit_price")),
            "currency": clean(row.get("currency")),
            "lead_time_days": parse_count(row.get("lead_time_days")),
            "primary": clean(row.get("primary")),
            "note": clean(row.get("note")),
        }
        out.append(
            make_record(
                "supplier_part",
                f"{supplier}:{part}",
                source,
                attrs,
                supplier_id=supplier,
                material_id=part,
            )
        )
    return out


def sites(source, ctx) -> list[dict]:
    out = []
    for row in rows(source.abspath):
        site = clean(row.get("site_id"))
        if site:
            attrs = {
                key: clean(row.get(key)) for key in ("name", "operator", "type", "country", "city")
            }
            out.append(make_record("site", site, source, attrs))
    return out
