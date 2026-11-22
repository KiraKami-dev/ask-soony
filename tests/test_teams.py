"""Teams alerts: the card says what does not match, buttons open the decision, posted once."""

import json


def _evidence_card(agent_env):
    (agent_env["state"] / "findings.json").write_text(
        json.dumps(
            [
                {
                    "id": "f-1",
                    "kind": "mismatch",
                    "title": "Short shipment",
                    "status": "open",
                    "level": "red",
                    "sides": [
                        {"source": "System says · ERP", "value": "48,000 due Sat"},
                        {"source": "Paper says · shipping advice", "value": "20,000 arrive Tue"},
                    ],
                }
            ]
        )
    )
    (agent_env["state"] / "decisions.json").write_text(
        json.dumps(
            [
                {
                    "finding_id": "f-1",
                    "headline": "20,000 arrive after launch",
                    "level": "red",
                    "options": [{"action": "Ask another supplier", "recommended": True}],
                }
            ]
        )
    )


def test_card_shows_the_mismatch_and_opens_the_app(agent_env, monkeypatch):
    from ask_soony import api, teams

    monkeypatch.setenv("ASK_APP_URL", "https://app.example.com")
    _evidence_card(agent_env)
    card = api._card("f-1")
    content = teams.card_payload(card)["attachments"][0]["content"]
    text = json.dumps(content)
    assert "THE SYSTEM SAYS" in text and "48,000 due Sat" in text and "20,000 arrive Tue" in text
    assert "Ask another supplier" in text
    urls = [a["url"] for a in content["actions"]]
    assert urls == ["https://app.example.com/d/f-1", "https://app.example.com/d/f-1?talk=1"]


def test_new_cards_are_posted_once_without_a_first_run_flood(agent_env, monkeypatch):
    from ask_soony import teams

    monkeypatch.setenv("ASK_TEAMS_WEBHOOK", "https://hooks.example.com/x")
    monkeypatch.setattr(teams, "load_dotenv", lambda: None)
    sent: list[dict] = []

    class Ok:
        status_code = 202
        text = ""

    monkeypatch.setattr(teams.httpx, "post", lambda url, json, timeout: sent.append(json) or Ok())
    assert teams.check_once() == []  # first run: existing cards are only remembered
    _evidence_card(agent_env)
    assert teams.check_once() == ["f-1"]
    assert teams.check_once() == []
    assert len(sent) == 1
