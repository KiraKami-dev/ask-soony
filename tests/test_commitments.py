"""Confirmed orders become inbound: reading replies, estimating arrival, the packing arithmetic,
supersede by goods receipt, and the follow-up when an order lands too late."""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from test_launch import make_portal

from ask_soony import commitments as cm
from ask_soony.launch import EditionPlan, PackClock

TZ = ZoneInfo("Europe/Berlin")
SUN = datetime(2030, 6, 2, tzinfo=TZ)
SUPPLIER = "orders@supplier.example.com"


# ---------------------------------------------------------------- reading a reply


def test_english_offer_is_not_a_confirmation():
    text = (
        "Hello,\n\nwe can release the 8,000 books from our store. Loading is possible within four "
        "hours once we receive your written order confirmation.\n\nBest,\nAnn"
    )
    r = cm.read_reply(text, SUN.replace(hour=7), asked=8000)
    assert r["quantity"] == 8000 and r["ready_hours"] == 4
    assert r["conditional"] and not r["confirms"] and not r["shipped"]


def test_polish_confirmation_with_loading_time():
    text = (
        "Dzień dobry,\n\nrozumiem i potwierdzam: wydajemy łącznie 8 000 szt. C-BOOK, nie 16 000. "
        "Zleciłam magazynowi przygotowanie towaru do załadunku; załadunek jest możliwy w ciągu "
        "4 godzin.\n\nPozdrawiam"
    )
    r = cm.read_reply(text, SUN.replace(hour=7), asked=8000)
    assert r["quantity"] == 8000  # the total, not the 16,000 it rules out
    assert r["confirms"] and r["loading"] and r["ready_hours"] == 4
    assert r["arrival"] is None


def test_stated_arrival_takes_the_latest_date_and_other_parts_are_ignored():
    text = (
        "28,000 C-CARD arrived today. The remaining 20,000 C-CARD are booked on the flight on "
        "Monday, 3 June, with delivery to Hamburg on Tuesday, 4 June. The C-OTHER shipment was "
        "shipped yesterday."
    )
    r = cm.read_reply(text, SUN.replace(hour=1), part="C-CARD", others={"C-OTHER"})
    assert r["quantity"] == 20000 and r["confirms"] and not r["shipped"]
    assert r["arrival"].date().isoformat() == "2030-06-04" and not r["arrival_known_time"]


def test_quantities_skip_codes_measures_and_dates():
    s = "PO 4500318735: 36,000 cards (128 GB) on AWB 020-48113304, 4 hours, 24 November, 8 000 szt."
    assert cm.quantities(s) == [36000, 8000]


# ---------------------------------------------------------------- the packing arithmetic


def _clock(remaining=600, rate=50):
    # Sunday shift 06:00-20:00; 600 copies left at 50/h
    clock = PackClock([(SUN.replace(hour=6), SUN.replace(hour=20))])
    clock.editions["P"] = EditionPlan(
        "P", day_one=1000, packed=1000 - remaining, rate_per_hour=rate
    )
    return clock


def test_arrival_before_the_line_runs_dry_counts_in_full():
    cur = SUN.replace(hour=7)
    # 300 short: 300 packable = 6 h, dry at 13:00. 500 arriving at 12:00 cover all 300.
    res = cm.simulate(_clock(), "P", cur, 300, [(SUN.replace(hour=12), 500)])
    assert res["short_before"] == 300 and res["short"] == 0
    assert res["runs_dry"] is None and res["saved"] == [300]


def test_arrival_after_the_line_runs_dry_counts_only_for_the_hours_left():
    cur = SUN.replace(hour=7)
    # dry at 13:00, parts arrive 16:00: 4 packing hours left x 50/h = 200 saved, 100 still short
    res = cm.simulate(_clock(), "P", cur, 300, [(SUN.replace(hour=16), 500)])
    assert res["saved"] == [200] and res["short"] == 100
    assert res["runs_dry"] == SUN.replace(hour=13)


def test_arrival_at_or_after_the_trucks_saves_nothing():
    cur = SUN.replace(hour=7)
    res = cm.simulate(_clock(), "P", cur, 300, [(SUN.replace(hour=20), 500)])
    assert res["saved"] == [0] and res["short"] == 300


def test_two_arrivals_add_up_in_time_order():
    cur = SUN.replace(hour=7)
    # 100 at 12:00 move the dry time to 15:00; 500 at 17:00 save 3 h x 50 = 150
    res = cm.simulate(
        _clock(), "P", cur, 300, [(SUN.replace(hour=12), 100), (SUN.replace(hour=17), 500)]
    )
    assert res["saved"] == [100, 150] and res["short"] == 50
    assert res["runs_dry"] == SUN.replace(hour=15)


# ---------------------------------------------------------------- transit estimate


def test_road_leg_from_distance_with_driver_breaks(monkeypatch):
    monkeypatch.setenv("ASK_ROAD_KMH", "70")
    monkeypatch.setenv("ASK_ROAD_DETOUR", "1.25")
    nodes = {
        "A": {"name": "Aville", "lat": 52.0, "lon": 10.0},
        "B": {"name": "Bcity", "lat": 52.0, "lon": 10.0},
    }
    # 560 km by road: 8 h driving, one 45 min break; capped by the route's transit days
    nodes["B"]["lon"] = 10.0 + (560 / 1.25) / (111.195 * 0.6157)
    hours, basis = cm.leg_hours(
        {"id": "R", "origin": "A", "destination": "B", "mode": "road", "transit_days": 1}, nodes
    )
    assert hours == pytest.approx(8.75, abs=0.05)
    assert "road" in basis and "1 driver break" in basis
    capped, _ = cm.leg_hours(
        {"id": "R", "origin": "A", "destination": "B", "mode": "road", "transit_days": 0.25}, nodes
    )
    assert capped == 6


# ---------------------------------------------------------------- end to end through /api/state

ORG_LON = 9.99 + (3 * 70 / 1.25) / (111.195 * 0.5939)  # about 3 h by road from the DC


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ASK_ROAD_KMH", "70")
    monkeypatch.setenv("ASK_ROAD_DETOUR", "1.25")
    portal = make_portal(tmp_path / "portal", "400")  # 600 left at 50/h, launch Sun 20:00
    master, erp = portal / "soony" / "master", portal / "soony" / "erp"
    (master / "sites.csv").write_text("site_id,name,type,lat,lon\nDC-1,Example DC,dc,53.55,9.99\n")
    (master / "ports.csv").write_text(
        f"locode,name,country,lat,lon,mode\nORG,Origin Town,PL,53.55,{ORG_LON},road\n"
    )
    (master / "routes.csv").write_text(
        "route_id,origin,destination,mode,carrier_id,transit_days,via\nRT-9,ORG,DC-1,road,LSP-9,1,\n"
    )
    (erp / "open_shipments.csv").write_text(
        "shipment_id,mode,reference,supplier_id,material_ids,route_id,etd,eta,status,updated_at\n"
        "SHP-OLD,road,T-OLD,SUP-9,C-OLD,RT-9,2030-05-01,2030-05-02,delivered,2030-05-02T10:00:00+02:00\n"
    )
    prof = portal / "suppliers" / "SUP-9_Acme"
    prof.mkdir(parents=True)
    (prof / "profile.csv").write_text(
        "supplier_id,name,country,city,lat,lon,contact_name,contact_email,preferred_language\n"
        f"SUP-9,Acme Paper,PL,Origin Town,53.55,{ORG_LON},Ann Example,{SUPPLIER},pl\n"
    )
    state, store = tmp_path / "state", tmp_path / "store"
    state.mkdir()
    store.mkdir()
    for k, v in {
        "ASK_PORTAL_DIR": portal,
        "ASK_STATE_DIR": state,
        "ASK_DATA_DIR": store,
        "ASK_NOW": "2030-06-02T07:00:00+02:00",
    }.items():
        monkeypatch.setenv(k, str(v))
    (state / "dispatch.json").write_text(
        json.dumps(
            {
                "editions": [{"product": "P-A", "day_one": 1000}],
                "parts": [
                    {
                        "part": "C-BOOK",
                        "description": "Book",
                        "editions": ["P-A"],
                        "per_copy": {"P-A": 1},
                        "needed": 600,
                        "usable": 600,
                        "gap": 0,
                    }
                ],
                "decisions": [
                    {
                        "id": "order-C-BOOK",
                        "title": "300 books short",
                        "edition": "P-A",
                        "part": "C-BOOK",
                        "at_risk": 300,
                        "options": [],
                        "messages": [
                            {
                                "to_email": SUPPLIER,
                                "to_role": "Ann Example (Acme Paper)",
                                "subject": "Order 500 Book from stock",
                                "body": "Please send 500.",
                            }
                        ],
                    }
                ],
            }
        )
    )
    (state / "decision_status.json").write_text(
        json.dumps({"order-C-BOOK": {"status": "sent", "at": "2030-06-02T06:30:00+02:00"}})
    )

    def reply(text: str, at: str = "2030-06-02T07:00:00+02:00") -> None:
        (state / "actions.json").write_text(
            json.dumps(
                [
                    {
                        "id": "a1",
                        "decision_id": "order-C-BOOK",
                        "at": "2030-06-02T06:30:00+02:00",
                        "kind": "approve",
                        "option": 0,
                        "channel": "email",
                        "to": SUPPLIER,
                        "action": "Email Ann Example: Order 500 Book from stock",
                        "status": "sent",
                        "message_id": "<m1@example.com>",
                        "subject": "300 books short",
                    },
                    {
                        "id": "r1",
                        "decision_id": "order-C-BOOK",
                        "at": at,
                        "kind": "reply",
                        "status": "replied",
                        "from": f"Ann Example <{SUPPLIER}>",
                        "subject": "Re: 300 books short",
                        "text": text,
                        "message_id": "<r1@example.com>",
                        "in_reply_to": "<m1@example.com>",
                    },
                ]
            )
        )

    from ask_soony import api

    return TestClient(api.app), reply, portal


def _book(state):
    return next(p for p in state["launch"]["parts"] if p["part"] == "C-BOOK")


def test_confirmed_order_in_time_closes_the_gap(env):
    client, reply, _ = env
    reply("Dzień dobry, potwierdzam: wydajemy 500 szt. C-BOOK. Załadunek w ciągu 2 godzin.")
    s = client.get("/api/state").json()
    book = _book(s)
    # without the order: 300 short, dry at 13:00; it lands about 07:00 + 2 h + 3 h = 12:00
    assert book["arithmetic"]["short_before"] == 300
    assert book["arithmetic"]["runs_dry_before"].startswith("2030-06-02T13:00")
    row = next(r for r in book["inbound"] if r.get("commitment"))
    c = row["commitment"]
    assert c["status"] == "confirmed" and c["quantity"] == 500 and c["estimated"]
    assert c["eta"].startswith("2030-06-02T12:0") or c["eta"].startswith("2030-06-02T11:5")
    assert row["counted"] and c["saves"] == 300
    assert book["short"] == 0 and book["runs_dry"] is None
    assert "500 ordered from Acme Paper, confirmed 07:00" in c["line"]
    assert "confirmation + 2 h loading" in c["line"]
    assert not [x for x in s["cards"] if x["kind"] == cm.KIND]
    card = next(x for x in s["cards"] if x["id"] == "order-C-BOOK")
    assert card["commitment_line"].startswith("Ordered 06:30 · Acme confirmed 500 at 07:00")
    assert "truck details pending" in card["commitment_line"]


def test_order_that_lands_after_the_line_runs_dry_opens_a_decision(env):
    client, reply, _ = env
    reply("We confirm 500 C-BOOK. Loading is possible within 6 hours.")
    s = client.get("/api/state").json()
    book = _book(s)
    # ready 13:00 + 3 h road = about 16:00; the line stood from 13:00, 4 h x 50 = about 200 saved
    eta = datetime.fromisoformat(book["arithmetic"]["arrivals"][0]["eta"])
    saved = round(50 * (SUN.replace(hour=20) - eta) / timedelta(hours=1))
    assert book["arithmetic"]["arrivals"][0]["saves"] == saved
    assert book["short"] == 300 - saved and book["runs_dry"].startswith("2030-06-02T13:00")
    late = next(x for x in s["cards"] if x["kind"] == cm.KIND)
    assert late["status"] == "open" and late["level"] == "red" and late["number"] == 300 - saved
    assert "the line runs dry at 13:00" in late["headline"]
    assert late["id"] in book["cards"] and late["id"] in s["inbox_ids"]
    ask, site, accept = late["options"]
    assert ask["channel"] == "email" and ask["to"] == SUPPLIER
    assert ask["in_reply_to"] == "<r1@example.com>" and ask["subject"] == "Re: 300 books short"
    assert ask["draft"].startswith("Dzień dobry") and "Dear Ann Example" in ask["draft"]
    assert site["channel"] == "notify" and accept["channel"] == "wait"
    strip = next(x for x in s["strip"] if x["edition"] == "P-A")
    assert strip["copies_at_risk"] >= 300 - saved
    assert strip["stops_at"].startswith("2030-06-02T13:00")


def test_offer_without_confirmation_is_listed_but_not_counted(env):
    client, reply, _ = env
    reply("We can release 500 C-BOOK once we receive your written PO.")
    book = _book(client.get("/api/state").json())
    row = next(r for r in book["inbound"] if r.get("commitment"))
    assert row["commitment"]["status"] == "offered" and not row["counted"]
    assert book["short"] == 300


def test_goods_receipt_supersedes_the_commitment(env):
    client, reply, portal = env
    reply("Potwierdzam 500 szt. C-BOOK, załadunek w ciągu 2 godzin.")
    (portal / "soony" / "erp" / "goods_receipts.csv").write_text(
        "gr_number,gr_date,po_number,po_line,supplier_id,material_id,plant,qty_ordered,qty_received\n"
        "GR-1,2030-06-02,PO-1,1,SUP-9,C-BOOK,DC-1,500,500\n"
    )
    s = client.get("/api/state").json()
    book = _book(s)
    assert not [r for r in book["inbound"] if r.get("commitment")]  # in stock, not on its way
    card = next(x for x in s["cards"] if x["id"] == "order-C-BOOK")
    assert "Acme delivered 500" in card["commitment_line"]
    assert not [x for x in s["cards"] if x["kind"] == cm.KIND]


def test_late_ask_goes_out_in_the_suppliers_thread(monkeypatch):
    from ask_soony import cardtools

    sent = {}
    monkeypatch.setattr(cardtools, "known_addresses", lambda: {SUPPLIER})
    monkeypatch.setattr(
        cardtools,
        "_send_mail_in_thread",
        lambda to, subject, body, irt, refs: sent.update(to=to, irt=irt, refs=refs) or "<n@x>",
    )
    monkeypatch.setattr(cardtools, "_send_mail", lambda *a, **k: pytest.fail("not in thread"))
    option = {
        "channel": "email",
        "to": SUPPLIER,
        "draft": "Can you send a van?",
        "subject": "Re: Order",
        "in_reply_to": "<r1@example.com>",
        "references": ["<m1@example.com>", "<r1@example.com>"],
    }
    out = cardtools.deliver({"id": "late-x", "headline": "x"}, option, None)
    assert out["message_id"] == "<n@x>" and sent["irt"] == "<r1@example.com>"
    assert sent["refs"] == "<m1@example.com> <r1@example.com>"
