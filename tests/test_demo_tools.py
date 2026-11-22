"""Activity `via`, the voice assistant's teach_routine, and the demo card script."""

import importlib.util
import json
from pathlib import Path

import pytest

TOOL = "/api/cards/gap-X/tool"


def _activity(agent_env, card="gap-X") -> dict:
    items = agent_env["client"].get("/api/state").json()["activity"]
    return next(i for i in items if i.get("card") == card)


def test_activity_says_who_decided(agent_env):
    agent_env["client"].post(
        TOOL, json={"name": "decide", "arguments": {"decision": "approve", "confirmed": True}}
    )
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

    client.post(
        TOOL, json={"name": "decide", "arguments": {"decision": "approve", "confirmed": True}}
    )
    out = client.post(TOOL, json=teach).json()
    assert out == {"ok": True, "name": "Chase late trucks", "description": "d"}
    assert pushes == [("Routine draft ready", "Chase late trucks", "gap-X")]
    drafts = client.get("/api/routines/drafts").json()["drafts"]
    assert drafts[0]["card_id"] == "gap-X" and drafts[0]["routine"]["enabled"] is False
    assert drafts[0]["instruction"] == "next time just do that"
    assert not (agent_env["state"] / "routines.json").exists()  # nothing saved or switched on


# ---------------------------------------------------------------- demo_cards


def _load_script():
    path = Path(__file__).resolve().parent.parent / "scripts" / "demo_cards.py"
    spec = importlib.util.spec_from_file_location("demo_cards", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def demo(tmp_path, monkeypatch):
    portal = tmp_path / "portal"
    files = {
        "soony/master/products.csv": "product_id,lifecycle,launch_date,day_one_qty\n"
        "P-CE,launch,2030-06-02,100\n",
        "soony/master/bom.csv": "parent_id,child_id,qty_per\nP-CE,PART-S,1\n",
        "soony/erp/packout_log.csv": "date,product_id,units_packed\n2030-05-30,P-CE,40\n",
        "soony/erp/stock_snapshot.csv": "snapshot_date,material_id,batch,quantity\n"
        "2030-05-31,PART-S,B1,30\n2030-05-31,PART-S,5000413063,20\n",
        "soony/erp/goods_receipts.csv": "gr_number,po_number,po_line,supplier_id,material_id,"
        "qty_ordered,qty_received,remark\n5000413063,4500000001,1,SUP-1,PART-S,20,20,\n",
        "soony/receiving/DC-HAM/inspection_report_5000413063.pdf": "%PDF-1.4",
    }
    for rel, text in files.items():
        (portal / rel).parent.mkdir(parents=True, exist_ok=True)
        (portal / rel).write_text(text)
    state = tmp_path / "state"
    state.mkdir()
    (state / "findings.json").write_text(json.dumps([{"id": "keep-me", "title": "other"}]))
    (state / "actions.json").write_text(json.dumps([{"id": "a1", "decision_id": "keep-me"}]))
    (state / "routines.json").write_text(json.dumps([{"id": "r1", "enabled": True}]))
    monkeypatch.setenv("ASK_PORTAL_DIR", str(portal))
    monkeypatch.setenv("ASK_STATE_DIR", str(state))
    monkeypatch.setenv("ASK_NOW", "2030-06-02T08:00:00+02:00")
    mod = _load_script()
    text = "Lot does not meet AQL. Booked as 'unrestricted'. Use only under a concession."
    monkeypatch.setattr(mod, "pdf_text", lambda path: text)
    return mod, state


def test_demo_cards_merge_idempotently(demo):
    mod, state = demo
    assert mod.main([]) == 0
    first = json.loads((state / "findings.json").read_text())
    assert mod.main([]) == 0
    findings = json.loads((state / "findings.json").read_text())
    assert [f["id"] for f in findings] == [f["id"] for f in first]
    assert [f["id"] for f in findings] == ["keep-me", "qa-5000413063-PART-S"]
    impact = json.loads((state / "impacts.json").read_text())[0]
    assert (impact["copies_at_risk"], impact["edition"]) == (30, "P-CE")  # 60 to pack, 30 good
    decision = json.loads((state / "decisions.json").read_text())[0]
    assert decision["headline"].startswith("20 statues on the shelf failed inspection")
    assert decision["deadline"] >= "2030-06-02T10:00"
    # untouched without --reset
    assert json.loads((state / "actions.json").read_text())[0]["id"] == "a1"


def test_demo_cards_reset_backs_up_and_switches_routines_off(demo):
    mod, state = demo
    assert mod.main(["--reset"]) == 0
    assert json.loads((state / "actions.json").read_text()) == []
    assert json.loads((state / "routines.json").read_text())[0]["enabled"] is False
    backups = sorted(p.name for p in state.glob("*.before-demo-*.json"))
    assert any(b.startswith("actions.") for b in backups)
    assert any(b.startswith("routines.") for b in backups)
