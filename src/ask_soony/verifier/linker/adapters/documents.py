"""
Documents, linked by the keys in their filename (M2). Reading their contents comes later (M3 and
the Reader); the filename key is then checked against the key inside the document.
"""

from ask_soony.verifier.linker.normalize import parse_date
from ask_soony.verifier.linker.records import make_record


def _supplier_from_path(source, ctx) -> str | None:
    """Supplier folders are named '<id>_<name>'; the first path part that resolves wins."""
    for part in source.path.split("/")[:-1]:
        supplier_id = ctx.suppliers.resolve(part)
        if supplier_id:
            return supplier_id
    return None


def _file(source) -> str:
    return source.path.rsplit("/", 1)[-1]


def confirmation(source, ctx) -> list[dict]:
    meta = source.meta
    return [
        make_record(
            "confirmation",
            f"OA{meta['conf']}_{meta['po_number']}",  # the number alone may repeat across POs
            source,
            {"file": _file(source)},
            supplier_id=_supplier_from_path(source, ctx),
            po_number=meta["po_number"],
            doc_path=source.path,
        )
    ]


def shipping_document(source, ctx) -> list[dict]:
    """Shipping advice (air waybill) and packing list (container): what the supplier sent."""
    meta = source.meta
    reference = meta.get("awb") or meta.get("ref")
    return [
        make_record(
            "shipping_doc",
            _file(source).rsplit(".", 1)[0],
            source,
            {"file": _file(source), "doc_type": source.kind},
            supplier_id=_supplier_from_path(source, ctx),
            po_number=meta["po_number"],
            doc_path=source.path,
            reference=reference,
        )
    ]


def delivery_note(source, ctx) -> list[dict]:
    meta = source.meta
    return [
        make_record(
            "delivery_note_scan",
            meta["delivery_note"],
            source,
            {"file": _file(source), "scan_date": parse_date(meta["date"], ("%Y%m%d",))},
            delivery_note=meta["delivery_note"],
            doc_path=source.path,
            as_of=parse_date(meta["date"], ("%Y%m%d",)),
        )
    ]


def inspection_report(source, ctx) -> list[dict]:
    meta = source.meta
    return [
        make_record(
            "inspection_report",
            meta["batch"],
            source,
            {"file": _file(source)},
            batch=meta["batch"],
            doc_path=source.path,
        )
    ]
