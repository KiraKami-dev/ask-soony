from fastapi.testclient import TestClient
from test_alerts import policy

from ask_soony.alerts import AlertBook
from ask_soony.api import app
from ask_soony.store import Store


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("ASK_DATA_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("ASK_SOONY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ASK_PUBLIC_URL", "https://dash.example.com")
    store = Store(tmp_path / "store")
    alert = AlertBook(store).raise_alert(
        title="T", message="M", action="A", severity="critical", policy=policy()
    )
    store.db.close()
    return TestClient(app), alert


def test_twiml_needs_the_alert_token(tmp_path, monkeypatch):
    client, alert = _setup(tmp_path, monkeypatch)
    assert client.post(f"/hooks/twilio/twiml/{alert.id}?token=wrong").status_code == 403
    ok = client.post(f"/hooks/twilio/twiml/{alert.id}?token={alert.token}")
    assert ok.status_code == 200 and ok.text.startswith("<")


def test_keypad_answer_records_the_decision(tmp_path, monkeypatch):
    client, alert = _setup(tmp_path, monkeypatch)
    r = client.post(
        f"/hooks/twilio/{alert.id}?token={alert.token}",
        content="Digits=1&To=%2B15550000001",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200 and "recorded" in r.text
    store = Store(tmp_path / "store")
    assert AlertBook(store).get(alert.id).status != "open"
    store.db.close()


def test_openai_hook_rejects_unsigned_events(tmp_path, monkeypatch):
    client, _ = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENAI_WEBHOOK_SECRET", "whsec_" + "a" * 32)
    assert client.post("/hooks/openai", content=b"{}").status_code == 401
