"""Sign-off links: a passed-on case reaches its approver, and their answer comes back."""

import json

import pytest

from ask_soony import push, signoff, teams

SUPPLIER = "sales@supplier.example.com"
CARD = "gap-S"


@pytest.fixture
def env(agent_env, monkeypatch, tmp_path):
    """A case the buyer cannot sign (it costs money): the approval goes to Dr. Erika Example."""
    store = tmp_path / "store"
    (store / "decision_cards.json").write_text(
        json.dumps(
            {
                "cards": [
                    {
                        "id": CARD,
                        "title": "300 art books arrive after launch",
                        "edition": "P-A",
                        "at_risk": 300,
                        "part": "Book",
                        "options": ["Air freight about 5,000 EUR"],
                        "decision": "Pay for air freight?",
                        "messages": [
                            {
                                "to_role": "Supplier",
                                "to_email": SUPPLIER,
                                "subject": "Order air freight",
                                "body": "Please fly the books.",
                            },
                            {
                                "to_role": "Dr. Erika Example, Head of Procurement",
                                "to_email": None,
                                "subject": "Approve air freight",
                                "body": "Please approve.",
                            },
                            {
                                "to_role": "DC site lead",
                                "to_email": None,
                                "subject": "Expect the van",
                                "body": "A van with 300 books arrives at 14:00.",
                            },
                        ],
                    }
                ]
            }
        )
    )
    monkeypatch.setenv("ASK_VIEWER", "Sam Buyer")
    monkeypatch.setenv("ASK_APP_URL", "https://app.example.com")
    monkeypatch.delenv("ASK_SIGNOFF_SIMULATE", raising=False)
    out: dict[str, list] = {"push": [], "teams": []}
    monkeypatch.setattr(push, "subscriptions", lambda: [{"endpoint": "x"}])
    monkeypatch.setattr(push, "send", lambda *a, **k: out["push"].append((a, k)) or {"sent": 1})
    monkeypatch.setattr(teams, "post_payload", lambda p: out["teams"].append(p) or True)
    agent_env["out"] = out
    return agent_env


def _card(env, cid=CARD):
    return next(c for c in env["client"].get("/api/state").json()["cards"] if c["id"] == cid)


def _token(state) -> str:
    return next(iter(json.loads((state / "signoff_tokens.json").read_text())))


def _escalate(env, **body):
    r = env["client"].post(f"/api/cards/{CARD}/escalate", json={"note": "Please hurry", **body})
    assert r.status_code == 200, r.text
    return r.json()


def test_escalate_sends_a_link_and_records_channels(env):
    action = _escalate(env, option=0)
    assert action["channels"] == ["teams", "push"]
    (title, _body, card_id), kw = env["out"]["push"][0]
    assert title.startswith("Needs your OK: 300 art books")
    assert card_id == CARD and kw["url"].startswith("https://app.example.com/ok/")
    req = _card(env)["signoff_request"]
    assert req["kind"] == "escalation" and req["who"] == "Erika" and req["delivered"]
    assert "token" not in json.dumps(req)


def test_no_channel_is_said_honestly(env, monkeypatch):
    monkeypatch.setattr(push, "subscriptions", lambda: [])
    monkeypatch.setattr(teams, "post_payload", lambda p: False)
    action = _escalate(env)
    assert action["channels"] == []
    req = _card(env)["signoff_request"]
    assert req["delivered"] is False and req["answer"] is None


def test_token_valid_until_launch_only(env, monkeypatch):
    _escalate(env)
    token = _token(env["state"])
    assert signoff.token_entry(token) is not None
    assert signoff.token_entry("not-a-real-token-at-all") is None
    assert signoff.token_entry("short") is None
    exp = json.loads((env["state"] / "signoff_tokens.json").read_text())[token]["expires_at"]
    monkeypatch.setenv("ASK_NOW", exp)
    assert signoff.token_entry(token) is None
    assert env["client"].get(f"/api/ok/{token}").status_code == 404


def test_basic_auth_lets_only_valid_links_through(env, monkeypatch):
    from fastapi.testclient import TestClient

    from ask_soony import api

    _escalate(env)
    token = _token(env["state"])
    monkeypatch.setenv("ASK_DASHBOARD_PASSWORD", "example-password")
    app = api.FastAPI()
    app.include_router(signoff.router)
    from ask_soony.basic_auth import BasicAuth

    app.add_middleware(BasicAuth)
    c = TestClient(app)
    assert c.get(f"/api/ok/{token}").status_code == 200
    assert c.get("/api/ok/abcdefghijklmnopqrstuvwx").status_code == 404
    assert c.post(f"/api/cards/{CARD}/signoff/resend").status_code == 401  # dashboard only


def test_approve_by_link_carries_out_once(env):
    _escalate(env, option=0)
    token = _token(env["state"])
    page = env["client"].get(f"/api/ok/{token}").json()
    assert page["recommendation"]["action"].startswith("Email Supplier")
    assert page["note"] == "Please hurry" and page["asked_by_short"] == "Sam"
    r = env["client"].post(f"/api/ok/{token}", json={"answer": "approve"})
    assert r.status_code == 200, r.text
    assert len(env["sent"]) == 1 and env["sent"][0][0] == SUPPLIER
    again = env["client"].post(f"/api/ok/{token}", json={"answer": "approve"})
    assert again.status_code == 409 and len(env["sent"]) == 1
    card = _card(env)
    assert card["status"] == "sent"
    assert card["signoff_request"]["answer"]["decision"] == "approved"
    assert card["signoff_request"]["answer"]["simulated"] is False
    acts = json.loads((env["state"] / "actions.json").read_text())
    done = [a for a in acts if a["kind"] == "approve"]
    assert len(done) == 1 and done[0]["via"] == "signoff" and done[0]["by"] == "Dr. Erika Example"


def test_approve_keeps_the_option_named_in_the_note(env):
    card = _card(env)
    van = card["options"][2]
    _escalate(env, note=f"Please approve: {van['action']} ({van['cost']}). Call me")
    page = env["client"].get(f"/api/ok/{_token(env['state'])}").json()
    assert page["option"] == 2 and page["note"] == "Call me"


def test_decline_reopens_with_the_note(env):
    _escalate(env)
    token = _token(env["state"])
    r = env["client"].post(f"/api/ok/{token}", json={"answer": "decline", "note": "Too dear"})
    assert r.status_code == 200
    card = _card(env)
    assert card["status"] == "open" and not card["routed_to_other"]
    assert card["ui"]["note"] == "Too dear"
    assert card["signoff_request"]["answer"] == {
        **card["signoff_request"]["answer"],
        "decision": "declined",
        "note": "Too dear",
    }
    assert env["sent"] == []
    state = env["client"].get("/api/state").json()
    assert CARD in state["inbox_ids"]


def test_notify_option_reaches_the_role_and_records_answers(env):
    van = _card(env)["options"][2]
    assert van["channel"] == "notify"
    r = env["client"].post(f"/api/cards/{CARD}/approve", json={"option": 2})
    assert r.json()["action"]["channels"] == ["teams", "push"]
    (title, *_), _ = env["out"]["push"][-1]
    assert title.startswith("For DC site lead: ")
    token = _token(env["state"])
    page = env["client"].get(f"/api/ok/{token}").json()
    assert page["kind"] == "notify" and "300 books" in page["message"]
    assert env["client"].post(f"/api/ok/{token}", json={"answer": "got_it"}).status_code == 200
    assert env["client"].post(f"/api/ok/{token}", json={"answer": "done"}).status_code == 200
    assert env["client"].post(f"/api/ok/{token}", json={"answer": "done"}).status_code == 409
    req = _card(env)["signoff_request"]
    assert [a["answer"] for a in req["acks"]] == ["got_it", "done"]
    assert req["answer"]["decision"] == "done"


def test_resend_for_an_old_escalation(env):
    # Passed on before links existed: an escalate action without a request.
    from ask_soony import api

    api._record(CARD, "escalated", {"kind": "escalate", "to": "Dr. Erika Example", "note": ""})
    assert _card(env)["signoff_request"] is None
    r = env["client"].post(f"/api/cards/{CARD}/signoff/resend")
    assert r.status_code == 200 and r.json()["channels"] == ["teams", "push"]
    assert _card(env)["signoff_request"]["delivered"]


def test_simulated_approval_for_the_demo(env, monkeypatch):
    monkeypatch.setenv("ASK_SIGNOFF_SIMULATE", "0")
    _escalate(env, option=0)
    signoff._timers[-1].join(10)
    card = _card(env)
    assert card["status"] == "sent" and len(env["sent"]) == 1
    ans = card["signoff_request"]["answer"]
    assert ans["decision"] == "approved" and ans["simulated"] is True
    acts = json.loads((env["state"] / "actions.json").read_text())
    assert [a["via"] for a in acts if a["kind"] == "approve"] == ["signoff-simulated"]
    # The link is answered: a late tap does nothing.
    r = env["client"].post(f"/api/ok/{_token(env['state'])}", json={"answer": "approve"})
    assert r.status_code == 409 and len(env["sent"]) == 1
