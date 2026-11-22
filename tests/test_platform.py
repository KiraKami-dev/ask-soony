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
    monkeypatch.setenv("ASK_DATA_DIR", str(store))
    monkeypatch.setenv("ASK_NOW", "2030-06-01T21:00:00+02:00")
    from ask_soony import api

    return {"portal": portal, "state": state, "store": store, "client": TestClient(api.app)}


def _decision(**kw):
    card = {
        "id": "gap-X", "title": "300 copies short", "edition": "P-A", "at_risk": 300,
        "at_risk_if_acted": 0, "part": "PART-1", "system_says": "book 800",
        "paper_says": "note 80", "evidence": ["scan"], "decision": None,
        "options": ["Accept Acme Paper's offer: 300 from stock (about 900 EUR at list price)"],
        "act_by": "Sat 01 Jun 23:00", "runs_dry": None,
        "messages": [
            {"to_role": "Kim Lee, Site Lead", "to_email": None, "subject": "Please count",
             "body": "count"},
            {"to_role": "Acme Paper (Ann)", "to_email": "x@example.com",
             "subject": "Order: 300 from stock", "body": "b"},
        ],
    }
    card.update(kw)
    return card


def test_dispatch_decision_maps_to_inbox_and_strip(env):
    (env["state"] / "dispatch.json").write_text(json.dumps({
        "editions": [{"product": "P-A", "at_risk": 250}],
        "parts": [{"part": "PART-1", "description": "Book"}],
        "decisions": [_decision()],
    }))
    s = env["client"].get("/api/state").json()
    card = next(c for c in s["cards"] if c["id"] == "gap-X")
    rec = card["options"][0]
    assert rec["to"] == "x@example.com" and rec["cost"] == "about 900 EUR"
    assert rec["routine_type"] is None  # commits money: never automatic
    assert card["part"] == "Book" and card["supplier_id"] == "SUP-9"
    assert card["deadline"].startswith("2030-06-01T23:00")
    cell = next(x for x in s["strip"] if x["edition"] == "P-A")
    # never below an open card's figure (the verifier says 250, its card 300), never a card sum
    assert cell["copies_at_risk"] == 300
    assert cell["breaks_first"]["stops_at"].startswith("2030-06-02T11:00")  # not at night


def test_free_supplier_question_stays_with_buyer(env):
    (env["state"] / "dispatch.json").write_text(json.dumps({"decisions": [_decision(
        options=["Next flight after launch"],
        messages=[{"to_role": "Acme Paper (Ann)", "to_email": "x@example.com",
                   "subject": "Any quantity earlier?", "body": "b"}],
    )]}))
    card = env["client"].get("/api/state").json()["cards"][0]
    assert card["options"][0]["routine_type"] == "supplier_question"
    assert card["routed_to_other"] is False


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
