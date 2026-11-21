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
