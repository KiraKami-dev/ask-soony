"""SAP exports: purchase orders (both layouts), goods receipts, stock."""

from ask_soony.verifier.linker import adapters
from ask_soony.verifier.linker.normalize import (
    clean,
    parse_date,
    parse_number,
    po_line,
    po_line_state,
)
from ask_soony.verifier.linker.records import make_record

US_DATE = ("%m/%d/%Y",)


def _supplier(raw, ctx) -> tuple[str | None, dict]:
    """Resolved supplier id, plus the raw value in attrs when it can't be resolved."""
    supplier_id = ctx.suppliers.resolve(raw)
    return supplier_id, ({} if supplier_id or clean(raw) is None else {"supplier_raw": clean(raw)})


def purchase_orders(source, ctx, rows: list[dict] | None = None) -> list[dict]:
    records = []
    for row in adapters.rows(source.abspath) if rows is None else rows:
        po, line = clean(row.get("po_number")), po_line(row.get("po_line"))
        if po is None or line is None:
            continue
        supplier_id, extra = _supplier(row.get("supplier_id"), ctx)
        attrs = {
            "quantity": parse_number(row.get("quantity")),
            "unit_price": parse_number(row.get("unit_price")),
            "currency": clean(row.get("currency")),
            "order_date": parse_date(row.get("order_date")),
            "requested_date": parse_date(row.get("requested_date")),
            "promised_date": parse_date(row.get("promised_date")),
            "delivered_date": parse_date(row.get("actual_delivery_date")),
            "status": clean(row.get("status")),
            "incoterm": clean(row.get("incoterm")),
            "buyer": clean(row.get("buyer")),
            "note": clean(row.get("note")),
            **extra,
        }
        attrs["state"] = po_line_state(attrs["quantity"], attrs["delivered_date"])
        records.append(
            make_record(
                "po_line",
                f"{po}/{line}",
                source,
                attrs,
                supplier_id=supplier_id,
                material_id=clean(row.get("material_id")),
                po_number=po,
                po_line=line,
            )
        )
    return records


def purchase_orders_mapped(source, ctx) -> list[dict]:
    """
    A PO export under other column names (German Excel, another tool), read through the column
    mapping: canonical keys with numbers and dates already normalised.
    """
    from ask_soony import columns

    rows = []
    for row in columns.canonical_rows(source.abspath, "cache"):
        row = {k: v for k, v in row.items() if isinstance(k, str)}
        row.setdefault("promised_date", row.get("confirmed_date"))
        row.setdefault("actual_delivery_date", row.get("delivered_date"))
        rows.append(row)
    return purchase_orders(source, ctx, rows)


def purchase_orders_sap(source, ctx) -> list[dict]:
    """Legacy export with SAP field names, decimal commas and US dates."""
    records = []
    for row in adapters.rows(source.abspath):
        po, line = clean(row.get("EBELN")), po_line(row.get("EBELP"))
        if po is None or line is None:
            continue
        supplier_id, extra = _supplier(row.get("LIFNR"), ctx)
        attrs = {
            "quantity": parse_number(row.get("MENGE"), decimal_comma=True),
            "unit_price": parse_number(row.get("NETPR"), decimal_comma=True),
            "order_date": parse_date(row.get("BEDAT"), US_DATE),
            "requested_date": parse_date(row.get("EINDT"), US_DATE),
            "confirmed_date": parse_date(row.get("ZZCONF"), US_DATE),
            "delivered_date": parse_date(row.get("WEDAT"), US_DATE),
            "status": clean(row.get("ZZSTAT")),
            "buyer": clean(row.get("ERNAM")),
            "sap_item": clean(row.get("EBELP")),
            **extra,
        }
        attrs["state"] = po_line_state(attrs["quantity"], attrs["delivered_date"])
        records.append(
            make_record(
                "po_line",
                f"{po}/{line}",
                source,
                attrs,
                supplier_id=supplier_id,
                material_id=clean(row.get("MATNR")),
                po_number=po,
                po_line=line,
            )
        )
    return records


def goods_receipts(source, ctx) -> list[dict]:
    records = []
    for row in adapters.rows(source.abspath):
        gr = clean(row.get("gr_number"))
        if gr is None:
            continue
        supplier_id, extra = _supplier(row.get("supplier_id"), ctx)
        attrs = {
            "gr_date": parse_date(row.get("gr_date")),
            "qty_ordered": parse_number(row.get("qty_ordered")),
            "qty_received": parse_number(row.get("qty_received")),
            "plant": clean(row.get("plant")),
            "inspector": clean(row.get("inspector")),
            "remark": clean(row.get("remark")),
            **extra,
        }
        records.append(
            make_record(
                "goods_receipt",
                gr,
                source,
                attrs,
                supplier_id=supplier_id,
                material_id=clean(row.get("material_id")),
                po_number=clean(row.get("po_number")),
                po_line=po_line(row.get("po_line")),
                gr_number=gr,
                delivery_note=clean(row.get("delivery_note")),
            )
        )
    return records


def stock(source, ctx) -> list[dict]:
    """Rows with a batch become stock_batch records; rows without one are totals per material."""
    records = []
    for row in adapters.rows(source.abspath):
        material, batch = clean(row.get("material_id")), clean(row.get("batch"))
        if material is None:
            continue
        attrs = {
            "quantity": parse_number(row.get("quantity")),
            "uom": clean(row.get("uom")),
            "site": clean(row.get("site")),
            "snapshot_date": parse_date(row.get("snapshot_date")),
            "last_count": parse_date(row.get("last_count")),
        }
        if batch:
            records.append(
                make_record(
                    "stock_batch",
                    batch,
                    source,
                    attrs,
                    material_id=material,
                    batch=batch,
                    as_of=attrs["snapshot_date"],
                )
            )
        else:
            records.append(
                make_record(
                    "stock_total",
                    material,
                    source,
                    attrs,
                    material_id=material,
                    as_of=attrs["snapshot_date"],
                )
            )
    return records
