"""Issue priority (live/closed, due), trace leaves, as_of and one-sided version fields."""

from verifier.linker import build
from verifier.linker.graph import trace
from verifier.linker.tests.conftest import write


def add(portal, rel: str, line: str) -> None:
    path = portal / rel
    path.write_text(path.read_text() + line + "\n")


def issue(graph, rule, record_id):
    rows = graph.issues[(graph.issues.rule == rule) & (graph.issues.record_id == record_id)]
    assert len(rows) == 1, (rule, record_id)
    return rows.iloc[0]


def test_open_line_is_live_delivered_line_is_closed(portal):
    add(
        portal,
        "soony/erp/purchase_orders_new.csv",
        "9100000004,1,SUP-0007,CMP-A,DC-X,50,1.5,EUR,2026-02-01,2026-03-01,2026-03-05,,open,Buyer A,DAP,",
    )
    graph = build(portal)
    open_line = issue(graph, "confirmation_to_po_line", "po_line:9100000004/1")
    assert (bool(open_line.live), open_line.due) == (True, "2026-03-05")  # due = promised date
    delivered = issue(graph, "confirmation_to_po_line", "po_line:9100000002/1")
    assert not delivered.live


def test_issues_are_sorted_live_first_then_soonest(portal):
    graph = build(portal)
    live = graph.issues.live.astype(bool).tolist()
    assert live == sorted(live, reverse=True)  # no live issue after a closed one
    dues = [d for d, lv in zip(graph.issues.due, live) if lv and d]
    assert dues == sorted(dues)


def test_shipments_are_live_and_due_at_their_eta(portal):
    graph = build(portal)
    gap = issue(graph, "shipment_to_tracker", "shipment:SHP-2")
    assert (bool(gap.live), gap.due) == (True, "2026-02-03")


def test_receipt_inherits_closed_from_used_up_batch_and_delivered_line(portal):
    add(
        portal,
        "soony/erp/goods_receipts.csv",
        "7000000007,2026-02-12,9100000002,1,SUP-0007,CMP-B,DC-X,200,200,WZ-260211-007,Insp A,",
    )
    add(
        portal,
        "soony/erp/stock_snapshot.csv",
        "2026-02-10,DC-X,CMP-B,7000000007,0,PC,0,1,2026-02-01",
    )
    graph = build(portal)
    assert not issue(graph, "receipt_to_scan", "goods_receipt:7000000007").live


def test_unknown_is_not_hidden(portal):
    # The pin-like receipt links to nothing that has a live/closed rule: it stays live
    assert issue(build(portal), "receipt_to_po_line", "goods_receipt:7000000002").live


def test_trace_shows_other_orders_but_does_not_expand_them(portal):
    reached = {rid for _, rid, _ in trace(build(portal), "9100000001")}
    assert "po_line:9100000002/1" in reached  # shown: shares shipment SHP-1
    # Only linked to PO 9100000002: must not be pulled into PO 9100000001's trace
    assert "shipping_doc:Shipping_advice_020-1_9100000002" not in reached


def test_as_of_comes_from_the_source(portal):
    records = build(portal).records.set_index("record_id")
    assert records.at["stock_batch:7000000001", "as_of"] == "2026-02-10T00:00:00Z"  # snapshot
    assert records.at["delivery_note_scan:WZ-260202-002", "as_of"] == "2026-02-03T00:00:00Z"
    assert records.at["supplier:SUP-0008", "as_of"] == "2026-02-01T00:00:00Z"  # exportedAt
    assert records.at["supplier:SUP-0007", "as_of"] is None  # CSV profile: no date


def test_version_issue_lists_one_sided_fields_and_as_of(portal):
    write(
        portal,
        "suppliers/SUP-0007_Acme_Parts/profile.json",
        '{"exportedAt": "2026-02-01T09:00:00", "supplier": {"supplierId": "SUP-0007", '
        '"name": "Acme Parts", "status": "watch", "qualification": {"validUntil": "2026-01-31"}}}',
    )
    context = issue(
        build(portal), "versions", "supplier:SUP-0007@suppliers/SUP-0007_Acme_Parts/profile.csv"
    ).context
    assert "status: active (profile.csv) vs watch (profile.json)" in context
    assert "only in profile.json:" in context and "qualification_valid_until=2026-01-31" in context
    assert "as_of: unknown (profile.csv) vs 2026-02-01T09:00:00Z (profile.json)" in context


def test_receipt_without_stock_row_stays_live(portal):
    # Delivered PO line, no stock row at all: unknown, so not hidden (the PO line must not close it)
    add(
        portal,
        "soony/erp/goods_receipts.csv",
        "7000000008,2026-02-12,9100000002,1,SUP-0007,CMP-B,DC-X,200,200,WZ-260211-008,Insp A,",
    )
    assert issue(build(portal), "receipt_to_scan", "goods_receipt:7000000008").live


def test_receipt_with_stock_in_hand_is_live(portal):
    add(
        portal,
        "soony/erp/goods_receipts.csv",
        "7000000009,2026-02-12,9100000002,1,SUP-0007,CMP-B,DC-X,200,200,WZ-260211-009,Insp A,",
    )
    add(
        portal,
        "soony/erp/stock_snapshot.csv",
        "2026-02-10,DC-X,CMP-B,7000000009,150,PC,0,1,2026-02-01",
    )
    assert issue(build(portal), "receipt_to_scan", "goods_receipt:7000000009").live


def test_shipment_as_of_with_offset_becomes_utc(portal):
    path = portal / "soony/erp/open_shipments.csv"
    rows = path.read_text().splitlines()
    rows[1] = rows[1].replace(",in_transit,,,,", ",in_transit,,,2026-01-30T10:00:00+01:00,")
    path.write_text("\n".join(rows) + "\n")
    records = build(portal).records.set_index("record_id")
    assert records.at["shipment:SHP-1", "as_of"] == "2026-01-30T09:00:00Z"
