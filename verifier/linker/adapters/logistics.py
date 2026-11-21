"""Shipments: the ERP shipment feed and the hand-kept Excel tracker."""

import openpyxl

from verifier.linker import adapters
from verifier.linker.normalize import clean, parse_date, split_list
from verifier.linker.records import make_record


def shipments(source, ctx) -> list[dict]:
    records = []
    for row in adapters.rows(source.abspath):
        shipment = clean(row.get("shipment_id"))
        if shipment is None:
            continue
        attrs = {
            "mode": clean(row.get("mode")),
            "reference": clean(row.get("reference")),
            "carrier_id": clean(row.get("carrier_id")),
            "route_id": clean(row.get("route_id")),
            "origin": clean(row.get("origin")),
            "destination": clean(row.get("destination")),
            "etd": parse_date(row.get("etd")),
            "eta": parse_date(row.get("eta")),
            "status": clean(row.get("status")),
            "updated_at": clean(row.get("updated_at")),
            "note": clean(row.get("note")),
        }
        records.append(
            make_record(
                "shipment",
                shipment,
                source,
                attrs,
                supplier_id=ctx.suppliers.resolve(row.get("supplier_id")),
                shipment_id=shipment,
                reference=clean(row.get("reference")),
                po_numbers=split_list(row.get("po_numbers")),
                material_ids=split_list(row.get("material_ids")),
            )
        )
    return records


def _tracker_rows(source) -> list[dict]:
    sheet_name, header_row = source.meta["sheet"], source.meta["header_row"]
    workbook = openpyxl.load_workbook(source.abspath, read_only=True, data_only=True)
    try:
        values = list(workbook[sheet_name].iter_rows(values_only=True))
    finally:
        workbook.close()
    header = [str(cell).strip() if cell is not None else "" for cell in values[header_row]]
    return [dict(zip(header, row)) for row in values[header_row + 1 :] if any(row)]


def shipment_tracker(source, ctx) -> list[dict]:
    records = []
    for row in _tracker_rows(source):
        shipment = clean(row.get("Shipment"))
        if shipment is None:
            continue
        supplier_raw = clean(row.get("Supplier"))
        supplier_id = ctx.suppliers.resolve(supplier_raw)
        attrs = {
            "mode": clean(row.get("Mode")),
            "reference": clean(row.get("Container/AWB")),
            "carrier_id": clean(row.get("Carrier")),
            "origin": clean(row.get("From")),
            "destination": clean(row.get("To")),
            "etd": parse_date(row.get("ETD")),
            "eta": parse_date(row.get("ETA")),
            "status": clean(row.get("Status")),
            "comment": clean(row.get("Comment")),
            **({} if supplier_id or supplier_raw is None else {"supplier_raw": supplier_raw}),
        }
        records.append(
            make_record(
                "tracker_row",
                shipment,
                source,
                attrs,
                supplier_id=supplier_id,
                shipment_id=shipment,
                reference=clean(row.get("Container/AWB")),
                po_numbers=split_list(row.get("POs")),
            )
        )
    return records
