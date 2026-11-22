import json

import pytest

from ask_soony.alerts import AlertBook, Contact, Policy
from ask_soony.dispatch.alerts import sync
from ask_soony.escalation import DryRunCaller, Escalator, Mailer
from ask_soony.store import Store


def _policy() -> Policy:
    return Policy(
        contacts=[Contact("buyer", "Alex Buyer", "buyer", phone="+15550000001")],
        routes={
            "critical": {"channels": ["dashboard", "call"], "chain": ["buyer"], "ack_minutes": 0},
            "high": {"channels": ["dashboard"], "chain": ["buyer"], "ack_minutes": 0},
            "medium": {"channels": ["dashboard"], "chain": ["buyer"], "ack_minutes": 0},
            "low": {"channels": ["dashboard"], "chain": ["buyer"], "ack_minutes": 0},
            "info": {"channels": ["dashboard"], "chain": ["buyer"], "ack_minutes": 0},
        },
    )


def _decision(**changes) -> dict:
    decision = {
        "id": "book-gap",
        "title": "Art books booked but not delivered",
        "part": "ART-BOOK",
        "at_risk": 5900,
        "system_says": "SAP books 80,000 units",
        "paper_says": "Delivery note records 8,000 units",
        "act_by": "2026-11-22T01:00:00+01:00",
        "messages": [{"subject": "Count stock and approve the rush order"}],
    }
    decision.update(changes)
    return decision


def test_dispatch_decision_alerts_once_and_calls_the_policy_contact(tmp_path):
    dispatch = tmp_path / "dispatch.json"
    dispatch.write_text(json.dumps({"decisions": [_decision()]}))
    store = Store(tmp_path / "store")
    book = AlertBook(store)
    policy = _policy()
    escalator = Escalator(book, policy, DryRunCaller(), Mailer("", "", "", dry_run=True))

    assert sync(dispatch, tmp_path / "dispatch_alerts.json", book, policy, escalator) == [1]
    alert = book.get(1)
    assert alert and alert.source == "dispatch" and alert.external is False
    assert "5,900 copies are at risk" in alert.message
    assert [event["channel"] for event in book.events(1)] == ["dashboard", "call"]
    assert sync(dispatch, tmp_path / "dispatch_alerts.json", book, policy, escalator) == []


def test_dispatch_alerts_reject_missing_action_text(tmp_path):
    dispatch = tmp_path / "dispatch.json"
    dispatch.write_text(json.dumps({"decisions": [_decision(messages=[])]}))
    store = Store(tmp_path / "store")
    book = AlertBook(store)
    policy = _policy()
    escalator = Escalator(book, policy, DryRunCaller(), Mailer("", "", "", dry_run=True))

    with pytest.raises(RuntimeError, match="has no routed message"):
        sync(dispatch, tmp_path / "dispatch_alerts.json", book, policy, escalator)
    assert book.list() == []


def test_dispatch_alerts_rejects_an_unroutable_policy(tmp_path):
    dispatch = tmp_path / "dispatch.json"
    dispatch.write_text(json.dumps({"decisions": [_decision()]}))
    store = Store(tmp_path / "store")
    book = AlertBook(store)
    policy = Policy()
    escalator = Escalator(book, policy, DryRunCaller(), Mailer("", "", "", dry_run=True))

    with pytest.raises(RuntimeError, match="no valid contact chain"):
        sync(dispatch, tmp_path / "dispatch_alerts.json", book, policy, escalator)
    assert book.list() == []
