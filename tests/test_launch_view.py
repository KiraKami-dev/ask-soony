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
