"""Shipment truth: precedence of sources, contradictions, silence against the clock, the frozen
list as one finding, and cards only where a silent shipment matters for the launch."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from ask_soony import tracking

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2030, 6, 1, 9, 0, tzinfo=TZ)
LAUNCH = datetime(2030, 6, 2, 20, 0, tzinfo=TZ)


def row(sid="SH-1", **kw):
    base = {
        "shipment_id": sid,
        "mode": "air",
        "reference": f"020-{sid[-1] * 8}",
        "supplier_id": "SUP-9",
        "po_numbers": "PO1",
        "material_ids": "PART-A",
        "route_id": "R1",
        "etd": "2030-05-30",
        "eta": "2030-06-02",
        "status": "in_transit",
        "updated_at": "2030-05-30T20:00:00+02:00",  # 37 h before NOW
        "note": "",
    }
    return {**base, **kw}


def gr(po="PO1", mat="PART-A", qty=500, date="2030-05-31", **kw):
    return {
        "gr_number": "GR-1",
        "gr_date": date,
        "po_number": po,
        "po_line": "1",
        "material_id": mat,
        "qty_ordered": "500",
        "qty_received": str(qty),
        "remark": "",
        "_file": "soony/erp/goods_receipts.csv",
        "_row": 1,
        **kw,
    }


def derive_one(r, extra=None, **kw):
    res = tracking.reconcile(
        [r], {r["shipment_id"]: extra or []}, NOW, launch=LAUNCH, tracker_file="list.csv", **kw
    )
    return res["shipments"][r["shipment_id"]], res["tracker"]


# ---------------------------------------------------------------- precedence


def test_receipt_beats_the_list_and_the_contradiction_is_named():
    r = row()
    claims = tracking.receipt_claims([r], [gr()], TZ)
    d, tr = derive_one(r, claims["SH-1"])
    assert d["status"] == "received" and d["source"] == "receipt"
    assert d["received"]["qty"] == 500 and d["received"]["full"]
    assert d["contradictions"] == [
        "System export: in transit (30 May), ETA 2030-06-02; "
        "Hamburg booked GR GR-1 on 31 May: 500 PART-A"
    ]
    assert tr["contradicted"] == ["SH-1"] and tr["received_in_list"] == ["SH-1"]
    assert not d["silent"] and not d["alert"]  # received shipments are never silent


def test_receipt_from_before_the_row_was_touched_is_an_earlier_delivery():
    r = row()
    old = tracking.receipt_claims([r], [gr(date="2030-05-30")], TZ)  # same day as the update: ok
    assert old["SH-1"]
    before = tracking.receipt_claims([r], [gr(date="2030-05-29")], TZ)  # before it left
    assert before == {}
    other = tracking.receipt_claims([r], [gr(mat="PART-B")], TZ)  # other part, same order
    assert other == {}


def test_one_receipt_goes_to_the_shipment_whose_eta_is_closest():
    a, b = row("SH-1", eta="2030-06-10"), row("SH-2", eta="2030-05-31")
    found = tracking.receipt_claims([a, b], [gr()], TZ)
    assert list(found) == ["SH-2"]


def test_partial_receipt_against_shipped_quantity():
    r = row()
    claims = tracking.receipt_claims([r], [gr(qty=300)], TZ)
    d, _ = derive_one(r, claims["SH-1"], shipped={r["reference"]: 300})
    assert d["received"]["full"] and d["received"]["order_balance"] == 200
    d, _ = derive_one(r, claims["SH-1"], shipped={r["reference"]: 500})
    assert not d["received"]["full"]


def test_site_answer_beats_a_document():
    r = row()
    site = tracking.site_claims(
        [r],
        [
            {
                "kind": "received",
                "question": "ASK-received-SH-1-PART-A",
                "arrived": True,
                "count": 480,
                "at": "2030-06-01T08:00:00+02:00",
                "by": "Dock",
            }
        ],
        TZ,
    )
    doc = tracking.claim("document", "Notice", "delayed", NOW, "Notice: delayed")
    d, _ = derive_one(r, [*site["SH-1"], doc])
    assert d["status"] == "arrived_not_booked" and d["source"] == "site"


def test_site_no_is_news_but_not_a_status():
    r = row()
    site = tracking.site_claims(
        [r],
        [
            {
                "kind": "received",
                "subject": "SH-1:PART-A",
                "arrived": False,
                "at": "2030-06-01T08:00:00+02:00",
            }
        ],
        TZ,
    )
    d, _ = derive_one(r, site["SH-1"])
    assert d["status"] == "in_transit" and d["silent_hours"] == 1.0


def test_newer_document_beats_the_list_older_one_does_not_unless_delay():
    r = row(status="booked")
    newer = tracking.claim("document", "Shipping advice", "departed", NOW, "Shipping advice")
    d, _ = derive_one(r, [newer])
    assert d["status"] == "departed" and d["contradictions"]  # the list still says booked
    older = tracking.claim(
        "document", "PL", "departed", datetime(2030, 5, 1, tzinfo=TZ), "PL: departed"
    )
    d, _ = derive_one(r, [older])
    assert d["status"] == "booked"
    delay = tracking.claim(
        "document", "Notice", "delayed", datetime(2030, 5, 29, tzinfo=TZ), "Notice: delayed"
    )
    d, _ = derive_one(row(), [delay])
    assert d["status"] == "delayed" and d["contradictions"]


def test_shipped_never_undoes_at_the_port():
    r = row(status="arrived_port")
    reply = tracking.claim("reply", "Supplier reply", "departed", NOW, "Reply: shipped")
    d, _ = derive_one(r, [reply])
    assert d["status"] == "at_port" and d["silent_hours"] == 0.0  # but it counts as news


def test_two_lists_that_disagree_are_named():
    r = row(status="arrived_port")
    tables = [
        {
            "file": "soony/logistics/t.xlsx",
            "sheet": "T",
            "row": 1,
            "label": "Logistics sheet",
            "cells": {"Shipment": "SH-1", "Status": "in transit", "Comment": "call terminal"},
        }
    ]
    other = tracking.list_claims([r], tables, TZ)
    d, _ = derive_one(r, other["SH-1"])
    assert d["status"] == "at_port"
    assert any("Logistics sheet: in transit" in c for c in d["contradictions"])


def test_document_found_by_carrier_reference():
    r = row()
    docs = [
        {
            "file": "suppliers/SUP-9/Shipping_advice_020-11111111.pdf",
            "pages": [(1, "Shipping advice (short shipment) 2030-05-31 balance follows")],
        }
    ]
    found = tracking.doc_claims([r], docs, NOW)
    c = found["SH-1"][0]
    assert c["status"] == "departed" and c["short"] and c["at"].startswith("2030-05-31")
    assert tracking.doc_claims([r], [{"file": "x.pdf", "pages": [(1, "nothing")]}], NOW) == {}


def test_reply_tied_through_the_message_it_answers():
    r = row()
    actions = [
        {
            "kind": "approve",
            "status": "sent",
            "message_id": "<a@x>",
            "decision_id": "TRK-x",
            "shipment_id": "SH-1",
        },
        {
            "kind": "reply",
            "in_reply_to": "<a@x>",
            "at": "2030-06-01T08:30:00+02:00",
            "from": "Ann <a@example.com>",
            "summary": "Flight delayed by one day",
        },
    ]
    found = tracking.reply_claims([r], actions, {}, TZ)
    assert found["SH-1"][0]["status"] == "delayed"


# ---------------------------------------------------------------- silence and the frozen list


def test_silence_is_measured_against_the_clock_not_the_newest_row():
    rows = [row("SH-1"), row("SH-2", updated_at="2030-05-30T19:00:00+02:00")]
    res = tracking.reconcile(rows, {}, NOW, launch=LAUNCH, launch_parts={"PART-A"})
    assert res["shipments"]["SH-1"]["silent_hours"] == 37.0  # the newest row is 37 h old
    assert res["shipments"]["SH-1"]["silent"] and res["shipments"]["SH-1"]["alert"]
    assert res["tracker"]["list_age_hours"] == 37.0 and res["tracker"]["frozen"]


def test_fresh_source_makes_a_shipment_heard():
    r = row()
    reply = tracking.claim("reply", "Supplier reply", None, NOW, "Reply: on its way")
    d, _ = derive_one(r, [reply], launch_parts={"PART-A"})
    assert d["silent_hours"] == 0.0 and not d["silent"] and not d["alert"]


def test_what_matters_for_the_launch():
    late = row("SH-1", eta="2030-06-20")
    d, _ = derive_one(late, launch_parts={"PART-A"})
    assert d["silent"] and not d["matters"]  # arrives long after the trucks
    soon_after = row("SH-2", eta="2030-06-03")
    d, _ = derive_one(soon_after, launch_parts={"PART-A"}, short_copies={"PART-A": 120})
    assert d["matters"] and d["copies"] == 120  # short part, could be pulled forward
    d, _ = derive_one(soon_after, launch_parts={"PART-A"})
    assert not d["matters"]
    other = row("SH-3", material_ids="OTHER")
    d, _ = derive_one(other, launch_parts={"PART-A"})
    assert not d["matters"]


# ---------------------------------------------------------------- cards


def _cards(rows, extra=None, statuses=None, actions=None, **kw):
    res = tracking.reconcile(
        rows,
        extra or {},
        NOW,
        launch=LAUNCH,
        launch_parts={"PART-A"},
        short_copies={"PART-A": 120},
        tracker_file="list.csv",
    )
    return tracking.cards(
        res,
        rows,
        NOW,
        emails={"SUP-9": {"email": "a@example.com", "contact": "Ann"}},
        nodes={"SUP-9": {"name": "Acme"}},
        statuses=statuses or {},
        actions=actions or [],
        viewer="Pat Buyer",
        launch=LAUNCH,
        **kw,
    )


def test_frozen_list_that_contradicts_receipts_is_one_card():
    rows = [row("SH-1"), row("SH-2"), row("SH-3", eta="2030-06-20")]
    rec = tracking.receipt_claims(rows[:1], [gr()], TZ)
    cards = _cards(rows, rec)
    assert [c["id"].split("-")[1] for c in cards] == ["feed"]  # not one alarm per row
    feed = cards[0]
    assert feed["headline"] == (
        "The shipment list has not changed for 37 h; 1 shipment in it is already received"
    )
    assert "120 copies depend on" in feed["summary"]
    email = feed["options"][0]
    assert email["channel"] == "email" and email["routine_type"] == "ask_status"
    assert [m["shipments"] for m in email["messages"]] == [["SH-2"]]  # needed and silent only
    assert feed["options"][1]["channel"] == "notify"
    assert "SH-1" in feed["options"][1]["draft"]


def test_no_feed_card_when_the_list_is_fresh_or_uncontradicted():
    fresh = [row("SH-1", updated_at="2030-06-01T08:00:00+02:00")]
    rec = tracking.receipt_claims(fresh, [gr(date="2030-06-01")], TZ)
    assert _cards(fresh, rec) == []  # fresh list, received: nothing to do
    stale = [row("SH-1", eta="2030-06-20")]
    assert _cards(stale) == []  # frozen, but nothing contradicts it and nothing needed is silent


def test_silent_needed_shipment_gets_its_own_card_when_the_list_is_fine():
    rows = [row("SH-1", status="booked", etd="2030-05-31")]
    cards = _cards(rows, site_questions={"SH-1": "ASK-received-SH-1-PART-A"})
    assert [c["id"] for c in cards] == ["TRK-silent-SH-1"]
    c = cards[0]
    assert "should have left on 2030-05-31" in c["summary"]
    assert "120 copies depend on" in c["summary"]
    assert c["options"][0]["to"] == "a@example.com" and c["options"][0]["recommended"]
    assert c["level"] == "amber" and c["shipment"] == "SH-1"
    # ETA not passed: no question to the site yet
    assert all(o["channel"] != "floor" for o in c["options"])
    due = [row("SH-1", eta="2030-05-31")]
    c = _cards(due, site_questions={"SH-1": "ASK-received-SH-1-PART-A"})[0]
    assert c["options"][1]["channel"] == "floor"
    assert c["options"][1]["question"] == "ASK-received-SH-1-PART-A"


def test_shipment_card_folds_into_an_open_feed_card_and_waits_after_asking():
    rows = [row("SH-1"), row("SH-2")]
    rec = tracking.receipt_claims(rows[:1], [gr()], TZ)
    ids = [c["id"] for c in _cards(rows, rec)]
    assert ids == ["TRK-feed-203005302000"]
    # the feed card dismissed: the needed shipment shows up on its own
    dismissed = {"TRK-feed-203005302000": {"status": "dismissed"}}
    assert [c["id"] for c in _cards(rows, rec, statuses=dismissed)][1:] == ["TRK-silent-SH-2"]
    # a status question sent an hour ago: we wait instead of asking again
    asked = [{"status": "sent", "shipment_id": "SH-2", "at": "2030-06-01T08:00:00+02:00"}]
    assert [c["id"] for c in _cards(rows, rec, statuses=dismissed, actions=asked)][1:] == []


def test_received_and_unneeded_shipments_get_no_card():
    rows = [row("SH-1", updated_at="2030-06-01T08:00:00+02:00", eta="2030-05-31")]
    rec = tracking.receipt_claims(rows, [gr(date="2030-06-01")], TZ)
    assert _cards(rows, rec) == []
    assert _cards([row("SH-1", material_ids="OTHER", updated_at="2030-06-01T08:00:00+02:00")]) == []


# ---------------------------------------------------------------- sending


def test_feed_email_goes_out_once_per_supplier(tmp_path, monkeypatch):
    monkeypatch.setenv("ASK_STATE_DIR", str(tmp_path))
    sent = []
    option = {
        "action": "Email",
        "messages": [
            {"to": "a@example.com", "subject": "S", "body": "one", "shipments": ["SH-1"]},
            {"to": "b@example.com", "subject": "S", "body": "two", "shipments": ["SH-2"]},
            {"to": "stranger@example.net", "subject": "S", "body": "x", "shipments": ["SH-3"]},
        ],
    }
    out = tracking.send_each(
        {"id": "TRK-feed-1"},
        option,
        None,
        {"a@example.com", "b@example.com"},
        lambda to, subj, body: sent.append((to, body)) or f"<{to}>",
    )
    assert sent == [("a@example.com", "one"), ("b@example.com", "two")]  # unknown: not written
    assert out["message_id"] == "<a@example.com>"
    acts = json.loads((tmp_path / "actions.json").read_text())
    assert [(a["message_id"], a["decision_id"], a["shipment_ids"]) for a in acts] == [
        ("<b@example.com>", "TRK-feed-1", ["SH-2"])
    ]
    edited = "To a@example.com:\nnew one\n\n---\n\nTo b@example.com:\nnew two"
    sent.clear()
    tracking.send_each(
        {"id": "TRK-feed-1"},
        option,
        edited,
        {"a@example.com", "b@example.com"},
        lambda to, subj, body: sent.append((to, body)) or "<x>",
    )
    assert sent == [("a@example.com", "new one"), ("b@example.com", "new two")]


# ---------------------------------------------------------------- the state


def test_state_carries_derived_status_and_the_tracker(agent_env, tmp_path, monkeypatch):
    erp = tmp_path / "portal" / "soony" / "erp"
    (erp / "open_shipments.csv").write_text(
        "shipment_id,reference,route_id,mode,supplier_id,po_numbers,material_ids,status,etd,eta,"
        "updated_at\n"
        "SH-1,020-11111111,R1,air,SUP-9,PO1,PART-A,in_transit,2030-05-30,2030-06-01,"
        "2030-05-31T08:00:00+02:00\n"
        "SH-2,020-22222222,R1,air,SUP-9,PO2,PART-A,booked,2030-05-30,2030-06-02,"
        "2030-05-31T08:00:00+02:00\n"
    )
    (erp / "goods_receipts.csv").write_text(
        "gr_number,gr_date,po_number,po_line,supplier_id,material_id,plant,qty_ordered,"
        "qty_received,delivery_note,inspector,remark\n"
        "GR-9,2030-06-01,PO1,1,SUP-9,PART-A,DC,500,500,DN-1,X,\n"
    )
    state = agent_env["client"].get("/api/state").json()
    ships = {s["id"]: s for s in state["shipments"]}
    assert ships["SH-1"]["status"] == "received" and ships["SH-1"]["list_status"] == "in_transit"
    d = ships["SH-1"]["derived"]
    assert d["source"] == "receipt" and "GR GR-9" in d["contradictions"][0]
    assert ships["SH-2"]["derived"]["silent_hours"] == 37.0  # 21:00 vs 08:00 the day before
    assert state["tracker"]["list_age_hours"] == 37.0
    assert state["tracker"]["contradicted"] == ["SH-1"]
    feed = [c for c in state["cards"] if c["id"].startswith("TRK-feed-")]
    assert len(feed) == 1 and feed[0]["status"] == "open"
    # approving the recommendation asks the supplier, in one email (sender mocked)
    client = agent_env["client"]
    out = client.post(f"/api/cards/{feed[0]['id']}/approve", json={"option": 0}).json()
    assert out["action"]["status"] == "sent"
    assert [(to, subj) for to, subj, _ in agent_env["sent"]] == [
        ("sales@supplier.example.com", "Status of our open shipments")
    ]
    assert "SH-2" in agent_env["sent"][0][2] and "SH-1" not in agent_env["sent"][0][2]
    again = client.get("/api/state").json()
    assert not [c for c in again["cards"] if c["id"] == "TRK-silent-SH-2"]  # waiting for reply


# ---------------------------------------------------------------- the hand-kept tracker


def hk_row(sid="SH-1", status="in transit", eta="2030-06-02", comment=None, **kw):
    return {
        "Shipment": sid,
        "Container/AWB": "",
        "POs": "PO1",
        "ETA": eta,
        "Status": status,
        "Comment": comment,
        "_row": 1,
        "_id": sid,
        **kw,
    }


def test_handkept_comment_and_initials():
    r = row()
    found = tracking.handkept_claims(
        [r], [hk_row(comment="RP: call terminal")], TZ, people={"RP": "Raj Patel"}
    )
    c = found["SH-1"][0]
    assert c["source"] == "handkept" and c["comment"] == "RP: call terminal"
    assert c["by"] == "Raj Patel" and "(Raj Patel)" in c["text"]
    assert c["at"] is None  # no date column and never seen changing: no news
    plain = tracking.handkept_claims([r], [hk_row(comment="XY: ask")], TZ, people={})
    assert plain["SH-1"][0]["by"] == "XY"


def test_handkept_found_by_reference_too():
    r = row()
    found = tracking.handkept_claims([r], [hk_row(sid="", **{"Container/AWB": r["reference"]})], TZ)
    assert list(found) == ["SH-1"]


def test_handkept_beats_the_export_only_when_newer_and_not_behind():
    r = row(status="booked")  # export updated 30 May 20:00
    newer = tracking.handkept_claims(
        [r], [hk_row(status="in transit")], TZ, changed_at=datetime(2030, 6, 1, 7, tzinfo=TZ)
    )
    d, _ = derive_one(r, newer["SH-1"])
    assert d["status"] == "in_transit" and d["source"] == "handkept"
    assert d["silent_hours"] == 2.0  # a changed tracker is news
    behind = tracking.handkept_claims(
        [row(status="arrived_port")],
        [hk_row(status="in transit", comment="RP: call terminal")],
        TZ,
        changed_at=datetime(2030, 6, 1, 7, tzinfo=TZ),
    )
    d, _ = derive_one(row(status="arrived_port"), behind["SH-1"])
    assert d["status"] == "at_port" and d["source"] == "tracker" and d["handkept_behind"]
    assert any(c.startswith("Hand-kept tracker: in transit") for c in d["contradictions"])
    unchanged = tracking.handkept_claims([r], [hk_row(status="in transit")], TZ)
    d, _ = derive_one(r, unchanged["SH-1"])
    assert d["status"] == "booked"  # undated: the export stays


def test_handkept_eta_and_presence_flags():
    rows = [row("SH-1"), row("SH-2")]
    found = tracking.handkept_claims(rows, [hk_row("SH-1", eta="2030-06-05")], TZ)
    res = tracking.reconcile(
        rows,
        found,
        NOW,
        launch=LAUNCH,
        handkept={"file": "t.xlsx", "ids": ["SH-1", "SH-9"]},
    )
    d = res["shipments"]["SH-1"]
    assert "System export: ETA 2030-06-02; Hand-kept tracker: ETA 2030-06-05" in d["contradictions"]
    hk = res["tracker"]["handkept"]
    assert hk["only_in_export"] == ["SH-2"] and hk["only_in_handkept"] == ["SH-9"]
    assert hk["contradicted"] == ["SH-1"]
    assert res["shipments"]["SH-2"]["on_handkept"] is False


def test_comment_reaches_the_card():
    rows = [row("SH-1", status="arrived_port", eta="2030-05-31")]
    hk = tracking.handkept_claims(
        rows, [hk_row(comment="RP: call terminal")], TZ, people={"RP": "Raj Patel"}
    )
    c = _cards(rows, hk)[0]
    assert "comment “RP: call terminal” (Raj Patel)" in c["summary"]
    assert any("call terminal" in (e.get("value") or "") for e in c["evidence"])


def test_handkept_file_found_by_its_columns_and_dated_by_the_store(tmp_path, monkeypatch):
    import sqlite3

    import openpyxl

    (tmp_path / "soony" / "misc").mkdir(parents=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Shipment", "Carrier", "Container/AWB", "POs", "ETA", "Status", "Comment"])
    ws.append(["SH-1", "C", "X", "PO1", "2030-06-02", "in transit", "AB: chase"])
    wb.save(tmp_path / "soony" / "misc" / "whatever_name.xlsx")
    hk = tracking.read_handkept(tmp_path)
    assert hk["file"] == "soony/misc/whatever_name.xlsx" and hk["rows"][0]["_id"] == "SH-1"
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("ASK_DATA_DIR", str(store))
    assert tracking.file_times(tmp_path, hk["file"]) == (None, None)
    con = sqlite3.connect(store / "ask-soony.db")
    con.execute("CREATE TABLE versions (id INTEGER PRIMARY KEY, path TEXT, first_seen TEXT)")
    con.execute(
        "INSERT INTO versions (path, first_seen) VALUES (?, '2030-05-31T10:00:00+00:00')",
        (hk["file"],),
    )
    con.commit()
    changed, seen = tracking.file_times(tmp_path, hk["file"])
    assert changed is None and seen.isoformat().startswith("2030-05-31T10:00")
    con.execute(
        "INSERT INTO versions (path, first_seen) VALUES (?, '2030-06-01T06:00:00+00:00')",
        (hk["file"],),
    )
    con.commit()
    con.close()
    changed, _ = tracking.file_times(tmp_path, hk["file"])
    assert changed.isoformat().startswith("2030-06-01T06:00")
