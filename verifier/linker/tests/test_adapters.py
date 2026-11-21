import pytest

from verifier.linker import build


@pytest.fixture
def graph(portal):
    return build(portal)


def record(graph, record_id):
    rows = graph.records[graph.records.record_id == record_id]
    assert len(rows) == 1, record_id
    return rows.iloc[0]


def test_legacy_and_new_po_exports_give_the_same_shape(graph):
    legacy = record(graph, "po_line:9100000000/1")
    new = record(graph, "po_line:9100000001/1")
    assert (legacy.po_line, legacy.supplier_id, legacy.material_id) == ("1", "SUP-0007", "CMP-A")
    assert (new.po_line, new.supplier_id, new.material_id) == ("1", "SUP-0007", "CMP-A")
    assert legacy["attrs"]["unit_price"] == 1.5 == new["attrs"]["unit_price"]
    assert legacy["attrs"]["order_date"] == "2025-08-12"
    assert legacy["attrs"]["delivered_date"] == "2025-11-02"


def test_legacy_cancellation_line(graph):
    cancel = record(graph, "po_line:9100000000/51")
    assert cancel["attrs"]["quantity"] == -200
    assert cancel["attrs"]["delivered_date"] is None


def test_stock_rows_with_and_without_batch(graph):
    assert record(graph, "stock_batch:7000000001")["attrs"]["quantity"] == 500
    assert record(graph, "stock_total:FG-X").batch is None


def test_shipment_lists_are_split(graph):
    shipment = record(graph, "shipment:SHP-1")
    assert shipment.po_numbers == ["9100000001", "9100000002"]
    assert shipment.material_ids == ["CMP-A", "CMP-B"]
    tracker = record(graph, "tracker_row:SHP-1")
    assert tracker.po_numbers == ["9100000001", "9100000002"]
    assert tracker.supplier_id == "SUP-0007"  # resolved from the display name


def test_json_profile_is_read(graph):
    supplier = record(graph, "supplier:SUP-0008")
    assert supplier["attrs"]["status"] == "watch"
    assert supplier["attrs"]["qualification_valid_until"] == "2026-01-31"


def test_records_point_at_their_file_version(graph):
    po = record(graph, "po_line:9100000001/1")
    assert po.source == "soony/erp/purchase_orders_new.csv"
    assert len(po.source_sha256) == 64
