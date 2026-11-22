"""The packing clock in /api/state: shifts, launch, and per-edition numbers that match the cards."""

import json
from datetime import date
from zoneinfo import ZoneInfo

import openpyxl
import pytest
from fastapi.testclient import TestClient
from test_launch import make_portal

from ask_soony import launch

TZ = ZoneInfo("Europe/Berlin")


@pytest.fixture
def env(tmp_path, monkeypatch):
    portal = make_portal(tmp_path / "portal", "400")  # 600 copies left at 50/h
    state, store = tmp_path / "state", tmp_path / "store"
    state.mkdir()
    store.mkdir()
    monkeypatch.setenv("ASK_PORTAL_DIR", str(portal))
    monkeypatch.setenv("ASK_STATE_DIR", str(state))
    monkeypatch.setenv("ASK_DATA_DIR", str(store))
    monkeypatch.setenv("ASK_NOW", "2030-06-01T21:00:00+02:00")  # Saturday 21:00
    from ask_soony import api

    return {"portal": portal, "state": state, "client": TestClient(api.app)}


def _finding(state, fid, copies, summary="", label="copies_at_risk"):
    def read(name):
        path = state / name
        return json.loads(path.read_text()) if path.exists() else []

    findings, impacts = read("findings.json"), read("impacts.json")
    findings.append({"id": fid, "kind": "mismatch", "title": fid, "summary": summary,
                     "status": "open", "level": "red"})
    impacts.append({"finding_id": fid, label: copies, "edition": "P-A"})
    (state / "findings.json").write_text(json.dumps(findings))
    (state / "impacts.json").write_text(json.dumps(impacts))


def test_clock_has_shifts_launch_and_editions(env):
    clock = env["client"].get("/api/state").json()["clock"]
    assert clock["launch"].startswith("2030-06-02T20:00")
    shifts = [(w[0][11:16], w[1][11:16]) for w in clock["windows"]]
    assert shifts == [("06:00", "22:00"), ("06:00", "20:00")]
    ed = next(e for e in clock["editions"] if e["edition"] == "P-A")
    # nothing short: 600 left at 50/h = 12 h, 1 h tonight and 11 h from Sunday 06:00
    assert ed["finishes_at"].startswith("2030-06-02T17:00") and ed["stops_at"] is None
    assert ed["slack_hours"] == 3.0 and ed["copies_at_risk"] == 0


def test_edition_numbers_match_the_open_cards(env):
    _finding(env["state"], "f-books", 300, "200 more arrive in Hamburg on Tue 4 Jun, after launch.")
    _finding(env["state"], "f-statues", 120)
    _finding(env["state"], "f-sleeves", 9000, label="units_short")  # parts, not copies: left out
    s = env["client"].get("/api/state").json()
    ed = next(e for e in s["clock"]["editions"] if e["edition"] == "P-A")
    cell = next(x for x in s["strip"] if x["edition"] == "P-A")
    # the scarcest part bounds the loss: max of the open cards, not their sum
    assert ed["copies_at_risk"] == cell["copies_at_risk"] == 300
    assert 300 in [c["number"] for c in s["cards"]]
    # 300 of 600 packable = 6 h: 1 h tonight, 5 h from Sunday 06:00; the strip says the same
    assert ed["stops_at"].startswith("2030-06-02T11:00") and ed["finishes_at"] is None
    assert cell["breaks_first"]["stops_at"] == ed["stops_at"]
    books = next(c for c in s["cards"] if c["id"] == "f-books")
    assert books["arrives_at"].startswith("2030-06-04")


def test_packed_status_is_projected_at_plan_rate(env):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Launch status · Saturday 12:00"])
    ws.append(["Name", "Product", "Packed", "Rate per hour"])
    ws.append(["Big", "P-A", 500, 50])
    wb.save(env["portal"] / "soony" / "context" / "launch_status.xlsx")
    clock = launch.load(env["portal"], TZ)
    ed = clock.editions["P-A"]
    assert (ed.packed, ed.packed_as_of.hour) == (500, 12)
    now = clock.windows[0][0].replace(hour=21)
    assert clock.remaining_at(now, "P-A") == 50  # 500 left at noon, 9 shift hours at 50/h since
    assert clock.finishes(now, "P-A").hour == 22


def test_brief_shifts_when_no_plan_states_any():
    w = launch.default_windows(date(2030, 6, 2), TZ)
    shifts = [(s.strftime("%a %H"), e.strftime("%a %H")) for s, e in w]
    assert shifts == [("Sat 06", "Sat 22"), ("Sun 06", "Sun 20")]
