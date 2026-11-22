import json

from ask_soony import api, datacheck


def _portal(tmp_path, po_header: str):
    erp = tmp_path / "portal" / "soony" / "erp"
    erp.mkdir(parents=True)
    (tmp_path / "portal" / "soony" / "master").mkdir(parents=True)
    (erp / "purchase_orders.csv").write_text(f"{po_header}\nPO-1,M-1\n")
    return tmp_path / "portal"


def test_renamed_column_is_reported_not_skipped_silently(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "state_dir", lambda: tmp_path)
    root = _portal(tmp_path, "po_number,material_id")
    assert datacheck.check(root)["issues"] == []  # read fine
    (root / "soony/erp/purchase_orders.csv").write_text("po_number,material\nPO-1,M-1\n")
    issues = datacheck.check(root)["issues"]
    assert [(i["kind"], i["missing"]) for i in issues] == [("not_read", ["material_id"])]
    assert json.loads((tmp_path / datacheck.STATE).read_text())["issues"] == issues


def test_new_file_nobody_reads_is_listed_once(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "state_dir", lambda: tmp_path)
    root = _portal(tmp_path, "po_number,material_id")
    datacheck.check(root)
    (root / "soony/erp/carrier_slots.csv").write_text("slot,dock\n1,A\n")
    assert [i["kind"] for i in datacheck.check(root)["issues"]] == ["unused"]
    assert datacheck.check(root)["issues"] == []  # known now; no repeat


def test_new_file_of_another_format_is_listed_once(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "state_dir", lambda: tmp_path)
    root = _portal(tmp_path, "po_number,material_id")
    (root / "soony/context").mkdir(parents=True)
    datacheck.check(root)
    (root / "soony/context/slides.pptx").write_bytes(b"PK")
    (root / "suppliers/SUP-1/notices").mkdir(parents=True)
    (root / "suppliers/SUP-1/notices/delay.pdf").write_bytes(b"%PDF")  # the paper check reads it
    (root / "suppliers/SUP-1/profile.json").write_text("{}")  # the supplier master reads it
    issues = datacheck.check(root)["issues"]
    assert [(i["kind"], i["file"]) for i in issues] == [("unused", "soony/context/slides.pptx")]
    assert "PPTX" in issues[0]["text"]
    assert datacheck.check(root)["issues"] == []


def test_read_csv_handles_german_exports(tmp_path):
    from ask_soony.api import read_csv

    f = tmp_path / "po_export.csv"
    f.write_bytes("Bestellnummer;Pos;Einkäufer\r\n4500000001;10;A Buyer\r\n".encode("cp1252"))
    rows = read_csv(f)
    assert rows == [{"Bestellnummer": "4500000001", "Pos": "10", "Einkäufer": "A Buyer"}]
    g = tmp_path / "plain.csv"
    g.write_text("a,b\n1,2\n", encoding="utf-8")
    assert read_csv(g) == [{"a": "1", "b": "2"}]
