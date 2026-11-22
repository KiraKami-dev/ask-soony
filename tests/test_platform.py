"""Platform API over teammates' outputs, and the rehearsal drop."""

import json

import pytest
from fastapi.testclient import TestClient
from test_launch import make_portal


@pytest.fixture
def env(tmp_path, monkeypatch):
    portal = make_portal(tmp_path / "portal", "400")
    (portal / "suppliers" / "SUP-9_Acme").mkdir(parents=True)
    (portal / "suppliers" / "SUP-9_Acme" / "profile.csv").write_text(
        "supplier_id,name,country,lat,lon\nSUP-9,Acme Paper,PL,52.4,16.9\n"
    )
    state, store = tmp_path / "state", tmp_path / "store"
    state.mkdir()
    store.mkdir()
    monkeypatch.setenv("ASK_PORTAL_DIR", str(portal))
    monkeypatch.setenv("ASK_STATE_DIR", str(state))
    monkeypatch.setenv("ASK_CARDS_FILE", str(store / "decision_cards.json"))
    monkeypatch.setenv("ASK_NOW", "2030-06-01T21:00:00+02:00")
    from ask_soony import api

    return {"portal": portal, "state": state, "store": store, "client": TestClient(api.app)}


def test_verifier_card_maps_to_inbox_and_strip(env):
    (env["store"] / "decision_cards.json").write_text(json.dumps({"cards": [{
        "id": "gap-X", "title": "300 copies short", "edition": "P-A", "at_risk": 300, "part": "Book",
        "system_says": "book 800", "paper_says": "note 80", "evidence": ["scan of SUP-9 note"],
        "options": ["Rush order"], "decision": "Approve the rush?", "act_by": "Sat 01 Jun 23:00",
        "runs_dry": None,
        "messages": [{"to_role": "Supplier", "to_email": "x@example.com", "subject": "s", "body": "b"}],
    }]}))
    s = env["client"].get("/api/state").json()
    card = next(c for c in s["cards"] if c["id"] == "gap-X")
    assert card["supplier_id"] == "SUP-9" and card["options"][0]["draft"] == "b"
    assert card["deadline"].startswith("2030-06-01T23:00")
    cell = next(x for x in s["strip"] if x["edition"] == "P-A")
    assert cell["copies_at_risk"] == 300
    assert cell["breaks_first"]["stops_at"].startswith("2030-06-02T11:00")  # not at night


def test_event_impact_no_risk_is_logged_not_inboxed(env):
    (env["state"] / "impacts.json").write_text(json.dumps({"news#1": {
        "event_summary": "Magnet export rule", "overall_launch_risk": "no", "affected": [],
        "source": "soony/news/x.json", "analysed_at": "2030-06-01T20:00:00+00:00"}}))
    s = env["client"].get("/api/state").json()
    assert s["calm"] is True
    assert any("No launch risk" in a["text"] for a in s["activity"])


def test_drop_records_event_and_pulses_supplier(env):
    from ask_soony.runner import drop

    src = env["portal"] / "new.pdf"
    src.write_bytes(b"%PDF")
    out = drop("suppliers/SUP-9_Acme/notices/new.pdf", src)
    assert out["event"]["path"].endswith("new.pdf")
    s = env["client"].get("/api/state").json()
    assert s["events"][0]["node"] == "SUP-9"
    with pytest.raises(ValueError):
        drop("../outside.pdf", src)
