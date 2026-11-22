"""Planning adapters, the plan-vs-paper and old-revision checks, and parse_count."""

import hashlib
import json

import openpyxl

from ask_soony.verifier.linker import build
from ask_soony.verifier.linker.normalize import parse_count
from ask_soony.verifier.linker.tests.conftest import write


def test_parse_count_reads_any_notation():
    assert parse_count("42.900") == 42900
    assert parse_count("42,900") == 42900
    assert parse_count("42 900") == 42900
    assert parse_count(42900) == 42900
    assert parse_count("12") == 12
    assert parse_count("") is None


def plan_workbook(root):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Inbound"
    ws.append(
        ["Supplier", "Part", "Quantity", "Mode", "Reference", "ETD", "ETA", "Status", "Comment"]
    )
    ws.append(
        ["Acme Parts", "CMP-B", 200, "air", "020-1", "2026-02-01", "2026-02-03", "booked", None]
    )
    ws.append([])
    shifts = wb.create_sheet("Shifts")
    shifts.append(["Day", "Shift", "Hours", "Lines"])
    shifts.append(["Saturday", "06:00–22:00", 16, "L1"])
    shifts.append([None, None, 16, "total"])
    (root / "soony/context").mkdir(parents=True, exist_ok=True)
    wb.save(root / "soony/context/plan.xlsx")


def test_workbook_tables_become_records(portal):
    plan_workbook(portal)
    records = build(portal).records
    inbound = records[records.kind == "plan_inbound"].iloc[0]
    assert (inbound.reference, inbound.material_id, inbound.supplier_id) == (
        "020-1",
        "CMP-B",
        "SUP-0007",
    )
    assert inbound["attrs"]["quantity"] == 200
    shifts = records[records.kind == "plan_shift"]
    assert list(shifts.key) == ["Saturday 06:00–22:00"]  # the totals row is skipped


def add_reading(portal, readings, doc_rel, content):
    folder = readings / doc_rel.replace("/", "__")
    folder.mkdir(parents=True)
    sha = hashlib.sha256((portal / doc_rel).read_bytes()).hexdigest()
    (folder / "meta.json").write_text(json.dumps({"path": doc_rel, "sha256": sha, "status": "ok"}))
    base = {
        "references": [],
        "lines": [],
        "statements": [],
        "decisions": [],
        "corrections": [],
        "verdict": {
            "status": "clean",
            "summary_en": "",
            "red_flags": [],
            "confidence": "high",
            "observations": [],
        },
    }
    (folder / "verdict.json").write_text(json.dumps(base | content))


def val(field, raw, number):
    return {
        "field": field,
        "field_label": None,
        "value": raw,
        "number": number,
        "unit": "PCS",
        "written": "printed",
        "crossed_out": False,
        "legible": "clear",
    }


ADVICE = "suppliers/SUP-0007_Acme_Parts/order_confirmations/Shipping_advice_020-1_9100000002.pdf"


def test_plan_expects_more_than_the_supplier_shipped(portal, tmp_path):
    plan_workbook(portal)
    readings = tmp_path / "readings"
    add_reading(
        portal,
        readings,
        ADVICE,
        {
            "document_type": "shipping_advice",
            "lines": [
                {
                    "item": "1",
                    "material_id": "CMP-B",
                    "supplier_part_no": None,
                    "description": None,
                    "values": [
                        val("quantity_shipped", "120", 120),
                        {**val("date_expected", "2026-02-09", None), "unit": None},
                    ],
                }
            ],
        },
    )
    issues = build(portal, readings).issues
    found = issues[issues.rule == "plan_vs_paper"]
    assert list(found.units) == [80]
    assert (
        "plan 200 by 2026-02-03, supplier shipped 120; balance expected 2026-02-09"
        in found.context.iloc[0]
    )


CONF = "suppliers/SUP-0007_Acme_Parts/order_confirmations/OA123_9100000001.pdf"


def test_old_revision_in_stock_is_found_through_the_confirmation(portal, tmp_path):
    write(
        portal,
        "soony/master/bom_delta.csv",
        "change_id,parent_id,child_id,action,qty_per_old,qty_per_new,rev_old,rev_new,use_up_old_rev,effective_from,reason\n"
        "ECN-1,FG-1,CMP-A,revision,1,1,B,C,N,2026-02-01,new mark; rev B must not be packed",
    )
    readings = tmp_path / "readings"
    add_reading(
        portal,
        readings,
        CONF,
        {
            "document_type": "order_confirmation",
            "statements": [
                {"topic": "revision", "text_en": "Printed to artwork rev. B", "quote": "rev B"}
            ],
        },
    )
    issues = build(portal, readings).issues
    found = issues[issues.rule == "old_revision_in_stock"]
    # PO 9100000001 → receipt 7000000001 → batch 7000000001 (500 in stock)
    assert list(found.record_id) == ["stock_batch:7000000001"]
    assert list(found.units) == [500]


def test_old_revision_that_may_be_used_up_is_not_flagged(portal, tmp_path):
    write(
        portal,
        "soony/master/bom_delta.csv",
        "change_id,parent_id,child_id,action,qty_per_old,qty_per_new,rev_old,rev_new,use_up_old_rev,effective_from,reason\n"
        "ECN-2,FG-1,CMP-A,revision,1,1,B,C,Y,2026-02-01,use up rev B",
    )
    readings = tmp_path / "readings"
    add_reading(
        portal,
        readings,
        CONF,
        {
            "document_type": "order_confirmation",
            "statements": [
                {"topic": "revision", "text_en": "Printed to artwork rev. B", "quote": "rev B"}
            ],
        },
    )
    issues = build(portal, readings).issues
    assert issues[issues.rule == "old_revision_in_stock"].empty
