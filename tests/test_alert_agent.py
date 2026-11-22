"""Alert agent on the inbox API: one send path for Approve and voice, replies tied back to cards."""

import json
from email.message import EmailMessage

from conftest import SUPPLIER


def _status(agent_env) -> dict:
    return json.loads((agent_env["state"] / "decision_status.json").read_text())["gap-X"]


def test_approve_sends_the_supplier_email(agent_env):
    r = agent_env["client"].post("/api/cards/gap-X/approve", json={"option": 0})
    assert r.status_code == 200
    assert agent_env["sent"] == [(SUPPLIER, "300 copies short", "Earlier truck?")]
    actions = json.loads((agent_env["state"] / "actions.json").read_text())
    assert isinstance(actions, list) and actions[-1]["message_id"] == "<m1@x>"
    assert _status(agent_env)["status"] == "sent"


def test_approve_without_a_supplier_address_only_records(agent_env):
    agent_env["client"].post("/api/cards/gap-X/approve", json={"option": 1})
    assert agent_env["sent"] == []
    assert _status(agent_env)["status"] == "approved"


def test_voice_approves_only_after_a_yes_and_like_the_button(agent_env):
    tool = "/api/cards/gap-X/tool"
    no = agent_env["client"].post(
        tool, json={"name": "decide", "arguments": {"decision": "approve"}}
    )
    assert no.json()["ok"] is False and agent_env["sent"] == []
    yes = agent_env["client"].post(
        tool, json={"name": "decide", "arguments": {"decision": "approve", "confirmed": True}}
    )
    out = yes.json()
    assert (out["ok"], out["status"], out["sent_to"]) == (True, "sent", SUPPLIER)
    assert len(agent_env["sent"]) == 1
    again = agent_env["client"].post(
        tool, json={"name": "decide", "arguments": {"decision": "approve", "confirmed": True}}
    )
    assert again.json()["ok"] is False and len(agent_env["sent"]) == 1


def test_reply_is_attached_to_the_card(agent_env, monkeypatch):
    from ask_soony import replies

    agent_env["client"].post("/api/cards/gap-X/approve", json={"option": 0})
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
    st = _status(agent_env)
    assert st["status"] == "sent" and st["reply"] == "300 on Sunday 06:00"
    assert st["reply_text"] == "Yes, 300 on Sunday's 06:00 truck."
    card = next(
        c for c in agent_env["client"].get("/api/state").json()["cards"] if c["id"] == "gap-X"
    )
    assert card["ui"]["reply_from"] == "Ann"
    kinds = [a["kind"] for a in json.loads((agent_env["state"] / "actions.json").read_text())]
    assert kinds == ["approve", "reply"]
