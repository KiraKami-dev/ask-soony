"""Paper check: each rule on a small synthetic portal, LLM disabled, plus the state merge."""

import json

import pytest

from ask_soony import anchors, papercheck

FILES = {
    "soony/master/products.csv": "product_id,name,lifecycle,launch_date,day_one_qty\n"
    "P-STD,Std,launch,2030-06-02,1000\nP-CE,Ce,launch,2030-06-02,200\n",
    "soony/master/bom.csv": "parent_id,child_id,qty_per\n"
    "P-STD,PART-A,1\nP-STD,PART-SL,1\nP-CE,PART-S,1\nP-CE,PART-B,1\n",
    "soony/master/components.csv": "component_id,description\n"
    "PART-A,Game Card 128 GB\nPART-SL,Sleeve\nPART-S,Statue\nPART-B,Art Book\n",
    "soony/master/bom_delta_x.csv": "change_id,parent_id,child_id,rev_old,rev_new,"
    "use_up_old_rev,effective_from,reason\n"
    "ECN-1,P-STD,PART-SL,B,C,N,2030-05-01,rev B must not be packed\n",
    "soony/erp/purchase_orders.csv": "po_number,po_line,supplier_id,material_id,quantity,"
    "unit_price,currency,promised_date,status,buyer,incoterm\n"
    "4500000001,1,SUP-1,PART-A,500,9.8,USD,2030-06-01,open,Ann Buyer,FOB\n"
    "4500000002,1,SUP-2,PART-SL,800,0.4,CNY,2030-07-01,open,Ann Buyer,FOB\n"
    "4500000003,1,SUP-3,PART-B,80,2.0,EUR,2030-05-20,delivered,Ann Buyer,DAP\n"
    "4500000004,1,SUP-4,PART-S,20,10,EUR,2030-05-20,delivered,Ann Buyer,FOB\n"
    "4500000005,1,SUP-1,PART-A,300,10,USD,2030-06-03,open,Ann Buyer,DDP Hamburg\n",
    "soony/erp/goods_receipts.csv": "gr_number,po_number,po_line,supplier_id,material_id,"
    "qty_ordered,qty_received,delivery_note,inspector,remark\n"
    "5000000001,4500000003,1,SUP-3,PART-B,80,800,WZ-1-473,Rita Receiving,over delivery\n"
    "5000000002,4500000004,1,SUP-4,PART-S,20,20,DN-1-819,Rita Receiving,\n",
    "soony/erp/stock_snapshot.csv": "snapshot_date,material_id,batch,quantity\n"
    "2030-05-31,PART-A,B1,400\n2030-05-31,PART-B,5000000001,800\n"
    "2030-05-31,PART-S,5000000002,20\n2030-05-31,PART-S,B3,50\n2030-05-31,PART-SL,B4,2000\n",
    "soony/erp/packout_log.csv": "date,product_id,units_packed\n"
    "2030-05-30,P-STD,100\n2030-05-30,P-CE,40\n",
    "suppliers/SUP-1_Acme/profile.csv": "supplier_id,name,contact_name,contact_email\n"
    "SUP-1,Acme Memory,Kim Lee,kim@example.com\n",
    "suppliers/SUP-1_Acme/components.csv": "component_id,description\nPART-A,Game Card 128 GB\n",
    "suppliers/SUP-1_Acme/docs/advice_x.txt": "Shipping advice (short shipment)\n"
    "Your order 4500000001\nPart Description Ordered Shipped Balance\n"
    "PART-A Game Card 128 GB 500 300 200\nBalance 200 pcs: delivery to Hamburg 2030-06-04.\n",
    "suppliers/SUP-1_Acme/docs/ack_y.txt": "Order Acknowledgement\nYour order 4500000005\n"
    "We do not accept this term. The shipment is packed and on hold at our dock.\n",
    "suppliers/SUP-5_Other/profile.csv": "supplier_id,name,contact_email\n"
    "SUP-5,Other Memory,sales@example.org\n",
    "suppliers/SUP-5_Other/components.csv": "component_id,description\nPART-A,Game Card 128 GB\n",
    "suppliers/SUP-2_Pack/profile.csv": "supplier_id,name,contact_email\n"
    "SUP-2,Pack Co,pack@example.net\n",
    "suppliers/SUP-2_Pack/docs/conf_z.txt": "Order Confirmation\nYour order 4500000002\n"
    "00010 PR-SL-1 Sleeve 800 0.4 CNY 2030-07-01\n"
    "Artwork PR-SL-1: rev. B (sleeve_revB.pdf), per drawing on file.\n",
    "suppliers/SUP-3_Print/profile.csv": "supplier_id,name,contact_email\n"
    "SUP-3,Print Co,print@example.com\n",
    "suppliers/SUP-3_Print/notices/offer.txt": "Offer: rush service\n"
    "PART-B Art Book 100 pcs from stock, loaded within 4 h\n",
    "soony/receiving/DC/inspection_5000000002.txt": "Incoming inspection report\n"
    "Goods receipt 5000000002 · PO 4500000004\n"
    "Lot does not meet AQL. Booked to stock as 'unrestricted' by receiving.\n",
    "soony/receiving/DC/note_WZ-1-473.txt": "Delivery note WZ-1-473\nOrder 4500000003\n"
    "PART-B Art Book\n",
}


def write_portal(root, files=FILES):
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


@pytest.fixture
def portal(tmp_path, monkeypatch):
    root = write_portal(tmp_path / "portal")
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("ASK_PORTAL_DIR", str(root))
    monkeypatch.setenv("ASK_STATE_DIR", str(state))
    monkeypatch.setenv("ASK_DATA_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("ASK_NOW", "2030-06-01T21:00:00+02:00")
    monkeypatch.setenv("ASK_PAPERCHECK_LLM", "off")
    monkeypatch.delenv("ASK_LAUNCH_AT", raising=False)
    with anchors._lock:
        anchors._index.clear()
    anchors.build_index()
    return root, state


def by_id(cards):
    return {f["id"]: (f, d, i) for f, d, i in cards}


def test_rule_a_balance_after_launch(portal):
    f, d, i = by_id(papercheck.run(write=False))["paper-4500000001-PART-A"]
    assert f["level"] == "red" and f["arrives_at"] == "2030-06-04"
    # 900 to pack; 400 stock + 500 due, minus the 200 late = 200 short ("128 GB" is no quantity)
    assert (i["copies_at_risk"], i["edition"]) == (200, "P-STD")
    paper = [s for s in f["sides"] if s["source"].startswith("Paper says")][0]
    assert paper["doc"].endswith("advice_x.txt") and "500 300 200" in paper["quote"]
    assert d["options"][0]["to"] == "sales@example.org"  # the other supplier of the part
    assert d["options"][0]["recommended"] is True


def test_rule_b_old_revision_on_open_po(portal):
    f, d, i = by_id(papercheck.run(write=False))["paper-4500000002-PART-SL-rev"]
    assert f["level"] == "amber" and i["units_short"] == 800
    assert any("rev. B" in s["quote"] for s in f["sides"])
    assert d["options"][0]["channel"] == "email" and d["options"][0]["to"] == "pack@example.net"


def test_rule_c_power_of_ten_receipt_without_llm(portal):
    f, d, i = by_id(papercheck.run(write=False))["paper-4500000003-PART-B-gr"]
    # 160 to pack; SAP 800, shelf 80: 80 short; the offer of 100 is the paid option
    assert f["level"] == "red" and i["copies_at_risk"] == 80
    assert (
        d["options"][0]["cost"] == "about EUR 200" and d["options"][0]["to"] == "print@example.com"
    )
    assert d["options"][-1]["channel"] == "notify" and d["options"][-1]["to"] == "Rita Receiving"


def test_rule_c_vision_reading_wins(portal):
    root, _ = portal
    erp = papercheck.load_erp(root)
    docs = papercheck.claims(erp, None)
    note = next(x for x in docs if x.rel.endswith("note_WZ-1-473.txt"))
    note.lines = [
        {
            "po": "4500000003",
            "part": "PART-B",
            "material": "PART-B",
            "received": 80,
            "printed": 800,
            "corrected": True,
            "quote": "WZ-1-473 · PART-B · 800 / 80",
            "translation": "800 struck through, 80 by hand",
        }
    ]
    hits = papercheck.rule_receipt_booked_wrong(docs, erp)
    assert len(hits) == 1 and hits[0].paper[0]["value"] == "80 delivered, not 800"
    assert "corrected by hand" in hits[0].summary


def test_rule_d_failed_lot_counted_as_stock(portal):
    f, d, i = by_id(papercheck.run(write=False))["paper-4500000004-PART-S-qa"]
    assert f["level"] == "red" and i["copies_at_risk"] == 110  # 160 to pack, 50 good
    assert "does not meet AQL" in f["sides"][-1]["quote"]


def test_rule_e_held_at_supplier(portal):
    f, d, i = by_id(papercheck.run(write=False))["paper-4500000005-PART-A-hold"]
    assert f["level"] == "amber" and "on hold" in f["sides"][-1]["quote"]
    assert d["options"][0]["to"] == "kim@example.com"


def test_no_findings_without_contradiction(portal, monkeypatch):
    root, _ = portal
    (root / "soony/master/bom_delta_x.csv").write_text(
        "change_id,child_id,rev_old,rev_new,use_up_old_rev\nECN-1,PART-SL,B,C,Y\n"
    )
    ids = set(by_id(papercheck.run(write=False)))
    assert "paper-4500000002-PART-SL-rev" not in ids  # use-up allowed: no contradiction


def test_merge_is_idempotent_and_keeps_other_state(portal):
    root, state = portal
    (state / "findings.json").write_text(
        json.dumps(
            [
                {"id": "keep-me", "title": "other"},
                {
                    "id": "paper-4500000001-PART-A",
                    "status": "open",
                    "created_at": "2030-01-01T00:00:00",
                    "sides": [{"source": "Supplier email", "quote": "no earlier flight"}],
                    "trail": [
                        {"agent": "Mail agent", "at": "2030-01-01T00:00:00", "text": "asked"}
                    ],
                },
            ]
        )
    )
    (state / "decision_status.json").write_text(
        json.dumps({"paper-4500000001-PART-A": {"status": "approved"}})
    )
    papercheck.run(write=True)
    first = json.loads((state / "findings.json").read_text())
    papercheck.run(write=True)
    second = json.loads((state / "findings.json").read_text())
    assert [f["id"] for f in first] == [f["id"] for f in second]
    assert first == second
    hw = next(f for f in second if f["id"] == "paper-4500000001-PART-A")
    assert hw["created_at"] == "2030-01-01T00:00:00"
    assert any(s["source"] == "Supplier email" for s in hw["sides"])
    assert any(t["agent"] == "Mail agent" for t in hw["trail"])
    assert second[0]["id"] == "keep-me"
    assert json.loads((state / "decision_status.json").read_text())["paper-4500000001-PART-A"]
    decisions = json.loads((state / "decisions.json").read_text())
    assert len(decisions) == len({d["finding_id"] for d in decisions})

    # the hold is lifted: the card is resolved, not deleted
    (root / "suppliers/SUP-1_Acme/docs/ack_y.txt").write_text(
        "Order Acknowledgement\nYour order 4500000005\nShipped today.\n"
    )
    anchors.build_index()
    papercheck.run(write=True)
    third = {f["id"]: f for f in json.loads((state / "findings.json").read_text())}
    assert third["paper-4500000005-PART-A-hold"]["status"] == "resolved"


def test_llm_cache_is_used_without_key(portal, tmp_path):
    llm = papercheck.Llm(key="", model="gpt-4.1-mini", cache=tmp_path / "cache", max_calls=5)
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "k.json").write_text(json.dumps({"statements": []}))
    assert llm.ask("k", []) == {"statements": []}
    assert llm.ask("missing", []) is None and llm.calls == 0


def test_numbers_skip_sizes_and_dates():
    assert papercheck._numbers("Game Card 128 GB 48,000 28 000 2030-06-04 20.000") == [
        48000,
        28000,
        20000,
    ]


def _demo_script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "demo_cards.py"
    spec = importlib.util.spec_from_file_location("demo_cards_wrapper", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_demo_cards_reset_runs_papercheck(portal):
    _, state = portal
    (state / "findings.json").write_text(
        json.dumps([{"id": "keep-me"}, {"id": "qa-5000000002-PART-S"}])
    )
    (state / "decisions.json").write_text(json.dumps([{"finding_id": "qa-5000000002-PART-S"}]))
    (state / "actions.json").write_text(json.dumps([{"id": "a1"}]))
    (state / "routines.json").write_text(json.dumps([{"id": "r1", "enabled": True}]))
    assert _demo_script().main(["--reset", "--no-llm"]) == 0
    ids = [f["id"] for f in json.loads((state / "findings.json").read_text())]
    assert "keep-me" in ids and "qa-5000000002-PART-S" not in ids
    assert "paper-4500000004-PART-S-qa" in ids
    assert json.loads((state / "decisions.json").read_text())[0]["finding_id"].startswith("paper-")
    assert json.loads((state / "actions.json").read_text()) == []
    assert json.loads((state / "routines.json").read_text())[0]["enabled"] is False
    backups = {p.name.split(".")[0] for p in state.glob("*.before-demo-*.json")}
    assert {"actions", "routines", "findings"} <= backups
