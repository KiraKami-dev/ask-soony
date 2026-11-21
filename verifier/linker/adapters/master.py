"""Master data: suppliers and parts."""

from verifier.linker import adapters
from verifier.linker.discover import flatten, load_json
from verifier.linker.normalize import clean
from verifier.linker.records import make_record


def supplier_profile(source, ctx) -> list[dict]:
    records = []
    for row in adapters.rows(source.abspath):
        supplier_id = clean(row.get("supplier_id"))
        if supplier_id is None:
            continue
        attrs = {
            key: clean(row.get(key))
            for key in (
                "name",
                "legal_name",
                "country",
                "status",
                "qualification_status",
                "incoterm",
                "preferred_language",
                "contact_name",
                "contact_email",
            )
        }
        records.append(make_record("supplier", supplier_id, source, attrs, supplier_id=supplier_id))
    return records


def supplier_profile_json(source, ctx) -> list[dict]:
    """Vendor master extract as nested JSON ({'supplier': {...}})."""
    flat = flatten(load_json(source.abspath))
    supplier_id = clean(flat.get("supplier.supplierId"))
    if supplier_id is None:
        return []
    attrs = {
        "name": clean(flat.get("supplier.name")),
        "legal_name": clean(flat.get("supplier.legalName")),
        "vendor_no": clean(flat.get("supplier.vendorNo")),
        "country": clean(flat.get("supplier.address.country")),
        "status": clean(flat.get("supplier.status")),
        "qualification_status": clean(flat.get("supplier.qualification.status")),
        "qualification_valid_until": clean(flat.get("supplier.qualification.validUntil")),
        "incoterm": clean(flat.get("supplier.terms.incoterm")),
        "preferred_language": clean(flat.get("supplier.contact.language")),
        "contact_email": clean(flat.get("supplier.contact.email")),
        "contact_name": clean(flat.get("supplier.contact.name")),
        "notes": clean(flat.get("supplier.notes")),
        "exported_at": clean(flat.get("exportedAt")),
    }
    return [
        make_record(
            "supplier",
            supplier_id,
            source,
            attrs,
            supplier_id=supplier_id,
            as_of=attrs["exported_at"],
        )
    ]


def parts_master(source, ctx) -> list[dict]:
    records = []
    for row in adapters.rows(source.abspath):
        part = clean(row.get("component_id"))
        if part is None:
            continue
        attrs = {
            key: clean(row.get(key))
            for key in ("description", "category", "uom", "abc_class", "critical", "drawing_rev")
        }
        records.append(make_record("part", part, source, attrs, material_id=part))
    return records
