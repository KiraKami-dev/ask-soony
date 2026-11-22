"""Routines that chase, call, watch silent shipments and react to replies; trust modes and undo."""

import json
from email.message import EmailMessage

import pytest
from conftest import SUPPLIER

from ask_soony import api, cardtools, routines
from ask_soony.routines import clean_spec, compile_spec


@pytest.fixture(autouse=True)
def in_my_inbox(monkeypatch):
    """The fixture card is routed to its supplier; routines only see the buyer's own inbox."""
    orig = api.build_state

    def mine():
        s = orig()
        s["inbox_ids"] = [c["id"] for c in s["cards"] if c["status"] == "open"]
        return s

    monkeypatch.setattr(api, "build_state", mine)


@pytest.fixture
def pushes(monkeypatch):
    got: list[tuple] = []
    monkeypatch.setattr(
        routines, "_push", lambda title, body, card_id=None: got.append((title, body))
    )
    return got


@pytest.fixture
def thread_mail(monkeypatch):
    got: list[dict] = []

    def fake(to, subject, body, in_reply_to, references):
        got.append(dict(to=to, subject=subject, body=body, irt=in_reply_to, refs=references))
        return f"<f{len(got)}@x>"

    monkeypatch.setattr(cardtools, "_send_mail_in_thread", fake)
    return got


@pytest.fixture
def phone(tmp_path, monkeypatch):
    """A policy with one contact and a fake caller: nothing is dialled."""
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps({"contacts": [{"id": "b", "name": "Sam Buyer", "role": "Buyer", "phone": "+1"}]})
    )
    monkeypatch.setenv("ASK_ALERT_POLICY", str(policy))
    monkeypatch.setenv("ASK_DATA_DIR", str(tmp_path / "alerts"))
    monkeypatch.setenv("ASK_CALL_PROVIDER", "dry_run")
    calls: list[str] = []

    class Fake:
        name = "fake"

        def call(self, alert, contact, public_url):
            calls.append(f"{contact.name}: {alert.message}")
            return "fake call"

    from ask_soony import escalation

    monkeypatch.setattr(escalation, "make_caller", lambda provider: Fake())
    return calls


def _actions(agent_env) -> list[dict]:
    path = agent_env["state"] / "actions.json"
    return json.loads(path.read_text()) if path.exists() else []


def _status(agent_env) -> dict:
    path = agent_env["state"] / "decision_status.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _routine(spec: dict, mode: str = "quiet", rid: str = "r1", name: str = "Test") -> dict:
    return {"id": rid, "name": name, "spec": spec, "code": compile_spec(spec), "mode": mode}


CHASE_THEN_CALL = {
    "on": "waiting",
    "when": [{"field": "waiting_hours", "op": "gt", "value": 2, "text": "no answer for 2 h"}],
    "then": [
        {"do": "chase", "message": "Any news on {headline}?", "text": "Chase once"},
        {
            "do": "call",
            "who": "me",
            "when": [{"field": "waiting_hours", "op": "gt", "value": 4, "text": "after 4 h"}],
            "message": "{supplier} is silent about {headline}",
            "text": "Call me",
        },
    ],
}
CALL_BEFORE_DEADLINE = {
    "when": [{"field": "hours_left", "op": "lt", "value": 3, "text": "under 3 hours left"}],
    "then": [{"do": "call", "message": "{headline} is still open", "text": "Call"}],
}
ASK_SHIPMENT = {
    "on": "shipments",
    "when": [{"field": "silent_hours", "op": "gt", "value": 12, "text": "no update for 12 h"}],
    "then": [{"do": "ask_status", "text": "Ask where it is"}],
}
NOTE_TO_DC = {
    "when": [{"field": "option_to", "op": "contains", "value": "dc site lead", "text": "DC"}],
    "then": [{"do": "carry_out", "match": {"to": "dc site lead"}, "text": "Tell the DC lead"}],
}
PICKUP = {
    "when": [{"field": "option_action", "op": "contains", "value": "email supplier"}],
    "then": [{"do": "carry_out", "match": {"to": "supplier.example.com"}, "text": "Ask"}],
}


def _sent_at_21(agent_env):
    """Approve the supplier email at 21:00 (ASK_NOW), so the card waits for an answer."""
    agent_env["client"].post("/api/cards/gap-X/approve", json={"option": 0})
    assert _status(agent_env)["gap-X"]["status"] == "sent"


# ---------------------------------------------------------------- recipes


def test_new_fields_steps_and_triggers_compile():
    for spec in (CHASE_THEN_CALL, CALL_BEFORE_DEADLINE, ASK_SHIPMENT):
        clean = clean_spec(spec)
        assert clean["on"] == spec.get("on", "decisions")
        assert "first_time" in compile_spec(spec)
    assert clean_spec(CHASE_THEN_CALL)["then"][1]["when"][0]["value"] == 4
    assert clean_spec(CHASE_THEN_CALL)["then"][1]["who"] == "me"


@pytest.mark.parametrize(
    "bad, word",
    [
        (
            {
                "on": "decisions",
                "when": [{"field": "kind", "value": "x"}],
                "then": [{"do": "chase"}],
            },
            "step 1",
        ),
        (
            {"when": [{"field": "silent_hours", "op": "gt", "value": 3}], "then": [{"do": "log"}]},
            "condition 1",
        ),
        (
            {
                "when": [{"field": "hours_left", "op": "lt", "value": "soon"}],
                "then": [{"do": "log"}],
            },
            "number",
        ),
        (
            {
                "when": [{"field": "has_reply", "op": "is", "value": "maybe"}],
                "then": [{"do": "log"}],
            },
            "yes or no",
        ),
        (
            {"on": "moon", "when": [{"field": "kind", "value": "x"}], "then": [{"do": "log"}]},
            "watch",
        ),
    ],
)
def test_recipes_that_do_not_fit_their_trigger_are_refused(bad, word):
    with pytest.raises(ValueError, match=word):
        clean_spec(bad)


def test_open_cards_carry_hours_left_and_reply_flags(agent_env):
    card = routines._Run({"id": "x", "name": "x"}, dry=True).open_cards()[0]
    assert card["hours_left"] == 2.0  # decide-by 23:00, now 21:00
    assert card["has_reply"] is False and card["reply_after_launch"] is False


# ---------------------------------------------------------------- call


def test_call_before_the_deadline_dry_then_real(agent_env, phone, pushes):
    r = _routine(CALL_BEFORE_DEADLINE, mode="tell")
    dry = routines.run(r, dry=True)
    assert dry["ok"] and [d["do"] for d in dry["did"]] == ["call"] and phone == []
    rec = routines.run(r)
    assert rec["ok"] and rec["result"] == 1, rec
    assert phone == ["Sam Buyer: 300 copies short is still open"]
    act = _actions(agent_env)[-1]
    assert (act["kind"], act["channel"], act["via"]) == ("call", "call", "routine: Test")
    assert len(pushes) == 1 and "Calling" in pushes[0][1]  # tell: one line per action
    assert routines.run(r)["result"] == 0  # once per card
    card = next(c for c in api.build_state()["cards"] if c["id"] == "gap-X")
    assert card["called_at"]


# ---------------------------------------------------------------- waiting: chase, then call


def test_chase_once_in_the_thread_then_call(agent_env, thread_mail, phone, monkeypatch):
    _sent_at_21(agent_env)
    r = _routine(CHASE_THEN_CALL)
    assert routines.run(r)["result"] == 0  # just sent: nobody waits yet

    monkeypatch.setenv("ASK_NOW", "2030-06-01T23:30:00+02:00")
    waiting = routines._Run(r, dry=True).waiting_cards()
    assert waiting[0]["hours_since_sent"] == 2.5 and waiting[0]["to"] == SUPPLIER
    rec = routines.run(r)
    assert [d["do"] for d in rec["did"]] == ["chase"], rec
    assert thread_mail == [
        dict(
            to=SUPPLIER,
            subject="Re: 300 copies short",
            body="Any news on 300 copies short?",
            irt="<m1@x>",
            refs="<m1@x>",
        )
    ]
    assert routines.run(r)["did"] == []  # chased once only
    chase = _actions(agent_env)[-1]
    assert chase["kind"] == "chase" and chase["message_id"] == "<f1@x>"

    monkeypatch.setenv("ASK_NOW", "2030-06-02T01:30:00+02:00")
    rec = routines.run(r)
    assert [d["do"] for d in rec["did"]] == ["call"] and len(thread_mail) == 1
    assert phone and "silent about 300 copies short" in phone[0]


def test_repeat_hours_chases_again_in_the_next_window(agent_env, thread_mail, monkeypatch):
    _sent_at_21(agent_env)
    spec = {**CHASE_THEN_CALL, "then": [{**CHASE_THEN_CALL["then"][0], "repeat_hours": 2}]}
    r = _routine(spec)
    monkeypatch.setenv("ASK_NOW", "2030-06-01T23:30:00+02:00")
    routines.run(r)
    routines.run(r)
    assert len(thread_mail) == 1
    monkeypatch.setenv("ASK_NOW", "2030-06-02T01:30:00+02:00")
    routines.run(r)
    assert len(thread_mail) == 2 and thread_mail[1]["irt"] == "<f1@x>"


def test_chase_refuses_a_card_that_was_never_sent(agent_env, thread_mail):
    ctx = routines._Run({"id": "x", "name": "x"}, dry=False)
    assert ctx.chase("gap-X", "hello")["ok"] is False and thread_mail == []


# ---------------------------------------------------------------- shipments


def _shipments(agent_env, tmp_path):
    erp = tmp_path / "portal" / "soony" / "erp"
    (erp / "open_shipments.csv").write_text(
        "shipment_id,route_id,mode,supplier_id,status,etd,eta,updated_at\n"
        "SH-1,R1,sea,SUP-9,in_transit,2030-05-20T08:00,2030-06-02T08:00,2030-06-01T20:00\n"
        "SH-2,R1,sea,SUP-9,in_transit,2030-05-20T08:00,2030-06-02T09:00,2030-05-31T20:00\n"
    )


def test_silent_shipment_gets_a_status_question(agent_env, tmp_path):
    _shipments(agent_env, tmp_path)
    r = _routine(ASK_SHIPMENT, rid="s1")
    ships = {s["id"]: s for s in routines._Run(r, dry=True).silent_shipments()}
    assert ships["SH-2"]["silent_hours"] == 24 and ships["SH-1"]["silent_hours"] == 0
    assert ships["SH-2"]["supplier_name"] == "Acme Paper"
    assert routines.run(r, dry=True)["did"][0]["shipment"] == "SH-2" and agent_env["sent"] == []
    rec = routines.run(r)
    assert rec["result"] == 1
    assert [(to, subj) for to, subj, _ in agent_env["sent"]] == [
        (SUPPLIER, "Status of shipment SH-2")
    ]
    assert "Acme Paper" in agent_env["sent"][0][2]
    assert _actions(agent_env)[-1]["kind"] == "ask_status"
    routines.run(r)
    assert len(agent_env["sent"]) == 1


# ---------------------------------------------------------------- trust modes


def test_ask_mode_proposes_and_accept_executes_once(agent_env, pushes):
    r = _routine(PICKUP, mode="ask")
    routines._save([r])
    rec = routines.run(r)
    assert rec["did"][0].get("proposed") and agent_env["sent"] == []
    routines.run({**r, "seen": []})  # the same pending proposal is not repeated
    c = agent_env["client"]
    props = c.get("/api/routines/proposals").json()["proposals"]
    assert len(props) == 1
    p = props[0]
    assert (p["status"], p["card_id"], p["routine_id"]) == ("pending", "gap-X", "r1")
    assert p["step"]["do"] == "carry_out" and "Earlier truck?" in p["preview_text"]

    out = c.post(f"/api/routines/proposals/{p['id']}/accept").json()
    assert out["ok"] and out["result"]["status"] == "sent" and len(agent_env["sent"]) == 1
    again = c.post(f"/api/routines/proposals/{p['id']}/accept").json()
    assert again["ok"] is False and len(agent_env["sent"]) == 1
    assert c.get("/api/routines/proposals").json()["proposals"][0]["status"] == "done"
    assert _actions(agent_env)[-1]["via"] == "routine: Test"


def test_skip_a_proposal(agent_env):
    routines.run(_routine(PICKUP, mode="ask"))
    pid = routines._proposals()[0]["id"]
    assert agent_env["client"].post(f"/api/routines/proposals/{pid}/skip").json()["ok"]
    assert routines._proposals()[0]["status"] == "skipped" and agent_env["sent"] == []


def test_quiet_mode_does_not_push(agent_env, pushes):
    routines.run(_routine(PICKUP, mode="quiet"))
    assert len(agent_env["sent"]) == 1 and pushes == []


def test_mode_route(agent_env):
    c = agent_env["client"]
    body = {"id": "m1", "name": "N", "description": "d", "spec": PICKUP}
    assert c.post("/api/routines", json=body).json()["ok"]
    assert c.get("/api/routines").json()["routines"][0]["mode"] == "tell"  # default
    assert c.post("/api/routines/m1/mode", json={"mode": "ask"}).json()["mode"] == "ask"
    assert c.post("/api/routines/m1/mode", json={"mode": "loud"}).status_code == 400
    c.post("/api/routines", json={**body, "name": "N2"})  # a new version keeps its mode
    assert c.get("/api/routines").json()["routines"][0]["mode"] == "ask"


# ---------------------------------------------------------------- undo


def test_undo_puts_a_note_back_and_refuses_an_email(agent_env):
    rec = routines.run(_routine(NOTE_TO_DC))
    aid = rec["did"][0]["action_id"]
    assert _status(agent_env)["gap-X"]["status"] == "approved"
    c = agent_env["client"]
    out = c.post(f"/api/routines/actions/{aid}/undo").json()
    assert out == {**out, "ok": True, "undone": True}
    assert "gap-X" not in _status(agent_env)
    assert _actions(agent_env)[-1]["kind"] == "undo"
    assert c.post(f"/api/routines/actions/{aid}/undo").json()["ok"] is False  # once

    sent = routines.run(_routine(PICKUP, rid="r2"))["did"][0]["action_id"]
    refused = c.post(f"/api/routines/actions/{sent}/undo").json()
    assert refused == {"ok": False, "reason": routines.EMAIL_GONE}
    assert _status(agent_env)["gap-X"]["status"] == "sent"


def test_undo_refuses_a_call(agent_env, phone):
    rec = routines.run(_routine(CALL_BEFORE_DEADLINE))
    out = routines.undo(rec["did"][0]["action_id"])
    assert out == {"ok": False, "reason": routines.CALL_GONE}


# ---------------------------------------------------------------- would handle


def test_would_handle_lists_routines_that_are_off_or_ask(agent_env):
    routines._save(
        [
            {**_routine(PICKUP, mode="tell", rid="off"), "enabled": False},
            {**_routine(PICKUP, mode="ask", rid="ask"), "enabled": True},
            {**_routine(PICKUP, mode="tell", rid="on"), "enabled": True},
        ]
    )
    out = agent_env["client"].get("/api/routines/would-handle").json()
    assert sorted(x["routine_id"] for x in out["gap-X"]) == ["ask", "off"]
    assert {x["mode"] for x in out["gap-X"]} == {"tell", "ask"}
    assert agent_env["sent"] == [] and routines._proposals() == []


# ---------------------------------------------------------------- replies


def test_reply_facts_are_stored_and_after_launch_pushes(agent_env, monkeypatch):
    from ask_soony import push, replies

    _sent_at_21(agent_env)
    reply = EmailMessage()
    reply["From"], reply["Subject"] = f"Ann <{SUPPLIER}>", "Re: 300 copies short"
    reply["Message-ID"], reply["In-Reply-To"] = "<r1@supplier.example.com>", "<m1@x>"
    reply.set_content("We can ship 300 on Wednesday.")

    class FakeImap:
        def __init__(self, *a, **k): ...
        def login(self, *a): ...
        def select(self, *a, **k): ...
        def search(self, *a):
            return "OK", [b"1"]

        def fetch(self, *a):
            return "OK", [(b"1", reply.as_bytes())]

        def logout(self): ...

    model_calls: list[tuple] = []

    def fake_read(text, subject, launch="", today=""):
        model_calls.append((launch,))
        return {
            "summary": "300 ship Wednesday",
            "new_date": "2030-06-05T10:00:00+02:00",
            "quantity": "300",
            "confirms": True,
            "after_launch": False,  # the date decides, not the model's flag
        }

    pushed: list[tuple] = []
    monkeypatch.setattr(replies.imaplib, "IMAP4_SSL", FakeImap)
    monkeypatch.setattr(replies, "load_dotenv", lambda: None)
    monkeypatch.setattr(replies, "_read", fake_read)
    monkeypatch.setattr(push, "send", lambda title, body, card_id=None: pushed.append(body))
    for k in ("ASK_IMAP_HOST", "ASK_MAIL_ADDRESS", "ASK_MAIL_PASSWORD"):
        monkeypatch.setenv(k, "x")

    assert [r["card_id"] for r in replies.check_once()] == ["gap-X"]
    st = _status(agent_env)["gap-X"]
    assert st["reply"] == "300 ship Wednesday"
    assert st["reply_date"] == "2030-06-05T10:00:00+02:00" and st["reply_quantity"] == 300
    assert st["reply_after_launch"] is True and st["status"] == "sent"
    assert model_calls[0][0] == "2030-06-02T20:00:00+02:00"
    assert "Supplier confirms after launch: 300 copies short" in pushed

    spec = {
        "on": "replies",
        "when": [{"field": "reply_after_launch", "op": "is", "value": "yes", "text": "late"}],
        "then": [{"do": "notify", "channel": "phone", "message": "Late: {headline}", "text": "t"}],
    }
    rec = routines.run(_routine(spec), dry=True)
    assert rec["did"] and rec["did"][0]["text"] == "Late: 300 copies short"
