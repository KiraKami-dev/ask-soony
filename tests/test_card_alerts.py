"""Case alerts: today's cases reach the phone, and a decision on the phone reaches the case."""

import json
from datetime import datetime, timedelta

import pytest

from ask_soony import card_alerts, push, teams
from ask_soony.alerts import AlertBook, Policy
from ask_soony.escalation import DryRunCaller, Escalator, Mailer
from ask_soony.store import Store

NOW = datetime.fromisoformat("2030-06-01T21:00:00+02:00")


@pytest.fixture
def bridge(agent_env, tmp_path, monkeypatch):
    policy_file = tmp_path / "alert-policy.json"
    policy_file.write_text(
        json.dumps(
            {
                "contacts": [
                    {"id": "buyer", "name": "Sam Buyer", "role": "buyer", "phone": "+10000000000"},
                    {"id": "head", "name": "Head", "role": "head", "phone": ""},
                ],
                "routes": {
                    "critical": {
                        "channels": ["dashboard", "call"],
                        "chain": ["buyer", "head"],
                        "ack_minutes": 10,
                    }
                },
            }
        )
    )
    monkeypatch.setenv("ASK_ALERT_POLICY", str(policy_file))
    monkeypatch.setenv("ASK_CALL_PROVIDER", "dry_run")
    monkeypatch.setenv("ASK_VIEWER", "Sam Buyer")
    monkeypatch.setenv("ASK_CALL_WITHIN_MIN", "180")
    monkeypatch.setenv("ASK_CALL_AFTER_MIN", "15")
    monkeypatch.delenv("ASK_SIGNOFF_SIMULATE", raising=False)
    monkeypatch.setattr(push, "subscriptions", lambda: [])
    monkeypatch.setattr(teams, "post_payload", lambda p: True)
    store = Store(tmp_path / "alertstore")
    book = AlertBook(store)
    policy = Policy.load(policy_file)
    esc = Escalator(book, policy, DryRunCaller(), Mailer("", "", "", dry_run=True))
    yield {**agent_env, "book": book, "policy": policy, "esc": esc}
    store.db.close()


def _tick(b, minutes=0):
    return card_alerts.tick(
        b["book"], b["policy"], b["esc"].notify, NOW + timedelta(minutes=minutes)
    )


def _card(b, cid="gap-X"):
    return next(c for c in b["client"].get("/api/state").json()["cards"] if c["id"] == cid)


def _case_alerts(b):
    return [a for a in b["book"].list() if a.source == "card"]


def _actions(b):
    p = b["state"] / "actions.json"
    return json.loads(p.read_text()) if p.exists() else []


def test_raises_once_per_case_after_the_delay(bridge):
    assert _tick(bridge)["raised"] == 0  # first seen now: not yet
    assert _tick(bridge, 10)["raised"] == 0
    assert _tick(bridge, 16)["raised"] == 1
    assert _tick(bridge, 30)["raised"] == 0  # idempotent
    (alert,) = _case_alerts(bridge)
    assert alert.title == "300 copies short" and alert.severity == "critical"
    assert "Decide by" in alert.message and alert.action.endswith(".")
    calls = [e for e in bridge["book"].events(alert.id) if e["kind"] == "call"]
    assert calls and "dry run: would call Sam Buyer" in calls[0]["detail"]
    assert card_alerts.link_of(bridge["book"], alert.id)["card_id"] == "gap-X"


def test_no_alert_for_a_decided_case(bridge):
    _tick(bridge)
    bridge["client"].post("/api/cards/gap-X/dismiss", json={})
    assert _tick(bridge, 20)["raised"] == 0
    assert _case_alerts(bridge) == []


def test_no_alert_when_the_case_does_not_matter(bridge, monkeypatch):
    monkeypatch.setenv("ASK_CALL_WITHIN_MIN", "30")
    _tick(bridge)
    if _card(bridge)["level"] != "red":
        assert _tick(bridge, 20)["raised"] == 0


def _raise(b):
    _tick(b)
    _tick(b, 16)
    (alert,) = _case_alerts(b)
    return alert


def test_approve_on_the_phone_reaches_the_case_once(bridge):
    alert = _raise(bridge)
    bridge["book"].decide(alert.id, "approve", by="voice (Sam Buyer)", channel="call")
    card = _card(bridge)
    assert card["status"] in ("sent", "approved")
    (action,) = _actions(bridge)
    assert action["via"] == "call" and action["by"] == "Sam Buyer" and action["kind"] == "approve"
    assert any(t["agent"] == "Phone call" and "Sam Buyer at" in t["text"] for t in card["trail"])
    # reconcile and a second decide do not apply it again
    assert card_alerts.reconcile(bridge["book"]) == 0
    bridge["book"].decide(alert.id, "approve", by="voice (Sam Buyer)", channel="call")
    assert len(_actions(bridge)) == 1
    activity = bridge["client"].get("/api/state").json()["activity"]
    assert any(a.get("card") == "gap-X" and a.get("via") == "call" for a in activity)
    # the chain stops: nobody else is called
    assert bridge["esc"].tick(NOW + timedelta(hours=1)) == 0


def test_escalate_on_the_phone_passes_the_case_on(bridge):
    alert = _raise(bridge)
    bridge["book"].decide(alert.id, "escalate", by="phone …00", channel="call")
    (action,) = _actions(bridge)
    assert action["kind"] == "escalate" and action["via"] == "call"
    assert action["note"] == "Passed on from a phone call"
    assert _card(bridge)["status"] == "escalated"
    assert bridge["esc"].tick(NOW + timedelta(hours=1)) == 0  # no second call down the chain


def test_reject_on_the_phone_dismisses_the_case(bridge):
    alert = _raise(bridge)
    bridge["book"].decide(alert.id, "reject", by="phone …00", channel="call")
    (action,) = _actions(bridge)
    assert action["kind"] == "dismiss" and action["note"] == "Rejected on a phone call"
    assert _card(bridge)["status"] == "dismissed"


def test_decision_made_while_down_is_applied_by_reconcile(bridge, monkeypatch):
    alert = _raise(bridge)
    monkeypatch.setattr(card_alerts, "on_decided", lambda book, a: None)
    bridge["book"].decide(alert.id, "reject", by="phone …00", channel="call")
    assert _actions(bridge) == []
    assert _tick(bridge, 40)["applied"] == 1
    assert _card(bridge)["status"] == "dismissed"
    assert _tick(bridge, 41)["applied"] == 0


def test_alert_closes_when_the_case_is_decided_on_the_web(bridge):
    alert = _raise(bridge)
    bridge["client"].post("/api/cards/gap-X/dismiss", json={})
    assert _tick(bridge, 20)["closed"] == 1
    assert bridge["book"].get(alert.id).status == "resolved"
    assert bridge["esc"].tick(NOW + timedelta(hours=1)) == 0
    # a late phone answer does not touch the case decided on the web
    bridge["book"].decide(alert.id, "approve", by="voice (Sam Buyer)", channel="call")
    assert len(_actions(bridge)) == 1


def test_duplicate_send_is_not_forced(bridge, monkeypatch):
    from fastapi import HTTPException

    from ask_soony import api

    alert = _raise(bridge)

    def dup(card_id, body):
        raise HTTPException(409, {"duplicate": {}, "count": 1})

    monkeypatch.setattr(api, "approve", dup)
    bridge["book"].decide(alert.id, "approve", by="voice (Sam Buyer)", channel="call")
    link = card_alerts.link_of(bridge["book"], alert.id)
    assert link["applied_at"] and "already sent" in link["result"]
    assert _card(bridge)["status"] == "open"


SIGNED = "gap-S"


@pytest.fixture
def signoff_case(bridge, tmp_path):
    """A case the buyer cannot sign (it costs money): Dr. Erika Example must sign it."""
    (tmp_path / "store" / "decision_cards.json").write_text(
        json.dumps(
            {
                "cards": [
                    {
                        "id": SIGNED,
                        "title": "300 art books arrive after launch",
                        "edition": "P-A",
                        "at_risk": 300,
                        "part": "Book",
                        "options": ["Air freight about 5,000 EUR"],
                        "decision": "Pay for air freight?",
                        "act_by": "Sat 01 Jun 22:30",
                        "messages": [
                            {
                                "to_role": "Supplier",
                                "to_email": "sales@supplier.example.com",
                                "subject": "Order air freight",
                                "body": "Please fly the books.",
                            },
                            {
                                "to_role": "Dr. Erika Example, Head of Procurement",
                                "to_email": None,
                                "subject": "Approve air freight",
                                "body": "Please approve.",
                            },
                        ],
                    }
                ]
            }
        )
    )
    return bridge


def test_signoff_case_approved_on_the_phone_goes_to_the_signer(signoff_case):
    b = signoff_case
    card = _card(b, SIGNED)
    assert card["signoff"] == "Dr. Erika Example" and card["status"] == "open"
    _tick(b)
    _tick(b, 16)
    (alert,) = _case_alerts(b)
    b["book"].decide(alert.id, "approve", by="voice (Sam Buyer)", channel="call")
    (action,) = _actions(b)
    assert action["kind"] == "escalate" and action["via"] == "call"
    assert "needs your sign-off" in action["note"]
    assert b["sent"] == []  # nothing went to the supplier
    assert "sign-off" in card_alerts.link_of(b["book"], alert.id)["result"]


def test_twilio_keypad_answer_reaches_the_case(bridge, tmp_path, monkeypatch):
    monkeypatch.setenv("ASK_DATA_DIR", str(tmp_path / "alertstore"))
    alert = _raise(bridge)
    r = bridge["client"].post(
        f"/hooks/twilio/{alert.id}?token={alert.token}",
        content="Digits=3&To=%2B10000000000",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200
    assert _card(bridge)["status"] == "dismissed"
    (action,) = _actions(bridge)
    assert action["via"] == "call"


def test_one_call_at_a_time(bridge, tmp_path):
    cards = json.loads((tmp_path / "store" / "decision_cards.json").read_text())
    second = {**cards["cards"][0], "id": "gap-Y", "title": "200 copies short"}
    cards["cards"].append(second)
    (tmp_path / "store" / "decision_cards.json").write_text(json.dumps(cards))
    _tick(bridge)
    assert _tick(bridge, 16)["raised"] == 1
    assert _tick(bridge, 17)["raised"] == 0  # the first call is still out
    # chain buyer -> head walked to its end (head has no phone): the next case may call
    bridge["esc"].tick(NOW + timedelta(hours=1))
    assert _tick(bridge, 16 + 25)["raised"] == 1
    assert len(_case_alerts(bridge)) == 2


def test_forget_starts_a_state_folder_afresh(bridge, tmp_path, monkeypatch):
    monkeypatch.setenv("ASK_DATA_DIR", str(tmp_path / "alertstore"))
    alert = _raise(bridge)
    assert card_alerts.forget(str(bridge["state"])) == 1
    assert bridge["book"].get(alert.id).status == "resolved"
    _tick(bridge, 20)
    assert _tick(bridge, 40)["raised"] == 1
