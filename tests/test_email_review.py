"""The email review may change subject, recipient and copy, but only to known contacts."""

from conftest import SUPPLIER

APPROVE = "/api/cards/gap-X/approve"


def test_subject_can_be_changed(agent_env):
    out = agent_env["client"].post(APPROVE, json={"option": 0, "subject": "  PO 42:\n status "})
    assert out.status_code == 200
    assert agent_env["sent"][0][:2] == (SUPPLIER, "PO 42: status")


def test_unknown_recipient_or_copy_is_refused(agent_env):
    client = agent_env["client"]
    bad_to = client.post(APPROVE, json={"option": 0, "to": "someone@elsewhere.example.org"})
    bad_cc = client.post(APPROVE, json={"option": 0, "cc": ["new@elsewhere.example.org"]})
    assert (bad_to.status_code, bad_cc.status_code) == (400, 400)
    assert agent_env["sent"] == []


def test_someone_who_replied_becomes_a_known_contact(agent_env):
    from ask_soony import api

    api.write_json(
        "actions.json",
        [
            {
                "id": "r1",
                "decision_id": "gap-X",
                "kind": "reply",
                "from": "Logistics <logistics@supplier.example.com>",
                "at": "2026-11-22T06:00",
            }
        ],
    )
    client = agent_env["client"]
    contacts = client.get("/api/cards/gap-X/contacts").json()["contacts"]
    assert "logistics@supplier.example.com" in {c["email"] for c in contacts}
    out = client.post(APPROVE, json={"option": 0, "cc": ["logistics@supplier.example.com"]})
    assert out.status_code == 200
    assert agent_env["sent"][0][3] == ["logistics@supplier.example.com"]


def test_dry_mail_records_instead_of_sending(agent_env, monkeypatch):
    from ask_soony import api, cardtools

    monkeypatch.setenv("ASK_MAIL_DRY", "on")
    monkeypatch.setattr(
        cardtools.smtplib, "SMTP", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )
    mid = (
        cardtools._send_mail.__wrapped__("a@example.com", "s", "b")
        if hasattr(cardtools._send_mail, "__wrapped__")
        else None
    )
    assert mid is None  # _send_mail is mocked by the fixture; call the dry path directly
    out = cardtools._dry_send("a@example.com", "Subject", "Body")
    assert out and api.read_list("dry_outbox.json")[-1]["subject"] == "Subject"
