"""Shared fixtures for the alert agent tests."""

import json

import pytest
from fastapi.testclient import TestClient
from test_launch import make_portal

SUPPLIER = "sales@supplier.example.com"


@pytest.fixture(autouse=True)
def _no_real_dotenv(tmp_path, monkeypatch):
    """Run every test in an empty folder, so code that calls load_dotenv() cannot pull the real
    .env (passwords, API keys) into the test process and change later tests."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def agent_env(tmp_path, monkeypatch):
    portal = make_portal(tmp_path / "portal", "400")
    profile = portal / "suppliers" / "SUP-9_Acme"
    profile.mkdir(parents=True)
    (profile / "profile.csv").write_text(
        "supplier_id,name,country,lat,lon,contact_email\n"
        f"SUP-9,Acme Paper,PL,52.4,16.9,{SUPPLIER}\n"
    )
    state, store = tmp_path / "state", tmp_path / "store"
    state.mkdir()
    store.mkdir()
    monkeypatch.setenv("ASK_PORTAL_DIR", str(portal))
    monkeypatch.setenv("ASK_STATE_DIR", str(state))
    monkeypatch.setenv("ASK_CARDS_FILE", str(store / "decision_cards.json"))
    monkeypatch.setenv("ASK_NOW", "2030-06-01T21:00:00+02:00")
    (store / "decision_cards.json").write_text(
        json.dumps(
            {
                "cards": [
                    {
                        "id": "gap-X",
                        "title": "300 copies short",
                        "edition": "P-A",
                        "at_risk": 300,
                        "part": "Book",
                        "options": ["Ask supplier for an earlier truck", "Ask the DC lead"],
                        "decision": "Chase the supplier?",
                        "act_by": "Sat 01 Jun 23:00",
                        "messages": [
                            {
                                "to_role": "Supplier",
                                "to_email": SUPPLIER,
                                "subject": "s",
                                "body": "Earlier truck?",
                            },
                            {
                                "to_role": "DC site lead",
                                "to_email": None,
                                "subject": "t",
                                "body": "Collect it",
                            },
                        ],
                    }
                ]
            }
        )
    )
    sent: list[tuple] = []
    from ask_soony import api, cardtools

    monkeypatch.setattr(
        cardtools,
        "_send_mail",
        lambda to, subject, body, cc=None: (
            sent.append((to, subject, body, *([cc] if cc else []))) or "<m1@x>"
        ),
    )
    return {"state": state, "client": TestClient(api.app), "sent": sent}


def voice_decide(client, card="gap-X", decision="approve", option=0) -> dict:
    """The voice assistant's two steps: prepare (summary + token), then decide after a yes."""
    tool = f"/api/cards/{card}/tool"
    args = {"decision": decision, "option_index": option}
    prep = client.post(tool, json={"name": "prepare_decision", "arguments": args}).json()
    token = prep.get("token", "")
    return client.post(
        tool, json={"name": "decide", "arguments": {**args, "token": token, "confirmed": True}}
    ).json()


@pytest.fixture(autouse=True)
def _no_live_reading(monkeypatch):
    """Tests never call a model: live document reading (verifier.image_recognizer.live) is off."""
    monkeypatch.setenv("ASK_READER", "off")
