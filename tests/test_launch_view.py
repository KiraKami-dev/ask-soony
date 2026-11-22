"""The Launch page's per-part rows in /api/state: when each part runs dry."""

import json

import pytest
from fastapi.testclient import TestClient
from test_launch import make_portal


@pytest.fixture
def env(tmp_path, monkeypatch):
    portal = make_portal(tmp_path / "portal", "400")  # 600 copies left at 50/h
    (portal / "soony" / "erp" / "open_shipments.csv").write_text(
        "shipment_id,mode,reference,supplier_id,route_id,etd,eta,status,updated_at,note\n"
        "SHP-1,sea,CONT-1,SUP-1,RT-1,2030-05-01,2030-05-31,arrived_port,"
        "2030-06-01T10:00:00+02:00,at the port\n"
    )
    state, store = tmp_path / "state", tmp_path / "store"
    state.mkdir()
    store.mkdir()
    monkeypatch.setenv("ASK_PORTAL_DIR", str(portal))
    monkeypatch.setenv("ASK_STATE_DIR", str(state))
    monkeypatch.setenv("ASK_DATA_DIR", str(store))
    monkeypatch.setenv("ASK_NOW", "2030-06-01T21:00:00+02:00")  # Saturday 21:00
    part = {"needed": 600, "usable": 600, "gap": 0, "per_copy": {"P-A": 1}, "editions": ["P-A"]}
    (state / "dispatch.json").write_text(
        json.dumps(
            {
                "editions": [{"product": "P-A", "day_one": 1000}],
                "parts": [
                    {
                        **part,
                        "part": "C-BOX",
                        "description": "Box",
                        "usable": 300,
                        "gap": 300,
                        "not_counted": [
                            {"reference": "CONT-1", "quantity": 500, "reason": "at the port"}
                        ],
                    },
                    {**part, "part": "C-BOOK", "description": "Book"},
                    {
                        **part,
                        "part": "C-CARD",
                        "description": "Card",
                        "gap": 50,
                        "editions": ["P-A", "P-B"],
                    },
                ],
                "decisions": [
                    {
                        "id": "collect-C-BOX",
                        "title": "Collect the boxes",
                        "edition": "P-A",
                        "part": "C-BOX",
                        "at_risk": 300,
                        "options": [],
                        "messages": [],
                    }
                ],
            }
        )
    )
    from ask_soony import api

    return TestClient(api.app)


def test_parts_say_when_they_run_dry(env):
    s = env.get("/api/state").json()
    parts = {p["part"]: p for p in s["launch"]["parts"]}
    ed = next(e for e in s["clock"]["editions"] if e["edition"] == "P-A")
    box = parts["C-BOX"]
    # 300 of 600 packable = 6 h: 1 h tonight, 5 h from Sunday 06:00, the same as the edition's stop
    assert box["short"] == 300 and box["runs_dry"].startswith("2030-06-02T11:00")
    assert box["runs_dry"] == ed["stops_at"]
    assert box["cards"] == ["collect-C-BOX"]
    assert box["inbound"][0]["shipment"]["status"] == "arrived_port"
    assert box["inbound"][0]["counted"] is False
    # covered parts have nothing short and no time; a shared part's time depends on the split
    assert parts["C-BOOK"]["short"] == 0 and parts["C-BOOK"]["runs_dry"] is None
    assert parts["C-CARD"]["shared"] and parts["C-CARD"]["runs_dry"] is None
    assert s["launch"]["hours_left"] == 15.0
    assert s["launch"]["editions"][0]["day_one"] == 1000


def test_state_carries_the_money_block(env):
    m = env.get("/api/state").json()["launch"]["money"]
    assert m["committed_today"] == [] and isinstance(m["pending_decisions"], list)


def _card(cid, status, options, **kw):
    return {"id": cid, "status": status, "options": options, "ui": {}, **kw}


def test_money_counts_what_was_committed_and_what_waits_for_a_price():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from ask_soony.launch_view import money, price

    cur = datetime(2030, 6, 2, 9, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    order = {"action": "Order 8,000 books", "cost": "about EUR 16,800", "recommended": True}
    van = {"action": "Ask Box Co for a van, or a split load", "cost": "unknown: they quote first"}
    free = {"action": "Ask the site", "cost": "No cost to ask", "recommended": True}
    cards = [
        _card("books", "sent", [order], supplier_name="Box Co", ui={"reply": "ok"}),
        _card("van", "sent", [van], supplier_name="Box Co", ui={"at": "2030-06-02T07:58:00+02:00"}),
        _card(
            "sign",
            "escalated",
            [{**van, "recommended": True}],
            approver="Dr. Ann Boss",
            signoff="Dr. Ann Boss",
        ),
        _card("mine", "open", [{"action": "Book air freight", "cost": "EUR 2,000"}]),
        _card("chase", "open", [free]),
        _card("old", "approved", [order]),
    ]
    actions = [
        {
            "decision_id": "books",
            "kind": "approve",
            "option": 0,
            "status": "sent",
            "at": "2030-06-02T06:50:00+02:00",
        },
        {
            "decision_id": "old",
            "kind": "approve",
            "option": 0,
            "status": "approved",
            "at": "2030-06-01T12:00:00+02:00",
        },  # yesterday: not today's money
        {
            "decision_id": "van",
            "kind": "approve",
            "option": 0,
            "status": "sent",
            "at": "2030-06-02T07:58:00+02:00",
        },
        {"decision_id": "sign", "kind": "escalate", "at": "2030-06-02T07:49:00+02:00"},
    ]
    m = money(cards, actions, cur, "Bo Buyer", "Dr. Ann Boss")
    assert m["committed_today"] == [{"currency": "EUR", "value": 16800.0}]
    got = {p["card"]: p for p in m["pending_decisions"]}
    assert set(got) == {"van", "sign", "mine"}  # free and decided-with-a-price stay out
    assert got["van"]["cost_value"] is None and got["van"]["holder"] == "Box Co"
    assert got["van"]["since"].startswith("2030-06-02T07:58")
    assert got["sign"]["holder_role"] == "approver" and got["sign"]["since"].endswith(
        "07:49:00+02:00"
    )
    assert got["mine"]["cost_value"] == 2000 and got["mine"]["holder"] == "Bo Buyer"
    assert price({"cost": "No cost"}) == (False, None, None)
    assert price({"cost": "PLN 4.200"})[2] == "PLN"
