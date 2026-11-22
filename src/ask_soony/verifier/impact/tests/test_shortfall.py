"""Shortfall and decision cards on a made-up portal (see verifier/linker/tests/conftest.py)."""

import hashlib
import json

import openpyxl

from ask_soony.dispatch.decisions.cards import build_cards
from ask_soony.verifier.impact.shortfall import compute
from ask_soony.verifier.linker import build
from ask_soony.verifier.linker.tests.conftest import make_portal, write

REPORT = "soony/receiving/DC-X/inspection_report_7000000001.pdf"


def portal_with_plan(tmp_path):
    root = make_portal(tmp_path / "portal")
    write(
        root,
        "soony/master/products.csv",
        "product_id,name,lifecycle,launch_date,weekly_plan_qty,day_one_qty\n"
        "FG-1,Edition One,launch,2026-03-01,10,1000\nFG-2,Edition Two,launch,2026-03-01,10,100",
    )
    write(
        root,
        "soony/master/bom.csv",
        "parent_id,child_id,qty_per,valid_from,valid_to\n"
        "FG-1,CMP-A,1,2024-01-01,9999-12-31\nFG-2,CMP-A,1,2024-01-01,9999-12-31\n"
        "FG-2,CMP-B,2,2024-01-01,9999-12-31\nFG-2,CMP-MAG,4,2024-01-01,9999-12-31",
    )
    write(
        root,
        "suppliers/SUP-0007_Acme_Parts/components.csv",
        "component_id,description,category,share,unit_price,currency,lead_time_days,moq,primary,note\n"
        "CMP-B,Part B,Cat,1,2.0,EUR,10,1,Y,\n"
        "CMP-MAG,Magnet,Cat,1,0,EUR,10,0,Y,fitted by supplier into CMP-B; not stocked separately",
    )
    # Stock update workbook: two tables on two sheets, counts written with dot thousands
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Components"
    ws.append(["Stock after receipts 2026-02-28, late shift (book stock)"])
    ws.append(
        [
            "Site",
            "Material",
            "Description",
            "Qty",
            "UoM",
            "Receipts today",
            "Used today",
            "Last count",
        ]
    )
    ws.append(["DC-X", "CMP-A", "Part A", "1.050", "PC", 0, 0, "2026-02-28"])
    ws.append(["DC-X", "CMP-B", "Part B", "400", "PC", 0, 0, "2026-02-28"])
    fg = wb.create_sheet("Finished goods")
    fg.append(["Packed copies DC-X, 2026-02-28 20:00"])
    fg.append(["Site", "Material", "Description", "Qty", "UoM", "Packed today", "Last count"])
    fg.append(["DC-X", "FG-1", "Edition One", "500", "PC", 0, "2026-02-28"])
    fg.append(["DC-X", "FG-2", "Edition Two", "0", "PC", 0, "2026-02-28"])
    (root / "soony/erp").mkdir(parents=True, exist_ok=True)
    wb.save(root / "soony/erp/stock_update.xlsx")
    return root


def failed_inspection(root, readings):
    folder = readings / "report"
    folder.mkdir(parents=True)
    sha = hashlib.sha256((root / REPORT).read_bytes()).hexdigest()
    (folder / "meta.json").write_text(json.dumps({"path": REPORT, "sha256": sha, "status": "ok"}))
    (folder / "verdict.json").write_text(
        json.dumps(
            {
                "document_type": "inspection_report",
                "references": [],
                "lines": [],
                "statements": [],
                "corrections": [],
                "decisions": [
                    {
                        "subject": "lot",
                        "outcome": "fail",
                        "decided_by": None,
                        "conditions": None,
                        "quote": "fail",
                    }
                ],
                "verdict": {
                    "status": "has_warnings",
                    "summary_en": "",
                    "red_flags": [],
                    "confidence": "high",
                    "observations": [],
                },
            }
        )
    )


def test_needs_come_from_demand_packed_and_bom(tmp_path):
    root = portal_with_plan(tmp_path)
    graph = build(root)
    result = compute(graph.records, graph.issues)
    remaining = {e.product: e.remaining for e in result.editions}
    assert remaining == {"FG-1": 500, "FG-2": 100}  # packed count from the stock update
    needs = {p.part: p for p in result.parts}
    assert needs["CMP-A"].needed == 600  # shared: 500 + 100
    assert needs["CMP-B"].needed == 200  # 2 per copy of FG-2
    assert needs["CMP-A"].book_stock == 1050  # '1.050' is a thousands group
    assert "CMP-MAG" not in needs  # fitted inside CMP-B by the supplier, never stocked
    assert [x["part"] for x in result.excluded] == ["CMP-MAG"]


def test_failed_lot_is_not_usable_and_puts_copies_at_risk(tmp_path):
    root = portal_with_plan(tmp_path)
    readings = tmp_path / "readings"
    failed_inspection(root, readings)
    graph = build(root, readings)
    result = compute(graph.records, graph.issues)
    part_a = next(p for p in result.parts if p.part == "CMP-A")
    assert [d.reason for d in part_a.deductions] == ["failed inspection"]
    assert part_a.usable == 1050 - 500  # batch 7000000001 holds 500
    assert part_a.gap == 50
    assert result.shared_gaps == [part_a]  # CMP-A is used by both editions


def test_decisions_route_to_the_correct_approver(tmp_path):
    root = portal_with_plan(tmp_path)
    readings = tmp_path / "readings"
    failed_inspection(root, readings)
    graph = build(root, readings)
    result = compute(graph.records, graph.issues)
    cards = build_cards(result, graph.issues, graph.records)
    concession = next(c for c in cards if c.id == "concession-CMP-A")
    assert concession.decision
    # No org chart in this portal: the card names the role to find, not a person
    assert concession.messages[0].to_role == "approver of exceptions (see the approval policy)"


def test_cards_carry_deadlines_and_before_after(tmp_path):
    root = portal_with_plan(tmp_path)
    wb = openpyxl.load_workbook(root / "soony/erp/stock_update.xlsx")
    shifts = wb.create_sheet("Shifts")
    shifts.append(["Day", "Shift", "Hours", "Lines"])
    shifts.append(["Sunday", "06:00–20:00", 14, "L1"])  # launch day is Sun 1 Mar 2026
    wb.save(root / "soony/erp/stock_update.xlsx")
    write(
        root,
        "soony/master/products.csv",
        "product_id,name,lifecycle,launch_date,weekly_plan_qty,day_one_qty\n"
        "FG-1,Edition One,launch,2026-03-01,10,1000\nFG-2,Edition Two,launch,2026-03-01,10,100",
    )
    readings = tmp_path / "readings"
    failed_inspection(root, readings)
    graph = build(root, readings)
    result = compute(graph.records, graph.issues)
    # No packing rate in this plan, so no run-out time; the concession still closes the gap
    part_a = next(p for p in result.parts if p.part == "CMP-A")
    assert part_a.gap == 50 and part_a.gap_if_acted == 0
    cards = build_cards(result, graph.issues, graph.records)
    concession = next(c for c in cards if c.id == "concession-CMP-A")
    assert (concession.at_risk, concession.at_risk_if_acted) == (50, 0)


def test_blocked_stock_card_asks_whether_it_was_packed(tmp_path):
    root = portal_with_plan(tmp_path)
    receipts = root / "soony/erp/goods_receipts.csv"
    receipts.write_text(
        receipts.read_text()
        + '7000000020,2026-02-20,9100000002,1,SUP-0007,CMP-B,DC-X,200,200,WZ-1,Insp,"1 pallet holds CMP-A (10 pcs)"\n'
    )
    graph = build(root)
    result = compute(graph.records, graph.issues)
    cards = build_cards(result, graph.issues, graph.records)
    blocked = next(c for c in cards if c.id.startswith("wrong"))
    assert blocked.blocked_only and blocked.at_risk == 0
    assert "do any packed copies already contain this stock?" in blocked.messages[0].body


def test_an_offer_counts_as_supply_with_quantity_offered(tmp_path):
    root = portal_with_plan(tmp_path)
    folder = tmp_path / "readings" / "offer"
    folder.mkdir(parents=True)
    path = "suppliers/SUP-0007_Acme_Parts/notices/offer.pdf"
    (folder / "meta.json").write_text(json.dumps({"path": path, "sha256": "0", "status": "ok"}))
    value = {
        "field": "quantity_offered",
        "field_label": None,
        "value": "1.300",
        "number": 1.3,
        "unit": "PCS",
        "written": "printed",
        "crossed_out": False,
        "legible": "clear",
    }
    lead = {**value, "field": "lead_time", "value": "loaded within 4 h", "number": 4}
    line = {
        "item": "1",
        "material_id": "CMP-B",
        "supplier_part_no": None,
        "description": None,
        "values": [value, lead],
    }
    (folder / "verdict.json").write_text(
        json.dumps({"document_type": "offer", "references": [], "lines": [line]})
    )
    graph = build(root, tmp_path / "readings")
    part_b = next(p for p in compute(graph.records, graph.issues).parts if p.part == "CMP-B")
    offers = [s for s in part_b.supply if s["kind"] == "offer"]
    assert [(o["quantity"], o["document"]) for o in offers] == [(1300, path)]  # '1.300' fixed


# ---- inbound that lands before packing ends (launch day here is Sun 1 Mar 2026)

ADVICE = "suppliers/SUP-0007_Acme_Parts/order_confirmations/Shipping_advice_020-1_9100000002.pdf"


def inbound_plan(root, rows):
    """A launch plan with an Inbound table (supplier, part, quantity, ..., status)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Inbound"
    ws.append(
        ["Supplier", "Part", "Quantity", "Mode", "Reference", "ETD", "ETA", "Status", "Comment"]
    )
    for row in rows:
        ws.append(["Acme Parts", *row, None])
    (root / "soony/context").mkdir(parents=True, exist_ok=True)
    wb.save(root / "soony/context/plan.xlsx")


def shipping_advice(root, readings, shipped, expected, part_in_item=False):
    """
    The supplier's shipping advice for AWB 020-1, read: CMP-A shipped `shipped`.
    part_in_item: the reader put the part number in `item` and left material_id empty.
    """
    folder = readings / "advice"
    folder.mkdir(parents=True)
    sha = hashlib.sha256((root / ADVICE).read_bytes()).hexdigest()
    (folder / "meta.json").write_text(json.dumps({"path": ADVICE, "sha256": sha, "status": "ok"}))

    def value(field, raw, number):
        return {
            "field": field,
            "field_label": None,
            "value": raw,
            "number": number,
            "unit": None,
            "written": "printed",
            "crossed_out": False,
            "legible": "clear",
        }

    (folder / "verdict.json").write_text(
        json.dumps(
            {
                "document_type": "shipping_advice",
                "references": [{"type": "air_waybill", "value": "020-1"}],
                "lines": [
                    {
                        "item": "CMP-A" if part_in_item else "1",
                        "material_id": None if part_in_item else "CMP-A",
                        "supplier_part_no": None,
                        "description": None,
                        "values": [
                            value("quantity_shipped", str(shipped), shipped),
                            value("date_expected", expected, None),
                        ],
                    }
                ],
                "statements": [],
                "corrections": [],
                "decisions": [],
                "verdict": {
                    "status": "has_warnings",
                    "summary_en": "",
                    "red_flags": [],
                    "confidence": "high",
                    "observations": [],
                },
            }
        )
    )


PLAN_ROWS = [
    ("CMP-A", 30, "air", "AWB-IN", "2026-02-27", "2026-02-28", "booked"),  # lands in time
    ("CMP-A", 1000, "sea", "CONT-LATE", "2026-02-01", "2026-03-05", "in transit"),  # after launch
    ("CMP-A", 500, "air", "AWB-X", "2026-02-27", "2026-02-28", "cancelled"),
    ("CMP-A", 40, "air", "020-1", "2026-02-27", "2026-02-28", "booked"),  # has a shipping advice
]


def test_inbound_before_packing_ends_closes_part_of_the_gap(tmp_path):
    root = portal_with_plan(tmp_path)
    inbound_plan(root, PLAN_ROWS)
    readings = tmp_path / "readings"
    failed_inspection(root, readings)  # CMP-A: 550 usable for 600 needed
    graph = build(root, readings)
    result = compute(graph.records, graph.issues)
    part_a = next(p for p in result.parts if p.part == "CMP-A")
    # Only AWB-IN counts: CONT-LATE lands after launch, AWB-X is cancelled, and 020-1 has a
    # shipping advice nobody has read yet (it may say short), so it is not counted either
    assert [(a["reference"], a["quantity"]) for a in part_a.arriving] == [("AWB-IN", 30)]
    assert part_a.gap == 600 - 550 - 30
    reasons = {a["reference"]: a["reason"] for a in part_a.not_counted}
    assert reasons["AWB-X"] == "cancelled"
    assert "not read yet" in reasons["020-1"]
    assert "CONT-LATE" not in reasons  # after launch is never 'arriving'
    cards = build_cards(result, graph.issues, graph.records)
    allocate = next(c for c in cards if c.id == "allocate-CMP-A")
    assert allocate.title == "Part A: 20 short across 2 editions"
    assert "550 in stock + 30 arriving before packing ends" in allocate.paper_says


def test_shipped_short_counts_only_what_the_paper_says_and_the_card_goes(tmp_path):
    root = portal_with_plan(tmp_path)
    inbound_plan(root, PLAN_ROWS)
    readings = tmp_path / "readings"
    failed_inspection(root, readings)
    shipping_advice(root, readings, shipped=25, expected="2026-03-04")  # 15 come after launch
    graph = build(root, readings)
    result = compute(graph.records, graph.issues)
    part_a = next(p for p in result.parts if p.part == "CMP-A")
    assert sorted((a["reference"], a["quantity"]) for a in part_a.arriving) == [
        ("020-1", 25),
        ("AWB-IN", 30),
    ]
    balance = next(a for a in part_a.not_counted if a["reference"] == "020-1")
    assert (balance["quantity"], balance["eta"]) == (15, "2026-03-04")
    assert part_a.gap == 0  # 550 + 30 + 25 >= 600
    cards = build_cards(result, graph.issues, graph.records)
    assert not any(c.id == "allocate-CMP-A" for c in cards)  # no gap left, nothing to allocate


def test_shipped_short_is_found_when_the_part_number_sits_in_item(tmp_path):
    root = portal_with_plan(tmp_path)
    inbound_plan(root, PLAN_ROWS)
    readings = tmp_path / "readings"
    failed_inspection(root, readings)
    shipping_advice(root, readings, shipped=25, expected="2026-03-04", part_in_item=True)
    graph = build(root, readings)
    part_a = next(p for p in compute(graph.records, graph.issues).parts if p.part == "CMP-A")
    assert ("020-1", 25) in [(a["reference"], a["quantity"]) for a in part_a.arriving]
    assert any(a["reference"] == "020-1" for a in part_a.not_counted)


def receipt(root, gr_date):
    path = root / "soony/erp/goods_receipts.csv"
    path.write_text(
        path.read_text()
        + f"7000000030,{gr_date},9100000002,1,SUP-0007,CMP-B,DC-X,200,200,WZ-9,Insp,\n"
    )


def test_inbound_received_before_the_stock_figure_is_not_counted_twice(tmp_path):
    root = portal_with_plan(tmp_path)  # stock figures as of 2026-02-28
    inbound_plan(root, [("CMP-B", 200, "air", "AWB-002", "2026-02-01", "2026-02-03", "booked")])
    receipt(root, "2026-02-20")
    graph = build(root)
    part_b = next(p for p in compute(graph.records, graph.issues).parts if p.part == "CMP-B")
    assert not [a for a in part_b.arriving if a["reference"] == "AWB-002"]
    shipment = next(a for a in part_b.not_counted if a["reference"] == "AWB-002")
    assert shipment["reason"] == "already in stock (goods receipt 7000000030)"


def test_inbound_received_after_the_stock_figure_counts(tmp_path):
    root = portal_with_plan(tmp_path)
    inbound_plan(root, [("CMP-B", 200, "air", "AWB-002", "2026-02-01", "2026-02-03", "booked")])
    receipt(root, "2026-03-01")
    graph = build(root)
    part_b = next(p for p in compute(graph.records, graph.issues).parts if p.part == "CMP-B")
    shipment = next(a for a in part_b.arriving if a["reference"] == "AWB-002")
    assert (shipment["quantity"], shipment["status"]) == (200, "received")


def test_overdue_shipment_without_receipt_is_not_supply(tmp_path):
    root = portal_with_plan(tmp_path)
    graph = build(root)  # ERP shipment CONT001: ETA 2026-02-02, still in transit, no receipt
    part_b = next(p for p in compute(graph.records, graph.issues).parts if p.part == "CMP-B")
    late = next(a for a in part_b.not_counted if a["reference"] == "CONT001")
    assert late["quantity"] == 300  # the open PO line 9100000001/2
    assert "passed before the 2026-02-28 stock figure" in late["reason"]
    assert part_b.arriving == []


def test_a_held_shipment_is_not_supply():
    from ask_soony.verifier.impact.shortfall import _skips

    for status in ("held_port_strike", "on hold", "customs hold", "blocked", "STUCK at terminal"):
        assert _skips(status).startswith("not moving"), status
    assert _skips("in_transit") is None
    assert _skips("arrived_port") == "at the port, needs collecting"


def test_port_pickup_is_no_fix_when_the_newer_erp_says_held(tmp_path):
    root = portal_with_plan(tmp_path)
    inbound_plan(
        root, [("CMP-A", 5000, "sea", "CONT001", "2026-01-20", "2026-02-02", "arrived_port")]
    )
    part_a = next(p for p in compute(*_graph(root)).parts if p.part == "CMP-A")
    assert [s["kind"] for s in part_a.supply] == ["at_port"]  # ERP says in_transit: offered

    shipments = (root / "soony/erp/open_shipments.csv").read_text()
    write(
        root,
        "soony/erp/open_shipments.csv",
        shipments.replace(
            "2026-02-02,in_transit,,,,", "2026-02-02,held_port_strike,,,2026-02-27T09:00:00+01:00,"
        ),
    )
    part_a = next(p for p in compute(*_graph(root)).parts if p.part == "CMP-A")
    assert part_a.supply == []  # held: no collect fix, both sides shown instead
    assert any("held_port_strike" in n["reason"] for n in part_a.not_counted)


def _graph(root):
    graph = build(root)
    return graph.records, graph.issues
