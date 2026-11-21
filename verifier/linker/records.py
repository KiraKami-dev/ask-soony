"""
The record: one thing in the data with an id, its join keys and everything else as attrs.

All records share one set of join columns, so link rules are plain merges on those columns.
"""

RECORD_COLUMNS = [
    "record_id",  # '<kind>:<key>', plus '@<source>' when several files hold the same entity
    "entity",  # '<kind>:<key>': the thing itself, shared by all its versions
    "kind",
    "key",
    "source",  # portal path of the file it came from
    "source_sha256",  # exact file version
    "supplier_id",
    "material_id",
    "po_number",
    "po_line",
    "gr_number",
    "batch",
    "delivery_note",
    "shipment_id",
    "reference",  # container number or air waybill
    "po_numbers",  # list
    "material_ids",  # list
    "attrs",  # dict: everything that is not a join key
]

# Kinds every chain passes through; trace does not walk across them.
HUB_KINDS = {"part", "supplier"}


def disambiguate(records):
    """
    Give every version of the same entity its own record id ('<entity>@<source>').

    Two files can describe the same thing (a supplier as CSV and as JSON export). Neither may
    silently win: both stay, and rules.check_versions reports where they differ.
    """
    dupes = records.record_id.duplicated(keep=False)
    if not dupes.any():
        return records
    records = records.copy()
    records.loc[dupes, "record_id"] = (
        records.loc[dupes, "entity"] + "@" + records.loc[dupes, "source"]
    )
    # Same entity twice in one file: number them
    again = records.record_id.duplicated(keep=False)
    if again.any():
        counter = records[again].groupby("record_id").cumcount() + 1
        records.loc[again, "record_id"] = (
            records.loc[again, "record_id"] + "#" + counter.astype(str)
        )
    return records


def make_record(kind: str, key: str, source, attrs: dict | None = None, **keys) -> dict:
    record = dict.fromkeys(RECORD_COLUMNS)
    record.update(
        record_id=f"{kind}:{key}",
        entity=f"{kind}:{key}",
        kind=kind,
        key=key,
        source=source.path,
        source_sha256=source.sha256,
        attrs=attrs or {},
        **keys,
    )
    return record
