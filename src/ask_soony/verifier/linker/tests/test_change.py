"""The portal changes during the hack. New and changed files must work without code changes."""

from ask_soony.verifier.linker import build
from ask_soony.verifier.linker.graph import save
from ask_soony.verifier.linker.tests.conftest import write


def test_new_file_with_a_known_layout_is_picked_up(portal):
    before = build(portal)
    write(
        portal,
        "soony/erp/shipments_late_drop.csv",
        "shipment_id,carrier_id,mode,reference,supplier_id,po_numbers,material_ids,etd,eta,status\n"
        "SHP-7,LSP-9,road,TRUCK7,SUP-0007,9100000002,CMP-B,2026-02-02,2026-02-04,booked",
    )
    after = build(portal)
    assert "shipment:SHP-7" not in set(before.records.record_id)
    assert ("shipment:SHP-7", "po_line:9100000002/1") in set(zip(after.links.src, after.links.dst))


def test_changed_file_is_rebuilt(portal):
    path = portal / "soony/erp/stock_snapshot.csv"
    path.write_text(path.read_text().replace("7000000099", "7000000001"))
    graph = build(portal)
    assert "stock_to_receipt" not in set(graph.issues.rule)


def test_new_supplier_profile_teaches_the_resolver(portal):
    path = portal / "soony/erp/purchase_orders_legacy.csv"
    path.write_text(
        path.read_text() + "9100000009;00010;42;CMP-A;DC-X;5;1,0;01/02/2026;02/02/2026;;;GL;X\n"
    )
    assert "unresolved_supplier" in set(build(portal).issues.rule)
    write(
        portal,
        "suppliers/SUP-0042_New_Co/profile.json",
        '{"supplier": {"vendorNo": 42, "supplierId": "SUP-0042", "name": "New Co"}}',
    )
    graph = build(portal)
    assert "unresolved_supplier" not in set(graph.issues.rule)
    line = graph.records[graph.records.record_id == "po_line:9100000009/1"].iloc[0]
    assert line.supplier_id == "SUP-0042"


def test_save_writes_all_tables(portal, tmp_path):
    import sqlite3

    db = tmp_path / "verifier.db"
    save(build(portal), db)
    with sqlite3.connect(db) as conn:
        tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {
        "linker_sources",
        "linker_records",
        "linker_links",
        "linker_issues",
        "linker_coverage",
        "linker_meta",
    } <= tables
