"""M2: documents linked by the keys in their filename."""

import pytest

from ask_soony.verifier.linker import build
from ask_soony.verifier.linker.tests.conftest import write

CONF = "confirmation:OA123_9100000001"
ADVICE = "shipping_doc:Shipping_advice_020-1_9100000002"


@pytest.fixture
def graph(portal):
    return build(portal)


def links_of(graph, rule):
    return set(
        zip(graph.links[graph.links.rule == rule].src, graph.links[graph.links.rule == rule].dst)
    )


def issues_of(graph, rule):
    return set(graph.issues[graph.issues.rule == rule].record_id)


def test_confirmation_links_to_its_po_lines_at_po_level(graph):
    links = graph.links[graph.links.rule == "confirmation_to_po_line"]
    assert set(links.dst) == {
        "po_line:9100000001/1",
        "po_line:9100000001/2",
        "po_line:9100000001/51",
    }
    assert set(links.method) == {"po_level"}
    conf = graph.records.set_index("record_id").loc[CONF]
    assert conf.supplier_id == "SUP-0007"  # from the supplier folder


def test_lines_without_confirmation_only_since_the_archive_starts(graph):
    orphans = issues_of(graph, "confirmation_to_po_line")
    assert orphans == {"po_line:9100000002/1"}
    # Legacy 2025 lines are older than the first confirmed order: no confirmation expected


def test_cancellations_need_no_confirmation(portal):
    # Only the oldest PO is confirmed, so the archive starts in 2025 and every later line counts
    (portal / "suppliers/SUP-0007_Acme_Parts/order_confirmations/OA123_9100000001.pdf").unlink()
    write(portal, "suppliers/SUP-0007_Acme_Parts/order_confirmations/OA9_9100000000.pdf", "pdf")
    orphans = issues_of(build(portal), "confirmation_to_po_line")
    assert {"po_line:9100000001/1", "po_line:9100000001/2", "po_line:9100000002/1"} == orphans
    # 9100000001/51 is a cancellation: not expected to be confirmed


def test_scan_links_to_receipt_by_delivery_note(graph):
    assert ("goods_receipt:7000000001", "delivery_note_scan:WZ-260202-002") in links_of(
        graph, "receipt_to_scan"
    )


def test_receipts_without_scan_only_since_scanning_started(graph):
    # GR 7000000002 (after the first scan) has no scan; GR 7000000000 (2025) is older than any scan
    assert issues_of(graph, "receipt_to_scan") == {"goods_receipt:7000000002"}


def test_scan_without_receipt_is_an_orphan(portal):
    write(portal, "soony/receiving/DC-X/20260210_WZ-260209-999.jpg", "jpg")
    assert "delivery_note_scan:WZ-260209-999" in issues_of(build(portal), "receipt_to_scan")


def test_inspection_report_links_to_its_batch(graph):
    assert ("inspection_report:7000000001", "goods_receipt:7000000001") in links_of(
        graph, "inspection_to_receipt"
    )


def test_shipping_doc_links_by_reference(portal):
    path = portal / "soony/erp/open_shipments.csv"
    path.write_text(path.read_text().replace("AWB-002", "020-1"))
    graph = build(portal)
    assert (ADVICE, "shipment:SHP-2") in links_of(graph, "shipping_doc_to_shipment")
    assert (ADVICE, "po_line:9100000002/1") in links_of(graph, "shipping_doc_to_po_line")


def test_shipping_doc_without_shipment_is_an_orphan(graph):
    # Fixture: the advice's AWB 020-1 is not in the feed (SHP-2 says AWB-002)
    assert issues_of(graph, "shipping_doc_to_shipment") == {ADVICE}


def test_document_for_unknown_po_is_an_orphan(portal):
    write(portal, "suppliers/SUP-0007_Acme_Parts/order_confirmations/OA77_9199999999.pdf", "pdf")
    assert "confirmation:OA77_9199999999" in issues_of(build(portal), "confirmation_to_po_line")


def test_confirmation_from_the_wrong_supplier_folder_is_a_conflict(portal):
    write(
        portal,
        "suppliers/SUP-0009_Gamma/profile.csv",
        "supplier_id,name,legal_name,qualification_status\nSUP-0009,Gamma,Gamma AG,qualified",
    )
    write(portal, "suppliers/SUP-0009_Gamma/order_confirmations/OA55_9100000002.pdf", "pdf")
    issues = build(portal).issues
    assert "confirmation:OA55_9100000002" in set(issues[issues.rule == "link_agreement"].record_id)
