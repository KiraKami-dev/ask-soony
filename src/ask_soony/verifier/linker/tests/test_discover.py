from ask_soony.verifier.linker.discover import discover


def kinds(root):
    return {s.path: s for s in discover(root)}


def test_files_are_classified_by_content(portal):
    found = kinds(portal)
    expected = {
        "soony/erp/purchase_orders_new.csv": "purchase_orders",
        "soony/erp/purchase_orders_legacy.csv": "purchase_orders_sap",
        "soony/erp/goods_receipts.csv": "goods_receipts",
        "soony/erp/stock_snapshot.csv": "stock",
        "soony/erp/open_shipments.csv": "shipments",
        "soony/logistics/tracker.xlsx": "shipment_tracker",
        "soony/master/components.csv": "parts_master",
        "suppliers/SUP-0007_Acme_Parts/profile.csv": "supplier_profile",
        "suppliers/SUP-0008_Beta_Works/profile.json": "supplier_profile_json",
    }
    for path, kind in expected.items():
        assert found[path].kind == kind, path


def test_tracker_header_found_below_a_title_row(portal):
    tracker = kinds(portal)["soony/logistics/tracker.xlsx"]
    assert tracker.meta == {"sheet": "Tracker", "header_row": 1}


def test_documents_get_keys_from_their_filename(portal):
    found = kinds(portal)
    conf = found["suppliers/SUP-0007_Acme_Parts/order_confirmations/OA123_9100000001.pdf"]
    assert (conf.kind, conf.meta["po_number"]) == ("confirmation", "9100000001")
    advice = found[
        "suppliers/SUP-0007_Acme_Parts/order_confirmations/Shipping_advice_020-1_9100000002.pdf"
    ]
    assert (advice.kind, advice.meta["awb"], advice.meta["po_number"]) == (
        "shipping_advice",
        "020-1",
        "9100000002",
    )
    note = found["soony/receiving/DC-X/20260203_WZ-260202-002.jpg"]
    assert (note.kind, note.meta["delivery_note"]) == ("delivery_note", "WZ-260202-002")
    report = found["soony/receiving/DC-X/inspection_report_7000000001.pdf"]
    assert (report.kind, report.meta["batch"]) == ("inspection_report", "7000000001")


def test_unknown_files_are_reported_not_dropped(portal):
    assert kinds(portal)["soony/context/notes.md"].kind == "unknown"


def test_broken_file_is_reported_not_fatal(portal):
    (portal / "soony/erp/broken.xlsx").write_bytes(b"not a workbook")
    broken = kinds(portal)["soony/erp/broken.xlsx"]
    assert broken.kind == "unknown" and "error" in broken.meta


def test_every_file_has_a_checksum(portal):
    assert all(len(s.sha256) == 64 for s in discover(portal))
