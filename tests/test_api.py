import pytest
from fastapi.testclient import TestClient

from ask_soony.api import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ASK_SOONY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ASK_SOONY_STATE", str(tmp_path / "missing.json"))
    return TestClient(app)


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
    assert body["source"] == "verifier" and body["cards"] == []


def test_action_persists_and_merges(client, tmp_path):
    card_id = client.get("/api/state").json()["cards"][0]["id"]
    r = client.post(f"/api/cards/{card_id}/action", json={"action": "allow_once", "note": "ok"})
    assert r.status_code == 200
    assert (tmp_path / "state" / "decisions.json").exists()
    card = client.get("/api/state").json()["cards"][0]
    assert card["human_decision"]["action"] == "allow_once"
    assert client.get("/api/feed").json()[0]["agent"] == "human"


def test_action_rejects_unknown(client):
    assert client.post("/api/cards/x/action", json={"action": "nope"}).status_code == 422


def test_feed_fixture_when_empty(client):
    assert len(client.get("/api/feed").json()) > 0


def test_mode_roundtrip(client):
    assert client.post("/api/mode", json={"mode": "autonomous"}).json() == {"mode": "autonomous"}
    assert client.get("/api/mode").json()["mode"] == "autonomous"
    assert client.get("/api/state").json()["mode"] == "autonomous"
    assert client.post("/api/mode", json={"mode": "bad"}).status_code == 422
