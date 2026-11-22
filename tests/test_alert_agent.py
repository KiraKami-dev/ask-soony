"""Alert agent on the inbox API: one send path for Approve and voice, replies tied back to cards."""

import json
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient
from test_launch import make_portal

SUPPLIER = "sales@supplier.example.com"


@pytest.fixture
def env(tmp_path, monkeypatch):
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
        lambda to, subject, body: sent.append((to, subject, body)) or "<m1@x>",
    )
    return {"state": state, "client": TestClient(api.app), "sent": sent}


def _status(env) -> dict:
    return json.loads((env["state"] / "decision_status.json").read_text())["gap-X"]


def test_approve_sends_the_supplier_email(env):
    r = env["client"].post("/api/cards/gap-X/approve", json={"option": 0})
    assert r.status_code == 200
    assert env["sent"] == [(SUPPLIER, "300 copies short", "Earlier truck?")]
    actions = json.loads((env["state"] / "actions.json").read_text())
    assert isinstance(actions, list) and actions[-1]["message_id"] == "<m1@x>"
    assert _status(env)["status"] == "sent"


def test_approve_without_a_supplier_address_only_records(env):
    env["client"].post("/api/cards/gap-X/approve", json={"option": 1})
    assert env["sent"] == []
    assert _status(env)["status"] == "approved"


def test_voice_approves_only_after_a_yes_and_like_the_button(env):
    tool = "/api/cards/gap-X/tool"
    no = env["client"].post(tool, json={"name": "decide", "arguments": {"decision": "approve"}})
    assert no.json()["ok"] is False and env["sent"] == []
    yes = env["client"].post(
        tool, json={"name": "decide", "arguments": {"decision": "approve", "confirmed": True}}
    )
    out = yes.json()
    assert (out["ok"], out["status"], out["sent_to"]) == (True, "sent", SUPPLIER)
    assert len(env["sent"]) == 1
    again = env["client"].post(
        tool, json={"name": "decide", "arguments": {"decision": "approve", "confirmed": True}}
    )
    assert again.json()["ok"] is False and len(env["sent"]) == 1


def test_reply_is_attached_to_the_card(env, monkeypatch):
    from ask_soony import replies

    env["client"].post("/api/cards/gap-X/approve", json={"option": 0})
    reply = EmailMessage()
    reply["From"], reply["Subject"] = f"Ann <{SUPPLIER}>", "Re: 300 copies short"
    reply["Message-ID"], reply["In-Reply-To"] = "<r1@supplier.example.com>", "<m1@x>"
    reply.set_content("Yes, 300 on Sunday's 06:00 truck.\n\n> Earlier truck?")

    class FakeImap:
        def __init__(self, *a, **k): ...
        def login(self, *a): ...
        def select(self, *a, **k): ...
        def search(self, *a):
            return "OK", [b"1"]

        def fetch(self, *a):
            return "OK", [(b"1", reply.as_bytes())]

        def logout(self): ...

    monkeypatch.setattr(replies.imaplib, "IMAP4_SSL", FakeImap)
    monkeypatch.setattr(replies, "load_dotenv", lambda: None)
    monkeypatch.setattr(replies, "summarise", lambda text, subject: "300 on Sunday 06:00")
    for k in ("ASK_IMAP_HOST", "ASK_MAIL_ADDRESS", "ASK_MAIL_PASSWORD"):
        monkeypatch.setenv(k, "x")

    assert [r["card_id"] for r in replies.check_once()] == ["gap-X"]
    assert replies.check_once() == []  # seen once only
    st = _status(env)
    assert st["status"] == "sent" and st["reply"] == "300 on Sunday 06:00"
    assert st["reply_text"] == "Yes, 300 on Sunday's 06:00 truck."
    card = next(c for c in env["client"].get("/api/state").json()["cards"] if c["id"] == "gap-X")
    assert card["ui"]["reply_from"] == "Ann"
    kinds = [a["kind"] for a in json.loads((env["state"] / "actions.json").read_text())]
    assert kinds == ["approve", "reply"]
