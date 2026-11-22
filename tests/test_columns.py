"""Column mapping: German, SAP and English exports read by meaning; invented data only."""

import json

from ask_soony import api, columns, datacheck, papercheck

GERMAN = (
    "Bestellnummer;Pos;Lieferant;Material;Werk;Menge;Preis;Waehrung;Bestelldatum;Wunschtermin;"
    "confirmed_delivery_dt;Status;Einkäufer;Bemerkung\r\n"
    "4500000001;10;SUP-1;PART-A;DC-1;1.500;12,5000;EUR;26.08.2030;20.05.2030;04.06.2030;open;"
    "Jörg Käufer;rev. 2030-06-01 supplier schedule +10d\r\n"
    "4500000002;20;SUP-2;PART-B;DC-1;800;0,4000;EUR;03.09.2030;01.06.2030;01.06.2030;open;A B;\r\n"
)


def test_german_cp1252_export_gets_canonical_rows(tmp_path):
    f = tmp_path / "po_export_2030-06-01.csv"
    f.write_bytes(GERMAN.encode("cp1252"))
    rows, m = columns.read(f)
    assert m.kind == "purchase_orders" and m.unknown == [] and m.model == []
    r = rows[0]
    assert r["Bestellnummer"] == "4500000001" and r["Preis"] == "12,5000"  # originals kept
    assert (r["po_number"], r["po_line"], r["supplier_id"], r["material_id"]) == (
        "4500000001",
        "1",
        "SUP-1",
        "PART-A",
    )
    assert (r["quantity"], r["unit_price"], r["currency"]) == ("1500", "12.5", "EUR")
    assert (r["order_date"], r["confirmed_date"]) == ("2030-08-26", "2030-06-04")
    assert (r["buyer"], r["note"][:4]) == ("Jörg Käufer", "rev.")
    assert rows[1]["po_line"] == "2"


def test_english_thousands_comma_stays_a_thousand(tmp_path):
    f = tmp_path / "orders.csv"
    f.write_text(
        "Order No,Item,Vendor,Part Number,Qty,Net Price,PO Date\n"
        '4500000009,1,SUP-1,PART-A,"28,000",9.80,10/30/2030\n'
        '4500000009,2,SUP-1,PART-B,"1,200",0.5,11/02/2030\n'
    )
    rows, m = columns.read(f)
    assert m.kind == "purchase_orders"
    assert [r["quantity"] for r in rows] == ["28000", "1200"]
    assert rows[0]["unit_price"] == "9.8"
    assert rows[0]["order_date"] == "2030-10-30"  # month first: '30' cannot be a month
    assert rows[0]["po_line"] == "1"  # lines 1, 2 are not SAP steps of ten


def test_decimal_comma_decided_per_file():
    assert columns.decimal_comma(",", ["28,000", "1,200"]) is False
    assert columns.decimal_comma(",", ["88,38"]) is True
    assert columns.decimal_comma(";", ["10000"]) is True
    assert columns.decimal_comma(";", ["9.80"]) is False
    assert columns.number("1.234,50", True) == "1234.5"


def test_header_matching_ignores_case_accents_and_spaces():
    m = columns.map_header(["BESTELL-NR", "pos.", "Währung", "einkaeufer", "MATNR"])
    assert m.columns == {
        "BESTELL-NR": "po_number",
        "pos.": "po_line",
        "Währung": "currency",
        "einkaeufer": "buyer",
        "MATNR": "material_id",
    }


def test_unknown_header_goes_to_the_model_once(tmp_path, monkeypatch):
    monkeypatch.setenv("ASK_DATA_DIR", str(tmp_path / "store"))
    calls = []

    def fake(header, unknown):
        calls.append(unknown)
        return {
            "columns": {
                "Kreditorennr": {"field": "supplier_id", "confidence": "high"},
                "Stk": {"field": "quantity", "confidence": "high"},
                "Lager": {"field": "plant", "confidence": "medium"},  # not sure: not used
            }
        }

    monkeypatch.setattr(columns, "_call_model", fake)
    header = ["Bestellnummer", "Pos", "Kreditorennr", "Material", "Stk", "Lager"]
    m = columns.map_header(header, model=True)
    assert m.kind == "purchase_orders"
    assert m.columns["Kreditorennr"] == "supplier_id" and m.columns["Stk"] == "quantity"
    assert sorted(m.model) == ["Kreditorennr", "Stk"] and m.unknown == ["Lager"]
    columns.map_header(header, model=True)  # cached by header signature
    assert len(calls) == 1
    assert columns.map_header(header, model=False).kind is None  # table only


def _portal(tmp_path, monkeypatch):
    root = tmp_path / "portal"
    files = {
        "soony/master/products.csv": "product_id,name,lifecycle,launch_date,day_one_qty\n"
        "P-STD,Std,launch,2030-06-02,1000\n",
        "soony/master/bom.csv": "parent_id,child_id,qty_per\nP-STD,PART-A,1\nP-STD,PART-B,1\n",
        "soony/master/components.csv": "component_id,description\nPART-A,Card\nPART-B,Sleeve\n",
        "soony/erp/purchase_orders.csv": "po_number,po_line,supplier_id,material_id,quantity,"
        "promised_date,status,incoterm\n"
        "4500000001,1,SUP-1,PART-A,1500,2030-05-25,open,FOB\n"
        "4500000002,2,SUP-2,PART-B,800,2030-06-01,open,FOB\n",
        "soony/erp/stock_snapshot.csv": "snapshot_date,material_id,batch,quantity\n"
        "2030-05-31,PART-A,B1,10\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    monkeypatch.setenv("ASK_PORTAL_DIR", str(root))
    monkeypatch.setenv("ASK_DATA_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("ASK_PAPERCHECK_LLM", "off")
    monkeypatch.delenv("ASK_LAUNCH_AT", raising=False)
    monkeypatch.setattr(api, "state_dir", lambda: tmp_path)
    return root


def test_papercheck_reads_a_german_po_export_and_keeps_the_change(tmp_path, monkeypatch):
    root = _portal(tmp_path, monkeypatch)
    erp = papercheck.load_erp(root)
    assert erp.due_before_launch("PART-A") == 1500
    datacheck.check(root)  # baseline
    new = root / "soony/erp/po_export_2030-06-01.csv"
    new.write_bytes(GERMAN.encode("cp1252"))
    papercheck.READS.clear()
    erp = papercheck.load_erp(root)
    line = erp.po_lines("4500000001", "PART-A")
    assert len(line) == 1 and line[0]["promised_date"] == "2030-06-04"
    assert line[0]["incoterm"] == "FOB"  # the older file's values the newer one lacks stay
    assert erp.due_before_launch("PART-A") == 0  # moved past launch
    moved = [c for c in erp.revisions if c["field"] == "promised_date"]
    assert [(c["po"], c["old"], c["new"]) for c in moved] == [
        ("4500000001", "2030-05-25", "2030-06-04")
    ]
    side = papercheck._po_side(line, "x")
    assert "2030-05-25 → 2030-06-04" in side["quote"]
    issues = datacheck.check(root)["issues"]
    mapped = [i for i in issues if i["kind"] == "mapped"]
    assert [i["file"] for i in mapped] == ["soony/erp/po_export_2030-06-01.csv"]
    assert (
        "read as purchase orders via column mapping (Bestellnummer → po_number"
        in (mapped[0]["text"])
    )
    assert not [i for i in issues if i["kind"] == "unused"]
    assert datacheck.data_health()["issues"] == []  # no gap in the picture
    assert not [i for i in datacheck.check(root)["issues"] if i["kind"] == "mapped"]  # once


def test_supplier_schedule_csv_is_held_against_the_po(tmp_path, monkeypatch):
    root = _portal(tmp_path, monkeypatch)
    sched = root / "suppliers/SUP-2_Pack/confirmations/revised_schedule.csv"
    sched.parent.mkdir(parents=True)
    sched.write_text(
        "customer_po,item,part_no,description,qty,confirmed_old,confirmed_new,remark\n"
        "4500000002,20,PART-B,Sleeve,800,2030-06-01,2030-06-11,capacity re-planning\n"
    )
    (root / "suppliers/SUP-2_Pack/profile.csv").write_text(
        "supplier_id,name,contact_email\nSUP-2,Pack Co,pack@example.net\n"
    )
    erp = papercheck.load_erp(root)
    docs = papercheck.schedule_docs(erp)
    assert [d.supplier for d in docs] == ["SUP-2"]
    ln = docs[0].lines[0]
    assert (ln["po"], ln["material"], ln["confirmed_date"]) == (
        "4500000002",
        "PART-B",
        "2030-06-11",
    )
    hits = papercheck.rule_late_or_short(docs, erp)  # promised 06-01, now after the 06-02 launch
    assert [(h.po, h.material, h.rule) for h in hits] == [("4500000002", "PART-B", "late")]


def test_datacheck_state_keeps_mapping(tmp_path, monkeypatch):
    root = _portal(tmp_path, monkeypatch)
    (root / "soony/erp/po_export_2030-06-01.csv").write_bytes(GERMAN.encode("cp1252"))
    datacheck.check(root)
    state = json.loads((tmp_path / datacheck.STATE).read_text())
    assert state["mapped"]["soony/erp/po_export_2030-06-01.csv"]["Menge"] == "quantity"
