"""Emails sent from the shared mailbox outside the app are tied to their card."""

from ask_soony.outbox import match_card

CARDS = [
    {"id": "paper-4500318768-CMP-ABC-008-gr", "supplier_id": "SUP-1", "part": "CMP-ABC-008"},
    {"id": "paper-4500318700-CMP-ABC-001", "supplier_id": "SUP-2", "part": "CMP-ABC-001"},
    {"id": "allocate-CMP-ABC-001", "supplier_id": "SUP-2", "part": "CMP-ABC-001"},
]
SUP = {"a@example.com": "SUP-1", "b@example.com": "SUP-2"}


def test_thread_reference_wins():
    acts = [{"message_id": "<m1@x>", "decision_id": "paper-4500318700-CMP-ABC-001"}]
    assert match_card("b@example.com", "Re: x", "", ["<m1@x>"], CARDS, acts, SUP) == (
        "paper-4500318700-CMP-ABC-001"
    )


def test_order_number_or_a_unique_part_decides():
    assert match_card("a@example.com", "Order CMP-ABC-008", "", [], CARDS, [], SUP) == (
        "paper-4500318768-CMP-ABC-008-gr"
    )
    assert match_card("b@example.com", "PO 4500318700", "", [], CARDS, [], SUP) == (
        "paper-4500318700-CMP-ABC-001"
    )


def test_ambiguous_or_unrelated_stays_unmatched():
    assert match_card("b@example.com", "About CMP-ABC-001", "", [], CARDS, [], SUP) is None
    assert match_card("c@example.org", "PO 4500318768", "", [], CARDS, [], SUP) is None


def test_second_send_is_held_until_forced(agent_env):
    from ask_soony import api

    api.write_json(
        "actions.json",
        [
            {
                "id": "o1",
                "decision_id": "gap-X",
                "kind": "sent_outside",
                "message_id": "<o1@x>",
                "to": "sales@supplier.example.com",
                "at": api.now().isoformat(),
                "signed": "A Buyer",
            }
        ],
    )
    client = agent_env["client"]
    held = client.post("/api/cards/gap-X/approve", json={"option": 0})
    assert held.status_code == 409 and agent_env["sent"] == []
    ok = client.post("/api/cards/gap-X/approve", json={"option": 0, "force": True})
    assert ok.status_code == 200 and len(agent_env["sent"]) == 1


def test_sync_does_not_overwrite_actions_written_meanwhile(agent_env, monkeypatch):
    """The IMAP fetch takes time; an approval recorded during it must survive the sync."""
    from ask_soony import api, outbox

    api.write_json("actions.json", [])

    def slow_sent_folder(box):
        api.write_json("actions.json", [{"id": "meanwhile", "decision_id": "gap-X"}])
        return None  # no Sent folder: sync records nothing

    class Box:
        def __init__(self, *a, **k): ...
        def login(self, *a): ...
        def logout(self): ...

    monkeypatch.setenv("ASK_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("ASK_MAIL_ADDRESS", "user@example.com")
    monkeypatch.setenv("ASK_MAIL_PASSWORD", "x")
    monkeypatch.setattr(outbox.imaplib, "IMAP4_SSL", Box)
    monkeypatch.setattr(outbox, "_sent_folder", slow_sent_folder)
    outbox.sync()
    assert [a["id"] for a in api.read_list("actions.json")] == ["meanwhile"]
