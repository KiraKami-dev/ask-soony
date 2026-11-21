"""M3: readings (what the image recognizer read) joined to the linker. All data is made up."""

import hashlib
import json

from verifier.linker import build
from verifier.linker.tests.conftest import write

SCAN = "soony/receiving/DC-X/20260203_WZ-260202-002.jpg"  # receipt 7000000001: 500 of CMP-A
ADVICE = "suppliers/SUP-0007_Acme_Parts/order_confirmations/Shipping_advice_020-1_9100000002.pdf"
REPORT = "soony/receiving/DC-X/inspection_report_7000000001.pdf"
CONF = "suppliers/SUP-0007_Acme_Parts/order_confirmations/OA123_9100000001.pdf"


def value(field, raw, number=None, written="printed", crossed_out=False):
    return {
        "field": field,
        "field_label": None,
        "value": raw,
        "number": number,
        "unit": "PCS",
        "written": written,
        "crossed_out": crossed_out,
        "legible": "clear",
    }


def verdict(refs=(), lines=(), decisions=(), doc_type="delivery_note"):
    return {
        "document_type": doc_type,
        "references": [{"type": t, "value": v} for t, v in refs],
        "lines": [
            {
                "item": str(i),
                "material_id": m,
                "supplier_part_no": s,
                "description": None,
                "values": vals,
            }
            for i, (m, s, vals) in enumerate(lines, start=1)
        ],
        "statements": [],
        "decisions": list(decisions),
        "corrections": [],
        "verdict": {
            "status": "clean",
            "summary_en": "",
            "red_flags": [],
            "confidence": "high",
            "observations": [],
        },
    }


def add_reading(portal, readings, doc_rel, content, sha=None):
    """Write a reading the way the image recognizer stores it."""
    folder = readings / doc_rel.replace("/", "__")
    folder.mkdir(parents=True, exist_ok=True)
    sha = sha or hashlib.sha256((portal / doc_rel).read_bytes()).hexdigest()
    (folder / "meta.json").write_text(json.dumps({"path": doc_rel, "sha256": sha, "status": "ok"}))
    (folder / "verdict.json").write_text(json.dumps(content))


def issues(graph, rule):
    return graph.issues[graph.issues.rule == rule]


def test_booked_quantity_differs_from_handwritten_correction(portal, tmp_path):
    readings = tmp_path / "readings"
    add_reading(
        portal,
        readings,
        SCAN,
        verdict(
            refs=[("po_number", "9100000001"), ("delivery_note", "WZ-260202-002")],
            lines=[
                (
                    "CMP-A",
                    None,
                    [
                        value("quantity_delivered", "500", 500, crossed_out=True),
                        value("quantity_delivered", "50", 50, written="handwritten"),
                    ],
                )
            ],
        ),
    )
    found = issues(build(portal, readings), "delivery_vs_receipt")
    assert list(found.record_id) == ["goods_receipt:7000000001"]
    context = found.context.iloc[0]
    assert "paper 50 (handwritten; printed 500 crossed out)" in context
    assert "SAP received 500" in context


def test_dotted_thousands_are_not_a_mismatch(portal, tmp_path):
    receipts = portal / "soony/erp/goods_receipts.csv"
    receipts.write_text(
        receipts.read_text().replace(",500,500,WZ-260202-002", ",1500,1500,WZ-260202-002")
    )
    readings = tmp_path / "readings"
    # The reader got the number wrong (1.5); the raw text '1.500' is the evidence
    add_reading(
        portal,
        readings,
        SCAN,
        verdict(
            lines=[("CMP-A", None, [value("quantity_delivered", "1.500", 1.5)])],
        ),
    )
    assert issues(build(portal, readings), "delivery_vs_receipt").empty


def test_delivered_but_not_booked(portal, tmp_path):
    write(portal, "soony/receiving/DC-X/20260210_WZ-260209-999.jpg", "jpg")
    readings = tmp_path / "readings"
    add_reading(
        portal,
        readings,
        "soony/receiving/DC-X/20260210_WZ-260209-999.jpg",
        verdict(
            refs=[("po_number", "9100000002")],
            lines=[
                (
                    "CMP-B",
                    None,
                    [
                        value("quantity_delivered", "200", 200),
                        value("date_received", "10.02.2026", written="stamped"),
                    ],
                )
            ],
        ),
    )
    found = issues(build(portal, readings), "delivery_vs_receipt")
    assert found.detail.iloc[0] == "Delivered on paper, but not booked in SAP"
    assert "CMP-B 200; PO 9100000002; received 10.02.2026" in found.context.iloc[0]


def test_shipped_less_than_ordered(portal, tmp_path):
    readings = tmp_path / "readings"
    add_reading(
        portal,
        readings,
        ADVICE,
        verdict(
            doc_type="shipping_advice",
            refs=[("po_number", "9100000002")],
            lines=[
                (
                    "CMP-B",
                    None,
                    [
                        value("quantity_ordered", "200", 200),
                        value("quantity_shipped", "120", 120),
                        value("quantity_balance", "80", 80),
                        value("date_expected", "2026-02-20"),
                    ],
                )
            ],
        ),
    )
    found = issues(build(portal, readings), "shipped_vs_ordered")
    assert list(found.record_id) == ["po_line:9100000002/1"]
    assert "shipped 120 of 200, balance 80, expected 2026-02-20" in found.context.iloc[0]


def test_failed_lot_still_in_stock(portal, tmp_path):
    readings = tmp_path / "readings"
    add_reading(
        portal,
        readings,
        REPORT,
        verdict(
            doc_type="inspection_report",
            lines=[("CMP-A", None, [value("sample_size", "50", 50), value("defective", "6", 6)])],
            decisions=[
                {
                    "subject": "lot",
                    "outcome": "fail",
                    "decided_by": None,
                    "conditions": "use only under concession",
                    "quote": "rejected",
                }
            ],
        ),
    )
    found = issues(build(portal, readings), "failed_lot_in_stock")
    assert list(found.record_id) == ["stock_batch:7000000001"]
    assert "defective=6" in found.context.iloc[0]


def test_filename_and_content_disagree(portal, tmp_path):
    readings = tmp_path / "readings"
    add_reading(
        portal,
        readings,
        CONF,
        verdict(doc_type="order_confirmation", refs=[("po_number", "9100000009")]),
    )
    found = issues(build(portal, readings), "reading_keys")
    assert "PO: filename 9100000001, document ['9100000009']" in found.context.iloc[0]


def test_reading_of_an_older_file_version(portal, tmp_path):
    readings = tmp_path / "readings"
    add_reading(portal, readings, CONF, verdict(doc_type="order_confirmation"), sha="0" * 64)
    assert not issues(build(portal, readings), "reading_file").empty


def test_supplier_part_numbers_are_mapped_from_another_reading(portal, tmp_path):
    contract = "suppliers/SUP-0007_Acme_Parts/contracts/agreement.pdf"
    write(portal, contract, "pdf")
    readings = tmp_path / "readings"
    # A contract annex pairs CMP-B with the supplier's own number XP-9 ...
    add_reading(
        portal, readings, contract, verdict(doc_type="contract", lines=[("CMP-B", "XP-9", [])])
    )
    # ... so a confirmation that only names XP-9 still links to the PO line for CMP-B
    add_reading(
        portal,
        readings,
        CONF,
        verdict(
            doc_type="order_confirmation",
            refs=[("po_number", "9100000001")],
            lines=[("XP-9", None, [value("quantity_confirmed", "300", 300)])],
        ),
    )
    graph = build(portal, readings)
    links = graph.links[graph.links.rule == "doc_line_to_po_line"]
    assert ("doc_line:" + CONF + "#1", "po_line:9100000001/2") in set(zip(links.src, links.dst))


def test_corrections_come_from_values_not_the_readers_list(portal, tmp_path):
    readings = tmp_path / "readings"
    content = verdict(lines=[("CMP-A", None, [value("quantity_delivered", "500", 500)])])
    content["corrections"] = [
        {"what": "signature", "original": None, "replacement": "Ann B.", "where": "received by"}
    ]
    add_reading(portal, readings, SCAN, content)
    records = build(portal, readings).records.set_index("record_id")
    assert records.at["reading:" + SCAN, "attrs"]["corrections"] == []  # a signature isn't one


def test_receipt_remark_naming_another_part_is_flagged(portal):
    receipts = portal / "soony/erp/goods_receipts.csv"
    receipts.write_text(
        receipts.read_text()
        + "7000000010,2026-02-12,9100000002,1,SUP-0007,CMP-B,DC-X,200,200,WZ-260211-010,Insp A,"
        '"1 pallet labelled CMP-B holds CMP-A (40 pcs)"\n'
    )
    found = issues(build(portal), "remark_names_other_part")
    assert list(found.record_id) == ["goods_receipt:7000000010"]
    assert "remark names CMP-A" in found.context.iloc[0]


def test_remark_naming_its_own_part_is_fine(portal):
    receipts = portal / "soony/erp/goods_receipts.csv"
    receipts.write_text(
        receipts.read_text()
        + "7000000011,2026-02-12,9100000002,1,SUP-0007,CMP-B,DC-X,200,200,WZ-260211-011,Insp A,"
        "CMP-B cartons wet\n"
    )
    assert issues(build(portal), "remark_names_other_part").empty


def test_partial_and_full_receipts_on_open_lines(portal):
    receipts = portal / "soony/erp/goods_receipts.csv"
    # 9100000001/2 is open with 300 ordered: receive 100 (partial)
    receipts.write_text(
        receipts.read_text()
        + "7000000012,2026-03-01,9100000001,2,SUP-0007,CMP-B,DC-X,300,100,WZ-260301-012,Insp A,\n"
    )
    found = issues(build(portal), "open_line_received")
    assert list(found.type) == ["partial"]
    assert "received 100 of 300" in found.context.iloc[0]
    receipts.write_text(
        receipts.read_text()
        + "7000000013,2026-03-02,9100000001,2,SUP-0007,CMP-B,DC-X,300,200,WZ-260302-013,Insp A,\n"
    )
    found = issues(build(portal), "open_line_received")
    assert found.detail.iloc[0] == "Received in full, but the PO line has no delivery date"


def test_only_the_reading_of_the_current_file_version_is_used(portal, tmp_path):
    readings = tmp_path / "readings"
    old = verdict(lines=[("CMP-A", None, [value("quantity_delivered", "999", 999)])])
    add_reading(portal, readings, SCAN, old, sha="0" * 64)
    # Same document, read again after it changed: a second folder
    current = verdict(lines=[("CMP-A", None, [value("quantity_delivered", "500", 500)])])
    folder = readings / (SCAN.replace("/", "__") + "__current")
    folder.mkdir(parents=True)
    sha = hashlib.sha256((portal / SCAN).read_bytes()).hexdigest()
    (folder / "meta.json").write_text(json.dumps({"path": SCAN, "sha256": sha, "status": "ok"}))
    (folder / "verdict.json").write_text(json.dumps(current))
    graph = build(portal, readings)
    assert (graph.records.kind == "reading").sum() == 1  # the old one is not loaded
    assert issues(graph, "delivery_vs_receipt").empty  # 500 = 500, the 999 never counted
