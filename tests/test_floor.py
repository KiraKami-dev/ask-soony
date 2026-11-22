"""Ask the floor: stale counts become questions to the site, and the site's answer is the truth."""

import json
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo

import openpyxl
import pytest
from fastapi.testclient import TestClient
from test_launch import make_portal

from ask_soony import floor, launch

TZ = ZoneInfo("Europe/Berlin")
SAT_EVENING = "2030-06-01T21:00:00+02:00"  # the last count
CARD = "ASK-packed-P-A-06012100"


def _portal(tmp_path):
    portal = make_portal(tmp_path / "portal", "400")
    soony = portal / "soony"
    st = openpyxl.Workbook()
    fg = st.active
    fg.title = "Finished goods"
    fg.append(["Packed copies DC-X, 2030-06-01 21:00"])
    fg.append(["Site", "Material", "Qty"])
    fg.append(["DC-X", "P-A", "400"])
    comp = st.create_sheet("Components")
    comp.append(["Stock DC-X 2030-06-01, late shift (book stock, not counted)"])
    comp.append(["Site", "Material", "Description", "Qty", "UoM", "Used today", "Last count"])
    comp.append(["DC-X", "C-BOX", "Box", "300", "PC", 0, "2030-05-20"])  # tight, old count
    comp.append(["DC-X", "C-PIN", "Pin", "9.000", "PC", 0, "2030-05-20"])  # plenty: no question
    st.save(soony / "erp" / "stock_update_X.xlsx")
    (soony / "erp" / "packout_log.csv").write_text(
        "date,site,product_id,line,units_packed\n2030-05-30,DC-X,P-A,Cell 1,100\n"
    )
    m = soony / "master"
    (m / "sites.csv").write_text("site_id,name,type,city\nDC-X,Example DC,dc,Exampleton\n")
    (m / "bom.csv").write_text(
        "parent_id,child_id,qty_per\nP-A,C-BOX,1\nP-A,C-PIN,1\nP-A,C-LID,1\n"
    )
    (m / "components.csv").write_text("component_id,description\nC-BOX,Box\nC-PIN,Pin\nC-LID,Lid\n")
    (soony / "erp" / "purchase_orders_X.csv").write_text(
        "po_number,po_line,supplier_id,material_id,quantity,buyer\n"
        "PO1,1,SUP-9,C-LID,500,Sam Buyer\n"
    )
    (soony / "erp" / "open_shipments.csv").write_text(
        "shipment_id,mode,reference,supplier_id,po_numbers,material_ids,route_id,dest_site,"
        "etd,eta,status,updated_at,note\n"
        "SHP-1,road,TRK-1,SUP-9,PO1,C-LID,RT-1,DC-X,2030-05-28,2030-05-31,in_delivery,"
        "2030-05-31T12:00:00+02:00,\n"
    )
    return portal


@pytest.fixture
def env(tmp_path, monkeypatch):
    portal = _portal(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("ASK_PORTAL_DIR", str(portal))
    monkeypatch.setenv("ASK_STATE_DIR", str(state))
    monkeypatch.setenv("ASK_CARDS_FILE", str(tmp_path / "none.json"))
    monkeypatch.setenv("ASK_DATA_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("ASK_NOW", "2030-06-02T07:00:00+02:00")  # 1 h Sat + 1 h Sun = 2 h
    monkeypatch.setenv("ASK_APP_URL", "https://app.example.com")
    sent: dict[str, list] = {"teams": [], "push": []}
    from ask_soony import push, teams

    monkeypatch.setattr(teams, "post_payload", lambda p: sent["teams"].append(p) or True)
    monkeypatch.setattr(push, "subscriptions", lambda: [{"endpoint": "x"}])
    monkeypatch.setattr(push, "send", lambda *a, **k: sent["push"].append((a, k)) or {"sent": 1})
    from ask_soony import api

    return {"portal": portal, "state": state, "client": TestClient(api.app), "sent": sent}


def _state(env):
    return env["client"].get("/api/state").json()


def _card(env, cid=CARD):
    return next((c for c in _state(env)["cards"] if c["id"] == cid), None)


def _ask(env, cid=CARD) -> str:
    r = env["client"].post(f"/api/cards/{cid}/approve", json={"option": 0})
    assert r.status_code == 200, r.text
    assert r.json()["action"]["status"] == "sent"
    tokens = json.loads((env["state"] / "count_tokens.json").read_text())
    return next(t for t, v in tokens.items() if v["question"] == cid)


# ---------------------------------------------------------------- staleness


def test_staleness_counts_only_shift_hours(env):
    clock = launch.load(env["portal"], TZ)
    ed = clock.editions["P-A"]
    assert (ed.packed, ed.packed_as_of.hour) == (400, 21)
    assert "stock_update_X.xlsx" in ed.packed_source
    night = datetime(2030, 6, 2, 5, 0, tzinfo=TZ)  # 1 h until Sat 22:00, nothing at night
    f = floor.prepare(env["portal"], launch.load(env["portal"], TZ), night, {"shipments_raw": []})
    e = f.editions["P-A"]
    assert (e["stale_hours"], e["age_hours"], e["stale"]) == (1.0, 8.0, False)
    sun = datetime(2030, 6, 2, 7, 0, tzinfo=TZ)
    e = floor.prepare(env["portal"], launch.load(env["portal"], TZ), sun, {}).editions["P-A"]
    assert (e["stale_hours"], e["stale"]) == (2.0, True)
    assert e["projected"] == 500  # 400 + 2 shift hours at 50/h


def test_threshold_from_env(env, monkeypatch):
    monkeypatch.setenv("ASK_PACKOUT_STALE_HOURS", "3")
    assert _card(env) is None
    monkeypatch.setenv("ASK_PACKOUT_STALE_HOURS", "1.5")
    assert _card(env)["status"] == "open"


# ---------------------------------------------------------------- the card and the ask


def test_stale_count_becomes_a_card(env):
    c = _card(env)
    assert c["kind"] == "packed" and c["edition"] == "P-A" and c["level"] == "amber"
    assert c["headline"] == "Big count is 10 h old"
    assert c["summary"] == (
        "The pack-out log last counted 400 at Sat 21:00; we assume 500 by now, and if the cell "
        "stopped it is already 100 short."
    )
    rec, wait = c["options"]
    assert rec["action"] == "Ask Exampleton for the current count" and rec["recommended"]
    assert rec["cost"] == "No cost" and rec["routine_type"] == "site_packed"
    assert wait["action"] == "Wait for the next pack-out log"
    assert c["id"] in _state(env)["inbox_ids"]
    launch_ed = _state(env)["launch"]["editions"][0]
    assert launch_ed["stale"] and launch_ed["counted"]["number"] == 400


def test_waiting_takes_it_off_the_list(env):
    r = env["client"].post(f"/api/cards/{CARD}/approve", json={"option": 1})
    assert r.json()["action"]["status"] == "dismissed"
    assert _card(env)["status"] == "dismissed"
    assert not env["sent"]["teams"]


def test_approve_sends_a_link_and_the_answer_closes_the_card(env):
    token = _ask(env)
    payload = env["sent"]["teams"][0]
    action = payload["attachments"][0]["content"]["actions"][0]
    assert action["url"] == f"https://app.example.com/count/{token}"
    assert env["sent"]["push"][0][1]["url"].endswith(token)
    assert _card(env)["status"] == "sent"

    page = env["client"].get(f"/api/count/{token}").json()
    assert page["kind"] == "packed" and page["site"] == "Exampleton"
    assert page["last"]["value"] == 400 and page["max"] == 1000

    r = env["client"].post(f"/api/count/{token}", json={"count": 430, "by": "Ana"})
    assert r.status_code == 200
    assert r.json()["text"] == "Thanks. Launch now uses 430 packed at 07:00."
    saved = json.loads((env["state"] / "floor_counts.json").read_text())
    assert saved[0] | {"id": "-"} == saved[0] | {
        "id": "-",
        "edition": "P-A",
        "count": 430,
        "by": "Ana",
        "token": token,
    }

    s = _state(env)
    c = next(c for c in s["cards"] if c["id"] == CARD)
    assert c["status"] == "answered"
    texts = [a["text"] for a in s["activity"] if a.get("card") == CARD]
    assert "Exampleton reported 430 at 07:00" in texts
    assert "Asked Exampleton for the Big count" in texts
    ed = s["launch"]["editions"][0]
    assert ed["counted"] == ed["counted"] | {"number": 430, "reported": True, "by": "Ana"}
    # 30 packed instead of the 100 planned since Sat 21:00: the cell likely stood still
    assert ed["below_plan"] == {"copies": 70, "since": SAT_EVENING, "stood_still": True}
    cell = next(x for x in s["strip"] if x["edition"] == "P-A")
    assert cell["remaining"] == 570 and cell["packed_as_of"].startswith("2030-06-02T07:00")
    assert not any(c["kind"] == "packed" and c["status"] == "open" for c in s["cards"])


def test_undo_says_the_question_went_out(env):
    _ask(env)
    act = next(a for a in json.loads((env["state"] / "actions.json").read_text()))
    r = env["client"].post(f"/api/routines/actions/{act['id']}/undo").json()
    assert r == {"ok": False, "reason": "The question already went to Exampleton."}


def test_no_channel_means_nothing_is_sent(env, monkeypatch):
    from ask_soony import push, teams

    monkeypatch.setattr(teams, "post_payload", lambda p: False)
    monkeypatch.setattr(push, "subscriptions", lambda: [])
    r = env["client"].post(f"/api/cards/{CARD}/approve", json={"option": 0})
    assert r.status_code == 503 and "Exampleton" in r.json()["detail"]
    assert _card(env)["status"] == "open"


# ---------------------------------------------------------------- the count page


def test_wrong_or_expired_token_is_404(env, monkeypatch):
    token = _ask(env)
    c = env["client"]
    assert c.get("/api/count/not-a-real-token-at-all").status_code == 404
    assert c.post("/api/count/not-a-real-token-at-all", json={"count": 1}).status_code == 404
    assert floor.public_path(f"/count/{token}") and floor.public_path(f"/api/count/{token}")
    assert not floor.public_path("/api/state") and not floor.public_path("/count/nope")
    monkeypatch.setenv("ASK_NOW", "2030-06-02T20:00:00+02:00")  # launch: the link expires
    assert c.get(f"/api/count/{token}").status_code == 404
    assert not floor.public_path(f"/count/{token}")


def test_basic_auth_lets_only_the_count_page_through(env, monkeypatch):
    from fastapi import FastAPI

    from ask_soony.basic_auth import BasicAuth

    token = _ask(env)
    monkeypatch.setenv("ASK_DASHBOARD_PASSWORD", "pw")
    app = FastAPI()
    for path in ("/count/{t}", "/api/count/{t}", "/api/state"):
        app.add_api_route(path, lambda: {"ok": True})
    app.add_middleware(BasicAuth)
    c = TestClient(app)
    assert c.get(f"/count/{token}").status_code == 200
    assert c.get(f"/api/count/{token}").status_code == 200
    assert c.get("/count/wrong-token-0000000000").status_code == 404
    assert c.get("/api/state").status_code == 401


def test_count_is_checked_before_it_counts(env):
    token = _ask(env)
    c = env["client"]
    low = c.post(f"/api/count/{token}", json={"count": 350})
    assert low.status_code == 422
    assert low.json()["detail"] == (
        "That is less than the 400 already counted at Sat 21:00. Please count again."
    )
    high = c.post(f"/api/count/{token}", json={"count": 1200})
    assert high.status_code == 422 and "1,000" in high.json()["detail"]
    assert not (env["state"] / "floor_counts.json").exists()


def test_ocr_suggests_the_largest_plausible_number():
    words = [
        ["Line", 0.1, 0.1, 0.2, 0.15],
        ["3", 0.3, 0.1, 0.32, 0.15],
        ["52", 0.1, 0.5, 0.15, 0.55],
        ["100", 0.16, 0.5, 0.22, 0.55],
        ["70.000", 0.1, 0.7, 0.2, 0.75],  # above day one: not plausible
        ["DN-261120-732", 0.1, 0.8, 0.3, 0.85],
    ]
    out = floor.suggest_numbers(words, 40_000, 60_000)
    assert out["suggestion"] == 52_100 and out["box"] == [0.1, 0.5, 0.22, 0.55]
    assert out["note_number"] == "DN-261120-732"
    assert floor.suggest_numbers(words, 80_000, 90_000)["suggestion"] is None


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs tesseract")
def test_photo_of_the_sheet_suggests_but_never_submits(env):
    import base64
    import io

    from PIL import Image, ImageDraw, ImageFont

    token = _ask(env)
    img = Image.new("RGB", (900, 400), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=72)
    d.text((40, 60), "Packed total", fill="black", font=font)
    d.text((40, 200), "455", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    out = env["client"].post(f"/api/count/{token}/photo", json={"image": data}).json()
    assert out["suggestion"] == 455 and out["photo"].endswith(".jpg")
    assert not (env["state"] / "floor_counts.json").exists()
    r = env["client"].post(f"/api/count/{token}", json={"count": 455, "photo": out["photo"]})
    assert r.status_code == 200
    ev = next(c for c in _state(env)["cards"] if c["id"] == CARD)["evidence"][-1]
    assert ev["photo"].endswith("?crop=1")
    assert env["client"].get(ev["photo"]).headers["content-type"] == "image/jpeg"


# ---------------------------------------------------------------- other kinds


def test_due_shipment_without_receipt_is_a_question(env):
    cid = "ASK-received-SHP-1-C-LID"
    c = _card(env, cid)
    assert c["kind"] == "received" and c["shipment"] == "SHP-1"
    assert c["headline"] == "Did the 500 lids from the supplier reach Exampleton?"
    assert "Big needs 500 more lids than Exampleton's books show" in c["summary"]
    assert "out for delivery since Fri 12:00" in c["summary"]
    token = _ask(env, cid)
    page = env["client"].get(f"/api/count/{token}").json()
    assert page["kind"] == "received" and page["max"] == 750
    no = env["client"].post(f"/api/count/{token}", json={"arrived": False})
    assert no.status_code == 200 and _card(env, cid)["status"] == "sent"  # stays open
    env["client"].post(f"/api/count/{token}", json={"arrived": True, "count": 480, "by": "Kim"})
    s = _state(env)
    assert next(c for c in s["cards"] if c["id"] == cid)["status"] == "answered"
    assert s["launch"]["floor"]["parts"]["C-LID"]["received"][0]["quantity"] == 480
    # the goods receipt is booked: the books caught up, the reported stock is no longer used
    (env["portal"] / "soony" / "erp" / "goods_receipts.csv").write_text(
        "gr_number,po_number,material_id,qty_received\nGR1,PO1,C-LID,480\n"
    )
    s = _state(env)
    assert "C-LID" not in s["launch"]["floor"]["parts"]


def test_tight_book_stock_counted_long_ago_is_a_question(env):
    s = _state(env)
    kinds = {c["id"]: c for c in s["cards"] if c["kind"] == "count"}
    assert list(kinds) == ["ASK-count-C-BOX-0520"]  # the pin has plenty, no question
    c = kinds["ASK-count-C-BOX-0520"]
    assert c["headline"] == "Are there really 300 boxes in Exampleton?"
    assert c["summary"] == (
        "Big needs 500 boxes until the trucks; the books say 300, about 200 after packing since, "
        "last counted Mon 20 May, so the gap is at least 300."
    )
    token = _ask(env, c["id"])
    assert env["client"].get(f"/api/count/{token}").json()["kind"] == "count"
    env["client"].post(f"/api/count/{token}", json={"count": 250})
    s = _state(env)
    assert s["launch"]["floor"]["parts"]["C-BOX"]["counted"]["count"] == 250
    box = next(x for x in s["cards"] if x["id"] == "ASK-count-C-BOX-0520")
    assert box["status"] == "answered"


# ---------------------------------------------------------------- routines


def test_repeated_count_decisions_are_offered_as_a_routine(env, monkeypatch):
    from ask_soony import routines

    token = _ask(env)
    env["client"].post(f"/api/count/{token}", json={"count": 430})
    # The next count goes stale too: a new question for the same edition.
    monkeypatch.setenv("ASK_NOW", "2030-06-02T09:30:00+02:00")
    second = next(
        c for c in _state(env)["cards"] if c["kind"] == "packed" and c["status"] == "open"
    )
    assert second["id"] != CARD and second["headline"] == "Big count is 2 h old"
    _ask(env, second["id"])
    groups = routines.suggestions()
    assert [g["count"] for g in groups if g["key"].startswith("site|packed")] == [2]


def test_routine_drafted_from_a_count_card_asks_on_its_own(env, monkeypatch):
    from ask_soony import routines

    def no_model(prompt):  # the site question recipe needs no model
        raise AssertionError("no model call expected")

    monkeypatch.setattr(routines, "_complete", no_model)
    token = _ask(env)
    env["client"].post(f"/api/count/{token}", json={"count": 430})
    draft = env["client"].post("/api/routines/suggest", json={"card_id": CARD}).json()
    r = draft["routine"]
    assert r["spec"]["when"][0] == r["spec"]["when"][0] | {
        "field": "kind",
        "op": "is",
        "value": "packed",
        "text": "a packing count is stale",
    }
    assert r["spec"]["then"][0]["text"] == "Ask Exampleton for the current count"
    assert r["name"] == "Ask Exampleton when a count is stale"
    env["client"].post("/api/routines", json={**r, "mode": "quiet"})

    monkeypatch.setenv("ASK_NOW", "2030-06-02T09:30:00+02:00")  # stale again
    sent_before = len(env["sent"]["teams"])
    recs = routines.run_enabled()
    assert recs[0]["ok"], recs
    assert len(env["sent"]["teams"]) == sent_before + 1
    s = _state(env)
    line = next(a for a in s["activity"] if a["text"].startswith("Your routine"))
    assert line["text"] == "Your routine asked Exampleton for the Big count"
    assert line["via"].startswith("routine:")


def test_kind_and_edition_are_routine_conditions(env):
    from ask_soony import routines

    spec = {
        "on": "decisions",
        "when": [
            {"field": "kind", "op": "is", "value": "packed", "text": "a packing count is stale"},
            {"field": "edition", "op": "contains", "value": "big", "text": "for the Big edition"},
        ],
        "then": [{"do": "carry_out", "match": {"action_contains": "ask"}, "text": "Ask"}],
    }
    rec = routines.run({"id": "r", "name": "T", "code": routines.compile_spec(spec)}, dry=True)
    assert [d["card"] for d in rec["did"]] == [CARD]
    other = {**spec, "when": [{**spec["when"][1], "value": "standard"}]}
    rec = routines.run({"id": "r", "name": "T", "code": routines.compile_spec(other)}, dry=True)
    assert rec["did"] == []


def test_reported_packed_count_corrects_the_backflushed_book_stock(env):
    # Book stock is backflushed: the 100 copies projected since Sat 21:00 took 100 boxes off
    # the books. The site counts only 420 packed: 20 boxes used, 580 copies still need one.
    token = _ask(env)
    env["client"].post(f"/api/count/{token}", json={"count": 420})
    box = next(c for c in _state(env)["cards"] if c["id"] == "ASK-count-C-BOX-0520")
    assert "about 280 after packing since" in box["summary"]
    assert "needs 580 boxes" in box["summary"] and box["number"] == 300
