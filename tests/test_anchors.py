"""Evidence anchoring: tokens, matching a card's evidence to a document row, the table view."""

import pytest
from fastapi.testclient import TestClient

from ask_soony import anchors


def test_numbers_match_across_separators():
    assert anchors.norm("56.000") == anchors.norm("56,000") == "56000"
    assert anchors.norm("1,234,567 pcs") == "1234567 PCS"
    t = anchors.search_tokens("Book stock 118,400 on PO 4500318751 for NHSU4471921 in 2026")
    assert t == {"118400": 2, "4500318751": 2, "NHSU4471921": 3}  # the bare year is skipped


@pytest.fixture
def portal(tmp_path, monkeypatch):
    root = tmp_path / "portal"
    (root / "soony" / "master").mkdir(parents=True)
    erp = root / "soony" / "erp"
    erp.mkdir()
    (erp / "stock.csv").write_text(
        "part,qty,as_of\nCMP-A-001,4200,2030-06-01\nCMP-B-002,118400,2030-06-01\n"
    )
    (root / "notes.md").write_text("intro\nContainer ABCU1234567 discharged at the port\n")
    monkeypatch.setenv("ASK_PORTAL_DIR", str(root))
    monkeypatch.setenv("ASK_DATA_DIR", str(tmp_path / "store"))
    monkeypatch.setattr(anchors, "_index", {})
    monkeypatch.setattr(anchors, "_state", {"building": False, "done": False, "files": 0})
    anchors.build_index()
    return root


def test_evidence_finds_the_row_and_the_line(portal):
    hit = anchors.find("Book stock 118,400", context="CMP-B-002")[0]
    assert (hit["doc"], hit["kind"], hit["row"]) == ("soony/erp/stock.csv", "table", 2)
    line = anchors.find("ABCU1234567 discharged", prefer="document")[0]
    assert (line["doc"], line["kind"], line["row"]) == ("notes.md", "text", 1)
    assert anchors.find("nothing to look for here") == []


def test_table_view_marks_the_row(portal):
    from ask_soony import api

    r = TestClient(api.app).get("/api/doc/table", params={"path": "soony/erp/stock.csv", "row": 2})
    body = r.json()
    assert body["header"] == ["part", "qty", "as_of"]
    assert [x["hit"] for x in body["rows"]] == [False, True]
    outside = TestClient(api.app).get("/api/doc/table", params={"path": "../../etc/passwd"})
    assert outside.status_code == 404
