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
