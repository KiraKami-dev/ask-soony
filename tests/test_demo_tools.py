"""Activity `via`, the voice assistant's teach_routine, and the demo card script."""

import json

from conftest import voice_decide

TOOL = "/api/cards/gap-X/tool"


def _activity(agent_env, card="gap-X") -> dict:
    items = agent_env["client"].get("/api/state").json()["activity"]
    return next(i for i in items if i.get("card") == card)


def test_activity_says_who_decided(agent_env):
    voice_decide(agent_env["client"])
    assert _activity(agent_env)["via"] == "voice"


def test_escalate_and_dismiss_record_via(agent_env):
    client = agent_env["client"]
    client.post("/api/cards/gap-X/escalate", json={"note": "n", "via": "routine: Chase"})
    assert _activity(agent_env)["via"] == "routine: Chase"
    client.post("/api/cards/gap-X/dismiss", json={})
    actions = json.loads((agent_env["state"] / "actions.json").read_text())
    assert actions[-1]["via"] == "inbox" and _activity(agent_env)["via"] == "inbox"


def test_teach_routine_drafts_only_after_a_decision(agent_env, monkeypatch):
    from ask_soony import push, routines

    calls, pushes = [], []
    monkeypatch.setattr(
        routines,
        "suggest",
        lambda cid: (
            calls.append(cid)
            or {"routine": {"id": "r1", "name": "Chase late trucks", "description": "d"}}
        ),
    )
    monkeypatch.setattr(push, "send", lambda *a: pushes.append(a))
    client = agent_env["client"]
    teach = {"name": "teach_routine", "arguments": {"instruction": "next time just do that"}}

    early = client.post(TOOL, json=teach).json()
    assert early == {"ok": False, "error": "decide first"} and calls == []

    voice_decide(client)
    out = client.post(TOOL, json=teach).json()
    assert out == {"ok": True, "name": "Chase late trucks", "description": "d"}
    assert pushes == [("Routine draft ready", "Chase late trucks", "gap-X")]
    drafts = client.get("/api/routines/drafts").json()["drafts"]
    assert drafts[0]["card_id"] == "gap-X" and drafts[0]["routine"]["enabled"] is False
    assert drafts[0]["instruction"] == "next time just do that"
    assert not (agent_env["state"] / "routines.json").exists()  # nothing saved or switched on


# ---------------------------------------------------------------- demo_cards
