import pytest

from verifier.linker import build
from verifier.linker.graph import trace
from verifier.linker.rules import RULES
from verifier.linker.tests.conftest import write


def links_of(graph, rule):
    return set(zip(*[graph.links[graph.links.rule == rule][c] for c in ("src", "dst")]))


def issues_of(graph, rule):
    return set(graph.issues[graph.issues.rule == rule].record_id)


@pytest.fixture
def graph(portal):
    return build(portal)


def test_complete_chain_links_fully(graph):
    assert ("goods_receipt:7000000001", "po_line:9100000001/1") in links_of(
        graph, "receipt_to_po_line"
    )
    assert ("stock_batch:7000000001", "goods_receipt:7000000001") in links_of(
        graph, "stock_to_receipt"
    )
    assert ("shipment:SHP-1", "tracker_row:SHP-1") in links_of(graph, "shipment_to_tracker")
    assert ("shipment:SHP-1", "po_line:9100000001/1") in links_of(graph, "shipment_to_po_line")


def test_legacy_line_numbers_link_to_receipts(graph):
    assert ("goods_receipt:7000000000", "po_line:9100000000/1") in links_of(
        graph, "receipt_to_po_line"
    )


def test_link_methods_are_honest(graph):
    # The feed has no line numbers: shipment links are PO-level candidates, never 'exact'
    shipment_links = graph.links[graph.links.rule == "shipment_to_po_line"]
    assert set(shipment_links.method) == {"po_level"}
    # Every link carries its rule's method; only PO-number joins are po_level
    methods = {rule.name: rule.method for rule in RULES}
    for link in graph.links[graph.links.rule.isin(methods)].itertuples():
        assert link.method == methods[link.rule], link
    for rule in RULES:
        on_po_only = {s for s, _ in rule.on} <= {"po_number", "po_numbers", "material_ids"}
        assert (rule.method == "po_level") == (on_po_only and rule.dst == "po_line"), rule.name
    coverage = graph.coverage.set_index("rule")
    assert coverage.at["shipment_to_po_line", "method"] == "po_level"


def test_orphans_carry_context(graph):
    issues = graph.issues[graph.issues.record_id == "shipment:SHP-2"]
    assert "status=booked" in issues.context.iloc[0]
    assert "mode=air" in issues.context.iloc[0]


def test_receipt_without_po_is_an_orphan(graph):
    assert issues_of(graph, "receipt_to_po_line") >= {"goods_receipt:7000000002"}
    assert "goods_receipt:7000000002" in issues_of(graph, "goods_receipt_to_part")


def test_delivered_line_without_receipt_is_an_orphan(graph):
    orphans = issues_of(graph, "receipt_to_po_line")
    assert "po_line:9100000002/1" in orphans
    # Open lines and cancellations are not expected to have a receipt
    assert "po_line:9100000001/2" not in orphans
    assert "po_line:9100000001/51" not in orphans


def test_stock_batch_without_receipt_is_an_orphan(graph):
    assert issues_of(graph, "stock_to_receipt") == {"stock_batch:7000000099"}


def test_tracker_gaps_both_ways(graph):
    assert issues_of(graph, "shipment_to_tracker") == {
        "shipment:SHP-2",
        "shipment:SHP-3",
        "tracker_row:SHP-9",
    }


def test_shipment_with_unknown_po(graph):
    assert issues_of(graph, "shipment_po_known") == {"shipment:SHP-3"}
    assert "shipment:SHP-3" in issues_of(graph, "shipment_to_po_line")


def test_coverage_counts(graph):
    row = graph.coverage[(graph.coverage.rule == "stock_to_receipt")].iloc[0]
    assert (row.expected, row.linked) == (2, 1)


def test_open_line_with_receipt_is_flagged(portal):
    path = portal / "soony/erp/goods_receipts.csv"
    path.write_text(
        path.read_text()
        + "7000000003,2026-03-01,9100000001,2,SUP-0007,CMP-B,DC-X,300,300,WZ-260301-004,Insp A,\n"
    )
    assert issues_of(build(portal), "open_line_received") == {"po_line:9100000001/2"}


def test_open_line_check_works_on_legacy_status_codes(portal):
    # Legacy lines say 'GL'/'LO', never 'open': the check must use facts, not status words
    path = portal / "soony/erp/purchase_orders_legacy.csv"
    path.write_text(
        path.read_text() + "9100000005;00010;7;CMP-A;DC-X;10;1,0;01/02/2026;02/02/2026;;;XX;BUYA\n"
    )
    receipts = portal / "soony/erp/goods_receipts.csv"
    receipts.write_text(
        receipts.read_text()
        + "7000000005,2026-02-03,9100000005,1,SUP-0007,CMP-A,DC-X,10,10,WZ-260203-005,Insp A,\n"
    )
    graph = build(portal)
    assert issues_of(graph, "open_line_received") == {"po_line:9100000005/1"}
    states = dict(
        zip(graph.records.record_id, graph.records["attrs"].map(lambda a: a.get("state")))
    )
    assert states["po_line:9100000000/1"] == "delivered"
    assert states["po_line:9100000000/51"] == "cancellation"


def test_linked_records_must_agree_on_supplier_and_material(portal):
    write(
        portal,
        "suppliers/SUP-0009_Gamma/profile.csv",
        "supplier_id,name,legal_name,qualification_status\nSUP-0009,Gamma,Gamma AG,qualified",
    )
    path = portal / "soony/erp/goods_receipts.csv"
    # Receipt for PO 9100000002/1 (SUP-0007, CMP-B), booked with another supplier and material
    path.write_text(
        path.read_text()
        + "7000000006,2026-03-01,9100000002,1,SUP-0009,CMP-A,DC-X,200,200,WZ-260301-006,Insp A,\n"
    )
    issues = build(portal).issues
    agreement = issues[issues.rule == "link_agreement"]
    assert set(agreement.record_id) == {"goods_receipt:7000000006"}
    assert {d.split(" ")[0] for d in agreement.detail} == {"supplier_id", "material_id"}


def test_clean_data_has_no_agreement_issues(graph):
    assert issues_of(graph, "link_agreement") == set()


def test_tracker_and_feed_po_lists_disagree(portal):
    path = portal / "soony/erp/open_shipments.csv"
    path.write_text(path.read_text().replace("9100000001|9100000002", "9100000001"))
    assert issues_of(build(portal), "tracker_pos_match") == {"shipment:SHP-1"}


def test_delivery_note_shared_across_suppliers(portal):
    write(
        portal,
        "suppliers/SUP-0009_Gamma/profile.csv",
        "supplier_id,name,legal_name,qualification_status\nSUP-0009,Gamma,Gamma AG,qualified",
    )
    path = portal / "soony/erp/goods_receipts.csv"
    path.write_text(
        path.read_text()
        + "7000000004,2026-03-01,9100000002,1,SUP-0009,CMP-B,DC-X,200,200,WZ-260202-002,Insp A,\n"
    )
    assert issues_of(build(portal), "delivery_note_suppliers") == {
        "goods_receipt:7000000001",
        "goods_receipt:7000000004",
    }


def test_unknown_supplier_is_reported(portal):
    path = portal / "soony/erp/purchase_orders_new.csv"
    path.write_text(
        path.read_text() + "9100000003,1,SUP-0666,CMP-A,DC-X,1,1,EUR,2026-01-01,,,,open,X,DAP,\n"
    )
    assert issues_of(build(portal), "unresolved_supplier") == {"po_line:9100000003/1"}


CSV_VERSION = "supplier:SUP-0007@suppliers/SUP-0007_Acme_Parts/profile.csv"
JSON_VERSION = "supplier:SUP-0007@suppliers/SUP-0007_Acme_Parts/profile.json"


def test_two_versions_that_disagree_keep_both_and_name_the_fields(portal):
    write(
        portal,
        "suppliers/SUP-0007_Acme_Parts/profile.json",
        '{"supplier": {"supplierId": "SUP-0007", "name": "Acme Parts", "status": "at risk"}}',
    )
    graph = build(portal)
    versions = graph.records[graph.records.entity == "supplier:SUP-0007"]
    assert set(versions.record_id) == {CSV_VERSION, JSON_VERSION}  # neither silently wins
    assert (CSV_VERSION, JSON_VERSION) in links_of(graph, "same_entity")
    issue = graph.issues[graph.issues.rule == "versions"].iloc[0]
    assert issue.type == "conflict"
    assert issue.context.startswith("status: active (profile.csv) vs at risk (profile.json)")
    assert "as_of: unknown (profile.csv) vs unknown (profile.json)" in issue.context


def test_identical_versions_are_a_duplicate_not_a_conflict(portal):
    write(
        portal,
        "suppliers/SUP-0007_Acme_Parts/profile.json",
        '{"supplier": {"supplierId": "SUP-0007", "name": "Acme Parts", "status": "active"}}',
    )
    issue = build(portal).issues.query("rule == 'versions'").iloc[0]
    assert issue.type == "duplicate"


def test_trace_shows_every_version(portal):
    write(
        portal,
        "suppliers/SUP-0007_Acme_Parts/profile.json",
        '{"supplier": {"supplierId": "SUP-0007", "name": "Acme Parts", "status": "at risk"}}',
    )
    reached = {rid for _, rid, _ in trace(build(portal), "SUP-0007")}
    assert {CSV_VERSION, JSON_VERSION} <= reached


def test_trace_records_the_hop(graph):
    hops = {rid: via for _, rid, via in trace(graph, "9100000001")}
    # PO 9100000002 is reached through the shipment that carries both orders
    assert hops["po_line:9100000002/1"] == "shipment:SHP-1"


def test_trace_follows_the_chain_but_not_through_hubs(graph):
    reached = {rid for _, rid, _ in trace(graph, "9100000001")}
    assert {
        "po_line:9100000001/1",
        "goods_receipt:7000000001",
        "stock_batch:7000000001",
        "shipment:SHP-1",
        "tracker_row:SHP-1",
    } <= reached
    # part:CMP-A is reached, but trace must not walk on to every other record of that part
    assert "goods_receipt:7000000000" not in reached
