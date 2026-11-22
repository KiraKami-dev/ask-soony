"""Routines in the Monty sandbox: only the building blocks, type-checked, money stays human."""

import json

import pytest

from ask_soony import api, routines


@pytest.fixture(autouse=True)
def in_my_inbox(monkeypatch):
    """The fixture card is routed to its supplier; routines only see the buyer's own inbox."""
    orig = api.build_state

    def mine():
        s = orig()
        s["inbox_ids"] = [c["id"] for c in s["cards"]]
        return s

    monkeypatch.setattr(api, "build_state", mine)


FREE_EMAIL = """
from soony import open_cards, carry_out, log, first_time
n = 0
for card in open_cards():
    for o in card["options"]:
        if o["channel"] == "email" and o["free"] and first_time(card["id"]):
            carry_out(card["id"], o["index"], "like last time")
            log("done " + card["id"])
            n += 1
            break
n
"""


def test_dry_run_records_what_it_would_do(agent_env):
    rec = routines.run({"id": "r1", "name": "T", "code": FREE_EMAIL}, dry=True)
    assert rec["ok"] and rec["result"] == 1
    assert rec["did"][0]["do"] == "carry_out" and rec["did"][0]["dry"] is True
    assert agent_env["sent"] == []  # nothing left the building


def test_real_run_acts_once_like_the_button(agent_env):
    r = {"id": "r1", "name": "Chase", "code": FREE_EMAIL}
    first = routines.run(r)
    assert first["ok"] and len(agent_env["sent"]) == 1
    actions = json.loads((agent_env["state"] / "actions.json").read_text())
    assert actions[-1]["via"] == "routine: Chase"
    assert routines.run(r)["result"] == 0  # the card is decided now, nothing repeats


def test_options_that_cost_money_stay_with_a_person(agent_env, monkeypatch):
    orig = api.build_state

    def priced():
        s = orig()
        for c in s["cards"]:
            for o in c["options"]:
                o["cost"] = "EUR 40,000"
        return s

    monkeypatch.setattr(api, "build_state", priced)
    code = """
from soony import open_cards, carry_out
r = carry_out(open_cards()[0]["id"], 0, "try")
r["error"] or "none"
"""
    rec = routines.run({"id": "r2", "name": "T", "code": code})
    assert rec["result"] == "costs money: a person decides" and agent_env["sent"] == []


def test_sandbox_rejects_anything_but_the_building_blocks(agent_env):
    for bad in ("import os\nos.listdir('.')", "from soony import open_cards\nopen_cards(1)"):
        rec = routines.run({"id": "x", "name": "x", "code": bad}, dry=True)
        assert rec["ok"] is False and rec["error"]

