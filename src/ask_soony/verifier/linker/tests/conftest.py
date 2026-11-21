"""
A tiny, made-up portal. No real portal data: every id, name and number here is invented.

One complete chain (PO 9100000001 → receipt 7000000001 → batch 7000000001 → shipment SHP-1 →
tracker row) plus deliberate gaps, each covered by a test.
"""

import json
from pathlib import Path

import openpyxl
import pytest


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


PROFILE_HEADER = "supplier_id,name,legal_name,country,status,qualification_status,incoterm"

PO_NEW = """
po_number,po_line,supplier_id,material_id,plant,quantity,unit_price,currency,order_date,requested_date,promised_date,actual_delivery_date,status,buyer,incoterm,note
9100000001,1,SUP-0007,CMP-A,DC-X,500,1.5,EUR,2026-01-02,2026-02-01,2026-02-01,2026-02-03,delivered,Buyer A,DAP,
9100000001,2,SUP-0007,CMP-B,DC-X,300,2.0,EUR,2026-01-02,2026-03-01,2026-03-01,,open,Buyer A,DAP,
9100000001,51,SUP-0007,CMP-A,DC-X,-100,1.5,EUR,2026-01-05,2026-02-01,2026-02-01,,cancelled,Buyer A,DAP,cancellation
9100000002,1,SUP-0007,CMP-B,DC-X,200,2.0,EUR,2026-01-10,2026-02-10,2026-02-10,2026-02-12,delivered,Buyer A,DAP,
"""

PO_LEGACY = """
EBELN;EBELP;LIFNR;MATNR;WERKS;MENGE;NETPR;BEDAT;EINDT;ZZCONF;WEDAT;ZZSTAT;ERNAM
9100000000;00010;7;CMP-A;DC-X;1000;1,5000;08/12/2025;10/30/2025;10/30/2025;11/02/2025;GL;BUYA
9100000000;00510;7;CMP-A;DC-X;-200;1,5000;09/01/2025;10/30/2025;10/30/2025;;LO;BUYA
"""

RECEIPTS = """
gr_number,gr_date,po_number,po_line,supplier_id,material_id,plant,qty_ordered,qty_received,delivery_note,inspector,remark
7000000000,2025-11-02,9100000000,1,SUP-0007,CMP-A,DC-X,1000,1000,WZ-251101-001,Insp A,
7000000001,2026-02-03,9100000001,1,SUP-0007,CMP-A,DC-X,500,500,WZ-260202-002,Insp A,
7000000002,2026-02-05,,,SUP-0007,PIN-X,DC-X,,900,WZ-260204-003,Insp B,no PO reference
"""

STOCK = """
snapshot_date,site,material_id,batch,quantity,uom,safety_stock,weeks_of_cover,last_count
2026-02-10,DC-X,CMP-A,7000000001,500,PC,0,1,2026-02-01
2026-02-10,DC-X,CMP-B,7000000099,50,PC,0,1,2026-02-01
2026-02-10,DC-X,FG-X,,1000,PC,0,1,2026-02-01
"""

SHIPMENTS = """
shipment_id,carrier_id,mode,reference,supplier_id,po_numbers,material_ids,route_id,origin,destination,via,dest_site,etd,eta,status,last_lat,last_lon,updated_at,note
SHP-1,LSP-9,sea,CONT001,SUP-0007,9100000001|9100000002,CMP-A|CMP-B,RT-9,AAAAA,BBBBB,,DC-X,2026-01-20,2026-02-02,in_transit,,,,
SHP-2,LSP-9,air,AWB-002,SUP-0007,9100000002,CMP-B,RT-9,AAAAA,BBBBB,,DC-X,2026-02-01,2026-02-03,booked,,,,
SHP-3,LSP-9,air,AWB-003,SUP-0007,9199999999,CMP-B,RT-9,AAAAA,BBBBB,,DC-X,2026-02-01,2026-02-03,booked,,,,
"""


def write_tracker(root: Path) -> None:
    path = root / "soony/logistics/tracker.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tracker"
    ws.append(["Open shipments (kept by hand)"])  # title row above the header
    ws.append(
        [
            "Shipment",
            "Carrier",
            "Mode",
            "Container/AWB",
            "Supplier",
            "POs",
            "From",
            "To",
            "ETD",
            "ETA",
            "Status",
            "Comment",
        ]
    )
    ws.append(
        [
            "SHP-1",
            "LSP-9",
            "sea",
            "CONT001",
            "Acme Parts",
            "9100000001, 9100000002",
            "AAAAA",
            "BBBBB",
            "2026-01-20",
            "2026-02-02",
            "in transit",
            None,
        ]
    )
    ws.append(
        [
            "SHP-9",
            "LSP-9",
            "sea",
            "CONT009",
            "Acme Parts",
            "9100000001",
            "AAAAA",
            "BBBBB",
            "2026-01-20",
            "2026-02-02",
            "in transit",
            None,
        ]
    )
    wb.save(path)


def make_portal(root: Path) -> Path:
    write(
        root,
        "suppliers/SUP-0007_Acme_Parts/profile.csv",
        f"{PROFILE_HEADER}\nSUP-0007,Acme Parts,Acme Parts Ltd.,XX,active,qualified,DAP",
    )
    write(
        root,
        "suppliers/SUP-0008_Beta_Works/profile.json",
        json.dumps(
            {
                "exportedAt": "2026-02-01",
                "supplier": {
                    "vendorNo": 8,
                    "supplierId": "SUP-0008",
                    "name": "Beta Works",
                    "legalName": "Beta Works Pvt.",
                    "status": "watch",
                    "qualification": {"status": "conditional", "validUntil": "2026-01-31"},
                },
            }
        ),
    )
    write(
        root,
        "soony/master/components.csv",
        "component_id,description,category,uom,abc_class,critical,drawing_rev\n"
        "CMP-A,Part A,Cat,PC,A,Y,A\nCMP-B,Part B,Cat,PC,B,N,B",
    )
    write(root, "soony/erp/purchase_orders_new.csv", PO_NEW)
    write(root, "soony/erp/purchase_orders_legacy.csv", PO_LEGACY)
    write(root, "soony/erp/goods_receipts.csv", RECEIPTS)
    write(root, "soony/erp/stock_snapshot.csv", STOCK)
    write(root, "soony/erp/open_shipments.csv", SHIPMENTS)
    write_tracker(root)
    # Documents: only their names matter to the linker so far
    write(root, "suppliers/SUP-0007_Acme_Parts/order_confirmations/OA123_9100000001.pdf", "pdf")
    write(
        root,
        "suppliers/SUP-0007_Acme_Parts/order_confirmations/Shipping_advice_020-1_9100000002.pdf",
        "pdf",
    )
    write(root, "soony/receiving/DC-X/20260203_WZ-260202-002.jpg", "jpg")
    write(root, "soony/receiving/DC-X/inspection_report_7000000001.pdf", "pdf")
    write(root, "soony/context/notes.md", "# something new")
    return root


@pytest.fixture
def portal(tmp_path: Path) -> Path:
    return make_portal(tmp_path / "portal")
