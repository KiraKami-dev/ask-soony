import json

import pytest
from fastapi.testclient import TestClient

from ask_soony.globe_api import app  # the globe dashboard API, kept beside the inbox API
from ask_soony.portal import RemoteFile
from ask_soony.store import Store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ASK_SOONY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ASK_DATA_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("ASK_SOONY_STATE", str(tmp_path / "missing.json"))
    return TestClient(app)


def _cards_file(path, cards, **top):
    path.write_text(json.dumps({"editions": [], "parts": [], "cards": cards, **top}))


def test_state_falls_back_to_fixture(client):
    body = client.get("/api/state").json()
    assert body["source"] == "fixture"
    assert body["cards"] and body["editions"]
    assert body["mode"] == "ask"


def test_state_uses_env_file(client, tmp_path, monkeypatch):
    f = tmp_path / "cards.json"
    f.write_text('{"editions": [], "parts": [], "cards": []}')
    monkeypatch.setenv("ASK_SOONY_STATE", str(f))
    body = client.get("/api/state").json()
    assert body["source"] == "files" and body["cards"] == []


def test_action_persists_and_merges(client, tmp_path):
    card_id = client.get("/api/state").json()["cards"][0]["id"]
    r = client.post(f"/api/cards/{card_id}/action", json={"action": "allow_once", "note": "ok"})
    assert r.status_code == 200
    assert (tmp_path / "state" / "decisions.json").exists()
    card = next(c for c in client.get("/api/state").json()["cards"] if c["id"] == card_id)
    assert card["human_decision"]["action"] == "allow_once"
    assert client.get("/api/feed").json()[0]["actor"] == "human"


def test_action_rejects_unknown(client):
    assert client.post("/api/cards/x/action", json={"action": "nope"}).status_code == 422


def test_feed_fixture_when_empty(client):
    assert len(client.get("/api/feed").json()) > 0


def test_mode_roundtrip(client):
    assert client.post("/api/mode", json={"mode": "autonomous"}).json() == {"mode": "autonomous"}
    assert client.get("/api/mode").json()["mode"] == "autonomous"
    assert client.get("/api/state").json()["mode"] == "autonomous"
    assert client.post("/api/mode", json={"mode": "bad"}).status_code == 422


def test_merges_cards_from_many_files(client, tmp_path, monkeypatch):
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.delenv("ASK_SOONY_STATE")
    _cards_file(
        store / "decision_cards.json",
        [{"id": "a", "title": "old"}, {"id": "b"}],
        editions=[{"product": "X"}],
        now="2030-01-01T00:00:00+00:00",
    )
    _cards_file(
        store / "forecast_cards.json",
        [{"id": "a", "title": "new", "stage": "before_arrival"}],
        editions=[{"product": "Y"}],
    )
    body = client.get("/api/state").json()
    cards = {c["id"]: c for c in body["cards"]}
    assert cards["a"]["title"] == "new" and cards["a"]["module"] == "forecast_cards"
    assert cards["b"]["source_file"] == "decision_cards.json"
    assert body["editions"] == [{"product": "X"}] and body["now"]


def test_state_env_accepts_glob_and_list(client, tmp_path, monkeypatch):
    _cards_file(tmp_path / "one_cards.json", [{"id": "1"}])
    _cards_file(tmp_path / "two_cards.json", [{"id": "2"}])
    _cards_file(tmp_path / "other.json", [{"id": "3"}])
    monkeypatch.setenv("ASK_SOONY_STATE", f"{tmp_path}/*_cards.json,{tmp_path}/other.json")
    ids = [c["id"] for c in client.get("/api/state").json()["cards"]]
    assert ids == ["1", "2", "3"]


def test_fixture_includes_forecast_card(client):
    cards = client.get("/api/state").json()["cards"]
    assert any(
        c["stage"] == "before_arrival" and c["module"] == "sample_forecast_cards" for c in cards
    )


def test_action_status_merged(client, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "actions.json").write_text(
        json.dumps(
            [{"card_id": "card-widget-a", "status": "sent", "at": "2030-01-01T00:00:00+00:00"}]
        )
    )
    cards = {c["id"]: c for c in client.get("/api/state").json()["cards"]}
    assert cards["card-widget-a"]["action_status"]["status"] == "sent"
    assert "action_status" not in cards["card-widget-b"]


def test_feed_from_store_db_and_mail(client, tmp_path):
    store = Store(tmp_path / "store")
    store.record_listing([RemoteFile("a.txt", "h1", 1)])
    store.record_listing([RemoteFile("a.txt", "h2", 1), RemoteFile("b.txt", "h3", 1)])
    store.save_analysis(
        store.current()["a.txt"],
        "p",
        "m",
        {"summary": "qty differs", "findings": [{"severity": "high", "title": "Mismatch"}]},
    )
    (tmp_path / "outbox").mkdir()
    (tmp_path / "outbox" / "1.json").write_text(
        json.dumps({"date": "2030-01-01T10:00:00+00:00", "to": "x@example.com", "subject": "Hi"})
    )
    events = client.get("/api/feed").json()
    texts = [e["text"] for e in events]
    assert any("2 changed files" in t for t in texts)
    assert any(t.startswith("Changed file a.txt") for t in texts)
    assert any("Analysis done for a.txt: qty differs" in t for t in texts)
    assert any("Finding opened (high): Mismatch" in t for t in texts)
    assert any("Sent to x@example.com" in t for t in texts)
    assert all({"at", "actor", "text"} <= set(e) for e in events)
    assert [e["at"] for e in events] == sorted((e["at"] for e in events), reverse=True)


def test_feed_survives_broken_db(client, tmp_path):
    (tmp_path / "store").mkdir()
    (tmp_path / "store" / "ask-soony.db").write_text("not a database")
    assert client.get("/api/feed").status_code == 200
